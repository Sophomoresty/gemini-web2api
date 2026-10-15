import http.client
import base64
import json
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs

import importlib.util
from pathlib import Path
from gemini_web2api.config import CONFIG, DEFAULT_CONFIG
from gemini_web2api.gemini import _build_headers, _build_payload
from gemini_web2api.models import MODELS, build_model_ticket, resolve_model
from gemini_web2api.server import GeminiHandler, ThreadedServer
from gemini_web2api.tools import google_contents_to_prompt, messages_to_prompt, parse_tool_calls

_SINGLE_SPEC = importlib.util.spec_from_file_location(
    "gemini_web2api_single",
    Path(__file__).resolve().parent.parent / "gemini_web2api.py",
)
single_file = importlib.util.module_from_spec(_SINGLE_SPEC)
_SINGLE_SPEC.loader.exec_module(single_file)


def _decode_payload(payload):
    outer = json.loads(parse_qs(payload)["f.req"][0])
    return json.loads(outer[1])


def _decode_sse(body):
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event_type = next(
            (line[len("event: "):] for line in lines if line.startswith("event: ")),
            None,
        )
        data = next(
            (line[len("data: "):] for line in lines if line.startswith("data: ")),
            None,
        )
        if event_type and data:
            events.append((event_type, json.loads(data)))
    return events


class PayloadPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def test_temporary_chats_default_to_disabled(self):
        self.assertIs(DEFAULT_CONFIG["temporary_chats"], False)

    def test_persistent_chat_payload(self):
        CONFIG["temporary_chats"] = False

        inner = _decode_payload(_build_payload("hello", 1, 4))

        self.assertEqual(inner[41], [2])
        self.assertIsNone(inner[45])

    def test_temporary_chat_payload(self):
        CONFIG["temporary_chats"] = True

        inner = _decode_payload(_build_payload("hello", 1, 4))

        self.assertEqual(inner[41], [1])
        self.assertEqual(inner[45], 1)

    def test_payload_includes_uploaded_image_refs(self):
        inner = _decode_payload(_build_payload("describe", 1, 4, ["/uploaded/image-ref"]))

        self.assertEqual(inner[0][0], "describe")
        self.assertEqual(inner[0][3], [[None, None, "/uploaded/image-ref"]])


class MessageParsingTests(unittest.TestCase):
    def test_messages_to_prompt_extracts_openai_image_url_data_url(self):
        image_data = base64.b64encode(b"fake png").decode()

        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
            ],
        }])

        self.assertEqual(prompt, "Describe [Image attached]")
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_messages_to_prompt_extracts_responses_input_image_url(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Describe"},
                {"type": "input_image", "image_url": "https://example.com/image.png"},
            ],
        }])

        self.assertEqual(prompt, "Describe [Image attached]")
        self.assertEqual(images, [("https://example.com/image.png", "image/png")])

    def test_messages_to_prompt_ignores_malformed_image_data_url(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,%%%"}},
            ],
        }])

        self.assertEqual(prompt, "Describe")
        self.assertEqual(images, [])

    def test_google_contents_to_prompt_extracts_inline_image_data(self):
        image_data = base64.b64encode(b"fake png").decode()

        prompt, images = google_contents_to_prompt({
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Describe"},
                    {"inlineData": {"mimeType": "image/png", "data": image_data}},
                ],
            }],
        })

        self.assertEqual(prompt, "Describe\n[Image attached]")
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_google_contents_to_prompt_ignores_malformed_inline_image_data(self):
        prompt, images = google_contents_to_prompt({
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Describe"},
                    {"inlineData": {"mimeType": "image/png", "data": "%%%"}},
                ],
            }],
        })

        self.assertEqual(prompt, "Describe")
        self.assertEqual(images, [])


class StreamingEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedServer(("127.0.0.1", 0), GeminiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.original_config = dict(CONFIG)
        CONFIG["api_keys"] = []
        CONFIG["log_requests"] = False

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def post_json(self, path, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    def post_chunked_json(self, path, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            encode_chunked=True,
        )
        response = connection.getresponse()
        body = response.read().decode()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_chat_stream_starts_with_assistant_role(self, generate_stream):
        generate_stream.return_value = iter(["hel", "lo"])

        status, headers, body = self.post_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        chunks = [
            json.loads(line[len("data: "):])
            for line in body.splitlines()
            if line.startswith("data: {")
        ]
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "hel"})
        self.assertEqual(chunks[2]["choices"][0]["delta"], {"content": "lo"})
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    @mock.patch("gemini_web2api.server.generate", return_value="chunked ok")
    def test_chat_accepts_chunked_body(self, _generate):
        status, _, body = self.post_chunked_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "chunked ok")

    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/image-ref")
    @mock.patch("gemini_web2api.server.generate", return_value="looks good")
    def test_chat_accepts_openai_image_url_data_url(self, generate, upload_image):
        image_data = base64.b64encode(b"fake png").decode()

        status, _, body = self.post_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_data}"
                            },
                        },
                    ],
                }],
            },
        )

        self.assertEqual(status, 200)
        upload_image.assert_called_once_with(b"fake png", "image.png", "image/png")
        self.assertEqual(generate.call_args.args[3], ["/uploaded/image-ref"])
        self.assertIn("[Image attached]", generate.call_args.args[0])
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "looks good")

    @mock.patch("gemini_web2api.server.fetch_image_bytes", return_value=b"\xff\xd8\xffremote jpeg")
    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/remote-ref")
    @mock.patch("gemini_web2api.server.generate", return_value="remote ok")
    def test_responses_accepts_input_image_url(self, generate, upload_image, fetch_image_bytes):
        status, _, _ = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": [{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "What is shown?"},
                        {
                            "type": "input_image",
                            "image_url": "https://example.com/image.jpg",
                        },
                    ],
                }],
            },
        )

        self.assertEqual(status, 200)
        fetch_image_bytes.assert_called_once_with("https://example.com/image.jpg")
        upload_image.assert_called_once_with(b"\xff\xd8\xffremote jpeg", "image.png", "image/jpeg")
        self.assertEqual(generate.call_args.args[3], ["/uploaded/remote-ref"])
        self.assertIn("[Image attached]", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/image-ref")
    @mock.patch("gemini_web2api.server.generate", return_value="top-level image ok")
    def test_responses_accepts_top_level_input_image(self, generate, upload_image):
        image_data = base64.b64encode(b"fake png").decode()

        status, _, _ = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": [
                    {"type": "input_text", "text": "What is shown?"},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_data}",
                    },
                ],
            },
        )

        self.assertEqual(status, 200)
        upload_image.assert_called_once_with(b"fake png", "image.png", "image/png")
        self.assertEqual(generate.call_args.args[3], ["/uploaded/image-ref"])
        self.assertIn("What is shown?", generate.call_args.args[0])
        self.assertIn("[Image attached]", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.upload_image", side_effect=RuntimeError("upload denied"))
    def test_google_image_upload_failure_returns_502(self, _upload_image):
        image_data = base64.b64encode(b"fake png").decode()

        status, _, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent",
            {
                "contents": [{
                    "role": "user",
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": image_data,
                        },
                    }],
                }],
            },
        )

        self.assertEqual(status, 502)
        self.assertIn("image upload failed: upload denied", json.loads(body)["error"]["message"])

    @mock.patch("gemini_web2api.server.generate_stream", return_value=iter(["streamed"]))
    def test_google_stream_generate_content_uses_sse(self, _generate_stream):
        status, headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:streamGenerateContent",
            {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": "Stream this"}],
                }],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        self.assertIn('"text": "streamed"', body)

    @mock.patch("gemini_web2api.server.generate", return_value="hello")
    def test_responses_text_stream_has_complete_event_sequence(self, _generate):
        status, headers, body = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": "hello",
                "stream": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        events = _decode_sse(body)
        self.assertEqual(
            [event_type for event_type, _ in events],
            [
                "response.created",
                "response.in_progress",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed",
            ],
        )
        self.assertEqual(
            [event["sequence_number"] for _, event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[4][1]["delta"], "hello")
        self.assertEqual(events[-1][1]["response"]["status"], "completed")
        self.assertEqual(events[-1][1]["response"]["output"][0]["content"][0]["text"], "hello")

    @mock.patch("gemini_web2api.server.parse_tool_calls")
    @mock.patch("gemini_web2api.server.generate", return_value="tool output")
    def test_responses_function_call_stream_has_complete_event_sequence(
        self, _generate, parse_tool_calls
    ):
        parse_tool_calls.return_value = (
            "",
            [
                {
                    "id": "call_test",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Shanghai"}'},
                }
            ],
        )

        status, _, body = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": "weather",
                "tools": [
                    {
                        "type": "function",
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    }
                ],
                "stream": True,
            },
        )

        self.assertEqual(status, 200)
        events = _decode_sse(body)
        self.assertEqual(
            [event_type for event_type, _ in events],
            [
                "response.created",
                "response.in_progress",
                "response.output_item.added",
                "response.function_call_arguments.delta",
                "response.function_call_arguments.done",
                "response.output_item.done",
                "response.completed",
            ],
        )
        self.assertEqual(
            [event["sequence_number"] for _, event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[2][1]["output_index"], 0)
        self.assertEqual(events[3][1]["delta"], '{"city":"Shanghai"}')
        self.assertEqual(events[4][1]["arguments"], '{"city":"Shanghai"}')
        self.assertEqual(events[-1][1]["response"]["output"][0]["name"], "get_weather")

    @mock.patch("gemini_web2api.server.generate", return_value="argon answer")
    def test_chat_completions_forwards_argon_reasoning_effort_and_ticket(self, generate):
        status, _, body = self.post_json(
            "/v1/chat/completions",
            {
                "model": "gemini-4-argon",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "solve this"}],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(generate.call_args.args[1], 8)
        self.assertEqual(generate.call_args.args[2], 0)
        ticket = json.loads(generate.call_args.args[4]["_ticket"])
        self.assertEqual(ticket[4], "95a4c0abcba24364")
        self.assertEqual(ticket[14], 8)
        self.assertEqual(ticket[15], 2)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "argon answer")


class ArgonAndModelRoutingTests(unittest.TestCase):
    def setUp(self):
        from gemini_web2api.gemini import _cookie_cache
        self.original_config = dict(CONFIG)
        self.original_cache = dict(_cookie_cache)
        CONFIG["cookie_file"] = None
        _cookie_cache.update({"str": "", "sapisid": None, "mtime": 0})

    def tearDown(self):
        from gemini_web2api.gemini import _cookie_cache
        CONFIG.clear()
        CONFIG.update(self.original_config)
        _cookie_cache.clear()
        _cookie_cache.update(self.original_cache)

    def test_modular_single_file_and_worker_models_in_sync(self):
        from gemini_web2api.models import REASONING_EFFORT_MAP, THINK_TO_VARIANT
        self.assertEqual(set(MODELS.keys()), set(single_file.MODELS.keys()))
        self.assertEqual(REASONING_EFFORT_MAP, single_file.REASONING_EFFORT_MAP)
        self.assertEqual(THINK_TO_VARIANT, single_file.THINK_TO_VARIANT)
        for name in MODELS:
            self.assertEqual(MODELS[name]["mode"], single_file.MODELS[name]["mode"])
            self.assertEqual(MODELS[name]["think"], single_file.MODELS[name]["think"])
            self.assertEqual(MODELS[name].get("variant"), single_file.MODELS[name].get("variant"))
            self.assertEqual(MODELS[name].get("ticket"), single_file.MODELS[name].get("ticket"))

        worker_path = Path(__file__).resolve().parents[1] / "cloudflare" / "worker.js"
        worker_src = worker_path.read_text(encoding="utf-8")
        for base_key in ("gemini-4-argon", "gemini-3.8-flash", "gemini-3.1-pro", "gemini-flash-lite"):
            self.assertIn(f"'{base_key}'", worker_src)

    def test_argon_thinking_variants_and_precedence(self):
        name, mode, think, err, extra = resolve_model("gemini-4-argon", reasoning_effort="low")
        self.assertIsNone(err)
        self.assertEqual((name, mode, think), ("gemini-4-argon", 8, 4))
        self.assertEqual(json.loads(extra["_ticket"])[15], 1)

        name, mode, think, err, extra = resolve_model("gemini-4-argon", reasoning_effort="medium")
        self.assertIsNone(err)
        self.assertEqual((name, mode, think), ("gemini-4-argon", 8, 2))
        self.assertEqual(json.loads(extra["_ticket"])[15], 4)

        name, mode, think, err, extra = resolve_model("gemini-4-argon", reasoning_effort="high")
        self.assertIsNone(err)
        self.assertEqual((name, mode, think), ("gemini-4-argon", 8, 0))
        ticket = json.loads(extra["_ticket"])
        self.assertEqual(len(ticket), 17)
        self.assertEqual(ticket[4], "95a4c0abcba24364")
        self.assertEqual(ticket[14], 8)
        self.assertEqual(ticket[15], 2)
        headers = _build_headers(extra)
        self.assertIn("X-Goog-Ext-525001261-Jspb", headers)

        # Explicit suffix (-low) takes precedence over body reasoning_effort ("high")
        name, mode, think, err, extra = resolve_model("gemini-4-argon-low", reasoning_effort="high")
        self.assertEqual((name, mode, think), ("gemini-4-argon-low", 8, 4))
        self.assertEqual(json.loads(extra["_ticket"])[15], 1)

        # Explicit @think=0 takes precedence over suffix (-low) and reasoning_effort
        name, mode, think, err, extra = resolve_model("gemini-4-argon-low@think=0", reasoning_effort="low")
        self.assertEqual((name, mode, think), ("gemini-4-argon-low", 8, 0))
        self.assertEqual(json.loads(extra["_ticket"])[15], 2)

        # Legacy models without levels ignore reasoning_effort and do not emit _ticket
        name, mode, think, err, extra = resolve_model("gemini-3.5-flash-thinking", reasoning_effort="low")
        self.assertEqual((name, mode, think), ("gemini-3.5-flash-thinking", 2, 0))
        self.assertNotIn("_ticket", extra)

    def test_custom_ticket_override(self):
        _, _, _, _, extra = resolve_model("gemini-4-argon", model_tickets={"gemini-4-argon": "aa11bb22cc33dd44"})
        ticket = json.loads(extra["_ticket"])
        self.assertEqual(ticket[4], "aa11bb22cc33dd44")
        _, _, _, _, extra_high = resolve_model("gemini-4-argon-high", model_tickets={"gemini-4-argon": "aa11bb22cc33dd44"})
        self.assertEqual(json.loads(extra_high["_ticket"])[4], "aa11bb22cc33dd44")

    def test_parse_netscape_cookies_includes_httponly(self):
        from gemini_web2api.gemini import _parse_netscape_cookies
        raw = (
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1800000000\t__Secure-1PSID\tsid_val\n"
            ".google.com\tTRUE\t/\tTRUE\t1800000000\tSAPISID\tsapi_val\n"
        )
        parsed = _parse_netscape_cookies(raw)
        self.assertIn("__Secure-1PSID=sid_val", parsed)
        self.assertIn("SAPISID=sapi_val", parsed)

    def test_http_400_refreshes_bl_and_retries_even_when_retry_attempts_is_one(self):
        import urllib.error
        from gemini_web2api.gemini import generate
        CONFIG["retry_attempts"] = 1
        calls = []

        class FakeResp:
            def read(self):
                inner = json.dumps([None, None, None, None, [["rc_1", ["ok_after_refresh"]]]])
                line = json.dumps([["wrb.fr", None, inner + (" " * 220)]])
                return line.encode("utf-8")

        def fake_urlopen(req, context=None, timeout=None):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
            return FakeResp()

        def fake_refresh():
            CONFIG["gemini_bl"] = "boq_assistant-bard-web-server_20261014.99_p0"
            return True

        with mock.patch("gemini_web2api.gemini.urllib.request.urlopen", side_effect=fake_urlopen), \
             mock.patch("gemini_web2api.gemini.refresh_bl_and_xsrf", side_effect=fake_refresh):
            out = generate("hello", 8, 2)
        self.assertEqual(out, "ok_after_refresh")
        self.assertEqual(len(calls), 2)
        self.assertIn("20261014.99_p0", calls[1])

    def test_ticket_canned_error_falls_back_without_header_even_when_retry_attempts_is_one(self):
        from gemini_web2api.gemini import generate, generate_stream, _is_canned_error
        seen_headers = []

        # 220 spaces satisfies the 200-char line length filter in _extract_texts_from_line
        def make_frame(msg: str) -> bytes:
            inner = json.dumps([None, None, None, None, [["rc_1", [msg]]]])
            line = json.dumps([["wrb.fr", None, inner + (" " * 220)]]) + "\n"
            return line.encode("utf-8")

        class FakeResp:
            def __init__(self, payload: bytes):
                self._payload = payload

            def read(self):
                return self._payload

        def fake_urlopen(req, context=None, timeout=None):
            seen_headers.append(dict(req.headers))
            if len(seen_headers) == 1:
                return FakeResp(make_frame("I encountered an error doing what you asked. Please try again."))
            return FakeResp(make_frame("fallback model answer"))

        _, mode, think, _, extra = resolve_model("gemini-4-argon")
        with mock.patch.dict(CONFIG, {"retry_attempts": 1}), \
             mock.patch("gemini_web2api.gemini.urllib.request.urlopen", side_effect=fake_urlopen):
            out = generate("hello", mode, think, None, extra)
        self.assertEqual(out, "fallback model answer")
        self.assertEqual(len(seen_headers), 2)
        self.assertTrue(any(k.lower() == "x-goog-ext-525001261-jspb" for k in seen_headers[0]))
        self.assertFalse(any(k.lower() == "x-goog-ext-525001261-jspb" for k in seen_headers[1]))

        # Negative test: multi-line explanation starting with "Sorry, something went wrong" must not match
        self.assertFalse(_is_canned_error("Sorry, something went wrong in your regex:\nuse \\d+ instead."))
        self.assertTrue(_is_canned_error("I encountered an error doing what you asked."))

        # Termination test: if fallback also returns canned error, stops after 2 requests
        seen_headers.clear()
        with mock.patch.dict(CONFIG, {"retry_attempts": 1}), \
             mock.patch(
                 "gemini_web2api.gemini.urllib.request.urlopen",
                 side_effect=lambda req, context=None, timeout=None: (
                     seen_headers.append(dict(req.headers))
                     or FakeResp(make_frame("I encountered an error doing what you asked."))
                 ),
             ):
            out2 = generate("hello", mode, think, None, extra)
        self.assertEqual(len(seen_headers), 2)
        self.assertIn("I encountered an error", out2)

        # Streaming test: multi-frame canned error falls back without yielding canned text
        stream_headers = []

        class FakeStreamResp:
            def __init__(self, lines):
                self.status_code = 200
                self._lines = lines

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                pass

            def iter_text(self):
                for ln in self._lines:
                    yield ln

        class FakeClient:
            def stream(self, method, url, content=None, headers=None, timeout=None):
                stream_headers.append(dict(headers or {}))
                if len(stream_headers) == 1:
                    return FakeStreamResp([
                        make_frame("I encountered an error ").decode(),
                        make_frame("I encountered an error doing what you asked.").decode(),
                    ])
                return FakeStreamResp([
                    make_frame("Short").decode(),
                    make_frame("Short valid streamed answer").decode(),
                ])

        with mock.patch.dict(CONFIG, {"retry_attempts": 1}), \
             mock.patch("gemini_web2api.gemini.HAS_HTTPX", True), \
             mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=FakeClient()):
            chunks = list(generate_stream("hello", mode, think, None, extra))
        self.assertEqual("".join(chunks), "Short valid streamed answer")
        self.assertEqual(len(stream_headers), 2)
        self.assertIn("X-Goog-Ext-525001261-Jspb", stream_headers[0])
        self.assertNotIn("X-Goog-Ext-525001261-Jspb", stream_headers[1])

    def test_ticket_timeout_falls_back_without_header_in_modular_and_standalone(self):
        import socket
        import urllib.error
        from gemini_web2api.gemini import generate, generate_stream

        def make_frame(msg: str) -> bytes:
            inner = json.dumps([None, None, None, None, [["rc_1", [msg]]]])
            line = json.dumps([["wrb.fr", None, inner + (" " * 220)]]) + "\n"
            return line.encode("utf-8")

        class FakeResp:
            def __init__(self, payload: bytes):
                self._payload = payload

            def read(self):
                return self._payload

        seen_calls = []

        def fake_urlopen(req, context=None, timeout=None):
            seen_calls.append((dict(req.headers), timeout))
            if len(seen_calls) == 1:
                raise urllib.error.URLError(socket.timeout("timed out"))
            return FakeResp(make_frame("fallback after timeout"))

        _, mode, think, _, extra = resolve_model("gemini-4-argon")
        with mock.patch.dict(CONFIG, {"retry_attempts": 1, "request_timeout_sec": 180, "ticket_timeout_sec": 25}), \
             mock.patch("gemini_web2api.gemini.urllib.request.urlopen", side_effect=fake_urlopen):
            out = generate("hello", mode, think, None, extra)
        self.assertEqual(out, "fallback after timeout")
        self.assertEqual(len(seen_calls), 2)
        self.assertTrue(any(k.lower() == "x-goog-ext-525001261-jspb" for k in seen_calls[0][0]))
        self.assertEqual(seen_calls[0][1], 25.0)
        self.assertFalse(any(k.lower() == "x-goog-ext-525001261-jspb" for k in seen_calls[1][0]))
        self.assertEqual(seen_calls[1][1], 180)

        # Standalone non-streaming parity + 2-request termination bound
        seen_calls.clear()
        with mock.patch.dict(single_file.CONFIG, {"retry_attempts": 1, "request_timeout_sec": 180, "ticket_timeout_sec": 25}), \
             mock.patch.object(single_file.urllib.request, "urlopen", side_effect=fake_urlopen):
            raw_out = single_file.gemini_stream_generate("hello", mode, think, None, extra)
            self.assertEqual(single_file.extract_response_text(raw_out), "fallback after timeout")
        self.assertEqual(len(seen_calls), 2)
        self.assertTrue(any(k.lower() == "x-goog-ext-525001261-jspb" for k in seen_calls[0][0]))
        self.assertEqual(seen_calls[0][1], 25.0)
        self.assertFalse(any(k.lower() == "x-goog-ext-525001261-jspb" for k in seen_calls[1][0]))
        self.assertEqual(seen_calls[1][1], 180)

        # Streaming ticket timeout after initial metadata-only wrb.fr frame falls back cleanly
        stream_calls = []
        meta_frame = json.dumps([["wrb.fr", None, '[null,["c_1","r_1"],{"18":"r_1"}]']]) + "\n"

        class TimeoutStreamResp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                pass

            def iter_text(self):
                yield meta_frame
                raise socket.timeout("read timed out")

        class OkStreamResp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                pass

            def iter_text(self):
                yield make_frame("stream fallback ok").decode()

        class TimeoutThenOkClient:
            def stream(self, method, url, content=None, headers=None, timeout=None):
                stream_calls.append((dict(headers or {}), timeout))
                if len(stream_calls) == 1:
                    return TimeoutStreamResp()
                return OkStreamResp()

        with mock.patch.dict(CONFIG, {"retry_attempts": 1, "request_timeout_sec": 180, "ticket_timeout_sec": 25}), \
             mock.patch("gemini_web2api.gemini.HAS_HTTPX", True), \
             mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=TimeoutThenOkClient()):
            chunks = list(generate_stream("hello", mode, think, None, extra))
        self.assertEqual("".join(chunks), "stream fallback ok")
        self.assertEqual(len(stream_calls), 2)
        self.assertIn("X-Goog-Ext-525001261-Jspb", stream_calls[0][0])
        self.assertEqual(stream_calls[0][1], 25.0)
        self.assertNotIn("X-Goog-Ext-525001261-Jspb", stream_calls[1][0])
        self.assertEqual(stream_calls[1][1], 180)

        # Mid-stream timeout AFTER emitting text must raise immediately without retrying or duplicating output
        mid_stream_calls = []

        class MidStreamTimeoutResp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                pass

            def iter_text(self):
                yield make_frame("x" * 305).decode()
                raise socket.timeout("mid-stream timeout")

        class MidStreamClient:
            def stream(self, method, url, content=None, headers=None, timeout=None):
                mid_stream_calls.append(dict(headers or {}))
                return MidStreamTimeoutResp()

        emitted = []
        with mock.patch.dict(CONFIG, {"retry_attempts": 3, "retry_delay_sec": 0}), \
             mock.patch("gemini_web2api.gemini.HAS_HTTPX", True), \
             mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=MidStreamClient()):
            with self.assertRaises(socket.timeout):
                for ch in generate_stream("hello", mode, think, None, extra):
                    emitted.append(ch)
        self.assertEqual(len(mid_stream_calls), 1)
        self.assertEqual(emitted, ["x" * 305])

        # Standalone streaming ticket timeout fallback + _ticket_timeout config resilience
        from gemini_web2api.gemini import _ticket_timeout
        with mock.patch.dict(CONFIG, {"request_timeout_sec": 180, "ticket_timeout_sec": 0}):
            self.assertEqual(_ticket_timeout(), 180.0)
        with mock.patch.dict(CONFIG, {"request_timeout_sec": 180, "ticket_timeout_sec": "bad"}):
            self.assertEqual(_ticket_timeout(), 25.0)

        standalone_stream_calls = []

        class FakeStandaloneHttpx:
            HTTPStatusError = type("HTTPStatusError", (Exception,), {})
            ConnectTimeout = type("ConnectTimeout", (Exception,), {})
            PoolTimeout = type("PoolTimeout", (Exception,), {})
            TimeoutException = type("TimeoutException", (Exception,), {})

            @staticmethod
            def HTTPTransport(proxy=None):
                return None

            class Client:
                def __init__(self, transport=None, timeout=None, verify=True):
                    self.timeout = timeout

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def stream(self, method, url, content=None, headers=None):
                    standalone_stream_calls.append((dict(headers or {}), self.timeout))
                    if len(standalone_stream_calls) == 1:
                        return TimeoutStreamResp()
                    return OkStreamResp()

        with mock.patch.dict(single_file.CONFIG, {"retry_attempts": 1, "request_timeout_sec": 180, "ticket_timeout_sec": 25}), \
             mock.patch.object(single_file, "HAS_HTTPX", True), \
             mock.patch.object(single_file, "httpx", FakeStandaloneHttpx):
            sf_chunks = list(single_file.gemini_stream_generate_iter("hello", mode, think, None, extra))
        self.assertEqual("".join(sf_chunks), "stream fallback ok")
        self.assertEqual(len(standalone_stream_calls), 2)
        self.assertIn("X-Goog-Ext-525001261-Jspb", standalone_stream_calls[0][0])
        self.assertEqual(standalone_stream_calls[0][1], 25.0)
        self.assertNotIn("X-Goog-Ext-525001261-Jspb", standalone_stream_calls[1][0])
        self.assertEqual(standalone_stream_calls[1][1], 180)


if __name__ == "__main__":
    unittest.main()
