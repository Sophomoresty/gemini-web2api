"""HTTP server: OpenAI-compatible API endpoints."""
import json
import time
import uuid
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from .config import CONFIG
from .models import MODELS, resolve_model
from .gemini import generate, generate_stream, log
from .tools import (messages_to_prompt, google_contents_to_prompt, parse_google_function_calls,
                    responses_input_to_messages, responses_tools_to_chat)
from .completion import CompletionRequest, complete, complete_stream
from .openai_format import (ChatStream, ResponsesStream, chat_usage, error_body, responses_output_items,
                            responses_usage, tool_calls_text)
from .multimodal import detect_image_mime, fetch_image_bytes, upload_image
from . import __version__


def _upload_images(images: list) -> list:
    """Upload images and return list of file references. Returns None if no images."""
    if not images:
        return None
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
            ref = upload_image(data, "image.png", mime or "image/png")
            file_refs.append(ref)
        except Exception as e:
            raise RuntimeError(f"image upload failed: {e}") from e
    return file_refs if file_refs else None


def _responses_format(req: dict):
    """Map Responses `text.format` to a Chat-style response_format."""
    fmt = (req.get("text") or {}).get("format") if isinstance(req.get("text"), dict) else None
    if not isinstance(fmt, dict):
        return None
    if fmt.get("type") == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": fmt.get("name"), "schema": fmt.get("schema", {})}}
    if fmt.get("type") == "json_object":
        return {"type": "json_object"}
    return None


class GeminiHandler(BaseHTTPRequestHandler):
    # ─── Transport hooks (overridden by the single-file entrypoint) ─────────

    def _models(self) -> dict:
        return MODELS

    def _resolve(self, name: str):
        """-> (model_name, model_id, think_mode, error, extra_fields)"""
        return resolve_model(name if isinstance(name, str) and name else CONFIG["default_model"])

    def _text_fn(self, model_id, think_mode, file_refs, extra_fields):
        return lambda prompt: generate(prompt, model_id, think_mode, file_refs, extra_fields)

    def _stream_fn(self, model_id, think_mode, file_refs, extra_fields):
        return lambda prompt: generate_stream(prompt, model_id, think_mode, file_refs, extra_fields)

    def _upload(self, images):
        return _upload_images(images)

    # ─── Plumbing ────────────────────────────────────────────────────────────

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

    def send_error_json(self, status: int, message: str, err_type: str = None, code=None, param=None):
        if err_type is None:
            err_type = {401: "invalid_request_error", 404: "invalid_request_error",
                        502: "upstream_error"}.get(status, "invalid_request_error" if status < 500 else "server_error")
        self.send_json(error_body(message, err_type, code, param), status)

    def _parse_body(self, body: bytes):
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return None

    def _parse_object(self, body: bytes):
        """Parse a JSON object body, replying 400 and returning None otherwise."""
        req = self._parse_body(body)
        if req is None:
            self.send_error_json(400, "invalid JSON body")
            return None
        if not isinstance(req, dict):
            self.send_error_json(400, "request body must be a JSON object")
            return None
        return req

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
                self.send_error_json(401, "invalid api key", code="invalid_api_key")
                return
            models = self._models()
            path = self.path.split("?", 1)[0]
            if path == "/v1/models":
                self.send_json({"object": "list", "data": [
                    {"id": n, "object": "model", "created": 1700000000,
                     "owned_by": "google", "description": c["desc"]}
                    for n, c in models.items()
                ]})
            elif path.startswith("/v1/models/"):
                name = path[len("/v1/models/"):]
                if name in models:
                    self.send_json({"id": name, "object": "model", "created": 1700000000,
                                    "owned_by": "google", "description": models[name]["desc"]})
                else:
                    self.send_error_json(404, f"model '{name}' not found", code="model_not_found")
            elif path.startswith("/v1beta/models"):
                self.send_json({"models": [
                    {"name": f"models/{n}", "displayName": n, "description": c["desc"],
                     "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]}
                    for n, c in models.items()
                ]})
            elif path == "/":
                self.send_json({"status": "ok", "version": __version__, "models": list(models.keys())})
            else:
                self.send_error_json(404, f"unknown path {path}")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            if self.path.startswith("/v1") and not self._authorized():
                self.send_error_json(401, "invalid api key", code="invalid_api_key")
                return
            body = self._read_request_body()
            path = self.path.split("?", 1)[0]
            if path == "/v1/chat/completions":
                self._handle_chat(body)
            elif path == "/v1/responses":
                self._handle_responses(body)
            elif ":streamGenerateContent" in path:
                self._handle_google_generate(body, stream=True)
            elif ":generateContent" in path:
                self._handle_google_generate(body, stream=False)
            else:
                self.send_error_json(404, f"unknown path {path}")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"POST error: {e}")
            try:
                self.send_error_json(500, str(e))
            except Exception:
                pass

    # ─── /v1/chat/completions ─────────────────────────────────────────────────

    def _handle_chat(self, body: bytes):
        req = self._parse_object(body)
        if req is None:
            return
        messages = req.get("messages")
        if not isinstance(messages, list) or not messages:
            self.send_error_json(400, "'messages' must be a non-empty array", param="messages")
            return
        model_name, model_id, think_mode, err, extra_fields = self._resolve(req.get("model"))
        if err:
            self.send_error_json(400, err, param="model")
            return

        tools = req.get("tools")
        tool_choice = req.get("tool_choice", "auto")
        response_format = req.get("response_format")
        prompt, images = messages_to_prompt(messages, tools, tool_choice, response_format)
        if not prompt.strip():
            self.send_error_json(400, "empty prompt", param="messages")
            return
        creq = CompletionRequest(prompt, tools, tool_choice, response_format, req.get("stop"))

        try:
            file_refs = self._upload(images)
        except RuntimeError as e:
            self.send_error_json(502, f"upstream error: {e}")
            return

        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        if req.get("stream"):
            include_usage = bool((req.get("stream_options") or {}).get("include_usage"))
            self._stream_chat(creq, cid, model_name, include_usage,
                              self._stream_fn(model_id, think_mode, file_refs, extra_fields))
            return

        try:
            result = complete(self._text_fn(model_id, think_mode, file_refs, extra_fields), creq)
        except Exception as e:
            log(f"Chat error: {e}")
            self.send_error_json(502, f"upstream error: {e}")
            return

        msg = {"role": "assistant",
               "content": result.text if (result.text or not result.tool_calls) else None,
               "refusal": None}
        if result.tool_calls:
            msg["tool_calls"] = result.tool_calls
        self.send_json({
            "id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "message": msg, "logprobs": None, "finish_reason": result.finish_reason}],
            "usage": chat_usage(prompt, result.text + tool_calls_text(result.tool_calls)),
        })

    def _stream_chat(self, creq, cid, model_name, include_usage, stream_fn):
        stream = ChatStream(self, cid, model_name, include_usage)
        text, calls, finish = "", [], "stop"
        try:
            for kind, value in complete_stream(stream_fn, creq):
                if kind == "text":
                    text += value
                    stream.content(value)
                elif kind == "tool_calls":
                    calls = value
                    stream.tool_calls(value)
                elif kind == "finish":
                    finish = value
            stream.finish(finish, chat_usage(creq.prompt, text + tool_calls_text(calls)))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log(f"Stream error: {e}")
            try:
                if stream.started:
                    stream.error(f"upstream error: {e}")
                else:
                    self.send_error_json(502, f"upstream error: {e}")
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ─── /v1/responses (Codex CLI) ───────────────────────────────────────────

    def _handle_responses(self, body: bytes):
        req = self._parse_object(body)
        if req is None:
            return
        model_name, model_id, think_mode, err, extra_fields = self._resolve(req.get("model"))
        if err:
            self.send_error_json(400, err, param="model")
            return

        messages = responses_input_to_messages(req)
        tools = responses_tools_to_chat(req.get("tools"))
        tool_choice = req.get("tool_choice", "auto")
        response_format = _responses_format(req)
        prompt, images = messages_to_prompt(messages, tools, tool_choice, response_format)
        if not prompt.strip():
            self.send_error_json(400, "empty input", param="input")
            return
        creq = CompletionRequest(prompt, tools, tool_choice, response_format)

        try:
            file_refs = self._upload(images)
        except RuntimeError as e:
            self.send_error_json(502, f"upstream error: {e}")
            return

        rid = f"resp_{uuid.uuid4().hex[:24]}"
        if req.get("stream"):
            stream = ResponsesStream(self, rid, model_name)
            text, calls = "", []
            try:
                for kind, value in complete_stream(
                        self._stream_fn(model_id, think_mode, file_refs, extra_fields), creq):
                    if kind == "text":
                        text += value
                        stream.text(value)
                    elif kind == "tool_calls":
                        calls = value
                        stream.function_calls(value)
                stream.complete(responses_usage(prompt, text + tool_calls_text(calls)))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log(f"Responses stream error: {e}")
                try:
                    if stream.started:
                        stream.fail(f"upstream error: {e}")
                    else:
                        self.send_error_json(502, f"upstream error: {e}")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return

        try:
            result = complete(self._text_fn(model_id, think_mode, file_refs, extra_fields), creq)
        except Exception as e:
            log(f"Responses error: {e}")
            self.send_error_json(502, f"upstream error: {e}")
            return
        self.send_json({
            "id": rid, "object": "response", "created_at": int(time.time()), "status": "completed",
            "model": model_name, "output": responses_output_items(result.text, result.tool_calls),
            "usage": responses_usage(prompt, result.text + tool_calls_text(result.tool_calls)),
        })

    # ─── /v1beta/models (Google Gemini CLI) ──────────────────────────────────

    def _handle_google_generate(self, body: bytes, stream: bool):
        req = self._parse_object(body)
        if req is None:
            return
        m = re.match(r'/v1beta/models/([^:?]+)', self.path)
        model_name = m.group(1) if m else CONFIG["default_model"]
        model_name, model_id, think_mode, err, extra_fields = self._resolve(model_name)
        if err:
            self.send_error_json(400, err)
            return

        tool_config = req.get("toolConfig", {})
        fc_mode = tool_config.get("functionCallingConfig", {}).get("mode", "AUTO")
        has_tools = bool(req.get("tools")) and fc_mode != "NONE"
        declared = {fn.get("name") for group in (req.get("tools") or []) if isinstance(group, dict)
                    for fn in group.get("functionDeclarations", []) if fn.get("name")}
        prompt, images = google_contents_to_prompt(req)
        if not prompt.strip():
            self.send_error_json(400, "empty content")
            return

        try:
            file_refs = self._upload(images)
        except RuntimeError as e:
            self.send_error_json(502, f"upstream error: {e}")
            return
        log(f"Google API: model={model_name} stream={stream} tools={has_tools} prompt_len={len(prompt)}")
        creq = CompletionRequest(prompt)

        if stream and not has_tools:
            started = False
            try:
                full_text = ""
                for kind, delta in complete_stream(
                        self._stream_fn(model_id, think_mode, file_refs, extra_fields), creq):
                    if kind != "text" or not delta:
                        continue
                    if not started:
                        self._start_sse()
                        started = True
                    full_text += delta
                    chunk_obj = {
                        "candidates": [{"content": {"parts": [{"text": delta}], "role": "model"}, "index": 0}],
                        "modelVersion": model_name,
                    }
                    self.wfile.write(f"data: {json.dumps(chunk_obj, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                if not started:
                    self._start_sse()
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
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log(f"Google stream error: {e}")
                if not started:
                    try:
                        self.send_error_json(502, f"upstream error: {e}")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
            return

        try:
            text = complete(self._text_fn(model_id, think_mode, file_refs, extra_fields), creq).text
        except Exception as e:
            self.send_error_json(502, f"upstream error: {e}")
            return

        response_parts = []
        if has_tools and text:
            clean_text, function_calls = parse_google_function_calls(text, declared or None)
            if function_calls:
                if clean_text:
                    response_parts.append({"text": clean_text})
                for fc in function_calls:
                    response_parts.append({"functionCall": {"name": fc["name"], "args": fc["args"]}})
            else:
                response_parts.append({"text": text})
        else:
            response_parts.append({"text": text})

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
        else:
            self.send_json(response_obj)

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
