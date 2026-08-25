"""Upstream error detection and parsing edge cases."""
import http.client
import json
import threading
import unittest

from gemini_web2api.gemini import extract_response_text, _extract_texts_from_line


class UpstreamErrorTests(unittest.TestCase):
    def test_bard_error_formats(self):
        # New JSPB format (2026-08): application.BardErrorInfo",[1060]]
        for code in (1060, 1037, 1013, 1050):
            raw = (
                ')]}\n\n121\n[["wrb.fr",null,null,null,null,[9,null,'
                '[["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",['
                + str(code) + ']]]]]]'
            )
            with self.assertRaises(RuntimeError) as ctx:
                extract_response_text(raw)
            self.assertIn(f"[{code}]", str(ctx.exception))
        # Old format still detected
        with self.assertRaises(RuntimeError):
            extract_response_text("junk BardErrorInfo [1037] junk")

    def test_short_wrb_line_parsed(self):
        # Regression: lines under 200 chars used to be skipped entirely
        inner = [None, ["c_1", "r_1"], None, None, [["cid", ["hi"]]]]
        line = '[["wrb.fr",null,' + json.dumps(json.dumps(inner)) + ']]'
        texts = _extract_texts_from_line(line)
        self.assertEqual(texts, ["hi"])

    def test_empty_raw(self):
        self.assertEqual(extract_response_text(""), "")


class StreamErrorChunkTests(unittest.TestCase):
    """Upstream failures during SSE streaming must end with a finish chunk + [DONE]."""

    @classmethod
    def setUpClass(cls):
        from gemini_web2api.server import GeminiHandler, ThreadedServer
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
        from gemini_web2api.config import CONFIG
        self.original_config = dict(CONFIG)
        CONFIG["api_keys"] = []
        CONFIG["log_requests"] = False

    def tearDown(self):
        from gemini_web2api.config import CONFIG
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def test_stream_error_emits_finish_chunk(self):
        from unittest import mock

        def failing_stream(*args, **kwargs):
            yield "partial "
            raise RuntimeError("Gemini upstream error [1060]: IP temporarily blocked")

        with mock.patch("gemini_web2api.server.generate_stream", side_effect=failing_stream):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request(
                "POST",
                "/v1/chat/completions",
                body=json.dumps({
                    "model": "gemini-3.6-flash",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                }),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            body = resp.read().decode()
            conn.close()

        self.assertEqual(resp.status, 200)
        self.assertIn("partial ", body)
        self.assertIn("[error] Gemini upstream error [1060]", body)
        self.assertIn('"finish_reason": "stop"', body)
        self.assertIn("data: [DONE]", body)




class NonStreamSuccessTests(unittest.TestCase):
    """Mocked upstream success: raw response -> OpenAI completion shape."""

    @classmethod
    def setUpClass(cls):
        from gemini_web2api.server import GeminiHandler, ThreadedServer
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
        from gemini_web2api.config import CONFIG
        self.original_config = dict(CONFIG)
        CONFIG["api_keys"] = []
        CONFIG["log_requests"] = False

    def tearDown(self):
        from gemini_web2api.config import CONFIG
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def test_non_stream_success_shape(self):
        from unittest import mock
        # server.generate() returns already-extracted text; feed it the parsed expectation
        with mock.patch("gemini_web2api.server.generate", return_value="Раз, два, три, четыре, пять."):
            status, _, body = self._post({"model": "gemini-3.6-flash",
                                          "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["choices"][0]["message"]["content"], "\u0420\u0430\u0437, \u0434\u0432\u0430, \u0442\u0440\u0438, \u0447\u0435\u0442\u044b\u0440\u0435, \u043f\u044f\u0442\u044c.")

    def _post(self, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/v1/chat/completions", body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        return resp.status, dict(resp.getheaders()), body


if __name__ == "__main__":
    unittest.main()
