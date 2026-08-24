"""Regression tests for StreamGenerate response parsing.

These simulate the multi-frame wire format that Gemini Web actually returns:
the main answer frame (carrying conversation/response ids) plus thought
summaries, alternative drafts and follow-up chip frames that do NOT carry
those ids.

The old parser picked the longest text found anywhere in the response, so a
long thought-summary / draft / chip frame won over a short real answer and
the API returned content unrelated to the user's question.
"""
import importlib.util
import json
import os
import unittest
from unittest import mock

from gemini_web2api.config import CONFIG
from gemini_web2api.gemini import (
    HAS_HTTPX,
    extract_response_text,
    generate_stream,
    _next_reqid,
)

SINGLE_FILE = os.path.join(os.path.dirname(__file__), "..", "gemini_web2api.py")


def load_single_file():
    spec = importlib.util.spec_from_file_location("gemini_web2api_single", SINGLE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── wire-format builders ────────────────────────────────────────────────────

def make_line(*inners):
    """Serialize one StreamGenerate chunk line containing several frames."""
    return json.dumps([["wrb.fr", "f%d" % i, json.dumps(inner)] for i, inner in enumerate(inners)])


def make_raw(*lines):
    body = "".join("%d\n%s\n" % (len(l), l) for l in lines)
    return ")]}'\n\n" + body


def answer_inner(segments, drafts=(), convo="c_abc123", resp="r_def456"):
    """Main answer frame: carries conversation id at [1] and response id at [2]."""
    inner = [None] * 6
    inner[1] = convo
    inner[2] = resp
    candidates = [[None, list(segments)]]
    for draft in drafts:
        candidates.append([None, list(draft)])
    inner[4] = candidates
    return inner


def noise_inner(segments):
    """A frame without conversation/response ids: thought summary, follow-up
    chip, image-agent or search frame."""
    inner = [None] * 6
    inner[4] = [[None, list(segments)]]
    return inner


ANATOMY_NOISE = (
    "The pharynx, larynx, and trachea form a continuous vertical pathway "
    "connecting your nasal cavity and mouth down to your lungs. " * 8
)
SHORT_ANSWER = "I'm not sure what you mean. Could you clarify?"


# ─── non-streaming parser ────────────────────────────────────────────────────

class ExtractResponseTextTests(unittest.TestCase):
    def test_thought_frame_longer_than_answer_is_ignored(self):
        raw = make_raw(
            make_line(noise_inner([ANATOMY_NOISE])),
            make_line(answer_inner([SHORT_ANSWER])),
        )
        self.assertEqual(extract_response_text(raw), SHORT_ANSWER)

    def test_chip_frame_after_answer_is_ignored(self):
        chip = "Would you like a closer look at how the epiglottis prevents choking during swallowing? " * 6
        raw = make_raw(
            make_line(answer_inner([SHORT_ANSWER])),
            make_line(noise_inner([chip])),
        )
        self.assertEqual(extract_response_text(raw), SHORT_ANSWER)

    def test_multi_segment_answer_is_joined(self):
        raw = make_raw(make_line(answer_inner(["你好，", "我是一段", "被切開的答案", "請重新組合"])))
        self.assertEqual(extract_response_text(raw), "你好，我是一段被切開的答案請重新組合")

    def test_longer_alternative_draft_does_not_override_primary(self):
        draft = "This alternative draft is much longer than the primary answer " * 5
        raw = make_raw(make_line(answer_inner([SHORT_ANSWER], drafts=[(draft,)])))
        self.assertEqual(extract_response_text(raw), SHORT_ANSWER)

    def test_progressive_updates_return_final_text(self):
        growing = ["Hel", "Hello", "Hello, world!"]
        raw = make_raw(*[make_line(answer_inner([t])) for t in growing])
        self.assertEqual(extract_response_text(raw), "Hello, world!")

    def test_fallback_when_no_frame_carries_ids(self):
        # Protocol drift: no answer frame ids anywhere. Longest joined
        # candidate is returned so the API stays functional.
        raw = make_raw(
            make_line(noise_inner(["short"])),
            make_line(noise_inner([SHORT_ANSWER])),
        )
        self.assertEqual(extract_response_text(raw), SHORT_ANSWER)

    def test_multi_frame_line_all_frames_parsed(self):
        # A single line carrying both noise and the answer frame.
        raw = make_raw(make_line(noise_inner([ANATOMY_NOISE]), answer_inner([SHORT_ANSWER])))
        self.assertEqual(extract_response_text(raw), SHORT_ANSWER)

    def test_bard_error_raises(self):
        with self.assertRaises(RuntimeError):
            extract_response_text(")]}'\n\n[3,[\"er\",null,\"BardErrorInfo [1037]\"]]\n")

    def test_garbage_returns_empty(self):
        self.assertEqual(extract_response_text(")]}'\n\n12\n[\"e\",4]\n"), "")


class SingleFileParityTests(unittest.TestCase):
    """The single-file build must behave identically."""

    @classmethod
    def setUpClass(cls):
        cls.single = load_single_file()

    def test_thought_frame_longer_than_answer_is_ignored(self):
        raw = make_raw(
            make_line(noise_inner([ANATOMY_NOISE])),
            make_line(answer_inner([SHORT_ANSWER])),
        )
        self.assertEqual(self.single.extract_response_text(raw), SHORT_ANSWER)

    def test_multi_segment_answer_is_joined(self):
        raw = make_raw(make_line(answer_inner(["a ", "b", "c"])))
        self.assertEqual(self.single.extract_response_text(raw), "a bc")

    def test_longer_alternative_draft_does_not_override_primary(self):
        draft = "This alternative draft is much longer than the primary answer " * 5
        raw = make_raw(make_line(answer_inner([SHORT_ANSWER], drafts=[(draft,)])))
        self.assertEqual(self.single.extract_response_text(raw), SHORT_ANSWER)


# ─── request ids ─────────────────────────────────────────────────────────────

class ReqidTests(unittest.TestCase):
    def test_reqids_unique_and_increasing_even_within_same_second(self):
        ids = [_next_reqid() for _ in range(50)]
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(ids, sorted(ids))


# ─── streaming ───────────────────────────────────────────────────────────────

class FakeStreamResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_text(self):
        yield from self._chunks


class FakeStreamContext:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return FakeStreamResponse(self._chunks)

    def __exit__(self, *args):
        return False


class FakeHttpClient:
    def __init__(self, chunk_iterables):
        self._chunk_iterables = chunk_iterables
        self.calls = 0

    def stream(self, method, url, content=None, headers=None):
        chunks = self._chunk_iterables[min(self.calls, len(self._chunk_iterables) - 1)]
        self.calls += 1
        return FakeStreamContext(chunks)


@unittest.skipUnless(HAS_HTTPX, "httpx not installed")
class GenerateStreamTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(CONFIG)
        CONFIG["retry_attempts"] = 1
        CONFIG["retry_delay_sec"] = 0
        CONFIG["log_requests"] = False

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def _run(self, client):
        with mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=client):
            return "".join(generate_stream("prompt", 1, 4))

    def test_stream_ignores_thought_frames_and_emits_answer_deltas(self):
        chunks = [
            ")]}'\n\n",
            make_line(noise_inner([ANATOMY_NOISE])) + "\n",
            make_line(answer_inner(["Hello"])) + "\n",
            make_line(answer_inner(["Hello, wor"])) + "\n",
            make_line(answer_inner(["Hello, world!"])) + "\n",
        ]
        self.assertEqual(self._run(FakeHttpClient([chunks])), "Hello, world!")

    def test_stream_tolerates_answer_rewrite_without_splicing(self):
        chunks = [
            make_line(answer_inner(["Original answer, already emitted."])) + "\n",
            make_line(answer_inner(["A completely different replacement text."])) + "\n",
        ]
        out = self._run(FakeHttpClient([chunks]))
        self.assertEqual(out, "Original answer, already emitted.")

    def test_stream_falls_back_when_no_answer_frame_ids(self):
        chunks = [make_line(noise_inner([SHORT_ANSWER])) + "\n"]
        self.assertEqual(self._run(FakeHttpClient([chunks])), SHORT_ANSWER)

    def test_stream_retries_when_first_attempt_is_empty(self):
        CONFIG["retry_attempts"] = 2
        empty = ["[\"e\",4]\n"]
        good = [make_line(answer_inner(["Recovered on retry"])) + "\n"]
        client = FakeHttpClient([empty, good])
        with mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=client):
            out = "".join(generate_stream("prompt", 1, 4))
        self.assertEqual(out, "Recovered on retry")
        self.assertEqual(client.calls, 2)

    def test_stream_raises_after_all_attempts_empty(self):
        with self.assertRaises(RuntimeError):
            self._run(FakeHttpClient([["[\"e\",4]\n"]]))


if __name__ == "__main__":
    unittest.main()
