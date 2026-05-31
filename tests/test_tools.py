import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gemini_web2api.tools import messages_to_prompt, parse_tool_calls, tool_names_from_tools


def first_call(text, allowed=None):
    clean, calls = parse_tool_calls(text, allowed)
    assert clean == ""
    assert len(calls) == 1
    fn = calls[0]["function"]
    return fn["name"], json.loads(fn["arguments"])


class ToolCallTests(unittest.TestCase):
    def test_prompt_includes_tool_catalog_and_choice_rules(self):
        tools = [{"type": "function", "function": {"name": "run", "description": "执行", "parameters": {}}}]
        prompt, images = messages_to_prompt(
            [{"role": "user", "content": "需要执行"}],
            tools,
            tool_choice="required",
            parallel_tool_calls=False,
        )
        self.assertEqual(images, [])
        self.assertIn("Available tools", prompt)
        self.assertIn("You must call at least one tool", prompt)
        self.assertIn("Call only one tool", prompt)

    def test_parse_legacy_fenced_tool_call(self):
        name, args = first_call('```tool_call\n{"name":"get_weather","arguments":{"city":"北京"}}\n```')
        self.assertEqual(name, "get_weather")
        self.assertEqual(args, {"city": "北京"})

    def test_parse_json_wrapper_and_fenced_json(self):
        name, args = first_call('{"tool_calls":[{"name":"search","input":{"q":"docs"}}]}')
        self.assertEqual(name, "search")
        self.assertEqual(args, {"q": "docs"})

        name, args = first_call('```json\n{"tool_calls":[{"name":"search","input":{"q":"api"}}]}\n```')
        self.assertEqual(name, "search")
        self.assertEqual(args, {"q": "api"})

    def test_parse_xml_and_dsml_tool_calls(self):
        name, args = first_call('<tool_calls><invoke name="read"><parameter name="path">README.md</parameter></invoke></tool_calls>')
        self.assertEqual(name, "read")
        self.assertEqual(args, {"path": "README.md"})

        name, args = first_call('<|DSML|tool_calls><|DSML|invoke name="read"><|DSML|parameter name="path"><![CDATA[src/main.py]]></|DSML|parameter></|DSML|invoke></|DSML|tool_calls>')
        self.assertEqual(name, "read")
        self.assertEqual(args, {"path": "src/main.py"})

    def test_tool_name_filtering_and_tool_choice(self):
        tools = [
            {"type": "function", "function": {"name": "allowed", "parameters": {}}},
            {"type": "function", "function": {"name": "blocked", "parameters": {}}},
        ]
        self.assertEqual(
            tool_names_from_tools(tools, {"type": "function", "function": {"name": "allowed"}}),
            ["allowed"],
        )
        clean, calls = parse_tool_calls('{"tool_calls":[{"name":"blocked","input":{}}]}', ["allowed"])
        self.assertEqual(clean, '{"tool_calls":[{"name":"blocked","input":{}}]}')
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
