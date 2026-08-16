"""Parsing and bounded download helpers for Gemini-generated images."""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover - exercised where optional dependency is absent
    curl_requests = None
    HAS_CURL_CFFI = False

from .config import CONFIG

MAX_GENERATED_IMAGE_BYTES = 10 * 1024 * 1024
MAX_GENERATED_IMAGE_REDIRECTS = 3
MAX_GENERATED_IMAGE_MEDIATORS = 2
MAX_GENERATED_IMAGE_URL_TEXT_BYTES = 8192
_ALLOWED_GENERATED_IMAGE_HOST = "googleusercontent.com"
_MEDIATOR_GENERATED_IMAGE_HOST = "work.fife.usercontent.google.com"
_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_MAGIC_MIMES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass(frozen=True)
class GeneratedImage:
    """Image metadata carried by a Gemini candidate rich-content block."""

    url: str
    alt: str = ""
    image_id: str = ""
    cid: str = ""
    rid: str = ""
    rcid: str = ""


@dataclass
class GenerationResult:
    """Structured Gemini result without changing the legacy ``generate`` API."""

    text: str = ""
    images: list[GeneratedImage] = field(default_factory=list)
    raw: str = ""


def _nested(value: Any, indexes: list[int], default: Any = None) -> Any:
    for index in indexes:
        if not isinstance(value, list) or index >= len(value):
            return default
        value = value[index]
    return default if value is None else value


def _jspb_field(container: Any, index: int, default: Any = None) -> Any:
    """Read a JSPB positional field or its trailing sparse-field representation."""
    if not isinstance(container, list):
        return default
    value = container[index] if index < len(container) else None
    if value in (None, [], {}) or isinstance(value, dict):
        sparse = container[-1] if container and isinstance(container[-1], dict) else None
        value = sparse.get(str(index + 1)) if sparse else None
    return default if value in (None, [], {}) else value


def _wrb_payloads(raw: str):
    for line in raw.splitlines():
        if '"wrb.fr"' not in line:
            continue
        try:
            envelope = json.loads(line)
            payload = _nested(envelope, [0, 2])
            if isinstance(payload, str):
                yield json.loads(payload)
        except (json.JSONDecodeError, TypeError, IndexError):
            continue


def extract_generation_result(raw: str, clean_text) -> GenerationResult:
    """Parse text and generated-image metadata from StreamGenerate response frames.

    Gemini places candidates at frame field ``[4]``.  A candidate's rich content
    is field ``[12]``; generated images are rich-content field 7 (or sparse key
    ``"8"``), whose entries live at ``[0]``.  Preview URL, alt text, and image
    ID are respectively ``[0][3][3]``, ``[0][3][2]``, and ``[1][0]``.
    """
    bard_error = re.search(r"BardErrorInfo\s*\[(\d+)\]", raw)
    if bard_error:
        raise RuntimeError("Gemini upstream rejected request: BardErrorInfo [%s]" % bard_error.group(1))

    text = ""
    images: list[GeneratedImage] = []
    seen = set()
    cid = rid = ""
    for frame in _wrb_payloads(raw):
        metadata = _nested(frame, [1], [])
        if isinstance(metadata, list):
            cid = _nested(metadata, [0], cid) or cid
            rid = _nested(metadata, [1], rid) or rid
        candidates = _nested(frame, [4], [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, list):
                continue
            candidate_text = _nested(candidate, [1, 0], "")
            if isinstance(candidate_text, str) and len(candidate_text) > len(text):
                text = candidate_text
            rcid = _nested(candidate, [0], "")
            rich = _nested(candidate, [12], [])
            generated_block = _jspb_field(rich, 7, [])
            entries = _nested(generated_block, [0], [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                url = _nested(entry, [0, 3, 3], "")
                if not isinstance(url, str) or not url:
                    continue
                image_id = _nested(entry, [1, 0], "")
                key = (url, image_id)
                if key in seen:
                    continue
                seen.add(key)
                alt = _nested(entry, [0, 3, 2], "")
                images.append(GeneratedImage(
                    url=url, alt=alt if isinstance(alt, str) else "",
                    image_id=image_id if isinstance(image_id, str) else "",
                    cid=cid if isinstance(cid, str) else "",
                    rid=rid if isinstance(rid, str) else "",
                    rcid=rcid if isinstance(rcid, str) else "",
                ))
    return GenerationResult(text=clean_text(text), images=images, raw=raw)


def _validated_https_url(url: str, allowed_hosts: set[str]) -> str:
    if not isinstance(url, str) or not url or len(url) > MAX_GENERATED_IMAGE_URL_TEXT_BYTES:
        raise ValueError("invalid generated image URL")
    if any(ch.isspace() for ch in url):
        raise ValueError("invalid generated image URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid generated image URL") from exc
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)):
        raise ValueError("generated image URL is not allowed")
    try:
        ipaddress.ip_address(host)
        raise ValueError("generated image URL is not allowed")
    except ValueError as exc:
        if str(exc) == "generated image URL is not allowed":
            raise
    if host not in allowed_hosts:
        raise ValueError("generated image URL is not allowed")
    return url


def validate_generated_image_url(url: str) -> str:
    """Permit only HTTPS googleusercontent image URLs, never private targets."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid generated image URL") from exc
    if host != _ALLOWED_GENERATED_IMAGE_HOST and not host.endswith("." + _ALLOWED_GENERATED_IMAGE_HOST):
        raise ValueError("generated image URL is not allowed")
    return _validated_https_url(url, {host})


def _validate_mediator_url(url: str) -> str:
    return _validated_https_url(url, {_MEDIATOR_GENERATED_IMAGE_HOST})


def _image_mime(data: bytes) -> str:
    for magic, mime in _MAGIC_MIMES:
        if data.startswith(magic):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("generated image has unsupported or invalid bytes")


def _limits() -> tuple[int, int]:
    configured_bytes = CONFIG.get("generated_image_max_bytes", MAX_GENERATED_IMAGE_BYTES)
    configured_redirects = CONFIG.get("generated_image_max_redirects", MAX_GENERATED_IMAGE_REDIRECTS)
    max_bytes = (min(configured_bytes, MAX_GENERATED_IMAGE_BYTES)
                 if isinstance(configured_bytes, int) and not isinstance(configured_bytes, bool)
                 else MAX_GENERATED_IMAGE_BYTES)
    max_redirects = (max(0, min(configured_redirects, MAX_GENERATED_IMAGE_REDIRECTS))
                     if isinstance(configured_redirects, int) and not isinstance(configured_redirects, bool)
                     else MAX_GENERATED_IMAGE_REDIRECTS)
    return max_bytes, max_redirects


def _request_args(stream: bool) -> dict:
    args = {
        "headers": {"Referer": "https://gemini.google.com/"},
        "timeout": CONFIG["request_timeout_sec"],
        "impersonate": "chrome",
        "allow_redirects": False,
        "stream": stream,
    }
    if CONFIG.get("proxy"):
        args["proxy"] = CONFIG["proxy"]
    return args


def _read_mediator_url(response) -> str:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "text/plain":
        raise ValueError("generated image mediator did not return text/plain")
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_GENERATED_IMAGE_URL_TEXT_BYTES:
                raise ValueError("generated image mediator response is too large")
        except ValueError as exc:
            if str(exc) == "generated image mediator response is too large":
                raise
            raise ValueError("invalid generated image mediator content length") from exc
    body = bytearray()
    for chunk in response.iter_content(chunk_size=1024):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > MAX_GENERATED_IMAGE_URL_TEXT_BYTES:
            raise ValueError("generated image mediator response is too large")
    try:
        url = bytes(body).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("generated image mediator did not return a URL") from exc
    if not url or any(ch.isspace() for ch in url):
        raise ValueError("generated image mediator did not return one URL")
    return url


def resolve_generated_image_url(url: str) -> str:
    """Resolve Gemini's bounded text mediators to a final image URL.

    The only non-googleusercontent hop is the exact ``work.fife`` host, which
    is accepted solely when it returns one small text/plain HTTPS URL.  This is
    used by ``response_format=url`` without downloading the final image bytes.
    """
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl_cffi is required for generated image download")
    current = validate_generated_image_url(url)
    _, max_redirects = _limits()
    redirects = mediators = 0
    source_is_mediator = False

    while True:
        response = curl_requests.get(current, **_request_args(stream=True))
        try:
            if response.status_code in _REDIRECT_STATUS:
                if redirects >= max_redirects:
                    raise ValueError("generated image exceeded redirect limit")
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("generated image redirect has no location")
                next_url = urljoin(current, location)
                # A work.fife URL is permitted only as the first text mediator;
                # redirect responses may otherwise remain on Google hosts.
                try:
                    current = validate_generated_image_url(next_url)
                    source_is_mediator = False
                except ValueError:
                    if source_is_mediator:
                        raise
                    current = _validate_mediator_url(next_url)
                    source_is_mediator = True
                redirects += 1
                continue
            if response.status_code != 200:
                raise RuntimeError("generated image download failed: HTTP %s" % response.status_code)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type.startswith("image/"):
                if source_is_mediator:
                    raise ValueError("generated image mediator returned image bytes")
                return current
            next_url = _read_mediator_url(response)
            mediators += 1
            if mediators > MAX_GENERATED_IMAGE_MEDIATORS:
                raise ValueError("generated image exceeded mediator limit")
            if source_is_mediator:
                # The second stage must lead back to an allowlisted final image host.
                return validate_generated_image_url(next_url)
            current = _validate_mediator_url(next_url)
            source_is_mediator = True
        finally:
            close = getattr(response, "close", None)
            if close:
                close()


def download_generated_image(url: str) -> tuple[bytes, str]:
    """Download a resolved generated image with verified image bytes."""
    final_url = resolve_generated_image_url(url)
    max_bytes, max_redirects = _limits()
    current = final_url
    for _ in range(max_redirects + 1):
        response = curl_requests.get(current, **_request_args(stream=True))
        try:
            if response.status_code in _REDIRECT_STATUS:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("generated image redirect has no location")
                current = validate_generated_image_url(urljoin(current, location))
                continue
            if response.status_code != 200:
                raise RuntimeError("generated image download failed: HTTP %s" % response.status_code)
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise ValueError("generated image exceeds 10 MiB")
                except ValueError as exc:
                    if str(exc) == "generated image exceeds 10 MiB":
                        raise
                    raise ValueError("invalid generated image content length") from exc
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("generated image exceeds 10 MiB")
                chunks.append(chunk)
            data = b"".join(chunks)
            mime = _image_mime(data)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != mime:
                raise ValueError("generated image content type does not match bytes")
            return data, mime
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
    raise ValueError("generated image exceeded redirect limit")
