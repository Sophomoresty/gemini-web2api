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
import secrets

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False

from .config import CONFIG
from .generated_image import GenerationResult, extract_generation_result

_ssl_ctx = None
_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
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


_IMAGE_MODEL_HEADER_KEY = "x-goog-ext-525001261-jspb"
# Current Flash Lite route from Gemini Web's page-model list. An
# account-specific discovered route remains preferred when available.
_IMAGE_MODEL_FALLBACK = ("8c46e95b1a07cecc", "2", 6)


def _build_headers(request_uuid: str = None) -> dict:
    account_prefix = _account_prefix()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{account_prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if request_uuid:
        headers["x-goog-ext-525005358-jspb"] = f'["{request_uuid}",1]'
    if account_prefix:
        headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def build_model_header(model_id: str, capacity_tail: str | int, model_category: int) -> dict:
    """Build the Gemini Web model-selection headers without session values."""
    return {
        _IMAGE_MODEL_HEADER_KEY: (
            f'[1,null,null,null,"{model_id}",null,null,0,[4,5,6,8],null,null,'
            f'{capacity_tail},null,null,{model_category}]'
        ),
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0,0,0]",
    }


def _image_model_headers(page_tokens: dict, session_uuid: str = None) -> dict:
    """Use current page model routing when available, with a bounded public fallback."""
    discovered = page_tokens.get("image_model")
    if (isinstance(discovered, (tuple, list)) and len(discovered) == 3
            and re.fullmatch(r"[a-f0-9]{16}", str(discovered[0]))
            and str(discovered[1]).isdigit() and str(discovered[2]) == "6"):
        model_id, capacity_tail, category = discovered
    else:
        model_id, capacity_tail, category = _IMAGE_MODEL_FALLBACK
    headers = build_model_header(str(model_id), str(capacity_tail), int(category))
    model_header = json.loads(headers[_IMAGE_MODEL_HEADER_KEY])
    model_header.extend([1, session_uuid or str(uuid.uuid4()).upper()])
    headers[_IMAGE_MODEL_HEADER_KEY] = json.dumps(model_header)
    return headers


def _apply_chat_persistence_flags(inner: list) -> None:
    """Apply Gemini Web persistence flags to an outgoing request payload."""
    if CONFIG.get("temporary_chats", False):
        # Match Gemini Web temporary-chat requests.
        inner[41] = [1]
        inner[45] = 1
    else:
        inner[41] = [2]


def _normalise_file_ref(file_ref) -> list:
    """Convert legacy refs and ``(ref, filename)`` pairs to Gemini's file shape."""
    if isinstance(file_ref, (tuple, list)) and len(file_ref) == 2:
        ref, filename = file_ref
    else:
        ref, filename = file_ref, "image.png"
    if not isinstance(ref, str) or not ref:
        raise ValueError("invalid uploaded file reference")
    return [[ref], filename or "image.png"]


def _build_payload(prompt: str, model_id: int, think_mode: int, file_refs: list = None,
                   extra_fields: dict = None, xsrf_token: str = None,
                   request_uuid: str = None) -> str:
    # File-bearing requests use the current 81-slot Gemini Web protocol and
    # require slot 80. Preserve the established text-only payload unchanged.
    # Callers may still pass old plain string refs.
    inner = [None] * (81 if file_refs else 102)
    if file_refs:
        refs = [_normalise_file_ref(ref) for ref in file_refs]
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
    inner[59] = request_uuid or str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if file_refs:
        inner[80] = 1
    if extra_fields:
        for k, v in extra_fields.items():
            inner[k] = v
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    if xsrf_token or CONFIG.get("xsrf_token"):
        params["at"] = xsrf_token or CONFIG["xsrf_token"]
    return urllib.parse.urlencode(params)


def _build_image_payload(prompt: str, request_uuid: str, xsrf_token: str = None) -> str:
    """Build the capture-derived 97-slot image StreamGenerate body.

    Slots 3 and 4 deliberately contain fresh browser-style opaque/request IDs;
    no captured values are retained.  This mode is separate from text and
    attachment payloads because Gemini's image route rejects their shape.
    """
    inner = [None] * 97
    inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    # 1 + 2538 URL-safe Base64 characters = the 2539-character GUI slot.
    inner[3] = "!" + secrets.token_urlsafe(1903)
    inner[4] = uuid.uuid4().hex
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[0]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [1]
    inner[53] = 0
    inner[59] = request_uuid
    inner[61] = []
    inner[67] = 0
    inner[68] = 1
    inner[79] = 6
    inner[80] = 1
    inner[91] = 0
    inner[96] = 0
    params = {"f.req": json.dumps([None, json.dumps(inner)])}
    if xsrf_token or CONFIG.get("xsrf_token"):
        params["at"] = xsrf_token or CONFIG["xsrf_token"]
    return urllib.parse.urlencode(params)


def _get_url(session_id: str = None) -> str:
    reqid = int(time.time()) % 1000000
    account_prefix = _account_prefix()
    params = {
        "bl": CONFIG["gemini_bl"],
        "hl": "en",
        "_reqid": reqid,
        "rt": "c",
    }
    if session_id:
        params["f.sid"] = session_id
    return (
        f"https://gemini.google.com{account_prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate?"
        f"{urllib.parse.urlencode(params)}"
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


def _generate_file_raw_with_curl(prompt: str, model_id: int, think_mode: int, file_refs: list,
                                 extra_fields: dict = None) -> str:
    """Send a file request with Chrome TLS/browser impersonation and return raw frames.

    Gemini currently rejects otherwise valid uploaded-file requests from the
    stdlib TLS stack. curl_cffi supplies the Chrome fingerprint used by Gemini
    Web while retaining this project's cookie, proxy, and timeout settings.
    """
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl_cffi is required for Gemini image input")

    # Import lazily because multimodal imports cookie helpers from this module.
    from .multimodal import _cached_page_tokens
    page_tokens = _cached_page_tokens(max_age=0)
    request_uuid = str(uuid.uuid4()).upper()
    body = _build_payload(
        prompt, model_id, think_mode, file_refs, extra_fields,
        xsrf_token=page_tokens.get("at"), request_uuid=request_uuid,
    )
    url = _get_url(page_tokens.get("f_sid"))
    headers = _build_headers(request_uuid)
    request_args = {
        "data": body,
        "headers": headers,
        "timeout": CONFIG["request_timeout_sec"],
        "impersonate": "chrome",
    }
    if CONFIG.get("proxy"):
        request_args["proxy"] = CONFIG["proxy"]

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            response = curl_requests.post(url, **request_args)
            response.raise_for_status()
            return response.text
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"File generation retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def _generate_image_raw_with_curl(prompt: str) -> str:
    """Send the dedicated GUI-equivalent image-generation request via Chrome TLS."""
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl_cffi is required for Gemini image generation")

    from .multimodal import _cached_page_tokens
    page_tokens = _cached_page_tokens(max_age=0)
    request_uuid = str(uuid.uuid4()).upper()
    body = _build_image_payload(
        prompt, request_uuid, xsrf_token=page_tokens.get("at")
    )
    headers = _build_headers(request_uuid)
    headers.update(_image_model_headers(page_tokens, str(uuid.uuid4()).upper()))
    request_args = {
        "data": body,
        "headers": headers,
        "timeout": CONFIG["request_timeout_sec"],
        "impersonate": "chrome",
    }
    if CONFIG.get("proxy"):
        request_args["proxy"] = CONFIG["proxy"]

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            response = curl_requests.post(_get_url(page_tokens.get("f_sid")), **request_args)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_err = exc
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Image generation retry {attempt+1}/{CONFIG['retry_attempts']}: {exc}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_image_structured(prompt: str) -> GenerationResult:
    """Generate an image with the GUI-specific payload and return rich metadata."""
    return extract_generation_result(_generate_image_raw_with_curl(prompt), clean_text)


def _batch_response_url(raw: str) -> str:
    """Extract the first full-size image URL from batchexecute's framed RPC body."""
    decoder = json.JSONDecoder()
    position = raw.find("\n") + 1 if raw.startswith(")]}'") else 0
    while position < len(raw):
        while position < len(raw) and raw[position].isspace():
            position += 1
        length = re.match(r"\d+\n", raw[position:])
        if not length:
            break
        position += length.end()
        try:
            envelope, position = decoder.raw_decode(raw, position)
        except json.JSONDecodeError:
            break
        pending = [envelope]
        while pending:
            record = pending.pop()
            if not isinstance(record, list):
                continue
            if (len(record) >= 3 and record[0] == "wrb.fr" and record[1] == "c8o8Fe"
                    and isinstance(record[2], str)):
                try:
                    payload = json.loads(record[2])
                    candidate = payload[0] if isinstance(payload, list) and payload else None
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(candidate, str):
                    return candidate
            pending.extend(item for item in record if isinstance(item, list))
    raise ValueError("full-size image RPC returned no URL")


def get_full_size_image(image) -> str | None:
    """Ask Gemini's c8o8Fe RPC for a full-size generated-image URL.

    Missing image metadata or a rejected/changed RPC is non-fatal: callers can
    continue with the preview resolution path.
    """
    if not HAS_CURL_CFFI or not all(isinstance(x, str) and x for x in
                                    (image.cid, image.rid, image.rcid, image.image_id)):
        return None
    try:
        from .multimodal import _cached_page_tokens
        page_tokens = _cached_page_tokens(max_age=0)
        payload = [
            [[None, None, None, [None, None, None, None, None, ""]], [image.image_id, 0],
             None, [19, ""], None, None, None, None, None, ""],
            [image.rid, image.rcid, image.cid, None, ""], 1, 0, 1,
        ]
        rpc = ["c8o8Fe", json.dumps(payload), None, "generic"]
        params = {
            "rpcids": "c8o8Fe", "hl": "en", "_reqid": int(time.time()) % 1000000,
            "rt": "c", "source-path": f"{_account_prefix()}/app/{image.cid}",
            "bl": CONFIG["gemini_bl"],
        }
        if page_tokens.get("f_sid"):
            params["f.sid"] = page_tokens["f_sid"]
        body = urllib.parse.urlencode({
            "f.req": json.dumps([[rpc]]), "at": page_tokens.get("at") or CONFIG.get("xsrf_token") or "",
        })
        request_uuid = str(uuid.uuid4()).upper()
        headers = _build_headers(request_uuid)
        headers.update(_image_model_headers(page_tokens))
        args = {"data": body, "headers": headers, "timeout": CONFIG["request_timeout_sec"],
                "impersonate": "chrome"}
        if CONFIG.get("proxy"):
            args["proxy"] = CONFIG["proxy"]
        url = f"https://gemini.google.com{_account_prefix()}/_/BardChatUi/data/batchexecute?{urllib.parse.urlencode(params)}"
        response = curl_requests.post(url, **args)
        try:
            response.raise_for_status()
            return _batch_response_url(response.text)
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
    except Exception as exc:
        log(f"Full-size image RPC unavailable: {exc}")
        return None


def _generate_file_with_curl(prompt: str, model_id: int, think_mode: int, file_refs: list,
                             extra_fields: dict = None) -> str:
    """Legacy text-only wrapper for Chrome-impersonated file generation."""
    return extract_response_text(_generate_file_raw_with_curl(
        prompt, model_id, think_mode, file_refs, extra_fields
    ))


def _generate_raw(prompt: str, model_id: int, think_mode: int, file_refs: list = None,
                  extra_fields: dict = None) -> str:
    """Generate once and retain the raw frames for structured rich-content parsing."""
    if file_refs:
        return _generate_file_raw_with_curl(prompt, model_id, think_mode, file_refs, extra_fields)

    body = _build_payload(prompt, model_id, think_mode, extra_fields=extra_fields).encode()
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
            return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_structured(prompt: str, model_id: int, think_mode: int, file_refs: list = None,
                        extra_fields: dict = None) -> GenerationResult:
    """Return text plus generated-image metadata while leaving ``generate`` unchanged."""
    return extract_generation_result(
        _generate_raw(prompt, model_id, think_mode, file_refs, extra_fields), clean_text
    )


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    """Non-streaming generation with retry."""
    return extract_response_text(_generate_raw(prompt, model_id, think_mode, file_refs, extra_fields))


def generate_stream(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Streaming generation via httpx with retry on connection failure.

    File requests intentionally yield one non-stream result because Gemini
    requires Chrome impersonation for those requests.
    """
    if file_refs:
        text = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        if text:
            yield text
        return

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
