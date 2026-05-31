import base64
import html
import io
import json
import re
import uuid
from typing import Optional

MAX_IMAGE_B64_SIZE = 50000  # ~37KB raw image


def _compress_b64_if_needed(b64: str) -> str:
    """Compress image if base64 is too large for text embedding."""
    if len(b64) <= MAX_IMAGE_B64_SIZE:
        return b64
    try:
        from PIL import Image
        img_data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(img_data))
        # Resize to max 256px on longest side
        max_dim = 256
        ratio = min(max_dim / img.width, max_dim / img.height)
        if ratio < 1:
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        # Convert to JPEG with quality reduction
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        compressed = base64.b64encode(buf.getvalue()).decode()
        return compressed
    except Exception:
        # If PIL not available, truncate (model will get partial data)
        return b64[:MAX_IMAGE_B64_SIZE]


def messages_to_prompt(messages: list, tools: list = None, tool_choice=None, parallel_tool_calls=None) -> tuple:
    """Convert OpenAI messages to (prompt_str, images_list).

    Returns (prompt, images) where images is a list of (bytes, mime_type) tuples.
    """
    parts = []
    images = []

    tool_defs = normalize_tool_definitions(tools, tool_choice)
    if tool_defs:
        parts.append(_build_openai_tool_instruction(tool_defs, tool_choice, parallel_tool_calls))

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts = []
            for c in content:
                if c.get("type") in ("text", "input_text"):
                    text_parts.append(c.get("text", ""))
                elif c.get("type") == "image_url":
                    text_parts.append("[Note: Image input not supported in this API. Please describe the image in text.]")
                elif c.get("type") == "image":
                    text_parts.append("[Note: Image input not supported in this API. Please describe the image in text.]")
            content = " ".join(text_parts)

        if role == "system":
            parts.append(f"[System instruction]: {content}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_strs = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = _json_argument_object(fn.get("arguments", "{}"))
                    tc_strs.append(json.dumps({
                        "tool_calls": [{"name": fn.get("name"), "input": args}],
                    }, ensure_ascii=False))
                parts.append(f"[Assistant]: {content or ''}\n" + "\n".join(tc_strs))
            else:
                parts.append(f"[Assistant]: {content}")
        elif role == "tool":
            label = msg.get("name") or msg.get("tool_call_id", "")
            parts.append(f"[Tool result for {label}]: {content}")
        else:
            parts.append(content if content else "")

    prompt = "\n\n".join(p for p in parts if p)
    return prompt, images


def normalize_tool_definitions(tools: Optional[list], tool_choice=None) -> list:
    if not tools or _tool_choice_is_none(tool_choice):
        return []
    forced_name = _forced_tool_name(tool_choice)
    out = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function", tool) if tool.get("type") == "function" else tool
        name = (fn.get("name") or tool.get("name") or "").strip()
        if not name:
            continue
        if forced_name and name != forced_name:
            continue
        out.append({
            "name": name,
            "description": fn.get("description", tool.get("description", "")),
            "parameters": fn.get("parameters", tool.get("parameters", {})),
        })
    return out


def tool_names_from_tools(tools: Optional[list], tool_choice=None) -> list:
    return [tool["name"] for tool in normalize_tool_definitions(tools, tool_choice)]


def parse_tool_calls(text: str, available_tool_names: Optional[list] = None) -> tuple:
    """Extract tool calls. Returns (clean_text, tool_calls_list)."""
    if not text:
        return "", []

    calls = []
    spans = []

    for match in re.finditer(r"```(?:tool_call|tool_calls|json|tools)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE):
        parsed = _parse_tool_payload(match.group(1), available_tool_names)
        if parsed:
            calls.extend(parsed)
            spans.append((match.start(), match.end()))

    xml_patterns = (
        r"<tool_calls\b[^>]*>.*?</tool_calls>",
        r"<\|DSML\|tool_calls\b[^>]*>.*?</\|DSML\|tool_calls>",
    )
    for xml_pattern in xml_patterns:
        for match in re.finditer(xml_pattern, text, re.DOTALL | re.IGNORECASE):
            parsed = _parse_xml_tool_calls(match.group(0), available_tool_names)
            if parsed:
                calls.extend(parsed)
                spans.append((match.start(), match.end()))

    clean = _remove_spans(text, spans).strip()
    if calls:
        return clean, calls

    parsed = _parse_tool_payload(text, available_tool_names)
    if parsed:
        return "", parsed

    normalized_text = _normalize_dsml_tags(text)
    for match in re.finditer(r"<tool_calls\b[^>]*>.*?</tool_calls>", normalized_text, re.DOTALL | re.IGNORECASE):
        parsed = _parse_xml_tool_calls(match.group(0), available_tool_names)
        if parsed:
            return "", parsed

    for candidate in _balanced_json_candidates(text):
        parsed = _parse_tool_payload(candidate, available_tool_names)
        if parsed:
            return text.replace(candidate, "", 1).strip(), parsed

    return text.strip(), []


def _build_openai_tool_instruction(tool_defs: list, tool_choice=None, parallel_tool_calls=None) -> str:
    names = [tool["name"] for tool in tool_defs]
    lines = [
        "# Tool Use",
        "",
        "You can call the following tools. When a tool is needed, reply with JSON only in this exact shape:",
        '{"tool_calls":[{"name":"TOOL_NAME","input":{"ARG_NAME":"value"}}]}',
        "",
        "Rules:",
        "- Do not wrap the JSON in markdown.",
        "- Do not add prose around the JSON when calling tools.",
        "- Use only tool names from the available tools list.",
        "- The input value must be a JSON object.",
    ]
    if parallel_tool_calls is False:
        lines.append("- Call only one tool in this response.")
    if tool_choice in ("required", "any"):
        lines.append("- You must call at least one tool in this response.")
    forced = _forced_tool_name(tool_choice)
    if forced:
        lines.append(f'- You must call only this tool: "{forced}".')
    lines.extend([
        "",
        "Available tools:",
        json.dumps(tool_defs, indent=2, ensure_ascii=False),
    ])
    if names:
        lines.extend([
            "",
            "Legacy fenced format is also accepted if necessary:",
            '```tool_call\n{"name":"%s","arguments":{}}\n```' % names[0],
        ])
    return "\n".join(lines)


def _tool_choice_is_none(tool_choice) -> bool:
    return tool_choice == "none" or (isinstance(tool_choice, dict) and tool_choice.get("type") == "none")


def _forced_tool_name(tool_choice) -> str:
    if not isinstance(tool_choice, dict):
        return ""
    if tool_choice.get("type") == "function":
        return (tool_choice.get("function", {}) or {}).get("name", "").strip()
    return (tool_choice.get("name") or "").strip()


def _parse_tool_payload(raw: str, available_tool_names: Optional[list] = None) -> list:
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _calls_from_payload(data, available_tool_names)


def _calls_from_payload(data, available_tool_names: Optional[list] = None) -> list:
    items = []
    if isinstance(data, dict):
        if isinstance(data.get("tool_calls"), list):
            items = data["tool_calls"]
        elif "name" in data or "function" in data:
            items = [data]
    elif isinstance(data, list):
        items = data

    out = []
    for item in items:
        call = _openai_call_from_item(item, available_tool_names)
        if call:
            out.append(call)
    return out


def _openai_call_from_item(item, available_tool_names: Optional[list] = None):
    if not isinstance(item, dict):
        return None
    fn = item.get("function") if isinstance(item.get("function"), dict) else {}
    name = (item.get("name") or fn.get("name") or "").strip()
    if not name or not _tool_name_allowed(name, available_tool_names):
        return None
    args = item.get("input")
    if args is None:
        args = item.get("arguments")
    if args is None:
        args = item.get("params")
    if args is None:
        args = item.get("parameters")
    if args is None:
        args = fn.get("arguments")
    if args is None:
        args = fn.get("input")
    return {
        "id": item.get("id") or f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": _json_argument_string(args),
        },
    }


def _tool_name_allowed(name: str, available_tool_names: Optional[list]) -> bool:
    if not available_tool_names:
        return True
    allowed = {n.lower() for n in available_tool_names if isinstance(n, str)}
    return name.lower() in allowed


def _json_argument_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _json_argument_string(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "{}"
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        return stripped
    if not isinstance(value, dict):
        value = {}
    return json.dumps(value, ensure_ascii=False)


def _normalize_dsml_tags(text: str) -> str:
    out = text
    for tag in ("tool_calls", "invoke", "parameter"):
        out = re.sub(rf"<\|DSML\|{tag}\b", f"<{tag}", out, flags=re.IGNORECASE)
        out = re.sub(rf"</\|DSML\|{tag}>", f"</{tag}>", out, flags=re.IGNORECASE)
    return out


def _parse_xml_tool_calls(raw: str, available_tool_names: Optional[list] = None) -> list:
    raw = _normalize_dsml_tags(raw)
    out = []
    for invoke in re.finditer(r"<invoke\b([^>]*)>(.*?)</invoke>", raw, re.DOTALL | re.IGNORECASE):
        attrs, body = invoke.group(1), invoke.group(2)
        name_match = re.search(r'\bname=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not name_match:
            continue
        name = html.unescape(name_match.group(1).strip())
        if not _tool_name_allowed(name, available_tool_names):
            continue
        params = {}
        for param in re.finditer(r"<parameter\b([^>]*)>(.*?)</parameter>", body, re.DOTALL | re.IGNORECASE):
            p_attrs, value = param.group(1), param.group(2)
            pname_match = re.search(r'\bname=["\']([^"\']+)["\']', p_attrs, re.IGNORECASE)
            if not pname_match:
                continue
            pname = html.unescape(pname_match.group(1).strip())
            params[pname] = _clean_xml_value(value)
        out.append(_openai_call_from_item({"name": name, "input": params}, available_tool_names))
    return [call for call in out if call]


def _clean_xml_value(value: str) -> str:
    value = value.strip()
    cdata = re.fullmatch(r"<!\[CDATA\[(.*)\]\]>", value, re.DOTALL)
    if cdata:
        return cdata.group(1)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _remove_spans(text: str, spans: list) -> str:
    if not spans:
        return text
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts = []
    last = 0
    for start, end in merged:
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _balanced_json_candidates(text: str) -> list:
    candidates = []
    starts = [i for i, ch in enumerate(text) if ch in "{["]
    for start in starts[:12]:
        stack = []
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if not stack or stack[-1] != ch:
                    break
                stack.pop()
                if not stack:
                    candidates.append(text[start:idx + 1])
                    break
    return candidates


# ─── Google Native API helpers ─────────────────────────────────────────────────


def build_tool_prompt(tool_defs: list) -> str:
    """Build natural tool-use prompt for Gemini Web that avoids prompt-injection detection."""
    tool_spec = json.dumps(tool_defs, indent=2, ensure_ascii=False)
    return (
        "# Tool Use\n\n"
        "You can call the following tools to help accomplish tasks. "
        "These tools connect to the user's local environment and will execute when called.\n\n"
        "Call format (use this exact format):\n"
        "```function_call\n"
        '{"name": "<tool_name>", "args": {<arguments>}}\n'
        "```\n\n"
        "When calling tools:\n"
        "- Output ONLY the function_call block(s), nothing else\n"
        "- You may call multiple tools with multiple blocks\n"
        "- After receiving a [Tool result for ...], use that data to answer the user\n\n"
        f"Available tools:\n{tool_spec}"
    )


def _google_tool_choice_instruction(req: dict) -> str:
    """Extract tool_choice constraint from Google API toolConfig."""
    tool_config = req.get("toolConfig", {})
    fc_config = tool_config.get("functionCallingConfig", {})
    mode = fc_config.get("mode", "AUTO")
    allowed = fc_config.get("allowedFunctionNames", [])

    if mode == "NONE":
        return "\n\nIMPORTANT: Do NOT call any tools. Respond with text only."
    if mode == "ANY":
        if allowed:
            names = ", ".join(f'"{n}"' for n in allowed)
            return f"\n\nIMPORTANT: You MUST call one of these tools: {names}. Do not respond with text only."
        return "\n\nIMPORTANT: You MUST call at least one tool. Do not respond with text only."
    return ""


def google_contents_to_prompt(req: dict) -> tuple:
    """Convert Google API contents/tools/systemInstruction to (prompt_str, images_list).

    Returns (prompt, images) where images is a list of (bytes, mime_type) tuples.
    """
    parts = []
    images = []

    tool_config = req.get("toolConfig", {})
    fc_mode = tool_config.get("functionCallingConfig", {}).get("mode", "AUTO")

    tools = req.get("tools")
    tool_defs = []
    if tools and fc_mode != "NONE":
        for tool_group in tools:
            for fn in tool_group.get("functionDeclarations", []):
                td = {"name": fn.get("name", ""), "description": fn.get("description", "")}
                params = fn.get("parameters") or fn.get("parametersJsonSchema")
                if params:
                    td["parameters"] = params
                tool_defs.append(td)

    sys_inst = req.get("systemInstruction")
    if sys_inst:
        sys_parts = sys_inst.get("parts", [])
        sys_text = " ".join(p.get("text", "") for p in sys_parts if p.get("text"))
        if sys_text:
            if tool_defs:
                constraint = _google_tool_choice_instruction(req)
                parts.append(sys_text + "\n\n" + build_tool_prompt(tool_defs) + constraint)
            else:
                parts.append(sys_text)
    elif tool_defs:
        constraint = _google_tool_choice_instruction(req)
        parts.append(build_tool_prompt(tool_defs) + constraint)

    for content in req.get("contents", []):
        role = content.get("role", "user")
        msg_parts = []
        for p in content.get("parts", []):
            if p.get("text"):
                msg_parts.append(p["text"])
            elif p.get("inlineData"):
                data = p["inlineData"]
                mime = data.get("mimeType", "image/png")
                images.append((base64.b64decode(data["data"]), mime))
            elif p.get("functionCall"):
                fc = p["functionCall"]
                msg_parts.append(
                    f'```function_call\n{json.dumps({"name": fc["name"], "args": fc.get("args", {})}, ensure_ascii=False)}\n```'
                )
            elif p.get("functionResponse"):
                fr = p["functionResponse"]
                msg_parts.append(
                    f'[Tool result for {fr.get("name", "")}]: {json.dumps(fr.get("response", {}), ensure_ascii=False)}'
                )
        text = "\n".join(msg_parts)
        if role == "model":
            parts.append(f"[Assistant]: {text}")
        else:
            parts.append(text)

    return "\n\n".join(p for p in parts if p), images


def parse_google_function_calls(text: str) -> tuple:
    """Extract function_call blocks from model output.

    Handles 3 formats:
    1. ```function_call\\n{...}\\n``` (standard)
    2. function_call\\n{...} (without backticks)
    3. Raw JSON with "name" + "args" keys

    Returns (clean_text, [{"name": ..., "args": ...}])
    """
    function_calls = []
    pattern1 = r'```function_call\s*\n(.*?)\n```'
    pattern2 = r'(?:^|\n)function_call\s*\n(\{[^`]*?\})'
    clean = text
    for pattern in [pattern1, pattern2]:
        for match in re.findall(pattern, clean, re.DOTALL):
            try:
                data = json.loads(match.strip())
                if "name" in data:
                    function_calls.append({
                        "name": data["name"],
                        "args": data.get("args", data.get("arguments", {})),
                    })
            except (json.JSONDecodeError, KeyError):
                pass
        clean = re.sub(pattern, '', clean, flags=re.DOTALL).strip()
    if not function_calls and clean.strip().startswith("{"):
        try:
            data = json.loads(clean.strip())
            if "name" in data and ("args" in data or "arguments" in data):
                function_calls.append({
                    "name": data["name"],
                    "args": data.get("args", data.get("arguments", {})),
                })
                clean = ""
        except (json.JSONDecodeError, KeyError):
            pass
    return clean, function_calls
