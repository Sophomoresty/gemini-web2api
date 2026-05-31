"""HTTP server: OpenAI-compatible API endpoints."""
import base64
from http import cookies
import hmac
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import re
import secrets
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from .config import CONFIG, api_keys_list, persist_config, validate_api_key_value
from .models import MODELS, resolve_model
from .gemini import HAS_HTTPX, generate, generate_stream, log, reset_httpx_client
from .tools import (
    google_contents_to_prompt,
    messages_to_prompt,
    parse_google_function_calls,
    parse_tool_calls,
    tool_names_from_tools,
)
from .multimodal import upload_image, fetch_image_bytes
from . import __version__
from .admin_ui import ADMIN_HTML

APP_ROOT = Path(__file__).resolve().parent.parent
LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Gemini2API 登录</title>
  <style>
    :root{--bg:#f3f7fb;--panel:#fff;--text:#152238;--muted:#667085;--line:#d8e0ea;--primary:#2563eb;--danger:#b42318}
    *{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f7fb;color:var(--text)}
    .login{width:min(420px,100%);background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 18px 60px rgba(21,34,56,.13);padding:28px}
    .brand{display:flex;align-items:center;gap:14px;margin-bottom:26px}.brand img{width:46px;height:46px;border-radius:8px;object-fit:cover;border:1px solid var(--line)}.brand strong{display:block;font-size:22px}.brand span{display:block;margin-top:3px;color:var(--muted);font-size:13px}
    label{display:block;margin:18px 0 8px;color:#304057;font-size:13px;font-weight:700}input{width:100%;border:1px solid var(--line);border-radius:8px;padding:13px 14px;font:inherit;color:var(--text);background:#fbfdff;outline:none}input:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
    button{width:100%;margin-top:22px;border:0;border-radius:8px;padding:13px 16px;font:inherit;font-weight:800;color:#fff;background:var(--primary);cursor:pointer}button:disabled{opacity:.62;cursor:wait}.status{min-height:22px;margin-top:14px;color:var(--danger);font-size:13px;line-height:1.6}.meta{margin-top:22px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:12px}
  </style>
</head>
<body>
  <main class="login">
    <div class="brand"><img src="/assets/logo.png" alt="" /><div><strong>Gemini2API</strong><span>Web 管理台</span></div></div>
    <form id="login-form">
      <label for="username">账号</label><input id="username" name="username" autocomplete="username" required />
      <label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required />
      <button id="submit" type="submit">登录</button><div id="status" class="status" role="status"></div>
    </form>
    <div class="meta"><span>v__VERSION__</span><span>Gemini Web Proxy</span></div>
  </main>
  <script>
    const form=document.getElementById("login-form"),statusEl=document.getElementById("status"),submit=document.getElementById("submit");
    form.addEventListener("submit",async event=>{event.preventDefault();statusEl.textContent="";submit.disabled=true;try{const payload={username:form.username.value.trim(),password:form.password.value};const response=await fetch("/admin/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.error||"登录失败")}window.location.href="/admin"}catch(error){statusEl.textContent=error.message}finally{submit.disabled=false}});
  </script>
</body>
</html>
"""

_STARTED_AT = int(time.time())
_STATS_LOCK = threading.Lock()
_STATS_CACHE = None


def _usage(prompt: str, text: str) -> dict:
    p = len(prompt) // 4
    c = len(text or "") // 4
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def _empty_stats() -> dict:
    return {
        "started_at": _STARTED_AT,
        "total": 0,
        "success": 0,
        "failure": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "by_model": {},
        "by_key": {},
        "recent": [],
    }


def _stats_path() -> Path:
    configured = CONFIG.get("session_stats_file")
    if configured:
        return Path(str(configured)).expanduser()
    return APP_ROOT / "session_stats.json"


def _load_stats() -> dict:
    global _STATS_CACHE
    if _STATS_CACHE is not None:
        return _STATS_CACHE
    path = _stats_path()
    try:
        if path.exists():
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                base = _empty_stats()
                base.update(data)
                base.setdefault("by_model", {})
                base.setdefault("by_key", {})
                base.setdefault("recent", [])
                _STATS_CACHE = base
                return _STATS_CACHE
    except Exception as e:
        log(f"Stats load error: {e}")
    _STATS_CACHE = _empty_stats()
    return _STATS_CACHE


def _save_stats(stats: dict):
    path = _stats_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception as e:
        log(f"Stats save error: {e}")


def _mask_key(value: str) -> str:
    value = str(value or "")
    if not value:
        return "-"
    if value == "Web Console":
        return value
    if len(value) <= 14:
        return value
    return f"{value[:8]}...{value[-6:]}"


def _normalize_usage(usage: dict = None) -> dict:
    usage = usage or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("promptTokenCount") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("candidatesTokenCount") or 0)
    total_tokens = int(usage.get("total_tokens") or usage.get("totalTokenCount") or (input_tokens + output_tokens))
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}


def _record_session(source: str, model: str, status: str, usage: dict = None, api_key: str = "", error: str = "", started_at: float = None):
    token_usage = _normalize_usage(usage)
    now = int(time.time())
    duration_ms = int((time.time() - started_at) * 1000) if started_at else 0
    key_label = _mask_key(api_key or "Web Console")
    with _STATS_LOCK:
        stats = _load_stats()
        stats["total"] = int(stats.get("total", 0)) + 1
        if status == "success":
            stats["success"] = int(stats.get("success", 0)) + 1
        else:
            stats["failure"] = int(stats.get("failure", 0)) + 1
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            stats[field] = int(stats.get(field, 0)) + int(token_usage.get(field, 0))
        for bucket_name, bucket_key in (("by_model", model or "-"), ("by_key", key_label)):
            bucket = stats.setdefault(bucket_name, {})
            item = bucket.setdefault(bucket_key, {"success": 0, "failure": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "last_at": 0})
            item[status] = int(item.get(status, 0)) + 1
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                item[field] = int(item.get(field, 0)) + int(token_usage.get(field, 0))
            item["last_at"] = now
        recent = stats.setdefault("recent", [])
        recent.insert(0, {
            "at": now,
            "source": source,
            "model": model or "-",
            "status": status,
            "key": key_label,
            "input_tokens": token_usage["input_tokens"],
            "output_tokens": token_usage["output_tokens"],
            "total_tokens": token_usage["total_tokens"],
            "duration_ms": duration_ms,
            "error": str(error or "")[:180],
        })
        del recent[80:]
        _save_stats(stats)


def _stats_snapshot() -> dict:
    with _STATS_LOCK:
        stats = _load_stats()
        total = int(stats.get("total", 0))
        success = int(stats.get("success", 0))
        summary = {
            "started_at": int(stats.get("started_at") or _STARTED_AT),
            "total": total,
            "success": success,
            "failure": int(stats.get("failure", 0)),
            "success_rate": (success / total) if total else 0,
            "input_tokens": int(stats.get("input_tokens", 0)),
            "output_tokens": int(stats.get("output_tokens", 0)),
            "total_tokens": int(stats.get("total_tokens", 0)),
        }

        def rows(bucket_name: str, key_field: str):
            items = []
            for key, item in (stats.get(bucket_name) or {}).items():
                ok = int(item.get("success", 0))
                fail = int(item.get("failure", 0))
                items.append({
                    key_field: key,
                    "success": ok,
                    "failure": fail,
                    "total": ok + fail,
                    "input_tokens": int(item.get("input_tokens", 0)),
                    "output_tokens": int(item.get("output_tokens", 0)),
                    "total_tokens": int(item.get("total_tokens", 0)),
                    "last_at": int(item.get("last_at", 0)),
                })
            return sorted(items, key=lambda x: x.get("last_at", 0), reverse=True)

        return {
            "summary": summary,
            "by_model": rows("by_model", "model"),
            "by_key": rows("by_key", "key"),
            "recent": list(stats.get("recent") or []),
            "path": str(_stats_path()),
        }


def _upload_images(images: list) -> list:
    """Upload images and return list of file references. Returns None if no images."""
    if not images:
        return None
    file_refs = []
    for item in images:
        try:
            if isinstance(item, tuple) and len(item) == 2:
                data, mime = item
                if isinstance(data, str):
                    data = fetch_image_bytes(data)
                    mime = mime or "image/png"
                if data:
                    ref = upload_image(data, "image.png", mime or "image/png")
                    file_refs.append(ref)
        except Exception as e:
            log(f"Image upload failed: {e}")
    return file_refs if file_refs else None


class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        message = fmt % args
        message = re.sub(r'([?&]key=)[^&\s"]+', r'\1<redacted>', message)
        log(message)

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def _query(self) -> dict:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _request_api_key(self) -> str:
        auth = self.headers.get("Authorization", "")
        key = auth.split(None, 1)[1].strip() if auth.lower().startswith("bearer ") else ""
        key = key or self.headers.get("x-api-key", "").strip()
        key = key or self.headers.get("x-goog-api-key", "").strip()
        key = key or (self._query().get("key", [""])[0]).strip()
        return key

    def _client_ip(self) -> str:
        forwarded = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Real-IP")
        if not forwarded:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return forwarded or self.client_address[0]

    def send_json(self, data, status=200, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_text: str, status=200, extra_headers=None):
        body = html_text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        if not path.exists():
            self.send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _write_sse_json(self, data: dict):
        self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())

    def _write_chat_tool_call_stream(self, cid: str, model_name: str, tool_calls: list):
        created = int(time.time())
        first = {"id": cid, "object": "chat.completion.chunk", "created": created,
                 "model": model_name, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
        self._write_sse_json(first)
        for index, call in enumerate(tool_calls or []):
            fn = call.get("function", {})
            head = {"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model_name, "choices": [{"index": 0, "delta": {"tool_calls": [{
                        "index": index,
                        "id": call.get("id", ""),
                        "type": "function",
                        "function": {"name": fn.get("name", ""), "arguments": ""},
                    }]}, "finish_reason": None}]}
            self._write_sse_json(head)
            args = fn.get("arguments", "{}")
            arg_chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                         "model": model_name, "choices": [{"index": 0, "delta": {"tool_calls": [{
                             "index": index,
                             "function": {"arguments": args},
                         }]}, "finish_reason": None}]}
            self._write_sse_json(arg_chunk)
        final = {"id": cid, "object": "chat.completion.chunk", "created": created,
                 "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
        self._write_sse_json(final)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _parse_body(self, body: bytes) -> dict:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return None

    def _parse_admin_body(self, body: bytes) -> dict:
        if not body:
            return {}
        ctype = self.headers.get("Content-Type", "")
        if "application/x-www-form-urlencoded" in ctype:
            return {k: v[0] if v else "" for k, v in urllib.parse.parse_qs(body.decode()).items()}
        return self._parse_body(body) or {}

    def _authorized(self):
        keys = api_keys_list()
        if not keys:
            return True
        return self._request_api_key() in keys

    def _proxy_info(self, proxy_value=None) -> dict:
        raw_proxy = CONFIG.get("proxy") if proxy_value is None else proxy_value
        proxy = str(raw_proxy or "").strip()
        if not proxy:
            return {"configured": False, "raw": "", "scheme": "", "host": "", "port": "", "username": "", "has_password": False}
        parsed = urllib.parse.urlparse(proxy)
        return {
            "configured": True,
            "raw": proxy,
            "scheme": parsed.scheme or "",
            "host": parsed.hostname or "",
            "port": parsed.port or "",
            "username": parsed.username or "",
            "has_password": bool(parsed.password),
        }

    def _settings_payload(self) -> dict:
        return {
            "web_username": str(CONFIG.get("web_username", "admin")),
            "default_model": str(CONFIG.get("default_model", "gemini-3.5-flash")),
            "proxy": str(CONFIG.get("proxy") or ""),
            "retry_attempts": int(CONFIG.get("retry_attempts", 3)),
            "retry_delay_sec": int(CONFIG.get("retry_delay_sec", 2)),
            "request_timeout_sec": int(CONFIG.get("request_timeout_sec", 180)),
            "log_requests": bool(CONFIG.get("log_requests", True)),
            "cookie_file": str(CONFIG.get("cookie_file") or ""),
            "cookie_enabled": bool(CONFIG.get("cookie_file")),
            "session_ttl_sec": int(CONFIG.get("session_ttl_sec", 86400)),
            "session_stats_file": str(_stats_path()),
            "streaming": "httpx" if HAS_HTTPX else "urllib",
        }

    def _session_secret(self) -> bytes:
        secret = CONFIG.get("session_secret")
        if not secret:
            secret = secrets.token_hex(32)
            CONFIG["session_secret"] = secret
        return str(secret).encode()

    def _make_session_token(self, username: str) -> str:
        expires_at = int(time.time()) + int(CONFIG.get("session_ttl_sec", 86400))
        payload = json.dumps({"u": username, "exp": expires_at}, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        sig = hmac.new(self._session_secret(), encoded.encode(), "sha256").hexdigest()
        return f"{encoded}.{sig}"

    def _verify_session_token(self, token: str) -> str:
        if not token or "." not in token:
            return ""
        encoded, sig = token.rsplit(".", 1)
        expected = hmac.new(self._session_secret(), encoded.encode(), "sha256").hexdigest()
        if not hmac.compare_digest(sig, expected):
            return ""
        try:
            padded = encoded + ("=" * (-len(encoded) % 4))
            payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        except Exception:
            return ""
        if int(payload.get("exp", 0)) < int(time.time()):
            return ""
        return str(payload.get("u") or "")

    def _cookie_header(self, value: str, max_age: int = None) -> str:
        name = CONFIG.get("session_cookie", "gemini2api_session")
        parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        return "; ".join(parts)

    def _current_user(self) -> str:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return ""
        morsel = jar.get(CONFIG.get("session_cookie", "gemini2api_session"))
        if not morsel:
            return ""
        username = self._verify_session_token(morsel.value)
        if username != str(CONFIG.get("web_username", "admin")):
            return ""
        return username

    def _require_web_auth(self) -> bool:
        if self._current_user():
            return True
        self.send_json({"error": "unauthorized"}, 401)
        return False

    def _render_login(self):
        self._send_html(LOGIN_HTML.replace("__VERSION__", __version__))

    def _render_admin(self):
        if not self._current_user():
            self._redirect("/admin/login")
            return
        self._send_html(ADMIN_HTML.replace("__VERSION__", __version__))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_HEAD(self):
        path = self._path()
        if path in ("/", "/admin", "/admin/", "/admin/login", "/login", "/healthz", "/v1/models", "/v1beta/models"):
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        try:
            path = self._path()
            if path == "/assets/logo.png":
                self._send_file(APP_ROOT / "logo.png", "image/png")
            elif path in ("/admin/login", "/login"):
                self._render_login()
            elif path in ("/admin", "/admin/", "/chat"):
                self._render_admin()
            elif path == "/admin/logout":
                self._redirect("/admin/login", {"Set-Cookie": self._cookie_header("", max_age=0)})
            elif path == "/admin/api/status":
                if not self._require_web_auth():
                    return
                self.send_json({
                    "status": "ok",
                    "version": __version__,
                    "default_model": CONFIG["default_model"],
                    "proxy": CONFIG.get("proxy") or "",
                    "proxy_info": self._proxy_info(),
                    "api_auth": bool(api_keys_list()),
                    "cookie": bool(CONFIG.get("cookie_file")),
                    "google_native_api": True,
                    "responses_api": True,
                    "claude_code_api": False,
                    "streaming": "httpx" if HAS_HTTPX else "urllib",
                    "started_at": _STARTED_AT,
                })
            elif path == "/admin/api/settings":
                if not self._require_web_auth():
                    return
                self.send_json({"settings": self._settings_payload()})
            elif path == "/admin/api/session-stats":
                if not self._require_web_auth():
                    return
                self.send_json(_stats_snapshot())
            elif path == "/admin/api/proxy":
                if not self._require_web_auth():
                    return
                self.send_json({"proxy": CONFIG.get("proxy") or "", "proxy_info": self._proxy_info()})
            elif path == "/admin/api/models":
                if not self._require_web_auth():
                    return
                self._send_models_openai()
            elif path == "/admin/api/api-keys":
                if not self._require_web_auth():
                    return
                self.send_json({"api_keys": api_keys_list()})
            elif path == "/healthz":
                self.send_json({"status": "ok", "version": __version__})
            elif (path.startswith("/v1/") or path.startswith("/v1beta/")) and not self._authorized():
                self.send_json({"error": {"message": "invalid api key"}}, 401)
                return
            elif path == "/v1/models":
                self._send_models_openai()
            elif path.startswith("/v1beta/models"):
                self.send_json({"models": [
                    {"name": f"models/{n}", "displayName": n, "description": c["desc"],
                     "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]}
                    for n, c in MODELS.items()
                ]})
            elif path == "/":
                self._redirect("/admin")
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_models_openai(self):
        self.send_json({"object": "list", "data": [
            {"id": n, "object": "model", "created": 1700000000,
             "owned_by": "google", "description": c["desc"]}
            for n, c in MODELS.items()
        ]})

    def do_POST(self):
        try:
            path = self._path()
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            if path == "/admin/api/login":
                self._handle_login(body)
            elif path == "/admin/api/chat":
                self._handle_admin_chat(body)
            elif path == "/admin/api/api-keys":
                self._handle_api_key_create(body)
            elif path == "/admin/api/api-test":
                self._handle_admin_api_test(body)
            elif path == "/admin/api/proxy/test":
                self._handle_proxy_test(body)
            elif (path.startswith("/v1/") or path.startswith("/v1beta/")) and not self._authorized():
                self.send_json({"error": {"message": "invalid api key"}}, 401)
                return
            elif path == "/v1/chat/completions":
                self._handle_chat(body)
            elif path == "/v1/responses":
                self._handle_responses(body)
            elif ":generateContent" in path:
                self._handle_google_generate(body, stream=False)
            elif ":streamGenerateContent" in path:
                self._handle_google_generate(body, stream=True)
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"POST error: {e}")
            try:
                self.send_json({"error": {"message": str(e)}}, 500)
            except:
                pass

    def do_PATCH(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            path = self._path()
            if path in ("/admin/api/api-keys", "/admin/api/settings", "/admin/api/proxy") and not self._require_web_auth():
                return
            if path == "/admin/api/api-keys":
                self._handle_api_key_update(body)
            elif path == "/admin/api/settings":
                self._handle_settings_update(body)
            elif path == "/admin/api/proxy":
                self._handle_proxy_update(body)
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"PATCH error: {e}")
            self.send_json({"error": str(e)}, 500)

    def do_DELETE(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            path = self._path()
            if path == "/admin/api/api-keys" and not self._require_web_auth():
                return
            if path == "/admin/api/api-keys":
                self._handle_api_key_delete(body)
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"DELETE error: {e}")
            self.send_json({"error": str(e)}, 500)

    # ─── Web admin ───────────────────────────────────────────────────────────

    def _handle_login(self, body: bytes):
        data = self._parse_admin_body(body)
        username = str(data.get("username", ""))
        password = str(data.get("password", ""))
        expected_user = str(CONFIG.get("web_username", "admin"))
        expected_password = str(CONFIG.get("web_password", ""))
        if hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password):
            token = self._make_session_token(username)
            log(f"ADMIN_LOGIN_OK ip={self._client_ip()} user={username}")
            self.send_json({"ok": True}, 200, {
                "Set-Cookie": self._cookie_header(token, max_age=int(CONFIG.get("session_ttl_sec", 86400)))
            })
            return
        log(f"ADMIN_LOGIN_FAILED ip={self._client_ip()} user={username or '-'}")
        self.send_json({"error": "账号或密码错误"}, 401)

    def _handle_admin_chat(self, body: bytes):
        if not self._require_web_auth():
            return
        req = self._parse_admin_body(body)
        message = str(req.get("message", "")).strip()
        if not message:
            self.send_json({"error": "消息不能为空"}, 400)
            return
        model_name, model_id, think_mode, err, extra_fields = resolve_model(req.get("model") or CONFIG["default_model"])
        if err:
            self.send_json({"error": err}, 400)
            return
        history = req.get("history") or []
        messages = []
        if isinstance(history, list):
            for item in history[-20:]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})
        prompt, images = messages_to_prompt(messages)
        uploaded_images = _upload_images(images)
        started = time.time()
        if req.get("stream"):
            try:
                self._start_sse()
                full_text = ""
                for delta in generate_stream(prompt, model_id, think_mode, uploaded_images, extra_fields):
                    if not delta:
                        continue
                    full_text += delta
                    self.wfile.write(f"data: {json.dumps({'type': 'delta', 'delta': delta}, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                usage = _usage(prompt, full_text)
                _record_session("Web 在线聊天 Stream", model_name, "success", usage, api_key="Web 管理台", started_at=started)
                done = {"type": "done", "model": model_name, "content": full_text, "usage": usage}
                self.wfile.write(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                _record_session("Web 在线聊天 Stream", model_name, "failure", api_key="Web 管理台", error=e, started_at=started)
                try:
                    error = {"type": "error", "error": f"upstream error: {e}"}
                    self.wfile.write(f"data: {json.dumps(error, ensure_ascii=False)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return
        try:
            text = generate(prompt, model_id, think_mode, uploaded_images, extra_fields)
        except Exception as e:
            _record_session("Web 在线聊天", model_name, "failure", api_key="Web 管理台", error=e, started_at=started)
            self.send_json({"error": f"upstream error: {e}"}, 502)
            return
        usage = _usage(prompt, text or "")
        _record_session("Web 在线聊天", model_name, "success", usage, api_key="Web 管理台", started_at=started)
        self.send_json({"model": model_name, "content": text or "", "usage": usage})

    def _handle_api_key_create(self, body: bytes):
        if not self._require_web_auth():
            return
        req = self._parse_admin_body(body)
        key = str(req.get("key") or "").strip()
        if not key:
            key = f"sk-gemini2api-{secrets.token_urlsafe(24)}"
        try:
            key = validate_api_key_value(key)
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)
            return
        keys = api_keys_list()
        if key in keys:
            self.send_json({"error": "API 密钥已存在"}, 409)
            return
        keys.append(key)
        CONFIG["api_keys"] = keys
        try:
            persist_config()
        except Exception as e:
            self.send_json({"error": f"保存配置失败: {e}"}, 500)
            return
        self.send_json({"api_keys": keys, "created": key}, 201)

    def _handle_api_key_update(self, body: bytes):
        req = self._parse_admin_body(body)
        old_key = str(req.get("old_key") or "").strip()
        try:
            new_key = validate_api_key_value(req.get("new_key"))
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)
            return
        keys = api_keys_list()
        if old_key not in keys:
            self.send_json({"error": "原 API 密钥不存在"}, 404)
            return
        if new_key != old_key and new_key in keys:
            self.send_json({"error": "新 API 密钥已存在"}, 409)
            return
        CONFIG["api_keys"] = [new_key if key == old_key else key for key in keys]
        try:
            persist_config()
        except Exception as e:
            self.send_json({"error": f"保存配置失败: {e}"}, 500)
            return
        self.send_json({"api_keys": api_keys_list()})

    def _handle_api_key_delete(self, body: bytes):
        req = self._parse_admin_body(body)
        key = str(req.get("key") or "").strip()
        keys = api_keys_list()
        if key not in keys:
            self.send_json({"error": "API 密钥不存在"}, 404)
            return
        if len(keys) <= 1:
            self.send_json({"error": "至少保留一个 API 密钥，避免公开接口无鉴权"}, 400)
            return
        CONFIG["api_keys"] = [item for item in keys if item != key]
        try:
            persist_config()
        except Exception as e:
            self.send_json({"error": f"保存配置失败: {e}"}, 500)
            return
        self.send_json({"api_keys": api_keys_list()})

    def _handle_settings_update(self, body: bytes):
        req = self._parse_admin_body(body)
        old_proxy = CONFIG.get("proxy") or ""
        old_username = str(CONFIG.get("web_username", "admin"))

        def int_value(name: str, minimum: int = None, maximum: int = None):
            if name not in req:
                return None
            try:
                value = int(req.get(name))
            except (TypeError, ValueError):
                raise ValueError(f"{name} 必须是整数")
            if minimum is not None and value < minimum:
                raise ValueError(f"{name} 不能小于 {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"{name} 不能大于 {maximum}")
            return value

        try:
            if "default_model" in req:
                model = str(req.get("default_model") or "").strip()
                _, _, _, err, _ = resolve_model(model)
                if err:
                    raise ValueError(err)
                CONFIG["default_model"] = model
            if "proxy" in req:
                proxy = str(req.get("proxy") or "").strip()
                CONFIG["proxy"] = proxy or None
            if "cookie_file" in req:
                cookie_file = str(req.get("cookie_file") or "").strip()
                CONFIG["cookie_file"] = cookie_file or None
            if "web_username" in req:
                username = str(req.get("web_username") or "").strip()
                if not username:
                    raise ValueError("登录账号不能为空")
                CONFIG["web_username"] = username
            if "web_password" in req:
                password = str(req.get("web_password") or "")
                if password:
                    if len(password) < 4:
                        raise ValueError("登录密码不能少于 4 个字符")
                    CONFIG["web_password"] = password
            if "log_requests" in req:
                CONFIG["log_requests"] = bool(req.get("log_requests"))
            for name, minimum, maximum in (
                ("retry_attempts", 1, 10),
                ("retry_delay_sec", 0, 60),
                ("request_timeout_sec", 5, 900),
                ("session_ttl_sec", 300, 2592000),
            ):
                value = int_value(name, minimum, maximum)
                if value is not None:
                    CONFIG[name] = value
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)
            return

        if (CONFIG.get("proxy") or "") != old_proxy:
            reset_httpx_client()

        try:
            persist_config()
        except Exception as e:
            self.send_json({"error": f"保存配置失败: {e}"}, 500)
            return

        headers = {}
        new_username = str(CONFIG.get("web_username", "admin"))
        if new_username != old_username:
            headers["Set-Cookie"] = self._cookie_header(
                self._make_session_token(new_username),
                max_age=int(CONFIG.get("session_ttl_sec", 86400)),
            )
        self.send_json({"settings": self._settings_payload()}, extra_headers=headers)

    def _handle_proxy_update(self, body: bytes):
        req = self._parse_admin_body(body)
        proxy = str(req.get("proxy") or "").strip()
        old_proxy = CONFIG.get("proxy") or ""
        CONFIG["proxy"] = proxy or None
        if (CONFIG.get("proxy") or "") != old_proxy:
            reset_httpx_client()
        try:
            persist_config()
        except Exception as e:
            self.send_json({"error": f"保存配置失败: {e}"}, 500)
            return
        self.send_json({"proxy": CONFIG.get("proxy") or "", "proxy_info": self._proxy_info()})

    def _handle_proxy_test(self, body: bytes):
        if not self._require_web_auth():
            return
        req = self._parse_admin_body(body)
        proxy = str(req.get("proxy") if "proxy" in req else CONFIG.get("proxy") or "").strip()
        if not proxy:
            self.send_json({"success": False, "message": "未配置代理", "proxy": ""})
            return
        started = time.time()
        try:
            request = urllib.request.Request(
                "https://gemini.google.com/",
                headers={"User-Agent": "Mozilla/5.0 Gemini2API Proxy Test"},
                method="GET",
            )
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
            with opener.open(request, timeout=min(int(CONFIG.get("request_timeout_sec", 180)), 15)) as response:
                status = int(response.status)
                response.read(256)
            duration_ms = int((time.time() - started) * 1000)
            self.send_json({
                "success": status < 500,
                "status": status,
                "proxy": proxy,
                "duration_ms": duration_ms,
                "message": f"代理连通，HTTP {status}，耗时 {duration_ms}ms",
            })
        except Exception as e:
            duration_ms = int((time.time() - started) * 1000)
            self.send_json({
                "success": False,
                "proxy": proxy,
                "duration_ms": duration_ms,
                "message": f"代理测试失败: {e}",
            })

    def _handle_admin_api_test(self, body: bytes):
        if not self._require_web_auth():
            return
        req = self._parse_admin_body(body)
        mode = str(req.get("mode") or "chat").strip()
        message = str(req.get("message") or "").strip()
        api_key = str(req.get("api_key") or "").strip()
        model = str(req.get("model") or CONFIG.get("default_model") or "").strip()
        if not message:
            self.send_json({"error": "测试消息不能为空"}, 400)
            return
        model_name, _, _, err, _ = resolve_model(model)
        if err:
            self.send_json({"error": err}, 400)
            return
        keys = api_keys_list()
        if keys and api_key not in keys:
            self.send_json({"error": "请选择当前配置中的 API 密钥"}, 400)
            return

        base_url = f"http://127.0.0.1:{int(CONFIG.get('port', 8081))}"
        headers = {"Content-Type": "application/json"}
        if mode == "responses":
            url = f"{base_url}/v1/responses"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model_name, "input": message}
        elif mode == "google":
            quoted_model = urllib.parse.quote(model_name, safe="")
            url = f"{base_url}/v1beta/models/{quoted_model}:generateContent"
            headers["x-goog-api-key"] = api_key
            payload = {"contents": [{"role": "user", "parts": [{"text": message}]}]}
        else:
            mode = "chat"
            url = f"{base_url}/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model_name, "messages": [{"role": "user", "content": message}]}

        started = time.time()
        raw = ""
        status = 0
        parsed = None
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=int(CONFIG.get("request_timeout_sec", 180))) as response:
                    status = int(response.status)
                    raw = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                status = int(e.code)
                raw = e.read().decode("utf-8", errors="replace")
            if raw:
                parsed = json.loads(raw)
        except Exception as e:
            self.send_json({
                "success": False,
                "mode": mode,
                "model": model_name,
                "status": status,
                "duration_ms": int((time.time() - started) * 1000),
                "error": str(e),
            }, 502)
            return

        content = ""
        if isinstance(parsed, dict):
            if mode == "chat":
                content = (((parsed.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            elif mode == "responses":
                output = parsed.get("output") or []
                if output and isinstance(output[0], dict):
                    parts = output[0].get("content") or []
                    content = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
            elif mode == "google":
                candidates = parsed.get("candidates") or []
                if candidates:
                    parts = ((candidates[0].get("content") or {}).get("parts") or [])
                    content = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))

        self.send_json({
            "success": 200 <= status < 300,
            "mode": mode,
            "model": model_name,
            "status": status,
            "duration_ms": int((time.time() - started) * 1000),
            "content": content,
            "response": parsed if parsed is not None else raw[:4000],
        })

    # ─── /v1/chat/completions ─────────────────────────────────────────────────

    def _handle_chat(self, body: bytes):
        api_key = self._request_api_key()
        started = time.time()
        req = self._parse_body(body)
        if req is None:
            _record_session("OpenAI Chat", "-", "failure", api_key=api_key, error="invalid JSON", started_at=started)
            self.send_json({"error": {"message": "invalid JSON"}}, 400)
            return
        model_name, model_id, think_mode, err, extra_fields = resolve_model(
            req.get("model", CONFIG["default_model"]))
        if err:
            _record_session("OpenAI Chat", str(req.get("model") or "-"), "failure", api_key=api_key, error=err, started_at=started)
            self.send_json({"error": {"message": err}}, 400)
            return

        tools = req.get("tools")
        tool_choice = req.get("tool_choice")
        prompt, images = messages_to_prompt(req.get("messages", []), tools, tool_choice, req.get("parallel_tool_calls"))
        if not prompt.strip():
            _record_session("OpenAI Chat", model_name, "failure", api_key=api_key, error="empty prompt", started_at=started)
            self.send_json({"error": {"message": "empty prompt"}}, 400)
            return

        stream = req.get("stream", False)
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if stream and not tools:
            try:
                self._start_sse()
                full_text = ""
                uploaded_images = _upload_images(images)
                for delta in generate_stream(prompt, model_id, think_mode, uploaded_images, extra_fields):
                    full_text += delta
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model_name, "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                end = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(end)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                _record_session("OpenAI Chat Stream", model_name, "success", _usage(prompt, full_text), api_key=api_key, started_at=started)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                _record_session("OpenAI Chat Stream", model_name, "failure", api_key=api_key, error=e, started_at=started)
                raise
            return

        try:
            text = generate(prompt, model_id, think_mode, _upload_images(images), extra_fields)
        except Exception as e:
            _record_session("OpenAI Chat", model_name, "failure", api_key=api_key, error=e, started_at=started)
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        tool_calls = None
        available_tool_names = tool_names_from_tools(tools, tool_choice)
        if available_tool_names and text:
            text, tool_calls = parse_tool_calls(text, available_tool_names)
        msg = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"

        if stream:
            self._start_sse()
            if tool_calls:
                self._write_chat_tool_call_stream(cid, model_name, tool_calls)
            else:
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": model_name, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text or ""}, "finish_reason": None}]}
                self._write_sse_json(chunk)
                end = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self._write_sse_json(end)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            _record_session("OpenAI Chat Stream", model_name, "success", _usage(prompt, text or ""), api_key=api_key, started_at=started)
        else:
            usage = {"prompt_tokens": len(prompt)//4, "completion_tokens": len(text or "")//4,
                     "total_tokens": (len(prompt)+len(text or ""))//4}
            _record_session("OpenAI Chat", model_name, "success", usage, api_key=api_key, started_at=started)
            self.send_json({
                "id": cid, "object": "chat.completion", "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": usage,
            })

    # ─── /v1/responses (Codex CLI) ───────────────────────────────────────────

    def _handle_responses(self, body: bytes):
        api_key = self._request_api_key()
        started = time.time()
        req = self._parse_body(body)
        if req is None:
            _record_session("OpenAI Responses", "-", "failure", api_key=api_key, error="invalid JSON", started_at=started)
            self.send_json({"error": {"message": "invalid JSON"}}, 400)
            return
        model_name, model_id, think_mode, err, extra_fields = resolve_model(
            req.get("model", CONFIG["default_model"]))
        if err:
            _record_session("OpenAI Responses", str(req.get("model") or "-"), "failure", api_key=api_key, error=err, started_at=started)
            self.send_json({"error": {"message": err}}, 400)
            return

        input_items = req.get("input", [])
        tools = req.get("tools")
        tool_choice = req.get("tool_choice")
        messages = []
        if req.get("instructions"):
            messages.append({"role": "system", "content": req["instructions"]})
        if isinstance(input_items, str):
            messages.append({"role": "user", "content": input_items})
        elif isinstance(input_items, list):
            for item in input_items:
                if isinstance(item, str):
                    messages.append({"role": "user", "content": item})
                elif isinstance(item, dict):
                    if item.get("type") == "function_call_output":
                        messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""),
                                         "name": item.get("name", ""), "content": item.get("output", "")})
                    elif item.get("role") == "assistant" or (item.get("type") == "message" and item.get("role") == "assistant"):
                        cp = item.get("content", [])
                        text_acc, tc_list = "", []
                        if isinstance(cp, list):
                            for c in cp:
                                if isinstance(c, dict):
                                    if c.get("type") == "output_text": text_acc += c.get("text", "")
                                    elif c.get("type") == "function_call": tc_list.append(c)
                        elif isinstance(cp, str):
                            text_acc = cp
                        m = {"role": "assistant", "content": text_acc or None}
                        if tc_list:
                            m["tool_calls"] = [{"id": tc.get("call_id", f"call_{i}"), "type": "function",
                                                "function": {"name": tc.get("name",""), "arguments": tc.get("arguments","{}")}}
                                               for i, tc in enumerate(tc_list)]
                        messages.append(m)
                    else:
                        role = item.get("role", "user")
                        content = item.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if c.get("type") in ("text", "input_text"))
                        messages.append({"role": role, "content": content})

        if tools:
            tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", {})}}
                     if t.get("type") == "function" and "function" not in t else t for t in tools]

        prompt, images = messages_to_prompt(messages, tools, tool_choice, req.get("parallel_tool_calls"))
        if not prompt.strip():
            _record_session("OpenAI Responses", model_name, "failure", api_key=api_key, error="empty input", started_at=started)
            self.send_json({"error": {"message": "empty input"}}, 400)
            return

        try:
            text = generate(prompt, model_id, think_mode, _upload_images(images), extra_fields)
        except Exception as e:
            _record_session("OpenAI Responses", model_name, "failure", api_key=api_key, error=e, started_at=started)
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        tool_calls = None
        available_tool_names = tool_names_from_tools(tools, tool_choice)
        if available_tool_names and text:
            text, tool_calls = parse_tool_calls(text, available_tool_names)

        rid = f"resp_{uuid.uuid4().hex[:16]}"
        mid = f"msg_{uuid.uuid4().hex[:12]}"
        output = []
        if tool_calls:
            for tc in tool_calls:
                output.append({"type": "function_call", "id": tc["id"], "call_id": tc["id"],
                               "name": tc["function"]["name"], "arguments": tc["function"]["arguments"], "status": "completed"})
        if text or not tool_calls:
            output.append({"type": "message", "id": mid, "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text or "", "annotations": []}]})

        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            ev = {"type": "response.created", "response": {"id": rid, "object": "response", "status": "in_progress", "model": model_name, "output": []}}
            self.wfile.write(f"event: response.created\ndata: {json.dumps(ev)}\n\n".encode())
            for item in output:
                if item["type"] == "function_call":
                    ev = {"type": "response.function_call_arguments.done", "item_id": item["id"], "call_id": item["call_id"], "name": item["name"], "arguments": item["arguments"]}
                    self.wfile.write(f"event: response.function_call_arguments.done\ndata: {json.dumps(ev)}\n\n".encode())
                elif item["type"] == "message":
                    for ci, cp in enumerate(item["content"]):
                        ev = {"type": "response.output_text.done", "item_id": item["id"], "content_index": ci, "text": cp["text"]}
                        self.wfile.write(f"event: response.output_text.done\ndata: {json.dumps(ev)}\n\n".encode())
            resp_obj = {"id": rid, "object": "response", "status": "completed", "model": model_name, "output": output,
                        "usage": {"input_tokens": len(prompt)//4, "output_tokens": len(text or "")//4, "total_tokens": (len(prompt)+len(text or ""))//4}}
            self.wfile.write(f"event: response.completed\ndata: {json.dumps({'type': 'response.completed', 'response': resp_obj})}\n\n".encode())
            self.wfile.flush()
            _record_session("OpenAI Responses Stream", model_name, "success", resp_obj["usage"], api_key=api_key, started_at=started)
        else:
            usage = {"input_tokens": len(prompt)//4, "output_tokens": len(text or "")//4, "total_tokens": (len(prompt)+len(text or ""))//4}
            _record_session("OpenAI Responses", model_name, "success", usage, api_key=api_key, started_at=started)
            self.send_json({"id": rid, "object": "response", "created_at": int(time.time()), "status": "completed",
                            "model": model_name, "output": output,
                            "usage": usage})

    # ─── /v1beta/models (Google Gemini CLI) ──────────────────────────────────

    def _handle_google_generate(self, body: bytes, stream: bool):
        api_key = self._request_api_key()
        started = time.time()
        req = self._parse_body(body)
        if req is None:
            _record_session("Google Native", "-", "failure", api_key=api_key, error="invalid JSON", started_at=started)
            self.send_json({"error": {"message": "invalid JSON"}}, 400)
            return
        m = re.match(r'/v1beta/models/([^:?]+)', self.path)
        model_name = m.group(1) if m else CONFIG["default_model"]
        model_name, model_id, think_mode, err, extra_fields = resolve_model(model_name)
        if err:
            _record_session("Google Native", str(model_name or "-"), "failure", api_key=api_key, error=err, started_at=started)
            self.send_json({"error": {"message": err}}, 400)
            return

        tool_config = req.get("toolConfig", {})
        fc_mode = tool_config.get("functionCallingConfig", {}).get("mode", "AUTO")
        has_tools = bool(req.get("tools")) and fc_mode != "NONE"
        prompt, images = google_contents_to_prompt(req)
        if not prompt.strip():
            _record_session("Google Native", model_name, "failure", api_key=api_key, error="empty content", started_at=started)
            self.send_json({"error": {"message": "empty content"}}, 400)
            return

        file_refs = _upload_images(images)
        log(f"Google API: model={model_name} stream={stream} tools={has_tools} prompt_len={len(prompt)}")

        if stream and not has_tools:
            try:
                self._start_sse()
                full_text = ""
                for delta in generate_stream(prompt, model_id, think_mode, file_refs, extra_fields):
                    if not delta:
                        continue
                    full_text += delta
                    chunk_obj = {
                        "candidates": [{"content": {"parts": [{"text": delta}], "role": "model"}, "index": 0}],
                        "modelVersion": model_name,
                    }
                    self.wfile.write(f"data: {json.dumps(chunk_obj, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                final_chunk = {
                    "candidates": [{"finishReason": "STOP", "index": 0}],
                    "usageMetadata": {
                        "promptTokenCount": len(prompt) // 4,
                        "candidatesTokenCount": len(full_text) // 4,
                        "totalTokenCount": (len(prompt) + len(full_text)) // 4,
                    },
                    "modelVersion": model_name,
                }
                self.wfile.write(f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                _record_session("Google Native Stream", model_name, "success", final_chunk["usageMetadata"], api_key=api_key, started_at=started)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                _record_session("Google Native Stream", model_name, "failure", api_key=api_key, error=e, started_at=started)
                raise
            return

        try:
            text = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        except Exception as e:
            _record_session("Google Native", model_name, "failure", api_key=api_key, error=e, started_at=started)
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        if not text:
            log("Warning: empty response from Gemini")

        response_parts = []
        if has_tools and text:
            clean_text, function_calls = parse_google_function_calls(text)
            if function_calls:
                if clean_text:
                    response_parts.append({"text": clean_text})
                for fc in function_calls:
                    response_parts.append({"functionCall": {"name": fc["name"], "args": fc["args"]}})
            else:
                response_parts.append({"text": text})
        else:
            response_parts.append({"text": text or "I apologize, but I was unable to generate a response. Please try again."})

        candidate = {
            "content": {"parts": response_parts, "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
        usage = {
            "promptTokenCount": len(prompt) // 4,
            "candidatesTokenCount": len(text or "") // 4,
            "totalTokenCount": (len(prompt) + len(text or "")) // 4,
        }
        response_obj = {
            "candidates": [candidate],
            "usageMetadata": usage,
            "modelVersion": model_name,
        }

        if stream:
            self._start_sse()
            self.wfile.write(f"data: {json.dumps(response_obj, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            _record_session("Google Native Stream", model_name, "success", usage, api_key=api_key, started_at=started)
        else:
            _record_session("Google Native", model_name, "success", usage, api_key=api_key, started_at=started)
            self.send_json(response_obj)


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
