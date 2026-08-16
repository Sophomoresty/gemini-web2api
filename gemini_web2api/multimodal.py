"""Multimodal: Scotty resumable upload for Gemini image input."""
import ipaddress
import re
import socket
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

try:
    from curl_cffi import CurlOpt
except ImportError:  # pragma: no cover - exercised when image support is absent
    CurlOpt = None

from .config import CONFIG
from .gemini import (
    HAS_CURL_CFFI,
    _account_prefix,
    _get_ssl_ctx,
    curl_requests,
    load_cookie,
    log,
    make_sapisidhash,
)

_MAX_REMOTE_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REMOTE_IMAGE_REDIRECTS = 3
_REDIRECT_STATUS = {301, 302, 303, 307, 308}


def _get_page_tokens() -> dict:
    """Fetch WIZ_global_data tokens from the configured Gemini account page."""
    auth_user = CONFIG.get("auth_user")
    account_prefix = _account_prefix()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://gemini.google.com{account_prefix}/app",
    }
    if account_prefix:
        headers["X-Goog-AuthUser"] = str(auth_user)
    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    try:
        req = urllib.request.Request(
            f"https://gemini.google.com{account_prefix}/app", headers=headers
        )
        proxy = CONFIG.get("proxy")
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                urllib.request.HTTPSHandler(context=_get_ssl_ctx()),
            )
            resp = opener.open(req, timeout=30)
        else:
            resp = urllib.request.urlopen(req, context=_get_ssl_ctx(), timeout=30)
        html = resp.read().decode()
        tokens = {}
        patterns = {
            "push_id": (r'"qKIAYe":"([^"]+)"',),
            "pctx": (r'"Ylro7b":"([^"]+)"',),
            # These values bind file-bearing StreamGenerate requests to the
            # currently loaded Gemini Web session. Keep the previous XSRF key
            # as a fallback because page rollouts are not always simultaneous.
            "f_sid": (r'"FdrFJe":\s*"([^"]+)"',),
            "at": (
                r'"SNlM0e":\s*"([^"]+)"',
                r'"thykhd":\s*"([^"]+)"',
            ),
            # The account page carries the available image model as an
            # internal ID, capacity tail, and model category.
            "image_model": (r'\["(cf[a-f0-9]{14})",\s*(\d+),\s*(6)\]',),
        }
        for key, candidates in patterns.items():
            match = None
            for pattern in candidates:
                match = re.search(pattern, html)
                if match:
                    break
            if match:
                tokens[key] = (match.groups() if key == "image_model"
                               else match.group(1))
        return tokens
    except Exception as e:
        log(f"Page token fetch failed: {e}")
        return {}


_page_tokens_cache = {"tokens": {}, "ts": 0}


def _cached_page_tokens(max_age: int = 600) -> dict:
    """Return Gemini page tokens, refreshing when they are older than max_age.

    File generation asks for a fresh page state because ``f.sid`` is a
    short-lived frontend routing value; uploads can safely reuse the normal
    cache.
    """
    now = time.time()
    if now - _page_tokens_cache["ts"] > max_age:
        _page_tokens_cache["tokens"] = _get_page_tokens()
        _page_tokens_cache["ts"] = now
    return _page_tokens_cache["tokens"]


def detect_image_mime(image_bytes: bytes, fallback: str = "image/png") -> str:
    """Infer a common raster image MIME type from its file signature."""
    if not isinstance(image_bytes, bytes):
        return fallback
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes.startswith(b"BM"):
        return "image/bmp"
    if image_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
        brand = image_bytes[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"hevc", b"hevx"):
            return "image/heic"
    return fallback


def upload_image(image_bytes: bytes, filename: str = "image.png", mime_type: str = "image/png") -> str:
    """Upload image via Scotty resumable upload. Returns file reference path."""
    tokens = _cached_page_tokens()
    push_id = tokens.get("push_id", "feeds/mcudyrk2a4khkz")
    pctx = tokens.get("pctx", "CgcSBWjK7pYx")

    cookie_str, sapisid = load_cookie()
    ctx = _get_ssl_ctx()
    proxy = CONFIG.get("proxy")

    # Step 1: Initiate resumable upload
    start_headers = {
        "Push-ID": push_id,
        "X-Tenant-Id": "bard-storage",
        "X-Client-Pctx": pctx,
        "X-Goog-Upload-Header-Content-Length": str(len(image_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if cookie_str:
        start_headers["Cookie"] = cookie_str
    if sapisid:
        start_headers["Authorization"] = make_sapisidhash(sapisid)

    start_url = "https://content-push.googleapis.com/upload/"
    req = urllib.request.Request(start_url, data=b"", headers=start_headers, method="POST")

    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ctx)
        )
        resp = opener.open(req, timeout=30)
    else:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)

    upload_url = resp.headers.get("X-Goog-Upload-URL") or resp.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError(f"No upload URL in response headers: {dict(resp.headers)}")

    log(f"Upload session started: {upload_url[:80]}...")

    # Step 2: Upload file data + finalize
    upload_headers = {
        "X-Goog-Upload-Command": "upload, finalize",
        "X-Goog-Upload-Offset": "0",
        "Content-Type": "application/octet-stream",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    req2 = urllib.request.Request(upload_url, data=image_bytes, headers=upload_headers, method="POST")
    if proxy:
        resp2 = opener.open(req2, timeout=60)
    else:
        resp2 = urllib.request.urlopen(req2, context=ctx, timeout=60)

    file_ref = resp2.read().decode().strip()
    if not file_ref or not file_ref.startswith("/"):
        raise RuntimeError(f"Invalid file reference: {file_ref[:100]}")

    log(f"Image uploaded: {filename} -> {file_ref[:50]}...")
    return file_ref


def _validate_remote_image_url(url: str):
    """Return the URL, host, and one validated public address for a request hop."""
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise ValueError("invalid remote image URL")
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid remote image URL") from exc
    host = parsed.hostname
    if (parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)):
        raise ValueError("remote image URL is not allowed")

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ValueError("remote image host could not be resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("remote image host is not public")
    address = sorted(addresses, key=lambda item: (item.version, str(item)))[0]
    return url, host, address


def fetch_image_bytes(url: str) -> bytes:
    """Fetch one bounded public HTTPS raster image without implicit redirects."""
    if not HAS_CURL_CFFI or CurlOpt is None:
        log("Image fetch failed: curl_cffi is required for remote image input")
        return b""
    current = url
    try:
        for redirect_count in range(_MAX_REMOTE_IMAGE_REDIRECTS + 1):
            current, host, address = _validate_remote_image_url(current)
            address_text = str(address)
            if address.version == 6:
                address_text = f"[{address_text}]"
            resolve_entry = f"{host}:443:{address_text}"
            # Use a direct, pinned connection. A configured or environment
            # proxy could resolve the hostname again and bypass this check.
            session = curl_requests.Session(
                curl_options={CurlOpt.RESOLVE: [resolve_entry]},
                trust_env=False,
            )
            response = None
            try:
                response = session.get(
                    current,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=CONFIG["request_timeout_sec"],
                    impersonate="chrome",
                    allow_redirects=False,
                    stream=True,
                )
                if response.status_code in _REDIRECT_STATUS:
                    if redirect_count >= _MAX_REMOTE_IMAGE_REDIRECTS:
                        raise ValueError("remote image exceeded redirect limit")
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("remote image redirect has no location")
                    current = urllib.parse.urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise RuntimeError(
                        f"remote image fetch failed: HTTP {response.status_code}"
                    )
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    raise ValueError("remote image response is not an image")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        length = int(content_length)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("invalid remote image content length") from exc
                    if length < 0 or length > _MAX_REMOTE_IMAGE_BYTES:
                        raise ValueError("remote image exceeds size limit")

                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    body.extend(chunk)
                    if len(body) > _MAX_REMOTE_IMAGE_BYTES:
                        raise ValueError("remote image exceeds size limit")
                data = bytes(body)
                detected_type = detect_image_mime(data, "")
                declared_type = "image/jpeg" if content_type == "image/jpg" else content_type
                if not detected_type or declared_type != detected_type:
                    raise ValueError("remote image content type does not match bytes")
                return data
            finally:
                if response is not None:
                    close = getattr(response, "close", None)
                    if close:
                        close()
                session.close()
    except Exception as exc:
        log(f"Image fetch failed: {exc}")
    return b""
