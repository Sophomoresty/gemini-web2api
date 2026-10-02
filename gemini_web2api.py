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
import time
import ssl
import sys
import uuid
import re
import os
import hashlib
import argparse
import base64
import binascii
from typing import Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

__version__ = "1.1.0"

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260716.08_p0",
    "auth_user": None,
    "xsrf_token": None,
    "default_model": "gemini-3.6-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "temporary_chats": True,
    "persistent_chat": False,
}

# ─── Persistent chat window (for cache reuse) ────────────────────────────────
# When persistent_chat is enabled, the proxy reuses the SAME Gemini Web chat
# across requests within a DSH session window, so Google can serve a prefix KV
# cache (faster, less quota). The window is detected heuristically from the
# OpenAI messages array: an increasing message count = same chat continuing;
# a shrink (new DSH window / history reset) = start a fresh chat.
class ChatWindow:
    """Tracks the active Gemini Web conversation so persistent_chat can reuse
    it across requests (prefix KV cache reuse). Conversation continuation on
    the web protocol works via inner[2] = [conv_id, resp_id, ...]:
      - conv_id (c_xxx) identifies the conversation; stays stable.
      - resp_id (r_xxx) is the last response id; must be updated each turn.
    New window is detected heuristically: no history, message count shrink
    (new DSH window / history reset), or idle timeout."""

    def __init__(self):
        self.conv_id = None          # c_xxx (stable)
        self.resp_id = None          # r_xxx (updates every turn)
        self.sent_msg_count = 0      # msgs already sent to Google in this window
        self.last_msg_count = None
        self.last_ts = None
        self.is_new_window = False   # set by resolve() for the current request
        self._pending_send_count = 0  # msg count to advance on successful response

    def resolve(self, msg_count: int, idle_timeout_s: int = 1800) -> None:
        """Decide whether this request continues the current chat. If it is a
        new window, reset conv/resp ids so the next request starts fresh."""
        import time as _t
        now = _t.time()
        is_new = (
            self.conv_id is None
            or self.last_msg_count is None
            or msg_count < self.last_msg_count
            or (self.last_ts is not None and now - self.last_ts > idle_timeout_s)
        )
        self.is_new_window = is_new
        if is_new:
            self.conv_id = None
            self.resp_id = None
            self.sent_msg_count = 0
            log(f"ChatWindow: new window (msgs={msg_count})")
        else:
            log(f"ChatWindow: continue window (msgs={msg_count} conv={self.conv_id[:8] if self.conv_id else None})")
        self.last_msg_count = msg_count
        self.last_ts = now

    def update_from_response(self, conv_id, resp_id) -> None:
        """Store conversation ids observed in a response (called after a turn)."""
        if conv_id:
            self.conv_id = conv_id
        if resp_id:
            self.resp_id = resp_id
        # A real c_ id confirms this request succeeded upstream, so the sent
        # counter can advance (next request sends only the delta).
        if conv_id and self._pending_send_count:
            self.advance_sent(self._pending_send_count)
            self._pending_send_count = 0

    def advance_sent(self, msg_count: int) -> None:
        """Mark msg_count messages as successfully sent to the current chat.
        Called only after a successful upstream response, so a failed turn that
        DSH retries re-sends the same tail instead of computing an empty delta."""
        if msg_count > self.sent_msg_count:
            self.sent_msg_count = msg_count

    def mark_request_sent(self, msg_count: int) -> None:
        """Record the current request's total msg count as 'to be advanced'
        once the upstream responds successfully. Falls back safely if the
        window is not active."""
        self._pending_send_count = msg_count if self.active else 0

    def confirm_sent(self) -> None:
        """Called after a successful upstream response: advance the sent
        counter so the next request only sends the delta."""
        if getattr(self, "_pending_send_count", 0):
            self.advance_sent(self._pending_send_count)
            self._pending_send_count = 0

    @property
    def active(self) -> bool:
        return self.conv_id is not None


CHAT_WINDOW = ChatWindow()

CONFIG = dict(DEFAULT_CONFIG)

# ─── Models ──────────────────────────────────────────────────────────────────
# Mapping from JS source: MODE_CATEGORY enum (028-6eb337387583.js)
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE

MODELS = {
    "gemini-3.8-flash": {
        "mode": 1, "think": 0,
        "desc": "Latest all-around model (Gemini 3.8 Flash)",
    },
    "gemini-3.7-flash": {
        "mode": 1, "think": 4,
        "desc": "All-around model (Gemini 3.7 Flash)",
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
        "mode": 3, "think": 4,
        "desc": "Pro model (requires cookie for real routing)",
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
        "mode": 6, "think": 4,
        "desc": "Lightweight fast model",
    },
}

# ─── Model selection header (x-goog-ext-525001261-jspb) ─────────────────────
# Verified internal model IDs (from browser captures, Issue #82).
# When this header is absent, upstream ignores slot79 and serves the account
# default model, so model selection silently no-ops. See:
#   https://github.com/Sophomoresty/gemini-web2api/issues/82
MODEL_IDS = {
    "gemini-3.8-flash": "56fdd199312815e2",   # not yet verified separately; 3.7 ID is stable
    "gemini-3.7-flash": "56fdd199312815e2",   # cat 1 (verified)
    "gemini-3.6-flash": "56fdd199312815e2",   # alias to 3.7 id for now
    "gemini-3.5-flash": "56fdd199312815e2",
    "gemini-3.1-pro": "e6fa609c3fa255c0",     # cat 3 (verified)
    "gemini-flash-lite": "8c46e95b1a07cecc",  # cat 6 (verified)
    "gemini-3.5-flash-thinking": "56fdd199312815e2",
    "gemini-3.5-flash-thinking-lite": "56fdd199312815e2",
    "gemini-auto": None,                      # no header = account default
}

def build_model_header(model_name: str, model_id: int) -> Optional[str]:
    """Build the x-goog-ext-525001261-jspb model-selection header.

    Contract (Issue #82): [1,null,null,null,"<model_id>",null,null,0,
    [4,5,6,8,4,5,6,8],null,null,2,null,null,<category>,<extended>,"<uuid>"]
    idx4 = model selector; idx14 must equal payload slot79; idx15 = slot80.
    Returns None for models without a known internal ID (-> account default).
    """
    mid = MODEL_IDS.get(model_name)
    if not mid:
        return None
    try:
        return json.dumps(
            [1, None, None, None, mid, None, None, 0,
             [4, 5, 6, 8, 4, 5, 6, 8], None, None, 2,
             None, None, model_id, 0, str(uuid.uuid4())],
            separators=(",", ":"))
    except Exception:
        return None


# ─── Utilities ───────────────────────────────────────────────────────────────

def log(msg: str):
    """Log to stderr AND append to server.log (real-time)."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    try:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


_AUTH_FIELDS_LOADED = False


def load_cookie() -> tuple:
    """Load cookie from file. Returns (cookie_str, sapisid).

    Also supports the gemini-auth.json format exported by the bundled
    browser extension: {cookie, sapisid, auth_user, xsrf_token, gemini_bl}.
    Those auth fields are injected into CONFIG on first load, so the user
    only needs to point cookie_file at the exported json (no manual config
    edits for xsrf/bl/auth_user).
    """
    global _AUTH_FIELDS_LOADED
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file:
        return "", None
    if not os.path.exists(cookie_file):
        return "", None
    try:
        with open(cookie_file, "r") as f:
            content = f.read().strip()
        if content.startswith("{"):
            data = json.loads(content)
            cookie_str = data.get("cookie", "")
            sapisid = data.get("sapisid", "")
            # Inject auth metadata from the exported json (one-time).
            if not _AUTH_FIELDS_LOADED:
                if data.get("xsrf_token"):
                    CONFIG["xsrf_token"] = data["xsrf_token"]
                    log(f"xsrf loaded from auth file (len {len(data['xsrf_token'])})")
                if data.get("gemini_bl"):
                    CONFIG["gemini_bl"] = data["gemini_bl"]
                    log("gemini_bl loaded from auth file")
                if data.get("auth_user") is not None:
                    CONFIG["auth_user"] = data["auth_user"]
                    log(f"auth_user loaded from auth file: {data['auth_user']}")
                _AUTH_FIELDS_LOADED = True
        else:
            cookie_str = content
            pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
            sapisid = pairs.get("SAPISID", "")
        return cookie_str, sapisid if sapisid else None
    except Exception as e:
        log(f"Cookie load error: {e}")
        return "", None


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
    if CONFIG.get("persistent_chat", False):
        # Persistent chat: keep the same conversation across requests so Google
        # can reuse prefix KV cache. This does leave traces in the web UI.
        inner[41] = [2]
    elif CONFIG.get("temporary_chats", False):
        inner[41] = [1]
        inner[45] = 1
    else:
        inner[41] = [2]


def apply_chat_window(inner: list) -> None:
    """Inject the active persistent conversation ids into inner[2].

    Protocol (verified): inner[2][0] = conversation id (c_xxx, stable),
    inner[2][1] = last response id (r_xxx, updates each turn). Leaving both
    empty starts a brand-new conversation (stateless / no cache reuse).
    Also keeps inner[59] as a fresh per-request uuid.
    """
    if CONFIG.get("persistent_chat", False) and CHAT_WINDOW.active:
        inner[2] = [CHAT_WINDOW.conv_id, CHAT_WINDOW.resp_id,
                    "", None, None, None, None, None, None, ""]
    inner[59] = str(uuid.uuid4())


def _capture_session_ids(raw: str) -> None:
    """Scan a raw StreamGenerate response and store conv_id (c_xxx) and the
    latest response id (r_xxx) into CHAT_WINDOW for conversation continuation."""
    if not CONFIG.get("persistent_chat", False):
        return
    try:
        for line in raw.split("\n"):
            if '"wrb.fr"' not in line:
                continue
            arr = json.loads(line)
            inner_str = arr[0][2]
            if not inner_str:
                continue
            inner2 = json.loads(inner_str)
            if isinstance(inner2, list) and len(inner2) > 1 and isinstance(inner2[1], list):
                c = inner2[1][0] if len(inner2[1]) > 0 else None
                r = inner2[1][1] if len(inner2[1]) > 1 else None
                if isinstance(c, str) and c.startswith("c_"):
                    CHAT_WINDOW.update_from_response(c, r if isinstance(r, str) and r.startswith("r_") else None)
                    break
    except (json.JSONDecodeError, IndexError, TypeError):
        pass


def fetch_latest_bl() -> Optional[str]:
    """Fetch the latest gemini_bl from gemini.google.com page."""
    try:
        req = urllib.request.Request(
            "https://gemini.google.com/app",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
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
        m = re.search(r'(boq_assistant-bard-web-server_\d+\.\d+_p\d+)', html)
        if m:
            return m.group(1)
    except Exception as e:
        log(f"BL auto-update fetch failed: {e}")
    return None


def update_bl_if_needed() -> bool:
    """Attempt to fetch and update gemini_bl. Returns True if updated."""
    new_bl = fetch_latest_bl()
    if new_bl and new_bl != CONFIG["gemini_bl"]:
        log(f"BL auto-updated: {CONFIG['gemini_bl']} -> {new_bl}")
        CONFIG["gemini_bl"] = new_bl
        return True
    return False


def fetch_xsrf_token() -> Optional[str]:
    """Fetch the current xsrf token (SNlM0e) from the signed-in Gemini page.

    MUST send SNlM0e verbatim: it looks like
        "AIaPT3Nf6-OwMfS0dnpZKG5SHwQ4:1790841775902"
    i.e. ~28 chars + ':' + a 13-digit timestamp (42 chars total).

    WARNING: FdrFJe is NOT the xsrf token — it is a plain 18/20-digit session
    id. Sending it as `at=` makes upstream answer
        [["er",...,400,...,[{"...":["xsrf",
            "AIaPT3Nf6-OwMfS0dnpZKG5SHwQ4:1790841775902",["<account id>"]]}]]]
    which is exactly the "HTTP Error 400: Bad Request" seen when the auth file
    carries no xsrf_token. So SNlM0e is tried first and FdrFJe only remains as a
    last-resort fallback (kept for older page layouts where it still worked).
    """
    try:
        req = urllib.request.Request(
            "https://gemini.google.com/app",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie": load_cookie()[0],
            })
        ctx = ssl.create_default_context()
        proxy = CONFIG.get("proxy")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ctx))
        resp = opener.open(req, timeout=20)
        html = resp.read().decode("utf-8", errors="replace")
        # 首选：完整的 SNlM0e（含冒号与时间戳）
        m = re.search(r'"SNlM0e"\s*:\s*"([A-Za-z0-9_\-]+:\d{8,})"', html)
        if m:
            return m.group(1)
        # 次选：SNlM0e 可能不带时间戳后缀
        m2 = re.search(r'"SNlM0e"\s*:\s*"([A-Za-z0-9_\-]{15,})"', html)
        if m2:
            return m2.group(1)
        # 最后兜底：FdrFJe（注意：这只是会话 ID，不是 XSRF token）
        m3 = re.search(r'"FdrFJe"\s*:\s*"(-?\d+)"', html)
        if m3:
            log("WARNING: SNlM0e not found; falling back to FdrFJe (session id, "
                "not a real xsrf token) — upstream may reject with xsrf 400")
            return m3.group(1)
        return None
    except Exception as e:
        log(f"xsrf fetch failed: {e}")
        return None


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

def gemini_stream_generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None,
                           model_name: str = None) -> str:
    """Send prompt to Gemini StreamGenerate with retry."""
    inner = [None] * 80
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
    apply_chat_window(inner)
    inner[53] = 0
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id

    outer = [None, json.dumps(inner)]
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

    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    model_hdr = build_model_header(model_name, model_id)
    if model_hdr:
        headers["x-goog-ext-525001261-jspb"] = model_hdr

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            ctx = ssl.create_default_context()
            proxy = CONFIG.get("proxy")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx)
                )
                resp = opener.open(req, timeout=CONFIG["request_timeout_sec"])
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=CONFIG["request_timeout_sec"])
            raw = resp.read().decode("utf-8", errors="replace")
            # Persistent chat: capture c_/r_ ids from the raw response so the
            # next request continues this conversation (prefix KV cache reuse).
            if CONFIG.get("persistent_chat", False):
                _capture_session_ids(raw)
            return raw
        except urllib.error.HTTPError as e:
            if e.code == 405 and update_bl_if_needed():
                reqid = int(time.time()) % 1000000
                url = (
                    f"https://gemini.google.com{prefix}/_/BardChatUi/data/"
                    "assistant.lamda.BardFrontendService/StreamGenerate"
                    f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
                )
                log("Retrying with updated BL...")
                last_err = e
                continue
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def gemini_stream_generate_iter(prompt: str, model_id: int, think_mode: int, file_refs: list = None,
                                model_name: str = None):
    """Send prompt and yield incremental text deltas using httpx streaming."""
    inner = [None] * 80
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
    apply_chat_window(inner)
    inner[53] = 0
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id

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
    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    model_hdr = build_model_header(model_name, model_id)
    if model_hdr:
        headers["x-goog-ext-525001261-jspb"] = model_hdr

    proxy = CONFIG.get("proxy")

    if not HAS_HTTPX:
        # Fallback: non-streaming with urllib
        raw = gemini_stream_generate(prompt, model_id, think_mode, file_refs, model_name)
        text = extract_response_text(raw)
        if text:
            yield text
        return

    prev_text = ""
    transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
    with httpx.Client(transport=transport, timeout=CONFIG["request_timeout_sec"], verify=True) as client:
        try:
            with client.stream("POST", url, content=body, headers=headers) as resp:
                resp.raise_for_status()
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    if "BardErrorInfo" in buf:
                        import re as _re
                        m = _re.search(r'BardErrorInfo\s*\[(\d+)\]', buf)
                        if m:
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
                            # Persistent chat: capture conv_id (c_xxx) and
                            # response id (r_xxx) so the next request can
                            # continue this conversation (prefix KV cache).
                            if CONFIG.get("persistent_chat", False) and isinstance(inner2, list) and len(inner2) > 1 and isinstance(inner2[1], list):
                                try:
                                    _c = inner2[1][0] if len(inner2[1]) > 0 else None
                                    _r = inner2[1][1] if len(inner2[1]) > 1 else None
                                    if (isinstance(_c, str) and _c.startswith("c_")) or (isinstance(_r, str) and _r.startswith("r_")):
                                        CHAT_WINDOW.update_from_response(_c if isinstance(_c, str) and _c.startswith("c_") else None,
                                                                         _r if isinstance(_r, str) and _r.startswith("r_") else None)
                                except Exception:
                                    pass
                            if isinstance(inner2, list) and len(inner2) > 4 and inner2[4]:
                                for part in inner2[4]:
                                    if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                                        for t in part[1]:
                                            if isinstance(t, str) and len(t) > len(prev_text):
                                                delta = t[len(prev_text):]
                                                delta = clean_gemini_text(delta, strip=False)
                                                if delta:
                                                    yield delta
                                                prev_text = t
                        except (json.JSONDecodeError, IndexError, TypeError):
                            pass
        except Exception as e:
            if HAS_HTTPX and hasattr(e, 'response') and getattr(e.response, 'status_code', 0) == 405:
                if update_bl_if_needed():
                    log("BL updated, falling back to non-streaming for this request")
                    raw = gemini_stream_generate(prompt, model_id, think_mode, file_refs)
                    text = extract_response_text(raw)
                    if text:
                        yield text
                    return
            raise


def clean_gemini_text(text: str, strip: bool = True) -> str:
    """Remove internal code execution artifacts."""
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    return text.strip() if strip else text


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


def _truncate_tool_result(content: str, max_len: int = 1200) -> str:
    """Trim oversized tool results to keep the prompt lean (faster TTFT)."""
    if not content:
        return content
    if len(content) <= max_len:
        return content
    head = content[:max_len]
    # keep a tail snippet for context (e.g. last error line)
    tail = content[-200:]
    return f"{head}\n[... truncated by proxy: {len(content) - max_len} chars omitted ...]\n{tail}"


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
            # ── Tool filter + compact for speed ──────────────────────────
            # DSH sends 43 tools (~34KB / 8650 tok). The dev_*/job_*/goals
            # series are rarely needed and bulk up the prompt; core 10 tools
            # cover 90%+ daily coding. Keep full parameters, trim description
            # to the first sentence (~150 chars) to preserve call accuracy.
            CORE_TOOL_NAMES = {
                'read', 'edit', 'write', 'grep', 'glob', 'pwsh',
                'web_search', 'todo_write', 'todo_read', 'subagent',
            }
            filtered = []
            skipped = 0
            for t in tool_defs:
                if t.get("name", "") in CORE_TOOL_NAMES:
                    filtered.append(t)
                else:
                    skipped += 1
            if skipped:
                log(f"Tool filter: {len(tool_defs)} → {len(filtered)} core tools ({skipped} dev/job/goals skipped)")
            MAX_DESC = 150
            compact_defs = []
            for t in filtered:
                d = t.get("description", "") or ""
                # Keep the first sentence (usually the core meaning).
                d = d.split('.')[0].split('\n')[0].strip()
                if len(d) > MAX_DESC:
                    d = d[:MAX_DESC].rstrip() + "..."
                compact_defs.append({
                    "name": t.get("name", ""),
                    "description": d,
                    "parameters": t.get("parameters", {}),
                })
            TOOLS_BUDGET = PROMPT_MAX_BYTES * 3 // 4
            tools_json = json.dumps(compact_defs, ensure_ascii=False, separators=(",", ":"))
            try:
                sizes = sorted(((len(json.dumps(t, ensure_ascii=False, separators=(",",":"))), t.get("name","")) for t in compact_defs), reverse=True)
                top = ", ".join(f"{n}({s}B)" for s, n in sizes[:5])
                log(f"Tools: {len(compact_defs)} core, {len(tools_json)}B | {top}")
            except Exception:
                pass
            if len(tools_json) > TOOLS_BUDGET:
                slim_defs = [{"name": t["name"], "parameters": t["parameters"]} for t in compact_defs]
                tools_json = json.dumps(slim_defs, ensure_ascii=False, separators=(",", ":"))
                log(f"Tools block too large ({len(compact_defs)} tools), stripped descriptions only")
            parts.append(
                "[System instruction]: You are a coding agent with real tools available. "
                "When the user asks you to CREATE files, WRITE code, EDIT files, "
                "SEARCH the web, RUN commands, or MANAGE tasks — you MUST call the "
                "appropriate tool. Do NOT simply describe what you would do in text; "
                "actually invoke the tool. If no tool is needed, answer directly.\n\n"
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
            parts.append(f"[Tool result for {msg.get('name', '')}]: {_truncate_tool_result(content)}")
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
    # HTTP/1.0 + connection-close: SSE streams end when the connection closes
    # (EOF). HTTP/1.1 without chunked encoding makes clients wait forever for
    # a body terminator that BaseHTTPRequestHandler never sends.
    protocol_version = "HTTP/1.0"

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

    def _send_stream_headers(self):
        """SSE headers with proxy-buffering disabled for smooth streaming."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    @staticmethod
    def _usage_chunk(cid, model_name, prompt, full_text):
        """Build OpenAI-style usage chunk so DSH usage plugin can count tokens."""
        p_tokens = max(1, len(prompt) // 4)
        c_tokens = max(1, len(full_text) // 4)
        return {
            "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model_name, "choices": [],
            "usage": {"prompt_tokens": p_tokens, "completion_tokens": c_tokens,
                      "total_tokens": p_tokens + c_tokens},
        }

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
            elif self.path == "/messages" or self.path.endswith("/messages"):
                self._handle_search(body)
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

    def _resolve_model(self, model_name):
        think_override = None
        if "@think=" in model_name:
            model_name, think_str = model_name.rsplit("@think=", 1)
            think_override = int(think_str)
        cfg = MODELS.get(model_name)
        if not cfg:
            return None, None, None, f"Unknown model: {model_name}"
        return model_name, cfg["mode"], (think_override if think_override is not None else cfg["think"]), None

    def _call_gemini(self, prompt, model_id, think_mode, tools, file_refs=None, model_name=None):
        raw = gemini_stream_generate(prompt, model_id, think_mode, file_refs, model_name)
        text = extract_response_text(raw)
        tool_calls = None
        if tools and text:
            text, tool_calls = parse_tool_calls(text)
        return text or "", tool_calls

    def handle_chat(self, body: bytes):
        req = json.loads(body)
        # Debug: export the real DSH tools JSON once (for prompt-size analysis)
        try:
            _tools = req.get("tools")
            if _tools and len(_tools) >= 40:
                out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dsh-tools-real.json")
                if not os.path.exists(out):
                    with open(out, "w", encoding="utf-8") as f:
                        json.dump(_tools, f, ensure_ascii=False, indent=1)
                    log(f"Exported {len(_tools)} DSH tools to dsh-tools-real.json")
        except Exception:
            pass
        # Debug: log request structure that DSH sends (one-time diagnostic)
        try:
            msgs = req.get("messages", [])
            roles = [m.get("role") for m in msgs]
            last3 = []
            for m in msgs[-3:]:
                c = m.get("content")
                if c is None:
                    cstr = "<None>"
                elif isinstance(c, str):
                    cstr = c[:50]
                elif isinstance(c, list):
                    cstr = f"<list:{len(c)}>"
                else:
                    cstr = repr(c)[:50]
                tcs = f" tc={len(m.get('tool_calls', []))}" if m.get("tool_calls") else ""
                last3.append(f"{m.get('role')}:{cstr}{tcs}")
            log(f"DSH-REQ: keys={list(req.keys())} stream={req.get('stream')} "
                f"stream_options={req.get('stream_options')} tool_choice={req.get('tool_choice')} "
                f"msgs={len(msgs)} roles={roles[:5]}... last3={last3}")
        except Exception as e:
            log(f"DSH-REQ debug err: {e}")
        # Persistent chat: resolve the shared Gemini conversation from the
        # message window (same chat across requests => Google prefix KV cache
        # reuse). The conv/resp ids from the last response are injected into
        # inner[2] by the request builders; when persistent_chat is off the
        # window is not consulted and every request is stateless (fresh chat).
        try:
            if CONFIG.get("persistent_chat", False):
                CHAT_WINDOW.resolve(len(req.get("messages", [])))
        except Exception as e:
            log(f"ChatWindow resolve err: {e}")
        model_name, model_id, think_mode, err = self._resolve_model(
            req.get("model", CONFIG["default_model"]))
        if err:
            self.send_json({"error": {"message": err}}, 400)
            return

        tools = req.get("tools")
        all_msgs = req.get("messages", [])
        # ── Pure-incremental persistent chat ─────────────────────────────
        # persistent_chat=true + an ongoing window: only the messages added
        # since the last successfully-sent request are forwarded to Gemini
        # (Google's server keeps the conversation memory via conv_id). The
        # first request of a window sends everything (system + tools + history)
        # to seed the conversation.
        persist_mode = CONFIG.get("persistent_chat", False)
        if persist_mode and not CHAT_WINDOW.is_new_window and CHAT_WINDOW.active and CHAT_WINDOW.sent_msg_count > 0:
            start = CHAT_WINDOW.sent_msg_count
            if len(all_msgs) > start:
                send_msgs = all_msgs[start:]
                prompt, images = messages_to_prompt(send_msgs, None)  # no tool re-injection
                log(f"Persist-incremental: {len(send_msgs)} new msgs (of {len(all_msgs)}), sent_idx={start}")
            else:
                # Nothing genuinely new (retry of a failed turn): fall back to
                # full send to be safe.
                prompt, images = messages_to_prompt(all_msgs, tools)
                log(f"Persist-incremental: no new msgs, fallback full ({len(all_msgs)})")
        else:
            prompt, images = messages_to_prompt(all_msgs, tools)
        # PROMPT-STATS: structured breakdown of what goes into the prompt
        # (which roles / how many / char counts / est. tokens). Content is NOT
        # printed — only sizes — so it's cheap and stays readable.
        try:
            msgs = req.get("messages", [])
            role_counts = {}
            sys_chars = user_chars = asst_chars = tool_chars = 0
            for m in msgs:
                r = m.get("role", "?")
                role_counts[r] = role_counts.get(r, 0) + 1
                c = m.get("content")
                n = len(c) if isinstance(c, str) else (sum(len(p.get("text", "")) for p in c if isinstance(p, dict) and isinstance(p.get("text"), str)) if isinstance(c, list) else 0)
                if r == "system": sys_chars += n
                elif r == "user": user_chars += n
                elif r == "assistant": asst_chars += n
                elif r == "tool": tool_chars += n
            tools_json_chars = 0
            if tools:
                try:
                    _compact = [{"name": t.get("name", ""), "description": (t.get("description", "") or "")[:150], "parameters": t.get("parameters", {})} for t in tools]
                    tools_json_chars = len(json.dumps(_compact, ensure_ascii=False, separators=(",", ":")))
                except Exception:
                    pass
            prompt_chars = len(prompt)
            est_tok = prompt_chars // 4
            log(f"PROMPT-STATS: msgs={len(msgs)} roles={role_counts} | "
                f"sys={sys_chars}B user={user_chars}B asst={asst_chars}B tool={tool_chars}B "
                f"tools_json={tools_json_chars}B | prompt_total={prompt_chars}B (~{est_tok} tok)")
        except Exception as e:
            log(f"PROMPT-STATS err: {e}")
        # Global prompt budget: keep the head (system/tools + early context) and
        # the tail (recent turns), collapse the middle to keep TTFT low.
        # Set high enough that tool definitions (32KB for DSH's 43 tools) are
        # never clipped; conversation history is trimmed separately in
        # messages_to_prompt via _truncate_tool_result.
        MAX_PROMPT = 60000  # ~15k tokens
        if len(prompt) > MAX_PROMPT:
            head = prompt[:MAX_PROMPT * 3 // 4]
            tail = prompt[-MAX_PROMPT // 4:]
            prompt = f"{head}\n[... proxy: middle of prompt collapsed ...]\n{tail}"
            log(f"Prompt collapsed: {len(prompt)}B")
        if not prompt.strip():
            self.send_json({"error": {"message": "empty prompt"}}, 400)
            return
        # Record the total msg count so the persistent-window counter advances
        # only after a successful upstream response (confirm_sent via update_from_response).
        if persist_mode:
            CHAT_WINDOW.mark_request_sent(len(all_msgs))

        stream = req.get("stream", False)
        log(f"REQ: stream={stream} tools={len(tools) if tools else 0} model={model_name} prompt_bytes={len(prompt.encode('utf-8'))}")
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        try:
            file_refs = upload_images(images)
        except RuntimeError as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        if stream and not tools:
            # True streaming: forward chunks as they arrive
            self._send_stream_headers()
            try:
                full_text = ""
                first_chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                               "model": model_name, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(first_chunk)}\n\n".encode())
                self.wfile.flush()
                for delta_text in gemini_stream_generate_iter(prompt, model_id, think_mode, file_refs, model_name):
                    full_text += delta_text
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model_name, "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                # Final chunk
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                # Usage chunk for DSH usage plugin
                usage = self._usage_chunk(cid, model_name, prompt, full_text)
                self.wfile.write(f"data: {json.dumps(usage, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log(f"Stream error: {e}")
            return

        if stream and tools:
            # True streaming WITH tools: stream plain text as it arrives (fast
            # TTFT), but buffer ```tool_call JSON blocks so they are NOT leaked
            # into content — emit parsed tool_calls delta at the end instead.
            # (Leaking the JSON into content makes clients like DSH treat the
            #  turn as a text reply and never execute the tool.)
            self._send_stream_headers()
            try:
                first_chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                               "model": model_name, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(first_chunk)}\n\n".encode())
                self.wfile.flush()

                TOOL_START = "```tool_call"
                TOOL_END = "\n```"
                pending = ""          # text not yet classified
                in_tool = False
                tool_calls = []
                full_text = ""        # full raw text (for usage estimate)
                text_sent = ""        # text streamed as content

                def send_content(txt):
                    if not txt:
                        return
                    nonlocal text_sent
                    text_sent += txt
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model_name, "choices": [{"index": 0, "delta": {"content": txt}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()

                def parse_block(block_text):
                    """Parse one ```tool_call JSON block into a tool call dict."""
                    try:
                        data = json.loads(block_text.strip())
                        return {
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": data["name"],
                                "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False),
                            },
                        }
                    except (json.JSONDecodeError, KeyError, TypeError):
                        return None

                for delta_text in gemini_stream_generate_iter(prompt, model_id, think_mode, file_refs, model_name):
                    full_text += delta_text
                    pending += delta_text
                    # Process the pending buffer until it stabilizes (no
                    # tool block open and no new one started).
                    stable = False
                    while not stable:
                        stable = True
                        if not in_tool:
                            idx = pending.find(TOOL_START)
                            if idx >= 0:
                                send_content(pending[:idx])
                                pending = pending[idx:]
                                in_tool = True
                                stable = False
                            else:
                                # Stream all but a tail that could become the
                                # tool block opener (cross-chunk safety).
                                keep = min(len(pending), len(TOOL_START) - 1)
                                if len(pending) > keep:
                                    send_content(pending[:-keep] if keep else pending)
                                    pending = pending[-keep:] if keep else ""
                        else:
                            # Try to close the open tool block right away.
                            end_idx = pending.find(TOOL_END, len(TOOL_START))
                            if end_idx >= 0:
                                block = pending[len(TOOL_START):end_idx]
                                tc = parse_block(block)
                                if tc:
                                    tool_calls.append(tc)
                                pending = pending[end_idx + len(TOOL_END):]
                                in_tool = False
                                stable = False
                # Any remaining plain text after the loop.
                if pending and not in_tool:
                    send_content(pending)
                elif pending and in_tool:
                    # Unterminated tool block: drop it from content.
                    log(f"Unterminated tool block, dropped {len(pending)}B")

                # Final chunk: emit tool_calls (if any) + finish_reason.
                if tool_calls:
                    msg = {"role": "assistant", "content": text_sent or None, "tool_calls": tool_calls}
                    finish = "tool_calls"
                else:
                    msg = {}
                    finish = "stop"
                final_chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                               "model": model_name, "choices": [{"index": 0, "delta": msg, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode())
                # Usage chunk for DSH usage plugin
                usage = self._usage_chunk(cid, model_name, prompt, full_text)
                self.wfile.write(f"data: {json.dumps(usage, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                log(f"STREAM-COMPLETE: {cid} finish={finish} text={len(text_sent)}B tools={len(tool_calls)} raw={full_text[:120]!r}")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log(f"Stream tool error: {e}")
            return

        # Non-streaming (or tool calling which needs full response)
        try:
            text, tool_calls = self._call_gemini(prompt, model_id, think_mode, tools, file_refs, model_name)
        except Exception as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        msg = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"

        if stream:
            # Stream mode with tools: full response as delta (tool_calls parsed), then usage
            self._send_stream_headers()
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": model_name, "choices": [{"index": 0, "delta": msg, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            # Usage chunk for DSH usage plugin
            usage = self._usage_chunk(cid, model_name, prompt, text)
            self.wfile.write(f"data: {json.dumps(usage, ensure_ascii=False)}\n\n".encode())
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

    def _handle_search(self, body: bytes):
        """Handle Anthropic Messages API requests for DSH web_search.
        
        When DSH's web-search-deepseek provider is configured with
        baseURL=http://127.0.0.1:8081, it sends requests to /messages in
        Anthropic format. This method routes them through Gemini Web's
        native search capability (free) instead of DeepSeek API (paid).
        
        Retry: one retry on failure, then return error to DSH for its own retry.
        """
        req = json.loads(body)
        # Extract search query from Anthropic Messages format
        msgs = req.get("messages", [])
        query = ""
        for m in msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                query = c
                break
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        query = part.get("text", "")
                        break
        if not query:
            for t in req.get("tools", []):
                q = t.get("queries", []) if isinstance(t, dict) else []
                if q:
                    query = ", ".join(q)
                    break
        if not query:
            query = req.get("content", "")

        log(f"[Search] query: {query[:80]}")
        result_text = self._execute_search(query)
        
        # Return Anthropic Messages API format
        response = {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": result_text}],
            "model": req.get("model", "gemini-3.8-flash"),
            "stop_reason": "end_turn",
            "usage": {"input_tokens": len(query) // 4, "output_tokens": len(result_text) // 4},
        }
        self.send_json(response)
        log(f"[Search] done: {len(result_text)}B result")

    def _execute_search(self, query: str) -> str:
        """Execute search via Gemini Web RPC with retry.
        
        Returns formatted search result text, or error message.
        """
        max_retries = 1
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                cookie_str, sapisid = load_cookie()
                if not cookie_str:
                    last_error = "No cookie loaded"
                    continue
                
                xsrf = CONFIG.get("xsrf_token") or ""
                bl = CONFIG.get("gemini_bl")
                if not bl:
                    last_error = "No gemini_bl"
                    continue
                
                search_prompt = (
                    f"Please use Google Search to find information about: {query}\n"
                    f"Search for the latest information on this topic and provide:\n"
                    f"1. A brief summary of the search results\n"
                    f"2. Key facts and details\n"
                    f"3. Relevant source URLs if available"
                )
                
                inner = [None] * 80
                inner[0] = [search_prompt, 0, None, None, None, None, 0]
                inner[1] = ["en"]
                inner[2] = ["", "", "", None, None, None, None, None, None, ""]
                inner[6] = [0]
                inner[7] = 1
                inner[10] = 1
                inner[11] = 0
                inner[17] = [[0]]  # think=0 for fast search
                inner[18] = 0
                inner[27] = 1
                inner[30] = [4]
                inner[41] = [1]  # temporary chats for search
                inner[45] = 1
                inner[53] = 0
                inner[59] = str(uuid.uuid4())
                inner[61] = []
                inner[68] = 1
                inner[79] = 1  # model_id for 3.8-flash

                params = {"f.req": json.dumps([None, json.dumps(inner)])}
                if xsrf:
                    params["at"] = xsrf
                body = urllib.parse.urlencode(params).encode()
                reqid = int(time.time()) % 1000000
                prefix = account_prefix()
                url = (
                    f"https://gemini.google.com{prefix}/_/BardChatUi/data/"
                    "assistant.lamda.BardFrontendService/StreamGenerate"
                    f"?bl={bl}&hl=en&_reqid={reqid}&rt=c"
                )
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://gemini.google.com",
                    "Referer": f"https://gemini.google.com{prefix}/app",
                    "X-Same-Domain": "1",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cookie": cookie_str,
                }
                if CONFIG.get("auth_user") is not None:
                    headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
                if sapisid:
                    ts = int(time.time())
                    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
                    headers["Authorization"] = f"SAPISIDHASH {ts}_{h}"
                model_hdr = build_model_header("gemini-3.8-flash", 1)
                if model_hdr:
                    headers["x-goog-ext-525001261-jspb"] = model_hdr

                proxy_url = CONFIG.get("proxy")
                ctx = ssl.create_default_context()
                if proxy_url:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
                        urllib.request.HTTPSHandler(context=ctx))
                else:
                    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

                t0 = time.time()
                resp = opener.open(urllib.request.Request(url, data=body, headers=headers, method="POST"),
                                   timeout=CONFIG.get("request_timeout_sec", 60))
                raw = resp.read().decode("utf-8", errors="replace")
                latency = int((time.time() - t0) * 1000)

                text = extract_response_text(raw)
                if text:
                    log(f"[Search] {query[:40]}... → {len(text)}B ({latency}ms) attempt={attempt}")
                    return text
                else:
                    last_error = f"Empty response"
            except Exception as e:
                last_error = str(e)
            
            if attempt < max_retries:
                log(f"[Search] retry {attempt + 1}: {last_error}")
                time.sleep(1)

        return f"[Search failed after {max_retries + 1} attempts: {last_error}]"

    def handle_responses(self, body: bytes):
        """OpenAI Responses API for Codex CLI compatibility."""
        req = json.loads(body)
        model_name, model_id, think_mode, err = self._resolve_model(
            req.get("model", CONFIG["default_model"]))
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
            text, tool_calls = self._call_gemini(prompt, model_id, think_mode, tools, file_refs)
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

        model_name, model_id, think_mode, err = self._resolve_model(model_name)
        if err:
            self.send_json({"error": {"message": err}}, 400)
            return

        prompt, images = google_contents_to_prompt(req)
        if not prompt.strip():
            self.send_json({"error": {"message": "empty content"}}, 400)
            return

        try:
            file_refs = upload_images(images)
            text, _ = self._call_gemini(prompt, model_id, think_mode, None, file_refs)
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

    if not CONFIG.get("xsrf_token"):
        tok = fetch_xsrf_token()
        # fetch_xsrf_token() calls load_cookie() internally, which may have just
        # injected xsrf from the auth json file. Don't clobber that with the
        # auto-fetched value — the auth-file value (SNlM0e) is the authoritative
        # one the page expects as the `at` form field.
        if tok and not CONFIG.get("xsrf_token"):
            CONFIG["xsrf_token"] = tok
            log(f"xsrf auto-fetched (len {len(tok)})")

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
