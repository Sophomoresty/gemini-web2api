import http.client
import base64
import json
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs

from gemini_web2api.config import CONFIG, DEFAULT_CONFIG
from gemini_web2api.gemini import (
    _batch_response_url, _build_image_payload, _build_payload, _generate_file_with_curl,
    build_model_header, extract_response_text, generate_stream,
)
from gemini_web2api.generated_image import (
    download_generated_image, extract_generation_result, resolve_generated_image_url,
    validate_generated_image_url,
)
from gemini_web2api.multimodal import _get_page_tokens
from gemini_web2api.server import GeminiHandler, ThreadedServer
from gemini_web2api.tools import google_contents_to_prompt, messages_to_prompt


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
        inner = _decode_payload(_build_payload("describe", 1, 4, [("/uploaded/image-ref", "cat.png")]))

        self.assertEqual(len(inner), 81)
        self.assertEqual(inner[0][0], "describe")
        self.assertEqual(inner[0][3], [[["/uploaded/image-ref"], "cat.png"]])
        self.assertEqual(inner[80], 1)

    def test_payload_accepts_legacy_plain_file_refs(self):
        inner = _decode_payload(_build_payload("describe", 1, 4, ["/uploaded/image-ref"]))

        self.assertEqual(inner[0][3], [[["/uploaded/image-ref"], "image.png"]])

    def test_text_payload_shape_is_unchanged(self):
        inner = _decode_payload(_build_payload("hello", 1, 4))

        self.assertEqual(len(inner), 102)
        self.assertIsNone(inner[80])

    def test_image_payload_has_capture_derived_shape_and_fresh_values(self):
        first = _decode_payload(_build_image_payload("make a fox", "REQUEST-UUID"))
        second = _decode_payload(_build_image_payload("make a fox", "REQUEST-UUID"))

        self.assertEqual(len(first), 97)
        self.assertEqual(first[0][0], "make a fox")
        self.assertTrue(first[3].startswith("!"))
        self.assertEqual(len(first[3]), 2539)
        self.assertNotEqual(first[3], second[3])
        self.assertRegex(first[4], r"^[a-f0-9]{32}$")
        self.assertNotEqual(first[4], second[4])
        self.assertEqual(first[17], [[0]])
        self.assertEqual(first[41], [1])
        self.assertEqual(first[59], "REQUEST-UUID")
        self.assertEqual({i: first[i] for i in (6, 7, 10, 11, 18, 27, 30, 53, 61, 67, 68, 79, 80, 91, 96)}, {
            6: [0], 7: 1, 10: 1, 11: 0, 18: 0, 27: 1, 30: [4], 53: 0,
            61: [], 67: 0, 68: 1, 79: 6, 80: 1, 91: 0, 96: 0,
        })

    def test_model_header_uses_public_routing_constants(self):
        headers = build_model_header("cf41b0e0dd7d53e5", 1, 6)
        self.assertIn('"cf41b0e0dd7d53e5"', headers["x-goog-ext-525001261-jspb"])
        self.assertEqual(headers["x-goog-ext-73010989-jspb"], "[0]")
        self.assertEqual(headers["x-goog-ext-73010990-jspb"], "[0,0,0]")


class UpstreamErrorTests(unittest.TestCase):
    def test_extract_response_text_rejects_structured_bard_error(self):
        frame = [[
            "wrb.fr", None, None, None, None,
            [13, None, [[
                "type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",
                [1100],
            ]]],
        ]]

        with self.assertRaisesRegex(RuntimeError, r"BardErrorInfo \[1100\]"):
            extract_response_text(json.dumps(frame))

    def test_extract_response_text_still_rejects_legacy_bard_error(self):
        with self.assertRaisesRegex(RuntimeError, r"BardErrorInfo \[10\]"):
            extract_response_text("BardErrorInfo [10]")


class GeneratedImageTests(unittest.TestCase):
    def _raw_frame(self, candidate, cid="chat-id", rid="reply-id"):
        frame = [None, [cid, rid], None, None, [candidate]]
        return json.dumps([["wrb.fr", None, json.dumps(frame)]])

    def test_extracts_generated_image_metadata_from_rich_content_field_seven(self):
        image_entry = [
            [None, None, None, [None, None, "cat alt", "https://lh3.googleusercontent.com/a"]],
            ["image-id"],
        ]
        rich_content = [None] * 7 + [[[image_entry]]]
        candidate = ["candidate-id", ["A cat"]] + [None] * 10 + [rich_content]
        result = extract_generation_result(self._raw_frame(candidate), lambda text: text)

        self.assertEqual(result.text, "A cat")
        self.assertEqual(len(result.images), 1)
        self.assertEqual(result.images[0].url, "https://lh3.googleusercontent.com/a")
        self.assertEqual(result.images[0].alt, "cat alt")
        self.assertEqual(result.images[0].image_id, "image-id")
        self.assertEqual(result.images[0].rcid, "candidate-id")
        self.assertEqual(result.images[0].cid, "chat-id")
        self.assertEqual(result.images[0].rid, "reply-id")

    def test_extracts_generated_image_metadata_from_sparse_field_eight(self):
        image_entry = [
            [None, None, None, [None, None, "fox alt", "https://lh3.googleusercontent.com/fox"]],
            ["fox-image-id"],
        ]
        rich_content = [{"8": [[image_entry]]}]
        candidate = ["candidate-id", ["A fox"]] + [None] * 10 + [rich_content]

        result = extract_generation_result(self._raw_frame(candidate), lambda text: text)

        self.assertEqual(result.text, "A fox")
        self.assertEqual(len(result.images), 1)
        self.assertEqual(result.images[0].url, "https://lh3.googleusercontent.com/fox")
        self.assertEqual(result.images[0].image_id, "fox-image-id")

    def test_generated_url_validation_rejects_non_google_and_ssrf_shapes(self):
        self.assertEqual(
            validate_generated_image_url("https://lh3.googleusercontent.com/a"),
            "https://lh3.googleusercontent.com/a",
        )
        for url in (
            "http://lh3.googleusercontent.com/a", "https://evilgoogleusercontent.com/a",
            "https://googleusercontent.com@evil.example/a", "https://127.0.0.1/a",
            "https://lh3.googleusercontent.com:444/a",
        ):
            with self.assertRaises(ValueError):
                validate_generated_image_url(url)

    @mock.patch("gemini_web2api.generated_image.curl_requests")
    @mock.patch("gemini_web2api.generated_image.HAS_CURL_CFFI", True)
    def test_generated_download_checks_redirect_host_size_magic_and_type(self, requests):
        response = requests.get.return_value
        response.status_code = 200
        response.headers = {"Content-Type": "image/png", "Content-Length": "8"}
        response.iter_content.return_value = [b"\x89PNG\r\n\x1a\n"]
        response.close = mock.Mock()

        data, mime = download_generated_image("https://lh3.googleusercontent.com/a")

        self.assertEqual(data, b"\x89PNG\r\n\x1a\n")
        self.assertEqual(mime, "image/png")
        self.assertFalse(requests.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(requests.get.call_args.kwargs["impersonate"], "chrome")

    @mock.patch("gemini_web2api.generated_image.curl_requests")
    @mock.patch("gemini_web2api.generated_image.HAS_CURL_CFFI", True)
    def test_generated_url_resolves_exact_two_stage_text_mediators(self, requests):
        first, second = mock.Mock(), mock.Mock()
        first.status_code, first.headers = 200, {"Content-Type": "text/plain"}
        first.iter_content.return_value = [b"https://work.fife.usercontent.google.com/a"]
        second.status_code, second.headers = 200, {"Content-Type": "text/plain"}
        second.iter_content.return_value = [b"https://lh3.googleusercontent.com/rd-gg-dl/a"]
        first.close, second.close = mock.Mock(), mock.Mock()
        requests.get.side_effect = [first, second]

        self.assertEqual(resolve_generated_image_url("https://lh3.googleusercontent.com/gg-dl/a"),
                         "https://lh3.googleusercontent.com/rd-gg-dl/a")
        self.assertEqual(requests.get.call_count, 2)

    @mock.patch("gemini_web2api.generated_image.curl_requests")
    @mock.patch("gemini_web2api.generated_image.HAS_CURL_CFFI", True)
    def test_generated_download_rejects_unsafe_redirect_and_type_mismatch(self, requests):
        response = requests.get.return_value
        response.status_code = 302
        response.headers = {"Location": "https://example.com/not-an-image"}
        response.close = mock.Mock()
        with self.assertRaises(ValueError):
            download_generated_image("https://lh3.googleusercontent.com/a")

        response.status_code = 200
        response.headers = {"Content-Type": "image/jpeg"}
        response.iter_content.return_value = [b"\x89PNG\r\n\x1a\n"]
        with self.assertRaises(ValueError):
            download_generated_image("https://lh3.googleusercontent.com/a")


class FullSizeImageTests(unittest.TestCase):
    def test_batch_response_extracts_full_size_url(self):
        payload = json.dumps([["wrb.fr", "c8o8Fe", json.dumps(["https://lh3.googleusercontent.com/gg-dl/final"])]])
        raw = ")]}'" + chr(10) + str(len(payload)) + chr(10) + payload
        self.assertEqual(_batch_response_url(raw), "https://lh3.googleusercontent.com/gg-dl/final")


class FileGenerationTests(unittest.TestCase):
    @mock.patch("gemini_web2api.gemini.extract_response_text", return_value="cat")
    @mock.patch("gemini_web2api.gemini.curl_requests")
    @mock.patch("gemini_web2api.multimodal._cached_page_tokens", return_value={"f_sid": "session", "at": "token"})
    @mock.patch("gemini_web2api.gemini.HAS_CURL_CFFI", True)
    def test_file_generation_uses_chrome_impersonation_and_page_session(
        self, page_tokens, curl_requests, extract_response_text
    ):
        response = curl_requests.post.return_value
        response.text = "upstream body"

        self.assertEqual(
            _generate_file_with_curl("describe", 1, 4, [("/uploaded/ref", "cat.png")]),
            "cat",
        )

        page_tokens.assert_called_once_with(max_age=0)
        url, = curl_requests.post.call_args.args
        kwargs = curl_requests.post.call_args.kwargs
        self.assertIn("f.sid=session", url)
        self.assertEqual(kwargs["impersonate"], "chrome")
        sent_inner = _decode_payload(kwargs["data"])
        self.assertEqual(sent_inner[0][3], [[["/uploaded/ref"], "cat.png"]])
        self.assertEqual(sent_inner[80], 1)
        self.assertEqual(parse_qs(kwargs["data"])["at"], ["token"])
        request_uuid = kwargs["headers"]["x-goog-ext-525005358-jspb"]
        self.assertEqual(request_uuid, f'["{sent_inner[59]}",1]')
        response.raise_for_status.assert_called_once()
        extract_response_text.assert_called_once_with("upstream body")

    @mock.patch("gemini_web2api.gemini.generate", return_value="one result")
    def test_file_streaming_falls_back_to_one_non_stream_result(self, generate):
        self.assertEqual(
            list(generate_stream("describe", 1, 4, [("/uploaded/ref", "cat.png")])),
            ["one result"],
        )
        generate.assert_called_once_with("describe", 1, 4, [("/uploaded/ref", "cat.png")], None)


class PageTokenTests(unittest.TestCase):
    @mock.patch("gemini_web2api.multimodal.urllib.request.urlopen")
    @mock.patch("gemini_web2api.multimodal.load_cookie", return_value=("", None))
    def test_page_tokens_follow_configured_auth_user(self, _load_cookie, urlopen):
        response = urlopen.return_value
        response.read.return_value = b'{"FdrFJe":"sid","SNlM0e":"token"}'
        previous = CONFIG.get("auth_user")
        CONFIG["auth_user"] = "2"
        try:
            self.assertEqual(_get_page_tokens()["f_sid"], "sid")
        finally:
            CONFIG["auth_user"] = previous

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://gemini.google.com/u/2/app")
        self.assertEqual(request.get_header("X-goog-authuser"), "2")
        self.assertEqual(request.get_header("Referer"), "https://gemini.google.com/u/2/app")


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
        self.assertEqual(generate.call_args.args[3], [("/uploaded/image-ref", "image.png")])
        self.assertIn("[Image attached]", generate.call_args.args[0])
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "looks good")

    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/image-ref")
    @mock.patch("gemini_web2api.server.generate", side_effect=RuntimeError("file rejected"))
    def test_chat_image_stream_reports_failure_before_sse(self, _generate, _upload_image):
        image_data = base64.b64encode(b"fake png").decode()
        status, headers, body = self.post_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "stream": True,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                    ],
                }],
            },
        )

        self.assertEqual(status, 502)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn("file rejected", json.loads(body)["error"]["message"])

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
        self.assertEqual(generate.call_args.args[3], [("/uploaded/remote-ref", "image.png")])
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
        self.assertEqual(generate.call_args.args[3], [("/uploaded/image-ref", "image.png")])
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

    @mock.patch("gemini_web2api.server.resolve_generated_image_url", return_value="https://lh3.googleusercontent.com/rd-gg-dl/a")
    @mock.patch("gemini_web2api.server.get_full_size_image", return_value=None)
    @mock.patch("gemini_web2api.server.generate_image_structured")
    @mock.patch("gemini_web2api.server.download_generated_image", return_value=(b"\x89PNG\r\n\x1a\n", "image/png"))
    def test_image_generation_endpoint_returns_full_size_preferred_b64_or_resolved_url(self, download, generate_image_structured, full_size, resolve_url):
        from gemini_web2api.generated_image import GeneratedImage, GenerationResult
        image = GeneratedImage("https://lh3.googleusercontent.com/a")
        generate_image_structured.return_value = GenerationResult(images=[image])

        status, _, body = self.post_json("/v1/images/generations", {"prompt": "a cat"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"][0]["b64_json"], "iVBORw0KGgo=")
        download.assert_called_once_with(image.url)

        status, _, body = self.post_json("/v1/images/generations", {"prompt": "a cat", "response_format": "url"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"][0]["url"], "https://lh3.googleusercontent.com/rd-gg-dl/a")
        resolve_url.assert_called_once_with(image.url)

    def test_image_generation_endpoint_rejects_unsupported_options(self):
        status, _, _ = self.post_json("/v1/images/generations", {"prompt": "a cat", "n": 2})
        self.assertEqual(status, 400)
        status, _, _ = self.post_json("/v1/images/generations", {"prompt": "a cat", "size": "1024x1024"})
        self.assertEqual(status, 400)

    @mock.patch("gemini_web2api.server.get_full_size_image", return_value=None)
    @mock.patch("gemini_web2api.server.generate_image_structured")
    @mock.patch("gemini_web2api.server.download_generated_image", return_value=(b"\x89PNG\r\n\x1a\n", "image/png"))
    def test_responses_image_generation_is_native_and_streams_atomic_item(self, _download, generate_image_structured, _full_size):
        from gemini_web2api.generated_image import GeneratedImage, GenerationResult
        generate_image_structured.return_value = GenerationResult(text="caption", images=[GeneratedImage("https://lh3.googleusercontent.com/a")])
        status, headers, body = self.post_json("/v1/responses", {
            "input": "make a cat", "tools": [{"type": "image_generation"}], "stream": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        events = _decode_sse(body)
        image_events = [(name, event) for name, event in events if event.get("item", {}).get("type") == "image_generation_call"]
        self.assertEqual([name for name, _ in image_events], ["response.output_item.added", "response.output_item.done"])
        self.assertEqual(image_events[-1][1]["item"]["result"], "iVBORw0KGgo=")
        self.assertEqual(events[-1][0], "response.completed")

    @mock.patch("gemini_web2api.server.get_full_size_image", return_value=None)
    @mock.patch("gemini_web2api.server.generate_image_structured")
    @mock.patch("gemini_web2api.server.download_generated_image", return_value=(b"\x89PNG\r\n\x1a\n", "image/png"))
    def test_responses_image_generation_non_stream_returns_completed_item(self, _download, generate_image_structured, _full_size):
        from gemini_web2api.generated_image import GeneratedImage, GenerationResult
        generate_image_structured.return_value = GenerationResult(
            text="caption", images=[GeneratedImage("https://lh3.googleusercontent.com/a")]
        )

        status, headers, body = self.post_json("/v1/responses", {
            "input": "make a cat", "tools": [{"type": "image_generation"}],
        })

        response = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(response["object"], "response")
        self.assertEqual(response["status"], "completed")
        image_item = next(item for item in response["output"] if item["type"] == "image_generation_call")
        self.assertTrue(image_item["id"].startswith("imggen_"))
        self.assertEqual(image_item["status"], "completed")
        self.assertEqual(image_item["result"], "iVBORw0KGgo=")

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


if __name__ == "__main__":
    unittest.main()
