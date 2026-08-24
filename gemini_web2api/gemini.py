"""Gemini StreamGenerate protocol implementation with httpx streaming."""
import json
import time
import uuid
import re
import random
import threading
import urllib.request
import urllib.parse
import ssl
import os
import hashlib

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .config import CONFIG

_ssl_ctx = None
_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
_httpx_client = None

# Browser-like per-session RPC counter. The real Gemini web app sends an
# ever-increasing _reqid (+100000 per RPC) for every request in a page
# session. Deriving it from a timestamp made concurrent requests (e.g. a
# cronjob firing while the user is chatting) send identical ids.
_REQID_LOCK = threading.Lock()
_REQID_NEXT = random.randrange(10000, 99999)


def _next_reqid() -> int:
    global _REQID_NEXT
    with _REQID_LOCK:
        _REQID_NEXT += 100000
        return _REQID_NEXT


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


def load_cookie() -> tuple:
    """Load cookie from file with mtime-based caching."""
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file or not os.path.exists(cookie_file):
        return "", None
    try:
        mtime = os.path.getmtime(cookie_file)
        if mtime == _cookie_cache["mtime"] and _cookie_cache["str"]:
            return _cookie_cache["str"], _cookie_cache["sapisid"]
        with open(cookie_file, "r") as f:
            content = f.read().strip()
        if content.startswith("{"):
            data = json.loads(content)
            cookie_str = data.get("cookie", "")
            sapisid = data.get("sapisid", "")
        else:
            cookie_str = content
            pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
            sapisid = pairs.get("SAPISID", "")
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
    account_prefix = _account_prefix()
    return (
        f"https://gemini.google.com{account_prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={_next_reqid()}&rt=c"
    )


def clean_text(text: str, strip: bool = True) -> str:
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    text = re.sub(r'http://googleusercontent\.com/card_content/\d+\n?', '', text)
    return text.strip() if strip else text


def _iter_frames(line: str):
    """Yield the parsed inner payload of every wrb.fr frame in one response line.

    A single StreamGenerate chunk line can carry several frames; the answer,
    thought summaries, alternative drafts, follow-up chips and image-agent
    updates each arrive as separate frames.
    """
    if '"wrb.fr"' not in line or len(line) < 40:
        return
    try:
        frames = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(frames, list):
        return
    for frame in frames:
        if not (isinstance(frame, list) and len(frame) > 2 and frame[0] == "wrb.fr"):
            continue
        payload = frame[2]
        if not (isinstance(payload, str) and len(payload) >= 20):
            continue
        try:
            inner = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(inner, list) and len(inner) > 4 and inner[4]:
            yield inner


def _candidate_texts(inner: list) -> list:
    """Return [(candidate_index, joined_text)] for one frame payload.

    Each candidate's text is a *list* of segments in the wire format and must
    be joined; treating segments as standalone answers returned fragments.
    """
    out = []
    for idx, cand in enumerate(inner[4]):
        if isinstance(cand, list) and len(cand) > 1 and isinstance(cand[1], list):
            text = "".join(t for t in cand[1] if isinstance(t, str))
            if text:
                out.append((idx, text))
    return out


def _is_answer_frame(inner: list) -> bool:
    """True for the main answer frame, which carries the new conversation id
    (inner[1], e.g. "c_...") and response id (inner[2], e.g. "r_...").

    Thought summaries, follow-up chips, search and image-agent frames do not
    carry these ids -- that is what tells them apart from the real answer.
    """
    if len(inner) > 2:
        conv_id, resp_id = inner[1], inner[2]
        return (isinstance(conv_id, str) and bool(conv_id)
                and isinstance(resp_id, str) and bool(resp_id))
    return False


def _best_main_answer(line: str):
    """Best primary-candidate answer text found in one line, or None."""
    best = ""
    for inner in _iter_frames(line):
        if not _is_answer_frame(inner):
            continue
        for idx, text in _candidate_texts(inner):
            if idx == 0 and len(text) > len(best):
                best = text
    return best or None


def extract_response_text(raw: str) -> str:
    """Parse full response to get the final answer text.

    Selection order:
      1. Primary candidate (index 0) of the main answer frame -- the frame
         carrying conversation/response ids. Longest update wins (streaming
         re-sends this frame as the answer grows).
      2. Longest joined candidate from any frame (protocol drift fallback).
    Never the thought/draft/chip frames, which previously won the "longest
    text anywhere" heuristic and produced unrelated answers.
    """
    bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', raw)
    if bard_err:
        raise RuntimeError(f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]")
    main_best = ""
    any_best = ""
    for line in raw.split("\n"):
        for inner in _iter_frames(line):
            cands = _candidate_texts(inner)
            if not cands:
                continue
            is_main = _is_answer_frame(inner)
            for idx, text in cands:
                if len(text) > len(any_best):
                    any_best = text
                if is_main and idx == 0 and len(text) > len(main_best):
                    main_best = text
    return clean_text(main_best or any_best)


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    """Non-streaming generation with retry."""
    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields).encode()
    headers = _build_headers()
    ctx = _get_ssl_ctx()
    proxy = CONFIG.get("proxy")

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            req = urllib.request.Request(_get_url(), data=body, headers=headers, method="POST")
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
    """Streaming generation via httpx with retry on connection failure.

    Only the primary candidate of the main answer frame is streamed.
    Thought summaries, drafts and chip frames are ignored, deltas are only
    emitted when the new answer extends what was already emitted, and a
    mid-stream answer rewrite freezes the already-emitted text instead of
    splicing unrelated frames into the output.
    """
    if not HAS_HTTPX:
        text = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        if text:
            yield text
        return

    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields)
    headers = _build_headers()
    client = _get_httpx_client()

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        emitted_raw_text = ""
        emitted_any = False
        raw_lines = []
        try:
            with client.stream("POST", _get_url(), content=body, headers=headers) as resp:
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
                        raw_lines.append(line)
                        current = _best_main_answer(line)
                        if not current:
                            continue
                        if current == emitted_raw_text or emitted_raw_text.startswith(current):
                            continue  # duplicate or older update
                        if not current.startswith(emitted_raw_text):
                            if emitted_any:
                                log("Stream answer replaced mid-flight; keeping already-emitted text")
                                continue
                            emitted_raw_text = ""  # first frame was noise; adopt the real answer
                        delta = clean_text(current[len(emitted_raw_text):], strip=False)
                        emitted_raw_text = current
                        if delta:
                            emitted_any = True
                            yield delta
            if emitted_any:
                return
            # Strict parser matched nothing (protocol drift): rescan the full
            # buffered response with the fallback tiers before giving up.
            if raw_lines:
                fallback = extract_response_text("\n".join(raw_lines))
                if fallback:
                    yield fallback
                    return
            last_err = RuntimeError("no answer frame in Gemini stream response")
        except Exception as e:
            if emitted_any:
                # Content already streamed to the client; retrying would
                # duplicate it.
                raise
            last_err = e
        if attempt < CONFIG["retry_attempts"] - 1:
            log(f"Stream retry {attempt+1}/{CONFIG['retry_attempts']}: {last_err}")
            time.sleep(CONFIG["retry_delay_sec"])
    raise last_err
