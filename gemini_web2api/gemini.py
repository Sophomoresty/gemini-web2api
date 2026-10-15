"""Gemini StreamGenerate protocol implementation with httpx streaming."""
import json
import time
import uuid
import re
import urllib.request
import urllib.parse
import ssl
import os
import hashlib
import tempfile
import threading

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .config import CONFIG

_ssl_ctx = None
_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
_cookie_lock = threading.Lock()
_httpx_client = None


def log(msg: str):
    if CONFIG["log_requests"]:
        import sys
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()


def _get_ssl_ctx():
    global _ssl_ctx
    if _ssl_ctx is None:
        _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def _get_httpx_client():
    global _httpx_client
    if _httpx_client is None and HAS_HTTPX:
        proxy = CONFIG.get("proxy")
        transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
        _httpx_client = httpx.Client(transport=transport, timeout=CONFIG["request_timeout_sec"], verify=True)
    return _httpx_client


def _extract_sapisid(cookie_str: str) -> str:
    if not cookie_str:
        return ""
    m = re.search(r"(?:^|;\s*)SAPISID=([^;\s]+)", cookie_str)
    return m.group(1) if m else ""


def _normalize_model_tickets(raw_tickets) -> dict:
    if not isinstance(raw_tickets, dict):
        return {}
    clean = {}
    for mk, mv in raw_tickets.items():
        if len(clean) >= 32:
            break
        if (
            isinstance(mk, str)
            and isinstance(mv, str)
            and re.fullmatch(r"gemini-[a-z0-9._-]{1,48}", mk.strip())
            and re.fullmatch(r"[0-9a-fA-F]{16}", mv.strip())
        ):
            clean[mk.strip()] = mv.strip().lower()
    return clean


def _normalize_metadata(data: dict) -> dict:
    meta = {}
    for k in ("xsrf_token", "gemini_bl"):
        val = data.get(k)
        if isinstance(val, str) and val.strip() and not any(c in val for c in "\r\n\x00"):
            meta[k] = val.strip()
    au = data.get("auth_user")
    if isinstance(au, (int, str)) and not isinstance(au, bool) and str(au).strip().isdigit():
        meta["auth_user"] = str(au).strip()
    tickets = _normalize_model_tickets(data.get("model_tickets"))
    if tickets:
        meta["model_tickets"] = tickets
    return meta


def update_cookie_payload(data: dict) -> tuple:
    """Validate and apply a pushed cookie payload. Returns (ok: bool, err_msg: str, status: int)."""
    if not isinstance(data, dict):
        return False, "invalid JSON object", 400
    raw_cookie = data.get("cookie")
    if not isinstance(raw_cookie, str) or not raw_cookie.strip():
        return False, "missing or invalid cookie field", 400
    cookie_str = raw_cookie.strip()
    if len(cookie_str) > 16384 or any(c in cookie_str for c in "\r\n\x00"):
        return False, "invalid characters or length in cookie field", 400
    sapisid = _extract_sapisid(cookie_str)
    meta = _normalize_metadata(data)

    with _cookie_lock:
        same_cookie = cookie_str == _cookie_cache.get("str") and (sapisid or None) == _cookie_cache.get("sapisid")
        cur_tickets = dict(CONFIG.get("model_tickets") or {})
        new_tickets = meta.get("model_tickets") or {}
        tickets_changed = any(cur_tickets.get(k) != v for k, v in new_tickets.items())
        scalar_changed = any(meta.get(k) != CONFIG.get(k) for k in meta if k != "model_tickets")
        has_new_meta = scalar_changed or tickets_changed
        if same_cookie and not has_new_meta:
            return True, "", 200

        merged_tickets = dict(cur_tickets)
        merged_tickets.update(new_tickets)

        cf = CONFIG.get("cookie_file")
        mtime = _cookie_cache["mtime"]
        if cf:
            tmp_path = None
            try:
                existing = {}
                if os.path.exists(cf):
                    with open(cf, "r", encoding="utf-8-sig") as rf:
                        raw_existing = rf.read().strip()
                    if raw_existing.startswith("{"):
                        loaded = json.loads(raw_existing)
                        if isinstance(loaded, dict):
                            existing = loaded
                existing["cookie"] = cookie_str
                if sapisid:
                    existing["sapisid"] = sapisid
                disk_tickets = _normalize_model_tickets(existing.get("model_tickets"))
                if disk_tickets or merged_tickets:
                    combined = dict(cur_tickets)
                    combined.update(disk_tickets)
                    combined.update(new_tickets)
                    merged_tickets = _normalize_model_tickets(combined)
                    existing["model_tickets"] = merged_tickets
                for k, v in meta.items():
                    if k != "model_tickets":
                        existing[k] = v
                parent_dir = os.path.dirname(os.path.abspath(cf)) or "."
                fd, tmp_path = tempfile.mkstemp(dir=parent_dir, prefix=".cookie-", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as wf:
                    json.dump(existing, wf, ensure_ascii=False, indent=2)
                os.replace(tmp_path, cf)
                tmp_path = None
                mtime = os.path.getmtime(cf)
            except Exception as e:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                log(f"Cookie persist error: {e}")
                return False, "failed to persist cookie file", 500

        for k, v in meta.items():
            if k != "model_tickets":
                CONFIG[k] = v
        if merged_tickets:
            CONFIG["model_tickets"] = merged_tickets
        _cookie_cache.update({"str": cookie_str, "sapisid": sapisid or None, "mtime": mtime})
        log("Updated cookies via /v1/cookies")
        return True, "", 200


def load_cookie() -> tuple:
    """Load cookie from file with mtime-based caching."""
    with _cookie_lock:
        cookie_file = CONFIG.get("cookie_file")
        if not cookie_file or not os.path.exists(cookie_file):
            return _cookie_cache["str"], _cookie_cache["sapisid"]
        try:
            mtime = os.path.getmtime(cookie_file)
            if mtime == _cookie_cache["mtime"] and _cookie_cache["str"]:
                return _cookie_cache["str"], _cookie_cache["sapisid"]
            with open(cookie_file, "r", encoding="utf-8-sig") as f:
                content = f.read().strip()
            if content.startswith("{"):
                data = json.loads(content)
                cookie_str = data.get("cookie", "")
                sapisid = data.get("sapisid", "") or _extract_sapisid(cookie_str)
                meta = _normalize_metadata(data)
                if "model_tickets" in meta:
                    cur_tickets = dict(CONFIG.get("model_tickets") or {})
                    cur_tickets.update(meta["model_tickets"])
                    CONFIG["model_tickets"] = cur_tickets
            else:
                cookie_str = content
                sapisid = _extract_sapisid(cookie_str)
            _cookie_cache.update({"str": cookie_str, "sapisid": sapisid or None, "mtime": mtime})
            return cookie_str, sapisid if sapisid else None
        except Exception as e:
            log(f"Cookie load error: {e}")
            return _cookie_cache["str"], _cookie_cache["sapisid"]


def make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


def _account_prefix() -> str:
    """Return the Gemini account path prefix for non-default Google accounts."""
    auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def _build_headers() -> dict:
    account_prefix = _account_prefix()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{account_prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if account_prefix:
        headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def _apply_chat_persistence_flags(inner: list) -> None:
    """Apply Gemini Web persistence flags to an outgoing request payload."""
    if CONFIG.get("temporary_chats", False):
        # Match Gemini Web temporary-chat requests.
        inner[41] = [1]
        inner[45] = 1
    else:
        inner[41] = [2]


def _build_payload(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    inner = [None] * 102
    if file_refs:
        refs = [[None, None, ref] for ref in file_refs]
        inner[0] = [prompt, 0, None, refs, None, None, 0]
    else:
        inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    _apply_chat_persistence_flags(inner)
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for k, v in extra_fields.items():
            inner[k] = v
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    if CONFIG.get("xsrf_token"):
        params["at"] = CONFIG["xsrf_token"]
    return urllib.parse.urlencode(params)


def _get_url() -> str:
    reqid = int(time.time()) % 1000000
    account_prefix = _account_prefix()
    return (
        f"https://gemini.google.com{account_prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
    )


def clean_text(text: str, strip: bool = True) -> str:
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    text = re.sub(r'http://googleusercontent\.com/card_content/\d+\n?', '', text)
    return text.strip() if strip else text


def _extract_texts_from_line(line: str) -> list:
    """Parse a single wrb.fr line and return list of text strings found."""
    if '"wrb.fr"' not in line or len(line) < 200:
        return []
    try:
        arr = json.loads(line)
        inner_str = arr[0][2]
        if not inner_str or len(inner_str) < 50:
            return []
        inner = json.loads(inner_str)
        if not (isinstance(inner, list) and len(inner) > 4 and inner[4]):
            return []
        texts = []
        for part in inner[4]:
            if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                for t in part[1]:
                    if isinstance(t, str) and t:
                        texts.append(t)
        return texts
    except (json.JSONDecodeError, IndexError, TypeError):
        return []


def extract_response_text(raw: str) -> str:
    """Parse full response to get final text."""
    bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', raw)
    if bard_err:
        raise RuntimeError(f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]")
    last_text = ""
    for line in raw.split("\n"):
        for t in _extract_texts_from_line(line):
            if len(t) > len(last_text):
                last_text = t
    return clean_text(last_text)


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    """Non-streaming generation with retry."""
    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields).encode()
    url = _get_url()
    headers = _build_headers()
    ctx = _get_ssl_ctx()
    proxy = CONFIG.get("proxy")

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx)
                )
                resp = opener.open(req, timeout=CONFIG["request_timeout_sec"])
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=CONFIG["request_timeout_sec"])
            raw = resp.read().decode("utf-8", errors="replace")
            return extract_response_text(raw)
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_stream(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Streaming generation via httpx with retry on connection failure."""
    if not HAS_HTTPX:
        text = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        if text:
            yield text
        return

    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields)
    url = _get_url()
    headers = _build_headers()
    client = _get_httpx_client()

    last_err = None
    emitted_raw_text = ""
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            with client.stream("POST", url, content=body, headers=headers) as resp:
                resp.raise_for_status()
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    if "BardErrorInfo" in buf:
                        bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', buf)
                        if bard_err:
                            raise RuntimeError(
                                f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]"
                            )
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        for t in _extract_texts_from_line(line):
                            if t == emitted_raw_text or emitted_raw_text.startswith(t):
                                continue
                            if not t.startswith(emitted_raw_text):
                                raise RuntimeError("Gemini stream content changed during retry")
                            delta = clean_text(t[len(emitted_raw_text):], strip=False)
                            emitted_raw_text = t
                            if delta:
                                yield delta
            return
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Stream retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err
