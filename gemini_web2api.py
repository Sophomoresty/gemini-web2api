#!/usr/bin/env python3
"""
gemini-web2api - Gemini Web to OpenAI API proxy.

Converts Google Gemini's web interface into an OpenAI-compatible API server.
Zero authentication required. Works on any platform (Windows/macOS/Linux).

Usage:
    pip install httpx
    python gemini_web2api.py [--port 8081] [--config config.json]

Client configuration (Cherry Studio, ChatBox, etc.):
    Base URL: http://localhost:8081/v1
    API Key: (anything or empty)

How it works:
    Sends requests directly to Gemini's public StreamGenerate endpoint.
    The backend does not verify authentication for basic text generation.
    Model selection via MODE_CATEGORY field [79] in the request payload.
    This is NOT a user-tier spoofing attack - the endpoint simply doesn't
    require auth for anonymous access.
"""
import json
import urllib.request
import urllib.parse
import urllib.error
import ssl
import socket
import time
import uuid
import re
import os
import sys
import hashlib
import argparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    httpx = None
    HAS_HTTPX = False

__version__ = "1.1.0"

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "ticket_timeout_sec": 25,
    "gemini_bl": "boq_assistant-bard-web-server_20260716.08_p0",
    "auth_user": None,
    "xsrf_token": None,
    "default_model": "gemini-3.6-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "temporary_chats": False,
    "send_model_ticket": True,
    "model_tickets": {},
}

CONFIG = dict(DEFAULT_CONFIG)

# ─── Models ──────────────────────────────────────────────────────────────────
# MODE_CATEGORY enum (_.dF):
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING,
#   6=FLASH_LITE, 7=FLASH_PLUS, 8=ULTRA (4 Argon)
# THINKING_LEVEL enum in X-Goog-Ext-525001261-Jspb slot 15 (_.Fod):
#   1=STANDARD (Low), 4=MEDIUM (Medium), 2=EXTENDED (High), 3=DEEP_THINK

REASONING_EFFORT_MAP = {
    "minimal": (4, 1),
    "low": (4, 1),
    "medium": (2, 4),
    "high": (0, 2),
    "xhigh": (0, 2),
    "max": (0, 2),
}

THINK_TO_VARIANT = {0: 2, 2: 4, 4: 1}

MODELS = {
    "gemini-4-argon": {
        "mode": 8, "think": 2, "variant": 4, "ticket": "95a4c0abcba24364", "levels": True,
        "desc": "Gemini 4 Argon (Ultra web routing, medium thinking default)",
    },
    "gemini-3.8-flash": {
        "mode": 1, "think": 4, "variant": 1, "ticket": "56fdd199312815e2",
        "desc": "Fast all-around model (Flash family; serves 3.6/3.8 Flash per account)",
    },
    "gemini-3.8-flash-thinking": {
        "mode": 2, "think": 0, "variant": 2, "ticket": "56fdd199312815e2",
        "desc": "Gemini 3.8 Flash with deep thinking (~20k chars)",
    },
    "gemini-3.8-flash-thinking-lite": {
        "mode": 5, "think": 0, "variant": 1, "ticket": "56fdd199312815e2",
        "desc": "Gemini 3.8 Flash with adaptive thinking depth",
    },
    "gemini-3.7-flash": {
        "mode": 1, "think": 4,
        "desc": "Latest all-around model (Gemini 3.7 Flash)",
    },
    "gemini-3.6-flash": {
        "mode": 1, "think": 4,
        "desc": "All-around model (Gemini 3.6 Flash)",
    },
    "gemini-3.5-flash": {
        "mode": 1, "think": 4,
        "desc": "Alias for gemini-3.6-flash (backend upgraded)",
    },
    "gemini-3.5-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode, longest output (~20k chars)",
    },
    "gemini-3.1-pro": {
        "mode": 3, "think": 4, "variant": 1, "ticket": "e6fa609c3fa255c0", "levels": True,
        "desc": "Pro model (requires cookie for real routing)",
    },
    "gemini-3.1-pro-enhanced": {
        "mode": 3, "think": 4, "extra": {31: 2, 80: 3},
        "desc": "Pro with enhanced output (experimental)",
    },
    "gemini-auto": {
        "mode": 4, "think": 4,
        "desc": "Auto model selection",
    },
    "gemini-3.5-flash-thinking-lite": {
        "mode": 5, "think": 0,
        "desc": "Dynamic thinking with adaptive depth",
    },
    "gemini-flash-lite": {
        "mode": 6, "think": 4, "variant": 1, "ticket": "8c46e95b1a07cecc",
        "desc": "Lightweight fast model",
    },
}

for _base in ("gemini-4-argon", "gemini-3.1-pro"):
    for _lvl, (_think, _var) in (("low", (4, 1)), ("medium", (2, 4)), ("high", (0, 2))):
        MODELS[f"{_base}-{_lvl}"] = {
            **MODELS[_base],
            "think": _think,
            "variant": _var,
            "levels": False,
            "desc": f"{MODELS[_base]['desc']} ({_lvl} thinking)",
        }


def build_model_ticket(cfg: dict, model_name: str = None, variant_override: int = None, model_tickets: dict = None) -> Optional[str]:
    """Build the X-Goog-Ext-525001261-Jspb header value for StreamGenerate."""
    tickets = model_tickets if model_tickets is not None else (CONFIG.get("model_tickets") or {})
    base_name = re.sub(r"-(low|medium|high)$", "", model_name or "")
    ticket = tickets.get(model_name) or tickets.get(base_name) or cfg.get("ticket")
    if not ticket:
        return None
    variant = variant_override if variant_override is not None else cfg.get("variant", 1)
    arr = [
        1, None, None, None, ticket, None, None, 0,
        [4, 5, 6, 8, 4, 5, 6, 8],
        None, None, 2, None, None,
        cfg["mode"], variant, str(uuid.uuid4()),
    ]
    return json.dumps(arr, separators=(",", ":"))


def _extract_reasoning_effort(req: dict) -> Optional[str]:
    if not isinstance(req, dict):
        return None
    effort = req.get("reasoning_effort")
    if not effort and isinstance(req.get("reasoning"), dict):
        effort = req["reasoning"].get("effort")
    if not effort and isinstance(req.get("thinkingConfig"), dict):
        effort = req["thinkingConfig"].get("thinkingLevel")
    if not effort and isinstance(req.get("generationConfig"), dict):
        gen_cfg = req["generationConfig"]
        if isinstance(gen_cfg.get("thinkingConfig"), dict):
            effort = gen_cfg["thinkingConfig"].get("thinkingLevel")
    return effort

# ─── Utilities ───────────────────────────────────────────────────────────────

def log(msg: str):
    if CONFIG["log_requests"]:
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()


_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
_refresh_lock = threading.Lock()


def _parse_netscape_cookies(raw: str) -> str:
    pairs = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            domain = parts[0].lstrip(".").lower()
            if domain == "google.com" or domain.endswith(".google.com"):
                pairs[parts[5]] = parts[6]
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def load_cookie() -> tuple:
    """Load cookie from file with mtime-based caching. Returns (cookie_str, sapisid)."""
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file or not os.path.exists(cookie_file):
        return "", None
    try:
        mtime = os.path.getmtime(cookie_file)
        if mtime == _cookie_cache["mtime"]:
            return _cookie_cache["str"], _cookie_cache["sapisid"]
        with open(cookie_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content.startswith("{"):
            data = json.loads(content)
            cookie_str = data.get("cookie", "")
            sapisid = data.get("sapisid", "")
            if data.get("xsrf_token"):
                CONFIG["xsrf_token"] = data["xsrf_token"]
            if data.get("gemini_bl"):
                CONFIG["gemini_bl"] = data["gemini_bl"]
            if data.get("auth_user") not in (None, "") and CONFIG.get("auth_user") in (None, ""):
                CONFIG["auth_user"] = str(data["auth_user"])
            if isinstance(data.get("model_tickets"), dict):
                tickets = dict(CONFIG.get("model_tickets") or {})
                tickets.update(data["model_tickets"])
                CONFIG["model_tickets"] = tickets
        elif "\t" in content:
            cookie_str = _parse_netscape_cookies(content)
            sapisid = ""
        else:
            cookie_str = content
            pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
            sapisid = pairs.get("SAPISID", "")
        if not sapisid and cookie_str:
            m = re.search(r"(?:^|;\s*)SAPISID=([^;\s]+)", cookie_str)
            sapisid = m.group(1) if m else ""
        _cookie_cache.update({"str": cookie_str, "sapisid": sapisid or None, "mtime": mtime})
        return cookie_str, sapisid if sapisid else None
    except Exception as e:
        log(f"Cookie load error: {e}")
        return _cookie_cache["str"], _cookie_cache["sapisid"]


def make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


def account_prefix() -> str:
    """Return the Gemini account path prefix for non-default Google accounts."""
    auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def apply_chat_persistence_flags(inner: list) -> None:
    """Apply Gemini Web persistence flags to an outgoing request payload."""
    if CONFIG.get("temporary_chats", False):
        inner[41] = [1]
        inner[45] = 1
    else:
        inner[41] = [2]


def fetch_latest_bl() -> Optional[str]:
    """Fetch the latest gemini_bl (and xsrf_token) from gemini.google.com page."""
    try:
        cookie_str, sapisid = load_cookie()
        prefix = account_prefix()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        if cookie_str:
            headers["Cookie"] = cookie_str
        if sapisid:
            headers["Authorization"] = make_sapisidhash(sapisid)
        req = urllib.request.Request(f"https://gemini.google.com{prefix}/app", headers=headers)
        ctx = ssl.create_default_context()
        proxy = CONFIG.get("proxy")
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                urllib.request.HTTPSHandler(context=ctx))
            resp = opener.open(req, timeout=15)
        else:
            resp = urllib.request.urlopen(req, context=ctx, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        at_m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', html)
        if at_m:
            CONFIG["xsrf_token"] = at_m.group(1)
        m = re.search(r'"cfb2h"\s*:\s*"([^"]+)"', html) or re.search(
            r'(boq_assistant-bard-web-server_\d+\.\d+_p\d+)', html
        )
        if m:
            return m.group(1)
    except Exception as e:
        log(f"BL auto-update fetch failed: {e}")
    return None


def update_bl_if_needed() -> bool:
    """Attempt to fetch and update gemini_bl and xsrf_token. Returns True if updated."""
    old = (CONFIG.get("gemini_bl"), CONFIG.get("xsrf_token"))
    with _refresh_lock:
        if (CONFIG.get("gemini_bl"), CONFIG.get("xsrf_token")) != old:
            return True
        new_bl = fetch_latest_bl()
        if new_bl and new_bl != CONFIG["gemini_bl"]:
            log(f"BL auto-updated: {CONFIG['gemini_bl']} -> {new_bl}")
            CONFIG["gemini_bl"] = new_bl
        return (CONFIG.get("gemini_bl"), CONFIG.get("xsrf_token")) != old


def upload_images(images: list) -> list:
    """Upload parsed OpenAI image parts and return Gemini file references."""
    if not images:
        return None
    from gemini_web2api.multimodal import detect_image_mime, fetch_image_bytes, upload_image

    file_refs = []
    for item in images:
        if not (isinstance(item, tuple) and len(item) == 2):
            continue
        data, mime = item
        if isinstance(data, str):
            data = fetch_image_bytes(data)
            mime = mime or "image/png"
        if not data:
            raise RuntimeError("image fetch failed")
        mime = detect_image_mime(data, mime or "image/png")
        try:
            file_refs.append(upload_image(data, "image.png", mime or "image/png"))
        except Exception as e:
            raise RuntimeError(f"image upload failed: {e}") from e
    return file_refs if file_refs else None


# ─── Gemini Protocol ─────────────────────────────────────────────────────────

def gemini_stream_generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    """Send prompt to Gemini StreamGenerate with retry."""
    load_cookie()
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
    apply_chat_persistence_flags(inner)
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for k, v in extra_fields.items():
            if isinstance(k, int):
                inner[k] = v

    outer = [None, json.dumps(inner)]
    last_err = None
    refreshed = False
    attempts = max(1, int(CONFIG.get("retry_attempts", 3)))
    attempt = 0
    while attempt < attempts:
        params = {"f.req": json.dumps(outer)}
        if CONFIG.get("xsrf_token"):
            params["at"] = CONFIG["xsrf_token"]
        body = urllib.parse.urlencode(params).encode()
        reqid = int(time.time()) % 1000000
        prefix = account_prefix()
        url = (
            f"https://gemini.google.com{prefix}/_/BardChatUi/data/"
            "assistant.lamda.BardFrontendService/StreamGenerate"
            f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://gemini.google.com",
            "Referer": f"https://gemini.google.com{prefix}/app",
            "X-Same-Domain": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if prefix:
            headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
        has_ticket = bool(extra_fields and extra_fields.get("_ticket") and CONFIG.get("send_model_ticket", True))
        if has_ticket:
            headers["X-Goog-Ext-525001261-Jspb"] = extra_fields["_ticket"]
        cookie_str, sapisid = load_cookie()
        if cookie_str:
            headers["Cookie"] = cookie_str
        if sapisid:
            headers["Authorization"] = make_sapisidhash(sapisid)
        req_timeout = _ticket_timeout() if has_ticket else CONFIG["request_timeout_sec"]
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            ctx = ssl.create_default_context()
            proxy = CONFIG.get("proxy")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx)
                )
                resp = opener.open(req, timeout=req_timeout)
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=req_timeout)
            raw = resp.read().decode("utf-8", errors="replace")
            if has_ticket:
                bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', raw)
                if bard_err or _is_canned_error(extract_response_text(raw)):
                    extra_fields = _strip_ticket(extra_fields)
                    log("Model ticket rejected upstream; falling back without X-Goog-Ext-525001261-Jspb header")
                    continue
            return raw
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (400, 405) and not refreshed and update_bl_if_needed():
                refreshed = True
                log("Retrying with updated BL/XSRF...")
                continue
            attempt += 1
            if attempt < attempts:
                log(f"Retry {attempt}/{attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
        except Exception as e:
            if has_ticket and _is_timeout_error(e):
                extra_fields = _strip_ticket(extra_fields)
                log(f"Model ticket timed out ({e}); falling back without X-Goog-Ext-525001261-Jspb header")
                continue
            last_err = e
            attempt += 1
            if attempt < attempts:
                log(f"Retry {attempt}/{attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def gemini_stream_generate_iter(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Send prompt and yield incremental text deltas using httpx streaming."""
    load_cookie()
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
    apply_chat_persistence_flags(inner)
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for k, v in extra_fields.items():
            if isinstance(k, int):
                inner[k] = v

    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    if CONFIG.get("xsrf_token"):
        params["at"] = CONFIG["xsrf_token"]
    body = urllib.parse.urlencode(params)
    reqid = int(time.time()) % 1000000
    prefix = account_prefix()
    url = (
        f"https://gemini.google.com{prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
    )
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if prefix:
        headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
    has_ticket = bool(extra_fields and extra_fields.get("_ticket") and CONFIG.get("send_model_ticket", True))
    if has_ticket:
        headers["X-Goog-Ext-525001261-Jspb"] = extra_fields["_ticket"]
    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)

    proxy = CONFIG.get("proxy")

    if not HAS_HTTPX:
        # Fallback: non-streaming with urllib
        raw = gemini_stream_generate(prompt, model_id, think_mode, file_refs, extra_fields)
        text = extract_response_text(raw)
        if text:
            yield text
        return

    prev_text = ""
    yielded_any = False
    pending = None
    ticket_rejected = None
    req_timeout = _ticket_timeout() if has_ticket else CONFIG["request_timeout_sec"]
    transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
    with httpx.Client(transport=transport, timeout=req_timeout, verify=True) as client:
        try:
            with client.stream("POST", url, content=body, headers=headers) as resp:
                resp.raise_for_status()
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    if "BardErrorInfo" in buf:
                        m = re.search(r'BardErrorInfo\s*\[(\d+)\]', buf)
                        if m:
                            if not yielded_any and has_ticket:
                                raise _TicketRejectedError(f"BardErrorInfo [{m.group(1)}]")
                            raise RuntimeError(f"Gemini upstream rejected request: BardErrorInfo [{m.group(1)}]")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        if '"wrb.fr"' not in line or len(line) < 200:
                            continue
                        try:
                            arr = json.loads(line)
                            inner_str = arr[0][2]
                            if not inner_str or len(inner_str) < 50:
                                continue
                            inner2 = json.loads(inner_str)
                            if isinstance(inner2, list) and len(inner2) > 4 and inner2[4]:
                                for part in inner2[4]:
                                    if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                                        for t in part[1]:
                                            if isinstance(t, str) and len(t) > len(prev_text):
                                                if has_ticket and not yielded_any:
                                                    if len(t) <= 300:
                                                        pending = t
                                                        continue
                                                    pending = None
                                                delta = t[len(prev_text):]
                                                delta = clean_gemini_text(delta, strip=False)
                                                if delta:
                                                    yielded_any = True
                                                    yield delta
                                                prev_text = t
                        except (json.JSONDecodeError, IndexError, TypeError):
                            pass
                if pending is not None:
                    if _is_canned_error(clean_gemini_text(pending)):
                        raise _TicketRejectedError("canned error")
                    delta = clean_gemini_text(pending, strip=False)
                    prev_text = pending
                    if delta:
                        yielded_any = True
                        yield delta
        except _TicketRejectedError as e:
            ticket_rejected = str(e)
        except Exception as e:
            if yielded_any:
                raise
            if HAS_HTTPX and hasattr(e, 'response') and getattr(e.response, 'status_code', 0) in (400, 405):
                if update_bl_if_needed():
                    log("BL/XSRF updated, falling back to non-streaming for this request")
                    raw = gemini_stream_generate(prompt, model_id, think_mode, file_refs, extra_fields)
                    text = extract_response_text(raw)
                    if text:
                        yield text
                    return
                raise
            if has_ticket and _is_timeout_error(e):
                ticket_rejected = f"timeout: {e}"
            else:
                raise

    if ticket_rejected:
        log(f"Stream model ticket rejected ({ticket_rejected}); falling back without X-Goog-Ext-525001261-Jspb header")
        for d in gemini_stream_generate_iter(prompt, model_id, think_mode, file_refs, _strip_ticket(extra_fields)):
            yield d


def clean_gemini_text(text: str, strip: bool = True) -> str:
    """Remove internal code execution artifacts."""
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    return text.strip() if strip else text


_CANNED_ERROR_RE = re.compile(
    r"^\W*(?:i'?m having a hard time fulfilling your request"
    r"|sorry,? something went wrong"
    r"|something went wrong\.? please try"
    r"|i seem to be encountering an error"
    r"|i encountered an error doing what you asked"
    r"|an error occurred\.? please try"
    r"|i'?m unable to (?:process|complete) (?:that|this|your) request right now)"
    r"[^\n]{0,120}$",
    re.IGNORECASE,
)


class _TicketRejectedError(Exception):
    pass


def _is_canned_error(text: str) -> bool:
    if not text:
        return False
    s = text.strip().replace("\u2019", "'")
    if not s or len(s) > 300:
        return False
    return bool(_CANNED_ERROR_RE.match(s))


def _strip_ticket(extra_fields: dict) -> dict:
    if not extra_fields or not extra_fields.get("_ticket"):
        return extra_fields
    return {k: v for k, v in extra_fields.items() if k != "_ticket"}


def _ticket_timeout() -> float:
    try:
        req_t = float(CONFIG.get("request_timeout_sec") or 180)
    except (ValueError, TypeError):
        req_t = 180.0
    raw_tt = CONFIG.get("ticket_timeout_sec", 25)
    if raw_tt is None:
        return max(1.0, min(req_t, 25.0))
    try:
        tt = float(raw_tt)
    except (ValueError, TypeError):
        tt = 25.0
    if tt <= 0:
        return max(1.0, req_t)
    return max(1.0, min(req_t, tt))


def _is_timeout_error(err: BaseException) -> bool:
    if isinstance(err, urllib.error.HTTPError):
        return False
    if HAS_HTTPX and httpx is not None:
        if isinstance(err, (httpx.HTTPStatusError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return False
    if isinstance(err, (TimeoutError, socket.timeout)):
        return True
    if isinstance(err, urllib.error.URLError) and isinstance(getattr(err, "reason", None), (TimeoutError, socket.timeout)):
        return True
    if HAS_HTTPX and httpx is not None and isinstance(err, httpx.TimeoutException):
        return True
    return "timed out" in str(err).lower() or "timeout" in type(err).__name__.lower()


def extract_response_text(raw: str) -> str:
    """Parse StreamGenerate response to extract final text."""
    import re as _re
    bard_err = _re.search(r'BardErrorInfo\s*\[(\d+)\]', raw)
    if bard_err:
        raise RuntimeError(f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]")
    texts = []
    for line in raw.split("\n"):
        if '"wrb.fr"' not in line or len(line) < 200:
            continue
        try:
            arr = json.loads(line)
            inner_str = arr[0][2]
            if not inner_str or len(inner_str) < 50:
                continue
            inner = json.loads(inner_str)
            if isinstance(inner, list) and len(inner) > 4 and inner[4]:
                for part in inner[4]:
                    if isinstance(part, list) and len(part) > 1 and part[1]:
                        if isinstance(part[1], list):
                            for t in part[1]:
                                if isinstance(t, str) and len(t) > 0:
                                    texts.append(t)
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
    text = ""
    for t in reversed(texts):
        if t.strip():
            text = t
            break
    return clean_gemini_text(text)


# ─── OpenAI Format Helpers ───────────────────────────────────────────────────

PROMPT_MAX_BYTES = 60000


def decode_data_url(url: str):
    match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", url, re.DOTALL)
    if not match:
        return None
    mime = match.group(1) or "image/png"
    is_base64 = bool(match.group(2))
    data = match.group(3)
    try:
        if is_base64:
            return base64.b64decode(data, validate=True), mime
        return urllib.parse.unquote_to_bytes(data), mime
    except (ValueError, TypeError, binascii.Error):
        return None


def image_from_url(url: str, mime: str = None):
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        return decode_data_url(url)
    return url, mime or "image/png"


def image_from_part(part: dict):
    part_type = part.get("type")
    if part_type == "image_url":
        image_url = part.get("image_url", {})
        if isinstance(image_url, dict):
            return image_from_url(image_url.get("url"), image_url.get("mime_type"))
        return image_from_url(image_url)
    if part_type in ("input_image", "image"):
        image_url = part.get("image_url") or part.get("url")
        if isinstance(image_url, dict):
            return image_from_url(image_url.get("url"), image_url.get("mime_type"))
        if image_url:
            return image_from_url(image_url, part.get("mime_type"))
        image_data = part.get("data") or part.get("base64")
        if isinstance(image_data, str):
            mime = part.get("mime_type") or part.get("media_type") or "image/png"
            if image_data.startswith("data:"):
                return decode_data_url(image_data)
            try:
                return base64.b64decode(image_data, validate=True), mime
            except (ValueError, TypeError, binascii.Error):
                return None
    return None


def messages_to_prompt(messages: list, tools: list = None) -> tuple:
    """Convert OpenAI messages to (prompt_str, images_list)."""
    parts = []
    images = []
    if tools:
        tool_defs = []
        for tool in tools:
            fn = tool.get("function", tool) if tool.get("type") == "function" else tool
            tool_defs.append({
                "name": fn.get("name", tool.get("name", "")),
                "description": fn.get("description", tool.get("description", "")),
                "parameters": fn.get("parameters", tool.get("parameters", {})),
            })
        if tool_defs:
            tools_json = json.dumps(tool_defs, indent=2)
            if len(tools_json) > PROMPT_MAX_BYTES // 2:
                slim_defs = [{"name": t["name"], "description": t["description"]} for t in tool_defs]
                tools_json = json.dumps(slim_defs, indent=2)
                log(f"Tools block too large ({len(tool_defs)} tools), stripped parameters")
            parts.append(
                "[System instruction]: You have access to tools. "
                "To call a tool, respond with:\n"
                '```tool_call\n{"name": "func_name", "arguments": {...}}\n```\n'
                "Only use tool_call blocks when needed.\n\n"
                f"Available tools:\n{tools_json}"
            )
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for c in content:
                if c.get("type") in ("text", "input_text", "output_text"):
                    text_parts.append(c.get("text", ""))
                else:
                    image = image_from_part(c)
                    if image:
                        images.append(image)
                        text_parts.append("[Image attached]")
            content = " ".join(text_parts)
        if role == "system":
            parts.append(f"[System instruction]: {content}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_strs = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    tc_strs.append(
                        f'```tool_call\n{{"name": "{fn.get("name")}", '
                        f'"arguments": {fn.get("arguments", "{}")}}}\n```'
                    )
                parts.append(f"[Assistant]: {content or ''}\n" + "\n".join(tc_strs))
            else:
                parts.append(f"[Assistant]: {content}")
        elif role == "tool":
            parts.append(f"[Tool result for {msg.get('name', '')}]: {content}")
        else:
            parts.append(content if content else "")
    return "\n\n".join(p for p in parts if p), images


def google_contents_to_prompt(req: dict) -> tuple:
    """Convert Google API contents to (prompt_str, images_list)."""
    parts = []
    images = []

    sys_inst = req.get("systemInstruction")
    if sys_inst:
        sys_text = " ".join(
            part.get("text", "") for part in sys_inst.get("parts", []) if part.get("text")
        )
        if sys_text:
            parts.append(f"[System instruction]: {sys_text}")

    for content in req.get("contents", []):
        role = content.get("role", "user")
        text_parts = []
        for part in content.get("parts", []):
            if part.get("text"):
                text_parts.append(part["text"])
            elif part.get("inlineData"):
                data = part["inlineData"]
                try:
                    images.append((
                        base64.b64decode(data["data"], validate=True),
                        data.get("mimeType", "image/png"),
                    ))
                    text_parts.append("[Image attached]")
                except (KeyError, ValueError, TypeError, binascii.Error):
                    pass
        text = " ".join(text_parts)
        if role == "model":
            parts.append(f"[Assistant]: {text}")
        else:
            parts.append(text)

    return "\n\n".join(part for part in parts if part), images


def parse_tool_calls(text: str) -> tuple:
    """Extract tool_call blocks. Returns (clean_text, tool_calls_list)."""
    tool_calls = []
    pattern = r'```tool_call\s*\n(.*?)\n```'
    for match in re.findall(pattern, text, re.DOTALL):
        try:
            data = json.loads(match.strip())
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": data["name"],
                    "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False),
                },
            })
        except (json.JSONDecodeError, KeyError):
            pass
    clean = re.sub(pattern, '', text, flags=re.DOTALL).strip()
    return clean, tool_calls


# ─── HTTP Handler ────────────────────────────────────────────────────────────

class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        client_ip = self.client_address[0] if self.client_address else "-"
        log(f"{client_ip} {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        keys = CONFIG.get("api_keys") or []
        if not keys:
            return True
        # Authorization: Bearer <key>
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] in keys:
            return True
        # header keys (OpenAI x-api-key / Google x-goog-api-key)
        for h in ("x-api-key", "x-goog-api-key"):
            if self.headers.get(h, "") in keys:
                return True
        # query param ?key= (Gemini CLI native style)
        if "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                if pair.startswith("key=") and pair[4:] in keys:
                    return True
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        try:
            if self.path.startswith("/v1") and not self._authorized():
                self.send_json({"error": {"message": "invalid api key"}}, 401)
                return
            if self.path == "/v1/models":
                self.send_json({"object": "list", "data": [
                    {"id": n, "object": "model", "created": 1700000000,
                     "owned_by": "google", "description": c["desc"]}
                    for n, c in MODELS.items()
                ]})
            elif self.path.startswith("/v1beta/models"):
                self._handle_google_models_list()
            elif self.path == "/":
                self.send_json({"status": "ok", "version": __version__,
                                "models": list(MODELS.keys())})
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"GET error: {e}")

    def do_POST(self):
        try:
            if self.path.startswith("/v1") and not self._authorized():
                self.send_json({"error": {"message": "invalid api key"}}, 401)
                return
            body = self._read_request_body()
            if self.path == "/v1/chat/completions":
                self.handle_chat(body)
            elif self.path == "/v1/responses":
                self.handle_responses(body)
            elif ":streamGenerateContent" in self.path:
                self._handle_google_generate(body, stream=True)
            elif ":generateContent" in self.path:
                self._handle_google_generate(body, stream=False)
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

    def _read_request_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        if "chunked" in transfer_encoding.lower():
            chunks = []
            while True:
                size_line = self.rfile.readline()
                if not size_line:
                    break
                size_text = size_line.split(b";", 1)[0].strip()
                try:
                    size = int(size_text, 16)
                except ValueError:
                    raise ValueError("invalid chunked request body")
                if size == 0:
                    while True:
                        trailer = self.rfile.readline()
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)

        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _resolve_model(self, model_name, reasoning_effort=None):
        load_cookie()
        think_override = None
        variant_override = None
        if "@think=" in model_name:
            model_name, think_str = model_name.rsplit("@think=", 1)
            try:
                think_override = int(think_str)
            except ValueError:
                return None, None, None, f"Invalid think level: {think_str}", None
        cfg = MODELS.get(model_name)
        if not cfg:
            return None, None, None, f"Unknown model: {model_name}", None
        if think_override is not None:
            if cfg.get("levels") or cfg.get("ticket"):
                variant_override = THINK_TO_VARIANT.get(think_override)
        elif reasoning_effort and cfg.get("levels"):
            effort_key = str(reasoning_effort).strip().lower()
            if effort_key in REASONING_EFFORT_MAP:
                think_override, variant_override = REASONING_EFFORT_MAP[effort_key]
        extra = dict(cfg.get("extra") or {})
        effective_variant = variant_override if variant_override is not None else cfg.get("variant", 1)
        if CONFIG.get("send_model_ticket", True):
            ticket_header = build_model_ticket(cfg, model_name=model_name, variant_override=effective_variant)
            if ticket_header:
                extra["_ticket"] = ticket_header
        return model_name, cfg["mode"], (think_override if think_override is not None else cfg["think"]), None, extra

    def _call_gemini(self, prompt, model_id, think_mode, tools, file_refs=None, extra_fields=None):
        raw = gemini_stream_generate(prompt, model_id, think_mode, file_refs, extra_fields)
        text = extract_response_text(raw)
        tool_calls = None
        if tools and text:
            text, tool_calls = parse_tool_calls(text)
        return text or "", tool_calls

    def handle_chat(self, body: bytes):
        req = json.loads(body)
        model_name, model_id, think_mode, err, extra = self._resolve_model(
            req.get("model", CONFIG["default_model"]),
            reasoning_effort=_extract_reasoning_effort(req),
        )
        if err:
            self.send_json({"error": {"message": err}}, 400)
            return

        tools = req.get("tools")
        prompt, images = messages_to_prompt(req.get("messages", []), tools)
        if not prompt.strip():
            self.send_json({"error": {"message": "empty prompt"}}, 400)
            return

        stream = req.get("stream", False)
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        try:
            file_refs = upload_images(images)
        except RuntimeError as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        if stream and not tools:
            # True streaming: forward chunks as they arrive
            stream_iter = gemini_stream_generate_iter(prompt, model_id, think_mode, file_refs, extra)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                first_chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                               "model": model_name, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(first_chunk)}\n\n".encode())
                for delta_text in stream_iter:
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model_name, "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                # Final chunk
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log(f"Stream error: {e}")
            finally:
                if hasattr(stream_iter, "close"):
                    stream_iter.close()
            return

        # Non-streaming (or tool calling which needs full response)
        try:
            text, tool_calls = self._call_gemini(prompt, model_id, think_mode, tools, file_refs, extra)
        except Exception as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        msg = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"

        if stream:
            # Stream mode with tools: send as single chunk (need full parse for tool_calls)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": model_name, "choices": [{"index": 0, "delta": msg, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self.send_json({
                "id": cid, "object": "chat.completion", "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": len(prompt)//4, "completion_tokens": len(text)//4,
                          "total_tokens": (len(prompt)+len(text))//4},
            })

    def handle_responses(self, body: bytes):
        """OpenAI Responses API for Codex CLI compatibility."""
        req = json.loads(body)
        model_name, model_id, think_mode, err, extra = self._resolve_model(
            req.get("model", CONFIG["default_model"]),
            reasoning_effort=_extract_reasoning_effort(req),
        )
        if err:
            self.send_json({"error": {"message": err}}, 400)
            return

        input_items = req.get("input", [])
        tools = req.get("tools")

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
                    elif item.get("type") in ("input_text", "input_image", "image"):
                        messages.append({"role": "user", "content": [item]})
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
                        messages.append({"role": role, "content": item.get("content", "")})

        if tools:
            tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", {})}}
                     if t.get("type") == "function" and "function" not in t else t for t in tools]

        prompt, images = messages_to_prompt(messages, tools)
        if not prompt.strip():
            self.send_json({"error": {"message": "empty input"}}, 400)
            return

        try:
            file_refs = upload_images(images)
            text, tool_calls = self._call_gemini(prompt, model_id, think_mode, tools, file_refs, extra)
        except Exception as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

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
            seq = [0]

            def emit(ev_type, **fields):
                seq[0] += 1
                ev = {"type": ev_type, "sequence_number": seq[0], **fields}
                self.wfile.write(f"event: {ev_type}\ndata: {json.dumps(ev)}\n\n".encode())

            usage = {"input_tokens": len(prompt)//4, "output_tokens": len(text)//4, "total_tokens": (len(prompt)+len(text))//4}
            base_resp = {"id": rid, "object": "response", "created_at": int(time.time()), "model": model_name}
            emit("response.created", response={**base_resp, "status": "in_progress", "output": [], "usage": None})
            emit("response.in_progress", response={**base_resp, "status": "in_progress", "output": [], "usage": None})
            for oi, item in enumerate(output):
                if item["type"] == "function_call":
                    pending = {"type": "function_call", "id": item["id"], "call_id": item["call_id"],
                               "name": item["name"], "arguments": "", "status": "in_progress"}
                    emit("response.output_item.added", output_index=oi, item=pending)
                    emit("response.function_call_arguments.delta", item_id=item["id"], output_index=oi, delta=item["arguments"])
                    emit("response.function_call_arguments.done", item_id=item["id"], output_index=oi, arguments=item["arguments"])
                    emit("response.output_item.done", output_index=oi, item=item)
                elif item["type"] == "message":
                    pending = {"type": "message", "id": item["id"], "role": "assistant", "status": "in_progress", "content": []}
                    emit("response.output_item.added", output_index=oi, item=pending)
                    for ci, cp in enumerate(item["content"]):
                        emit("response.content_part.added", item_id=item["id"], output_index=oi, content_index=ci,
                             part={"type": "output_text", "text": "", "annotations": []})
                        emit("response.output_text.delta", item_id=item["id"], output_index=oi, content_index=ci, delta=cp["text"])
                        emit("response.output_text.done", item_id=item["id"], output_index=oi, content_index=ci, text=cp["text"])
                        emit("response.content_part.done", item_id=item["id"], output_index=oi, content_index=ci, part=cp)
                    emit("response.output_item.done", output_index=oi, item=item)
            emit("response.completed", response={**base_resp, "status": "completed", "output": output, "usage": usage})
            self.wfile.flush()
        else:
            self.send_json({"id": rid, "object": "response", "created_at": int(time.time()), "status": "completed",
                            "model": model_name, "output": output,
                            "usage": {"input_tokens": len(prompt)//4, "output_tokens": len(text)//4, "total_tokens": (len(prompt)+len(text))//4}})


    # ─── Google Native API (Gemini CLI compatible) ────────────────────────────

    def _parse_google_model_from_path(self):
        """Extract model name from /v1beta/models/{model}:method path."""
        m = re.match(r'/v1beta/models/([^:?]+)', self.path)
        if m:
            return m.group(1)
        return None

    def _handle_google_models_list(self):
        """GET /v1beta/models — Google AI format model list."""
        models = []
        for name, cfg in MODELS.items():
            models.append({
                "name": f"models/{name}",
                "displayName": name,
                "description": cfg["desc"],
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })
        self.send_json({"models": models})

    def _handle_google_generate(self, body: bytes, stream: bool):
        """Handle Google native generateContent / streamGenerateContent."""
        req = json.loads(body)
        model_name = self._parse_google_model_from_path()
        if not model_name:
            self.send_json({"error": {"message": "model not specified in path"}}, 400)
            return

        model_name, model_id, think_mode, err, extra = self._resolve_model(
            model_name,
            reasoning_effort=_extract_reasoning_effort(req),
        )
        if err:
            self.send_json({"error": {"message": err}}, 400)
            return

        prompt, images = google_contents_to_prompt(req)
        if not prompt.strip():
            self.send_json({"error": {"message": "empty content"}}, 400)
            return

        try:
            file_refs = upload_images(images)
            text, _ = self._call_gemini(prompt, model_id, think_mode, None, file_refs, extra)
        except Exception as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        candidate = {
            "content": {"parts": [{"text": text or ""}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
        usage = {
            "promptTokenCount": len(prompt) // 4,
            "candidatesTokenCount": len(text) // 4,
            "totalTokenCount": (len(prompt) + len(text)) // 4,
        }
        response_obj = {
            "candidates": [candidate],
            "usageMetadata": usage,
            "modelVersion": model_name,
        }

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(response_obj)}\n\n".encode())
            self.wfile.flush()
        else:
            self.send_json(response_obj)


# ─── Main ────────────────────────────────────────────────────────────────────

def load_config(path: str):
    if path and os.path.exists(path):
        with open(path) as f:
            CONFIG.update(json.load(f))
        log(f"Config loaded: {path}")


def main():
    parser = argparse.ArgumentParser(description="Gemini Web to OpenAI API")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None, help="Path to cookie file")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG")
    if not config_path:
        for p in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
            if os.path.exists(p):
                config_path = p
                break
    load_config(config_path)

    if args.port:
        CONFIG["port"] = args.port
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:
        CONFIG["proxy"] = args.proxy

    new_bl = fetch_latest_bl()
    if new_bl:
        CONFIG["gemini_bl"] = new_bl

    class ThreadedServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    port = CONFIG["port"]
    server = ThreadedServer((CONFIG["host"], port), GeminiHandler)
    print(f"gemini-web2api v{__version__}")
    print(f"  Listening: http://0.0.0.0:{port}")
    print(f"  Base URL:  http://localhost:{port}/v1")
    print(f"  Models:    {', '.join(MODELS.keys())}")
    print(f"  Cookie:    {'yes (' + CONFIG['cookie_file'] + ')' if CONFIG.get('cookie_file') else 'none (anonymous)'}")
    print(f"  Proxy:     {CONFIG.get('proxy') or 'none (uses system env HTTP_PROXY/HTTPS_PROXY)'}")
    print(f"  Retry:     {CONFIG['retry_attempts']}x / {CONFIG['retry_delay_sec']}s")
    print(f"  BL:        {CONFIG['gemini_bl']}")
    print(f"  Temporary: {'yes' if CONFIG.get('temporary_chats', False) else 'no'}")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
