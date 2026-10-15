"""Gemini StreamGenerate protocol implementation with httpx streaming."""
import json
import time
import uuid
import re
import urllib.request
import urllib.parse
import urllib.error
import ssl
import socket
import os
import hashlib
import threading

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    httpx = None
    HAS_HTTPX = False

from .config import CONFIG

_ssl_ctx = None
_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
_refresh_lock = threading.Lock()
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
    """Load cookie from file with mtime-based caching."""
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


def _account_prefix() -> str:
    """Return the Gemini account path prefix for non-default Google accounts."""
    auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def _build_headers(extra_fields: dict = None) -> dict:
    cookie_str, sapisid = load_cookie()
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
    if extra_fields and extra_fields.get("_ticket") and CONFIG.get("send_model_ticket", True):
        headers["X-Goog-Ext-525001261-Jspb"] = extra_fields["_ticket"]
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def refresh_bl_and_xsrf() -> bool:
    """Refresh gemini_bl and xsrf_token from gemini.google.com."""
    old = (CONFIG.get("gemini_bl"), CONFIG.get("xsrf_token"))
    with _refresh_lock:
        if (CONFIG.get("gemini_bl"), CONFIG.get("xsrf_token")) != old:
            return True
        try:
            cookie_str, sapisid = load_cookie()
            account_prefix = _account_prefix()
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            if cookie_str:
                headers["Cookie"] = cookie_str
            if sapisid:
                headers["Authorization"] = make_sapisidhash(sapisid)
            req = urllib.request.Request(f"https://gemini.google.com{account_prefix}/app", headers=headers)
            ctx = _get_ssl_ctx()
            proxy = CONFIG.get("proxy")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx),
                )
                resp = opener.open(req, timeout=15)
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=15)
            html = resp.read().decode("utf-8", errors="replace")
            bl_m = re.search(r'"cfb2h"\s*:\s*"([^"]+)"', html) or re.search(
                r"(boq_assistant-bard-web-server_\d+\.\d+_p\d+)", html
            )
            if bl_m:
                CONFIG["gemini_bl"] = bl_m.group(1)
            at_m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', html)
            if at_m:
                CONFIG["xsrf_token"] = at_m.group(1)
            return (CONFIG.get("gemini_bl"), CONFIG.get("xsrf_token")) != old
        except Exception as e:
            log(f"BL/XSRF auto-refresh failed: {e}")
            return False


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
            if isinstance(k, int):
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
    load_cookie()
    ctx = _get_ssl_ctx()
    proxy = CONFIG.get("proxy")
    refreshed = False

    last_err = None
    attempts = max(1, int(CONFIG.get("retry_attempts", 3)))
    attempt = 0
    while attempt < attempts:
        body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields).encode()
        url = _get_url()
        headers = _build_headers(extra_fields)
        has_ticket = bool(extra_fields and extra_fields.get("_ticket") and CONFIG.get("send_model_ticket", True))
        req_timeout = _ticket_timeout() if has_ticket else CONFIG["request_timeout_sec"]
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx)
                )
                resp = opener.open(req, timeout=req_timeout)
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=req_timeout)
            raw = resp.read().decode("utf-8", errors="replace")
            text = extract_response_text(raw)
            if has_ticket and _is_canned_error(text):
                extra_fields = _strip_ticket(extra_fields)
                log("Model ticket returned canned error; falling back without X-Goog-Ext-525001261-Jspb header")
                continue
            return text
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (400, 405) and not refreshed and refresh_bl_and_xsrf():
                refreshed = True
                log(f"HTTP {e.code}: refreshed BL/XSRF, retrying immediately...")
                continue
            attempt += 1
            if attempt < attempts:
                log(f"Retry {attempt}/{attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
        except Exception as e:
            if has_ticket and (_is_timeout_error(e) or (isinstance(e, RuntimeError) and "BardErrorInfo" in str(e))):
                extra_fields = _strip_ticket(extra_fields)
                log(f"Model ticket rejected or timed out ({e}); falling back without X-Goog-Ext-525001261-Jspb header")
                continue
            last_err = e
            attempt += 1
            if attempt < attempts:
                log(f"Retry {attempt}/{attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_stream(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Streaming generation via httpx with retry on connection failure."""
    if not HAS_HTTPX:
        text = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        if text:
            yield text
        return

    load_cookie()
    client = _get_httpx_client()
    refreshed = False

    last_err = None
    emitted_raw_text = ""
    yielded_any = False
    attempts = max(1, int(CONFIG.get("retry_attempts", 3)))
    attempt = 0
    while attempt < attempts:
        body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields)
        url = _get_url()
        headers = _build_headers(extra_fields)
        has_ticket = bool(extra_fields and extra_fields.get("_ticket") and CONFIG.get("send_model_ticket", True))
        req_timeout = _ticket_timeout() if has_ticket else CONFIG["request_timeout_sec"]
        pending = None
        try:
            with client.stream("POST", url, content=body, headers=headers, timeout=req_timeout) as resp:
                if resp.status_code in (400, 405) and not refreshed and not yielded_any:
                    refreshed = True
                    if refresh_bl_and_xsrf():
                        log(f"Stream HTTP {resp.status_code}: refreshed BL/XSRF, retrying immediately...")
                        continue
                resp.raise_for_status()
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    if "BardErrorInfo" in buf:
                        bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', buf)
                        if bard_err:
                            if not yielded_any and has_ticket:
                                raise _TicketRejectedError(f"BardErrorInfo [{bard_err.group(1)}]")
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
                            if has_ticket and not yielded_any:
                                if len(t) <= 300:
                                    pending = t
                                    continue
                                pending = None
                            delta = clean_text(t[len(emitted_raw_text):], strip=False)
                            emitted_raw_text = t
                            if delta:
                                yielded_any = True
                                yield delta
                if pending is not None:
                    if _is_canned_error(clean_text(pending)):
                        raise _TicketRejectedError("canned error")
                    delta = clean_text(pending, strip=False)
                    emitted_raw_text = pending
                    if delta:
                        yielded_any = True
                        yield delta
            return
        except _TicketRejectedError as e:
            extra_fields = _strip_ticket(extra_fields)
            log(f"Stream model ticket rejected ({e}); falling back without X-Goog-Ext-525001261-Jspb header")
            continue
        except Exception as e:
            if not yielded_any and has_ticket and _is_timeout_error(e):
                extra_fields = _strip_ticket(extra_fields)
                log(f"Stream model ticket timed out ({e}); falling back without X-Goog-Ext-525001261-Jspb header")
                continue
            if yielded_any:
                raise
            last_err = e
            attempt += 1
            if attempt < attempts:
                log(f"Stream retry {attempt}/{attempts}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err
