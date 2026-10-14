"""OpenAI compatibility: tool-call parsing, prompt building, retries, wire format.

Fixtures marked "observed" are real Gemini Web outputs captured while
running coding agents through the proxy.
"""
import http.client
import json
import threading
import unittest
from unittest import mock

from gemini_web2api.completion import (CompletionRequest, UpstreamFailure, classify_canned, complete,
                                       complete_stream)
from gemini_web2api.config import CONFIG
from gemini_web2api.server import GeminiHandler, ThreadedServer
from gemini_web2api.tools import (messages_to_prompt, parse_tool_calls, parse_tool_calls_detailed,
                                  responses_input_to_messages, responses_tools_to_chat, tool_names)


def _tool(name, **props):
    return {"type": "function", "function": {
        "name": name, "description": f"{name} tool",
        "parameters": {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}}}}


TOOLS = [_tool("read_file", path="string"), _tool("write_file", path="string", content="string"),
         _tool("get_weather", city="string")]
NAMES = tool_names(TOOLS)
CANNED = "I'm having a hard time fulfilling your request. Can I help you with something else instead?"


def _call(name, args):
    return "```tool_call\n" + json.dumps({"name": name, "arguments": args}) + "\n```"


class _ConfigMixin:
    def setUp(self):
        self.original_config = dict(CONFIG)
        CONFIG["api_keys"] = []
        CONFIG["log_requests"] = False
        CONFIG["retry_delay_sec"] = 0
        CONFIG["retry_attempts"] = 3

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)


# ─── Parser ────────────────────────────────────────────────────────────────────

class ParseToolCallsTests(unittest.TestCase):
    def test_canonical_fence(self):
        text = 'Thinking...\n```tool_call\n{"name": "read", "arguments": {"filePath": "a.txt"}}\n```\ndone'
        clean, calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "read")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"filePath": "a.txt"})
        self.assertEqual(calls[0]["type"], "function")
        self.assertTrue(calls[0]["id"].startswith("call_"))
        self.assertEqual(clean, "Thinking...\n\ndone")

    def test_function_call_fence_variant(self):
        _, calls = parse_tool_calls('```function_call\n{"name": "bash", "arguments": {"command": "ls"}}\n```')
        self.assertEqual([c["function"]["name"] for c in calls], ["bash"])

    def test_json_fence_with_call_shape(self):
        clean, calls = parse_tool_calls('```json\n{"name": "read", "arguments": {"filePath": "a.txt"}}\n```')
        self.assertEqual(len(calls), 1)
        self.assertEqual(clean, "")

    def test_json_fence_without_call_shape_left_intact(self):
        for text in ('Here is JSON:\n```json\n{"foo": 1}\n```',
                     'A person:\n```json\n{"name": "John", "age": 30}\n```'):
            clean, calls = parse_tool_calls(text, NAMES)
            self.assertEqual(calls, [])
            self.assertEqual(clean, text)

    def test_bracket_shorthand(self):
        clean, calls = parse_tool_calls('Sure [tool_call: read {"filePath": "pyproject.toml"}] ok')
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"filePath": "pyproject.toml"})
        self.assertEqual(clean, "Sure  ok")

    def test_bracket_shorthand_trailing_brace(self):
        # Observed: the model appends an extra closing brace.
        _, calls = parse_tool_calls('[tool_call: read { "filePath": "pyproject.toml" }}]')
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"filePath": "pyproject.toml"})

    def test_raw_json_object(self):
        clean, calls = parse_tool_calls('{"name": "bash", "args": {"command": "pwd"}}')
        self.assertEqual(calls[0]["function"]["name"], "bash")
        self.assertEqual(clean, "")

    def test_plain_prose_returns_no_calls(self):
        self.assertEqual(parse_tool_calls("The version is 1.1.0."), ("The version is 1.1.0.", []))

    def test_valid_names_filters_hallucinated_tools(self):
        text = _call("read_file", {}) + "\n" + _call("nuke_drive", {})
        result = parse_tool_calls_detailed(text, NAMES)
        self.assertEqual([c["function"]["name"] for c in result.tool_calls], ["read_file"])
        self.assertEqual(result.rejected, [{"name": "nuke_drive", "reason": "tool is not declared"}])
        self.assertNotIn("nuke_drive", result.text)

    def test_google_internal_tool_rejected(self):
        # Observed: "2+2?" with client tools produced this instead of an answer.
        text = '```tool_call\n{"name": "google:ds_python_interpreter", "arguments": {"code": "print(2 + 2)"}}\n```'
        result = parse_tool_calls_detailed(text, NAMES)
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.rejected[0]["name"], "google:ds_python_interpreter")
        self.assertEqual(result.text, "")

    def test_arguments_containing_code_fences(self):
        content = "# Demo\n\n```bash\npytest -q\n```\n"
        _, calls = parse_tool_calls(_call("write_file", {"path": "README.md", "content": content}), NAMES)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["content"], content)

    def test_raw_newlines_and_fences_inside_strings(self):
        # Observed: Gemini emits literal newlines inside JSON strings.
        text = ('```tool_call\n{"name": "write_file", "arguments": {"path": "R.md", '
                '"content": "a\n```bash\nls\n```\nb"}}\n```')
        _, calls = parse_tool_calls(text, NAMES)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["content"], "a\n```bash\nls\n```\nb")

    def test_closing_fence_on_same_line(self):
        _, calls = parse_tool_calls('```tool_call\n{"name": "read_file", "arguments": {"path": "x"}}```', NAMES)
        self.assertEqual(len(calls), 1)

    def test_unclosed_fence_at_end(self):
        _, calls = parse_tool_calls('```tool_call\n{"name": "read_file", "arguments": {"path": "x"}}', NAMES)
        self.assertEqual(len(calls), 1)

    def test_invalid_arguments_rejected_not_emptied(self):
        result = parse_tool_calls_detailed('```tool_call\n{"name": "write_file", "arguments": "{bad"}\n```', NAMES)
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.rejected, [{"name": "write_file", "reason": "arguments are not valid JSON"}])

    def test_flattened_arguments(self):
        # Observed: arguments placed next to "name" instead of under "arguments".
        text = '```tool_call\n{"name": "write_file", "path": "a", "content": "done"}\n```'
        _, calls = parse_tool_calls(text, NAMES)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a", "content": "done"})

    def test_string_arguments_decoded(self):
        _, calls = parse_tool_calls('```tool_call\n{"name": "read_file", "arguments": "{\\"path\\": \\"a\\"}"}\n```',
                                    NAMES)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a"})

    def test_namespaced_name_resolves_to_declared_tool(self):
        _, calls = parse_tool_calls(_call("default_api.read_file", {"path": "a"}), NAMES)
        self.assertEqual(calls[0]["function"]["name"], "read_file")

    def test_multiple_calls_in_one_fence(self):
        text = "```tool_call\n" + json.dumps([{"name": "get_weather", "arguments": {"city": c}}
                                              for c in ("Paris", "Tokyo")]) + "\n```"
        _, calls = parse_tool_calls(text, NAMES)
        self.assertEqual([json.loads(c["function"]["arguments"])["city"] for c in calls], ["Paris", "Tokyo"])

    def test_parallel_fences_get_unique_ids(self):
        text = _call("read_file", {"path": "a"}) + "\n" + _call("read_file", {"path": "b"})
        _, calls = parse_tool_calls(text, NAMES)
        self.assertEqual(len({c["id"] for c in calls}), 2)

    def test_tool_names_helper(self):
        tools = [{"type": "function", "function": {"name": "read"}},
                 {"type": "function", "function": {"name": "bash"}},
                 {"name": "custom-thing"},
                 {"type": "web_search"}]
        self.assertEqual(tool_names(tools), {"read", "bash", "custom-thing"})
        self.assertEqual(tool_names(None), set())


# ─── Prompt building ───────────────────────────────────────────────────────────

class PromptBuilderTests(unittest.TestCase):
    def test_lone_user_message_is_unlabelled(self):
        self.assertEqual(messages_to_prompt([{"role": "user", "content": "hi"}])[0], "hi")

    def test_tool_results_are_matched_by_tool_call_id(self):
        prompt, _ = messages_to_prompt([
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "print(1)"},
        ], TOOLS)
        self.assertIn("[Tool result for read_file (id: c1)]:\nprint(1)", prompt)
        self.assertIn('[User]: fix it', prompt)
        self.assertIn('```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```', prompt)

    def test_history_with_invalid_arguments_stays_valid_json(self):
        prompt, _ = messages_to_prompt([
            {"role": "user", "content": "x"},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "not-json"}}]},
        ])
        block = prompt.split("```tool_call\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(block), {"name": "read_file", "arguments": "not-json"})

    def test_null_and_list_content_and_developer_role(self):
        prompt, _ = messages_to_prompt([
            {"role": "developer", "content": "Be terse."},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "tool_calls": [{"id": "c2", "type": "function",
                                                  "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c2", "content": None},
            {"role": "tool", "tool_call_id": "c3", "content": [{"type": "text", "text": "list content"}]},
        ])
        self.assertIn("[System instruction]: Be terse.", prompt)
        self.assertIn("[User]: hello", prompt)
        self.assertNotIn("None", prompt)
        self.assertIn("[Tool result for unknown tool (id: c3)]:\nlist content", prompt)

    def test_tool_prompt_rules_and_trailing_reminder(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}], TOOLS,
                                       {"type": "function", "function": {"name": "get_weather"}})
        self.assertTrue(prompt.startswith("# Tools"))
        self.assertIn("Do not use your own built-in tools", prompt)
        self.assertIn("Never write, predict or assume tool results", prompt)
        tail = prompt.rsplit("\n\n", 1)[1]
        self.assertTrue(tail.startswith("[Reminder]: "))
        self.assertIn('You MUST call the tool "get_weather"', tail)

    def test_tool_choice_none_omits_tools(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}], TOOLS, "none")
        self.assertEqual(prompt, "hi")

    def test_response_format_instruction(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "person"}], None, None,
                                       {"type": "json_object"})
        self.assertIn("[Reminder]: Respond with a single valid JSON object only.", prompt)

    def test_large_tool_lists_are_slimmed(self):
        big = [_tool(f"big_t{i}", **{f"p{j}": "string" for j in range(30)}) for i in range(60)]
        for t in big:
            for p in t["function"]["parameters"]["properties"].values():
                p["description"] = "x" * 40
        core = [_tool("read", filePath="string"), _tool("bash", command="string")]
        core[1]["function"]["description"] = "Run a command. " * 600  # very long, like opencode's bash
        mcp = [_tool(f"mcp_srv_{i}", **{f"p{j}": "string" for j in range(30)}) for i in range(150)]
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}], big + core + mcp)
        self.assertLess(len(prompt), 70000)
        lines = {json.loads(l)["name"]: json.loads(l) for l in prompt.split("Available tools:\n", 1)[1]
                 .split("\n\n", 1)[0].splitlines()}
        self.assertEqual(len(lines), 212)  # every tool is still listed
        self.assertIn("parameters", lines["read"])  # core tools keep full schemas
        self.assertIn("parameters", lines["bash"])
        self.assertIn("parameter_names", lines["mcp_srv_0"])  # big MCP groups are slimmed


class ResponsesInputTests(unittest.TestCase):
    def test_top_level_function_call_items_are_kept(self):
        messages = responses_input_to_messages({"instructions": "sys", "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Weather in Rome?"}]},
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "call_id": "fc_1", "name": "get_weather", "arguments": '{"city": "Rome"}'},
            {"type": "function_call", "call_id": "fc_2", "name": "get_weather", "arguments": '{"city": "Oslo"}'},
            {"type": "function_call_output", "call_id": "fc_1", "output": "21C"},
        ]})
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "tool"])
        self.assertEqual([c["id"] for c in messages[2]["tool_calls"]], ["fc_1", "fc_2"])
        prompt, _ = messages_to_prompt(messages, TOOLS)
        self.assertIn("[Tool result for get_weather (id: fc_1)]:\n21C", prompt)
        self.assertIn('"city": "Oslo"', prompt)

    def test_builtin_tools_are_dropped(self):
        tools = responses_tools_to_chat([{"type": "web_search"},
                                         {"type": "function", "name": "f", "parameters": {"type": "object"}}])
        self.assertEqual([t["function"]["name"] for t in tools], ["f"])


# ─── Retries and completion logic ──────────────────────────────────────────────

class _FakeGen:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def stream(self, chunk=7):
        def fn(prompt):
            text = self(prompt)
            if isinstance(text, Exception):
                raise text
            return iter([text[i:i + chunk] for i in range(0, len(text), chunk)])
        return fn


class CannedReplyTests(unittest.TestCase):
    def test_observed_canned_replies(self):
        for text in (CANNED, "Sorry, something went wrong. Please try your request again.",
                     "I seem to be encountering an error. Can I try something else for you?",
                     "I encountered an error doing what you asked. Could you try again?",
                     "I\u2019m having a hard time fulfilling your request."):
            self.assertEqual(classify_canned(text), "failure", text)
        self.assertEqual(classify_canned("I'm just a language model, so I can't help you with that."), "refusal")
        # Observed with opencode; long, but still a failure.
        long_fail = ("I cannot execute tools directly because the tool call limit has been reached. However, "
                     "to fix util.py you will typically want to inspect it. " + "More advice. " * 40)
        self.assertEqual(classify_canned(long_fail), "refusal")
        for text in ("I am unable to use the file tools because my tool calls are exhausted for this session.",
                     "I am unable to execute tool calls as the maximum call limit has been reached, but here",
                     "I cannot make any further tool calls since the maximum limit has been reached.",
                     "I am unable to search the project files because the search tools are currently unavailable.",
                     "The search tools are currently unavailable in this environment, so I am unable to search.",
                     "I am unable to access the search or file tools to inspect the project contents directly.",
                     "I am unable to run search tools to locate files mentioning the word due to system limits.",
                     "I am unable to search the project files for the word without tool access."):
            self.assertEqual(classify_canned(text + " " + "x" * 400), "refusal", text)
        self.assertEqual(classify_canned("I cannot execute additional tool calls because the maximum limit "
                                         "has been reached. However, here is the code: " + "x" * 400), "refusal")

    def test_real_answers_are_not_canned(self):
        self.assertIsNone(classify_canned("2 + 2 = 4"))
        self.assertIsNone(classify_canned("The weather tool is unavailable in your region, so I used search. " * 5))
        self.assertIsNone(classify_canned("Sorry, something went wrong in your code: " + "x" * 400))


class CompleteTests(_ConfigMixin, unittest.TestCase):
    def test_canned_failure_is_retried(self):
        gen = _FakeGen(CANNED, _call("read_file", {"path": "a"}))
        result = complete(gen, CompletionRequest("p", TOOLS))
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(len(gen.prompts), 2)

    def test_persistent_canned_failure_raises(self):
        with self.assertRaises(UpstreamFailure):
            complete(_FakeGen(CANNED), CompletionRequest("p"))

    def test_empty_reply_is_retried(self):
        self.assertEqual(complete(_FakeGen("", "ok"), CompletionRequest("p")).text, "ok")

    def test_persistent_refusal_is_returned(self):
        text = "I'm just a language model, so I can't help you with that."
        self.assertEqual(complete(_FakeGen(text), CompletionRequest("p")).text, text)

    def test_undeclared_tool_triggers_corrective_retry(self):
        gen = _FakeGen(_call("google:ds_python_interpreter", {"code": "1"}), "2 + 2 = 4")
        result = complete(gen, CompletionRequest("p", TOOLS))
        self.assertEqual((result.text, result.tool_calls), ("2 + 2 = 4", []))
        self.assertIn("[Correction]:", gen.prompts[1])
        self.assertIn("google:ds_python_interpreter: tool is not declared", gen.prompts[1])

    def test_tutorial_reply_gets_one_nudge(self):
        # Observed with opencode: code shown instead of an edit tool call.
        tutorial = "Here are the changes:\n\n### 1. Update `util.py`\n```python\ndef subtract(a, b):\n    return a - b\n```"
        gen = _FakeGen(tutorial, _call("write_file", {"path": "util.py", "content": "x"}))
        self.assertEqual(complete(gen, CompletionRequest("p", TOOLS)).finish_reason, "tool_calls")
        self.assertIn("described the changes instead of making them", gen.prompts[1])
        # Without tools, or for ordinary answers, no retry happens.
        gen = _FakeGen(tutorial)
        complete(gen, CompletionRequest("p"))
        gen2 = _FakeGen("The answer is 391.")
        complete(gen2, CompletionRequest("p", TOOLS))
        self.assertEqual((len(gen.prompts), len(gen2.prompts)), (1, 1))

    def test_forced_tool_choice_is_enforced(self):
        gen = _FakeGen("Hello! How can I help?", _call("get_weather", {"city": "Paris"}))
        req = CompletionRequest("p", TOOLS, {"type": "function", "function": {"name": "get_weather"}})
        result = complete(gen, req)
        self.assertEqual(result.tool_calls[0]["function"]["name"], "get_weather")
        self.assertIn('must call the tool "get_weather"', gen.prompts[1])

    def test_required_tool_choice_gives_up_gracefully(self):
        result = complete(_FakeGen("no tools for you"), CompletionRequest("p", TOOLS, "required"))
        self.assertEqual((result.text, result.finish_reason), ("no tools for you", "stop"))

    def test_json_mode_extracts_json(self):
        result = complete(_FakeGen('Sure:\n```json\n{"name": "Sam", "age": 3}\n```'),
                          CompletionRequest("p", response_format={"type": "json_object"}))
        self.assertEqual(json.loads(result.text), {"name": "Sam", "age": 3})

    def test_json_mode_retries_non_json(self):
        gen = _FakeGen("* Name: Sam", '{"name": "Sam"}')
        result = complete(gen, CompletionRequest("p", response_format={"type": "json_object"}))
        self.assertEqual(json.loads(result.text), {"name": "Sam"})

    def test_stop_sequences(self):
        self.assertEqual(complete(_FakeGen("one\ntwo END three"), CompletionRequest("p", stop="END")).text,
                         "one\ntwo ")


class CompleteStreamTests(_ConfigMixin, unittest.TestCase):
    def _run(self, gen, req, chunk=7):
        return list(complete_stream(gen.stream(chunk), req))

    def test_plain_text_streams_through(self):
        events = self._run(_FakeGen("hello world, streaming"), CompletionRequest("p"), chunk=5)
        self.assertEqual([e for e in events if e[0] == "text"][0], ("text", "hello"))
        self.assertEqual("".join(v for k, v in events if k == "text"), "hello world, streaming")
        self.assertEqual(events[-1], ("finish", "stop"))

    def test_text_before_tool_call_is_streamed_and_call_extracted(self):
        prose = "I'll read the file first so I can see what needs to change. " * 4
        events = self._run(_FakeGen(prose + _call("read_file", {"path": "a.py"})), CompletionRequest("p", TOOLS))
        text = "".join(v for k, v in events if k == "text")
        self.assertEqual(text, prose)
        self.assertNotIn("```", text)
        calls = [v for k, v in events if k == "tool_calls"][0]
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a.py"})
        self.assertEqual(events[-1], ("finish", "tool_calls"))
        self.assertGreater(sum(1 for k, _ in events if k == "text"), 1)

    def test_text_after_tool_calls_is_dropped(self):
        # Observed: echoed [Reminder] lines and invented "[Tool result: ...]" after the call.
        reply = ("Let me look.\n" + _call("read_file", {"path": "a"}) +
                 '\n<div>[Tool result: {"files": ["a"]}]\n[Reminder]: Continue as the assistant.')
        result = complete(_FakeGen(reply), CompletionRequest("p", TOOLS))
        self.assertEqual((result.text, len(result.tool_calls)), ("Let me look.", 1))
        events = self._run(_FakeGen("x" * 200 + reply), CompletionRequest("p", TOOLS))
        self.assertEqual("".join(v for k, v in events if k == "text"), "x" * 200 + "Let me look.\n")

    def test_split_marker_is_not_leaked(self):
        prose = "x" * 200
        events = self._run(_FakeGen(prose + _call("read_file", {"path": "a"})), CompletionRequest("p", TOOLS),
                           chunk=3)
        self.assertEqual("".join(v for k, v in events if k == "text"), prose)

    def test_canned_failure_in_tool_stream_is_retried_without_leaking(self):
        gen = _FakeGen(CANNED, _call("read_file", {"path": "a"}))
        events = self._run(gen, CompletionRequest("p", TOOLS))
        self.assertFalse([v for k, v in events if k == "text"])
        self.assertEqual(events[-1], ("finish", "tool_calls"))
        self.assertEqual(len(gen.prompts), 2)

    def test_long_tool_limit_refusal_is_retried_in_stream(self):
        refusal = "I cannot execute tools directly because the tool call limit has been reached. However, " + "advice " * 80
        gen = _FakeGen(refusal, _call("read_file", {"path": "a"}))
        events = self._run(gen, CompletionRequest("p", TOOLS))
        self.assertFalse([v for k, v in events if k == "text"])
        self.assertEqual(events[-1], ("finish", "tool_calls"))

    def test_tutorial_reply_is_held_and_retried_in_stream(self):
        tutorial = ("To complete your request, here are the changes you can make to the project files. " * 3 +
                    "\n```python\ndef subtract(a, b):\n    return a - b\n```")
        gen = _FakeGen(tutorial, _call("write_file", {"path": "util.py", "content": "x"}))
        events = self._run(gen, CompletionRequest("p", TOOLS))
        self.assertFalse([v for k, v in events if k == "text"])
        self.assertEqual(events[-1], ("finish", "tool_calls"))

    def test_error_before_output_is_retried(self):
        gen = _FakeGen(RuntimeError("boom"), "fine")
        events = self._run(gen, CompletionRequest("p"))
        self.assertEqual(events, [("text", "fine"), ("finish", "stop")])

    def test_echoed_reminder_is_stripped_from_stream(self):
        # Observed: the reply started by repeating the [Reminder] line.
        echo = "[Reminder]: Continue as the assistant. Use the tool results above.\n"
        events = self._run(_FakeGen(echo + _call("read_file", {"path": "a"})), CompletionRequest("p", TOOLS), chunk=9)
        self.assertFalse([v for k, v in events if k == "text"])
        self.assertEqual(events[-1], ("finish", "tool_calls"))

    def test_echoed_assistant_label_is_stripped(self):
        events = self._run(_FakeGen("[Assistant]: Hello there"), CompletionRequest("p"), chunk=4)
        self.assertEqual("".join(v for k, v in events if k == "text"), "Hello there")
        self.assertEqual(complete(_FakeGen("[Assistant]: Hi"), CompletionRequest("p")).text, "Hi")

    def test_stop_sequence_in_stream(self):
        events = self._run(_FakeGen("alpha beta STOP gamma"), CompletionRequest("p", stop=["STOP"]), chunk=4)
        self.assertEqual("".join(v for k, v in events if k == "text"), "alpha beta ")


# ─── HTTP wire format ──────────────────────────────────────────────────────────

def _stream_of(*texts):
    replies = list(texts)

    def fake(*_args, **_kwargs):
        text = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(text, Exception):
            raise text
        return iter([text[i:i + 50] for i in range(0, len(text), 50)])
    return fake


def _no_network(*_args, **_kwargs):
    raise AssertionError("test attempted a real Gemini request")


class HttpTests(_ConfigMixin, unittest.TestCase):
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
        super().setUp()
        for name in ("generate", "generate_stream"):
            patcher = mock.patch(f"gemini_web2api.server.{name}", side_effect=_no_network)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def post(self, path, payload, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", path, body=raw if raw is not None else json.dumps(payload),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read().decode()
        connection.close()
        return response.status, body

    @staticmethod
    def chunks(body):
        return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]

    def chat(self, **extra):
        payload = {"model": "gemini-3.6-flash", "messages": [{"role": "user", "content": "go"}]}
        payload.update(extra)
        return self.post("/v1/chat/completions", payload)

    def test_streamed_tool_calls_carry_index_and_reassemble(self):
        args = {"path": "pyproject.toml", "content": "x" * 300}
        self.generate_stream.side_effect = _stream_of(_call("write_file", args))
        status, body = self.chat(stream=True, tools=TOOLS)
        self.assertEqual(status, 200)
        chunks = self.chunks(body)
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(len({c["created"] for c in chunks}), 1)
        tool_deltas = [c["choices"][0]["delta"]["tool_calls"][0] for c in chunks
                       if "tool_calls" in c["choices"][0]["delta"]]
        self.assertTrue(all("index" in d for d in tool_deltas))
        self.assertEqual(tool_deltas[0]["function"], {"name": "write_file", "arguments": ""})
        self.assertTrue(tool_deltas[0]["id"].startswith("call_"))
        self.assertEqual(json.loads("".join(d["function"]["arguments"] for d in tool_deltas)), args)
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    def test_streamed_unknown_tool_is_never_emitted(self):
        self.generate_stream.side_effect = _stream_of(_call("evil", {}))
        status, body = self.chat(stream=True, tools=TOOLS)
        self.assertEqual(status, 200)
        self.assertEqual(self.chunks(body)[-1]["choices"][0]["finish_reason"], "stop")
        self.assertNotIn('"tool_calls"', body)
        self.assertEqual(self.generate_stream.call_count, 3)

    def test_stream_include_usage(self):
        self.generate_stream.side_effect = _stream_of("hello there")
        _, body = self.chat(stream=True, stream_options={"include_usage": True})
        chunks = self.chunks(body)
        self.assertEqual(chunks[-1]["choices"], [])
        self.assertGreater(chunks[-1]["usage"]["completion_tokens"], 0)
        self.assertEqual(chunks[-2]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(all("usage" in c for c in chunks))

    def test_stream_error_after_output_sends_error_and_done(self):
        def broken(*_a, **_k):
            yield "partial answer "
            raise RuntimeError("connection dropped")
        self.generate_stream.side_effect = broken
        status, body = self.chat(stream=True)
        self.assertEqual(status, 200)
        last = self.chunks(body)[-1]
        self.assertEqual(last["error"]["type"], "upstream_error")
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    def test_stream_failure_before_output_is_http_502(self):
        self.generate_stream.side_effect = _stream_of(CANNED)
        status, body = self.chat(stream=True, tools=TOOLS)
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["type"], "upstream_error")

    def test_non_stream_tool_call_message_shape(self):
        self.generate.side_effect = None
        self.generate.return_value = _call("read_file", {"path": "a"})
        _, body = self.chat(tools=TOOLS)
        data = json.loads(body)
        choice = data["choices"][0]
        self.assertIsNone(choice["message"]["content"])
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertGreater(data["usage"]["completion_tokens"], 0)

    def test_non_stream_canned_failure_is_502_not_content(self):
        self.generate.side_effect = None
        self.generate.return_value = CANNED
        status, body = self.chat()
        self.assertEqual(status, 502)
        self.assertNotIn("choices", json.loads(body))

    def test_error_shapes(self):
        cases = [
            (self.post("/v1/chat/completions", None, raw="{oops"), 400),
            (self.post("/v1/chat/completions", "a string"), 400),
            (self.post("/v1/chat/completions", {"model": "gemini-3.6-flash"}), 400),
            (self.post("/v1/nope", {}), 404),
        ]
        for (status, body), expected in cases:
            self.assertEqual(status, expected)
            err = json.loads(body)["error"]
            self.assertEqual(set(err), {"message", "type", "param", "code"})

    def test_responses_stream_text_then_function_call(self):
        self.generate_stream.side_effect = _stream_of(
            "Checking the weather for you now, one moment please. " * 4 + _call("get_weather", {"city": "Rome"}))
        status, body = self.post("/v1/responses", {
            "model": "gemini-3.6-flash", "input": "Weather in Rome?", "stream": True,
            "tools": [{"type": "function", "name": "get_weather", "parameters": {"type": "object"}}]})
        self.assertEqual(status, 200)
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        types = [e["type"] for e in events]
        self.assertEqual(types[0:3], ["response.created", "response.in_progress", "response.output_item.added"])
        self.assertLess(types.index("response.output_text.delta"),
                        types.index("response.function_call_arguments.delta"))
        final = events[-1]["response"]
        self.assertEqual([o["type"] for o in final["output"]], ["message", "function_call"])
        self.assertTrue(final["output"][1]["call_id"].startswith("call_"))
        self.assertEqual([e["sequence_number"] for e in events], list(range(1, len(events) + 1)))

    def test_responses_history_reaches_prompt(self):
        self.generate.side_effect = None
        self.generate.return_value = "It is 21C in Rome."
        status, body = self.post("/v1/responses", {
            "model": "gemini-3.6-flash",
            "tools": [{"type": "function", "name": "get_weather", "parameters": {"type": "object"}}],
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "Weather in Rome?"}]},
                {"type": "function_call", "call_id": "fc_1", "name": "get_weather", "arguments": '{"city": "Rome"}'},
                {"type": "function_call_output", "call_id": "fc_1", "output": "21C"},
            ]})
        self.assertEqual(status, 200)
        prompt = self.generate.call_args.args[0]
        self.assertIn('{"name": "get_weather", "arguments": {"city": "Rome"}}', prompt)
        self.assertIn("[Tool result for get_weather (id: fc_1)]:\n21C", prompt)
        self.assertEqual(json.loads(body)["output"][0]["content"][0]["text"], "It is 21C in Rome.")


class SingleFileEntrypointTests(_ConfigMixin, unittest.TestCase):
    """gemini_web2api.py must expose the same endpoint behaviour as the package."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gemini_web2api.py")
        spec = importlib.util.spec_from_file_location("gemini_web2api_single", path)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_shares_package_config(self):
        self.assertIs(self.mod.CONFIG, CONFIG)

    def test_tool_call_stream_through_single_file_handler(self):
        server = ThreadedServer(("127.0.0.1", 0), self.mod.GeminiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        with mock.patch.object(self.mod, "gemini_stream_generate_iter",
                               side_effect=lambda *a, **k: iter([_call("read_file", {"path": "a"})])):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("POST", "/v1/chat/completions", body=json.dumps({
                "model": "gemini-3.6-flash", "stream": True, "tools": TOOLS,
                "messages": [{"role": "user", "content": "read a"}]}),
                headers={"Content-Type": "application/json"})
            body = conn.getresponse().read().decode()
            conn.close()
        chunks = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertIn('"name": "read_file"', body)

    def test_unknown_model_is_rejected_by_single_file(self):
        handler = self.mod.GeminiHandler.__new__(self.mod.GeminiHandler)
        self.assertEqual(handler._resolve("gpt-4o")[3], "Unknown model: gpt-4o")


if __name__ == "__main__":
    unittest.main()
