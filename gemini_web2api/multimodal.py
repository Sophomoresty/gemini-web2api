"""Multimodal: Scotty resumable upload for Gemini image input."""
import json
import base64
import urllib.request
import urllib.parse
import time
import ssl
import re
from urllib.parse import urlparse

from .config import CONFIG
from .gemini import load_cookie, make_sapisidhash, _get_ssl_ctx, log


def _get_page_tokens() -> dict:
    """Fetch WIZ_global_data tokens from Gemini page (Push-ID, X-Client-Pctx)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    cookie_str, sapisid = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    try:
        req = urllib.request.Request("https://gemini.google.com/app", headers=headers)
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
        for key, pattern in [
            ("push_id", r'"qKIAYe":"([^"]+)"'),
            ("pctx", r'"Ylro7b":"([^"]+)"'),
            ("at", r'"thykhd":"([^"]+)"'),
        ]:
            m = re.search(pattern, html)
            if m:
                tokens[key] = m.group(1)
        return tokens
    except Exception as e:
        log(f"Page token fetch failed: {e}")
        return {}


_page_tokens_cache = {"tokens": {}, "ts": 0}


def _cached_page_tokens() -> dict:
    now = time.time()
    if now - _page_tokens_cache["ts"] > 600:
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


def fetch_image_bytes(url: str) -> bytes:
    """Fetch image from URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        log(f"Image fetch skipped for unsupported URL scheme: {parsed.scheme or 'none'}")
        return b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        proxy = CONFIG.get("proxy")
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                urllib.request.HTTPSHandler(context=_get_ssl_ctx()),
            )
            resp = opener.open(req, timeout=30)
        else:
            resp = urllib.request.urlopen(req, context=_get_ssl_ctx(), timeout=30)
        return resp.read()
    except Exception as e:
        log(f"Image fetch failed: {e}")
        return b""
