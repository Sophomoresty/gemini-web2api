"""Tool calling and multimodal message parsing."""
import json
import re
import uuid
import base64
import binascii
import io
from urllib.parse import unquote_to_bytes

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


def _decode_data_url(url: str):
    match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", url, re.DOTALL)
    if not match:
        return None
    mime = match.group(1) or "image/png"
    is_base64 = bool(match.group(2))
    data = match.group(3)
    try:
        if is_base64:
            return base64.b64decode(data, validate=True), mime
        return unquote_to_bytes(data), mime
    except (ValueError, TypeError, binascii.Error):
        return None


def _image_from_url(url: str, mime: str = None):
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        return _decode_data_url(url)
    return url, mime or "image/png"


def _image_from_part(part: dict):
    part_type = part.get("type")
    if part_type == "image_url":
        image_url = part.get("image_url", {})
        if isinstance(image_url, dict):
            return _image_from_url(image_url.get("url"), image_url.get("mime_type"))
        return _image_from_url(image_url)
    if part_type in ("input_image", "image"):
        image_url = part.get("image_url") or part.get("url")
        if isinstance(image_url, dict):
            return _image_from_url(image_url.get("url"), image_url.get("mime_type"))
        if image_url:
            return _image_from_url(image_url, part.get("mime_type"))
        image_data = part.get("data") or part.get("base64")
        if isinstance(image_data, str):
            mime = part.get("mime_type") or part.get("media_type") or "image/png"
            if image_data.startswith("data:"):
                return _decode_data_url(image_data)
            try:
                return base64.b64decode(image_data, validate=True), mime
            except (ValueError, TypeError, binascii.Error):
                return None
    return None


# ─── OpenAI tool definitions ──────────────────────────────────────────────────

# Above this size the tool list is slimmed so the prompt is not silently
# truncated upstream.
TOOLS_JSON_MAX_CHARS = 60000


def normalize_tools(tools: list) -> list:
    """Normalize Chat Completions and Responses tool lists to
    [{"name", "description", "parameters"}]. Non-function tools
    (e.g. Responses built-ins like web_search) are skipped."""
    defs = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if tool.get("type") not in (None, "function") and "function" not in tool:
            continue
        name = fn.get("name")
        if not name or not isinstance(name, str):
            continue
        defs.append({
            "name": name,
            "description": fn.get("description") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return defs


def tool_names(tools: list) -> set:
    """Extract declared function names from an OpenAI tools list."""
    return {d["name"] for d in normalize_tools(tools)}


def tool_choice_name(tool_choice):
    """Return the forced function name for a named tool_choice, else None.

    Accepts both Chat ({"type":"function","function":{"name":..}}) and
    Responses ({"type":"function","name":..}) shapes."""
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return fn["name"]
        if tool_choice.get("name"):
            return tool_choice["name"]
    return None


def tools_active(tools, tool_choice) -> bool:
    return bool(normalize_tools(tools)) and tool_choice != "none"


def _tool_choice_instruction(tool_choice) -> str:
    name = tool_choice_name(tool_choice)
    if name:
        return f'You MUST call the tool "{name}" now. Do not call other tools and do not answer in text.'
    if tool_choice == "required":
        return "You MUST call at least one tool now. Do not answer in text only."
    return ""


def response_format_instruction(response_format) -> str:
    """Prompt instruction for response_format json_object / json_schema."""
    if not isinstance(response_format, dict):
        return ""
    rf_type = response_format.get("type")
    if rf_type == "json_object":
        return ("Respond with a single valid JSON object only. No markdown fences, "
                "no explanations before or after it.")
    if rf_type == "json_schema":
        schema = response_format.get("json_schema") or {}
        body = schema.get("schema", schema)
        return ("Respond with a single valid JSON value only, matching this JSON Schema. "
                "No markdown fences, no explanations before or after it.\n"
                f"JSON Schema: {json.dumps(body, ensure_ascii=False)}")
    return ""


def _strip_param_descriptions(schema):
    if isinstance(schema, dict):
        return {k: _strip_param_descriptions(v) for k, v in schema.items() if k != "description"}
    if isinstance(schema, list):
        return [_strip_param_descriptions(v) for v in schema]
    return schema


def _tool_spec_lines(tool_defs: list) -> str:
    lines = [json.dumps(d, ensure_ascii=False) for d in tool_defs]
    if sum(len(x) for x in lines) <= TOOLS_JSON_MAX_CHARS:
        return "\n".join(lines)
    # Over budget: strip nested parameter descriptions, then give full
    # schemas to the smallest tools first (core tools like read/edit/bash are
    # usually small; MCP servers can add huge ones). Tools that do not fit
    # keep their name, a shortened description and their parameter names.
    compact = [{**d, "description": d["description"][:1500],
                "parameters": _strip_param_descriptions(d["parameters"])} for d in tool_defs]
    slim = []
    for d in compact:
        params = d["parameters"] if isinstance(d["parameters"], dict) else {}
        props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        desc = d["description"].split("\n", 1)[0][:160]
        entry = {"name": d["name"], "description": desc}
        if props:
            entry["parameter_names"] = list(props)
        slim.append(json.dumps(entry, ensure_ascii=False))
    full = [json.dumps(d, ensure_ascii=False) for d in compact]
    # Tools sharing a prefix with many siblings (e.g. one MCP server's
    # "rea_*" tools) are ranked after standalone tools.
    prefix = [re.split(r"[_.:\-]", d["name"], maxsplit=1)[0] for d in compact]
    group = {p: prefix.count(p) for p in set(prefix)}
    order = sorted(range(len(full)), key=lambda i: (group[prefix[i]] >= 5, len(full[i])))
    budget = TOOLS_JSON_MAX_CHARS - sum(len(x) for x in slim)
    chosen = list(slim)
    for i in order:
        extra = len(full[i]) - len(slim[i])
        if extra <= budget:
            chosen[i] = full[i]
            budget -= extra
    return "\n".join(chosen)


def build_openai_tool_prompt(tool_defs: list) -> str:
    return (
        "# Tools\n\n"
        "You are connected to tools that run on the user's machine. Use them whenever the task "
        "needs real data or actions.\n\n"
        "To call a tool, reply with a fenced block in exactly this format:\n"
        '```tool_call\n{"name": "tool_name", "arguments": {"param": "value"}}\n```\n\n'
        "Rules:\n"
        "1. Only call tools listed under \"Available tools\". Do not use your own built-in tools "
        "(Google Search, code execution, maps, weather and similar); the listed tools replace them.\n"
        "2. \"arguments\" must be a valid JSON object matching the tool's parameters. "
        "Escape newlines in strings as \\n and quotes as \\\".\n"
        "3. You may output several tool_call blocks in one reply when the calls do not depend on each other.\n"
        "4. After your tool_call blocks, stop. Each result is sent back to you as a [Tool result ...] "
        "message. Never write, predict or assume tool results yourself.\n"
        "5. When no tool is needed, reply in plain text without any tool_call block.\n\n"
        f"Available tools:\n{_tool_spec_lines(tool_defs)}"
    )


# ─── OpenAI messages -> Gemini prompt ─────────────────────────────────────────

def _content_to_text(content, images: list) -> str:
    """Flatten OpenAI message content (str / None / parts list) to text,
    collecting image parts into `images`."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        text_parts = []
        for c in content:
            if isinstance(c, str):
                text_parts.append(c)
                continue
            if not isinstance(c, dict):
                continue
            if c.get("type") in ("text", "input_text", "output_text"):
                text_parts.append(c.get("text", ""))
            elif c.get("type") == "refusal":
                text_parts.append(c.get("refusal", ""))
            else:
                image = _image_from_part(c)
                if image:
                    images.append(image)
                    text_parts.append("[Image attached]")
        return " ".join(p for p in text_parts if p)
    return json.dumps(content, ensure_ascii=False)


def _render_tool_call(tc: dict) -> str:
    fn = tc.get("function") or {}
    raw_args = fn.get("arguments", {})
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args, strict=False) if raw_args.strip() else {}
        except ValueError:
            args = raw_args  # keep as a JSON string so the block stays valid JSON
    else:
        args = raw_args if raw_args is not None else {}
    call = {"name": fn.get("name", ""), "arguments": args}
    return f"```tool_call\n{json.dumps(call, ensure_ascii=False)}\n```"


def messages_to_prompt(messages: list, tools: list = None, tool_choice=None,
                       response_format=None) -> tuple:
    """Convert OpenAI messages to (prompt_str, images_list).

    Returns (prompt, images) where images is a list of (bytes, mime_type) tuples.
    Every turn is role-labelled (except a lone user message), tool results
    are matched to their call via tool_call_id, and a short reminder is
    appended so the tool protocol survives long agent conversations.
    """
    parts = []
    images = []
    messages = [m for m in (messages or []) if isinstance(m, dict)]

    tool_defs = normalize_tools(tools) if tool_choice != "none" else []
    if tool_defs:
        parts.append(build_openai_tool_prompt(tool_defs))

    label_users = bool(tool_defs) or len(messages) > 1 or any(
        m.get("role", "user") != "user" for m in messages)
    call_names = {}  # tool_call_id -> function name

    for msg in messages:
        role = msg.get("role", "user")
        content = _content_to_text(msg.get("content"), images)
        if role in ("system", "developer"):
            if content:
                parts.append(f"[System instruction]: {content}")
        elif role == "assistant":
            calls = msg.get("tool_calls") or []
            for tc in calls:
                if isinstance(tc, dict) and tc.get("id"):
                    call_names[tc["id"]] = (tc.get("function") or {}).get("name", "")
            blocks = [_render_tool_call(tc) for tc in calls if isinstance(tc, dict)]
            body = "\n".join(p for p in [content] + blocks if p)
            parts.append(f"[Assistant]: {body}")
        elif role in ("tool", "function"):
            call_id = msg.get("tool_call_id") or ""
            name = call_names.get(call_id) or msg.get("name") or "unknown tool"
            ref = f" (id: {call_id})" if call_id else ""
            parts.append(f"[Tool result for {name}{ref}]:\n{content}")
        else:
            if content:
                parts.append(f"[User]: {content}" if label_users else content)

    reminder = []
    if tool_defs:
        reminder.append("Continue as the assistant and keep working on the user's latest request. Showing "
                        "code is not the same as changing a file: to create or modify files or run commands you "
                        "must call the tools. To call a tool, output only tool_call blocks and then stop; results "
                        "come back in the next message. Reply in plain text only when the request is fully done "
                        "or no tool can help.")
        choice = _tool_choice_instruction(tool_choice)
        if choice:
            reminder.append(choice)
    rf = response_format_instruction(response_format)
    if rf:
        reminder.append(rf)
    if reminder and len(parts) > 0:
        parts.append("[Reminder]: " + " ".join(reminder))

    prompt = "\n\n".join(p for p in parts if p)
    return prompt, images


# ─── Responses API input -> chat messages ─────────────────────────────────────

def responses_tools_to_chat(tools: list) -> list:
    """Convert Responses tools ({type, name, parameters}) to chat format.
    Built-in tools (web_search, file_search, ...) are dropped."""
    out = []
    for d in normalize_tools(tools):
        out.append({"type": "function", "function": d})
    return out or None


def responses_input_to_messages(req: dict) -> list:
    """Convert a Responses API request (instructions + input) to chat messages.

    Handles string input, message items (any role, string or part-list
    content), top-level input_text/input_image parts, top-level
    function_call items (grouped onto an assistant turn) and
    function_call_output items. Reasoning items are ignored.
    """
    messages = []
    if req.get("instructions"):
        messages.append({"role": "system", "content": req["instructions"]})
    items = req.get("input", [])
    if isinstance(items, str):
        messages.append({"role": "user", "content": items})
        return messages
    if not isinstance(items, list):
        return messages

    for item in items:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "function_call":
            call = {"id": item.get("call_id") or item.get("id") or "", "type": "function",
                    "function": {"name": item.get("name", ""), "arguments": item.get("arguments") or "{}"}}
            last = messages[-1] if messages else None
            if last and last.get("role") == "assistant":
                last.setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif itype == "function_call_output":
            output = item.get("output", "")
            if not isinstance(output, str):
                output = output if isinstance(output, list) else json.dumps(output, ensure_ascii=False)
            messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""), "content": output})
        elif itype in ("input_text", "input_image", "image"):
            messages.append({"role": "user", "content": [item]})
        elif itype == "reasoning":
            continue
        elif itype in (None, "message") and item.get("role"):
            role = item["role"]
            content = item.get("content", "")
            if role == "assistant" and isinstance(content, list):
                text = "".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("type") in ("output_text", "text"))
                calls = [{"id": c.get("call_id") or c.get("id") or "", "type": "function",
                          "function": {"name": c.get("name", ""), "arguments": c.get("arguments") or "{}"}}
                         for c in content if isinstance(c, dict) and c.get("type") == "function_call"]
                m = {"role": "assistant", "content": text or None}
                if calls:
                    m["tool_calls"] = calls
                messages.append(m)
            else:
                messages.append({"role": role, "content": content})
    return messages


# ─── Tool call parsing ────────────────────────────────────────────────────────

_DECODER = json.JSONDecoder(strict=False)
_TOOL_FENCE_TAGS = {"tool_call", "tool_calls", "tool_code", "function_call", "tool_use"}
_FENCE_OPEN = re.compile(r"```[ \t]*([A-Za-z_]*)[ \t]*\r?\n?")
_BRACKET_OPEN = re.compile(r"\[tool_call\s*:\s*([A-Za-z0-9_.:\-]+)\s*")
_CALL_KEYS = {"name", "arguments", "args", "parameters", "input", "id", "type", "function"}
_ARG_KEYS = ("arguments", "args", "parameters", "input")


class ParseResult:
    """Outcome of tool-call extraction.

    text: model text with all tool-call blocks removed.
    preamble: text before the first tool-call block. Text after a call is
        usually the model predicting results or echoing the prompt, so
        callers returning tool calls should prefer this over `text`.
    tool_calls: OpenAI-shaped calls to declared tools.
    rejected: [{"name", "reason"}] for calls that were dropped.
    """

    def __init__(self, text: str, tool_calls: list, rejected: list, preamble: str = None):
        self.text = text
        self.tool_calls = tool_calls
        self.rejected = rejected
        self.preamble = text if preamble is None else preamble


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _repair_json(raw: str):
    """Best-effort repair of a broken JSON object body."""
    raw = raw.strip()
    candidates = [raw]
    if raw.endswith("}}"):
        candidates.append(raw[:-1])
    candidates += [raw + "}", raw + "}}"]
    for c in candidates:
        try:
            return json.loads(c, strict=False)
        except ValueError:
            continue
    return None


def _resolve_name(name: str, valid_names):
    if valid_names is None or name in valid_names:
        return name
    for sep in (".", ":"):
        if sep in name:
            short = name.rsplit(sep, 1)[1]
            if short in valid_names:
                return short
    return None


def _normalize_call(data, valid_names, strict_shape: bool):
    """Return ("ok", name, args) | ("bad", name, reason) | None (not a call).

    strict_shape=True (json/bare fences, raw JSON) only treats objects whose
    keys are all call-like as calls, so ordinary JSON samples survive."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("function"), dict) and "name" not in data:
        data = data["function"]
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    if strict_shape and (not set(data) <= _CALL_KEYS or not any(k in data for k in _ARG_KEYS)):
        return None
    if any(k in data for k in _ARG_KEYS):
        args = next(data[k] for k in _ARG_KEYS if k in data)
    else:
        # Observed: {"name": "attempt_completion", "result": "..."} with the
        # arguments flattened next to the name.
        args = {k: v for k, v in data.items() if k not in ("name", "id", "type")}
    if args is None:
        args = {}
    if isinstance(args, str):
        if not args.strip():
            args = {}
        else:
            try:
                args = json.loads(args, strict=False)
            except ValueError:
                args = _repair_json(args)
                if args is None:
                    return ("bad", name, "arguments are not valid JSON")
    if not isinstance(args, dict):
        return ("bad", name, "arguments must be a JSON object")
    resolved = _resolve_name(name, valid_names)
    if resolved is None:
        return ("bad", name, "tool is not declared")
    return ("ok", resolved, args)


def _calls_from(obj, valid_names, strict_shape):
    items = obj if isinstance(obj, list) else [obj]
    out = []
    for it in items:
        norm = _normalize_call(it, valid_names, strict_shape)
        if norm is None:
            return None  # any non-call element => the whole block is not a call list
        out.append(norm)
    return out or None


def parse_tool_calls_detailed(text: str, valid_names: set = None) -> ParseResult:
    """Extract tool calls from model text.

    Accepted forms:
    1. ```tool_call / ```function_call / ```tool_code fences (object or list)
    2. ```json or bare ``` fences whose JSON is call-shaped
    3. [tool_call: name {...}] bracket shorthand
    4. A reply that is only a call-shaped JSON object/list

    JSON is decoded with a real parser (not a regex up to the next fence),
    so arguments may contain ``` or raw newlines. Calls to undeclared
    tools or with unparseable arguments are rejected instead of being
    passed on with empty arguments.
    """
    text = text or ""
    spans = []  # (start, end, [normalized calls])
    pos = 0

    for m in _FENCE_OPEN.finditer(text):
        if m.start() < pos:
            continue
        tag = m.group(1).lower()
        if tag and tag not in _TOOL_FENCE_TAGS and tag != "json":
            continue
        explicit = tag in _TOOL_FENCE_TAGS
        j = _skip_ws(text, m.end())
        if j >= len(text) or text[j] not in "{[":
            continue
        try:
            obj, end = _DECODER.raw_decode(text, j)
        except ValueError:
            if not explicit:
                continue
            close = text.find("```", j)
            body_end = close if close != -1 else len(text)
            obj = _repair_json(text[j:body_end])
            span_end = close + 3 if close != -1 else len(text)
            if obj is None:
                nm = re.search(r'"name"\s*:\s*"([^"]+)"', text[j:body_end])
                spans.append((m.start(), span_end,
                              [("bad", nm.group(1) if nm else "unknown", "tool call is not valid JSON")]))
                pos = span_end
                continue
        else:
            k = _skip_ws(text, end)
            if text.startswith("}", k) and text.startswith("```", _skip_ws(text, k + 1)):
                k = _skip_ws(text, k + 1)  # tolerate one stray closing brace
            if text.startswith("```", k):
                span_end = k + 3
            elif explicit:
                span_end = end  # unclosed fence: accept the object
            else:
                continue
        calls = _calls_from(obj, valid_names, strict_shape=not explicit)
        if calls is None:
            if explicit:
                calls = [("bad", "unknown", "tool call must be an object with a name")]
            else:
                continue
        if not explicit and all(c[0] == "bad" and c[2] == "tool is not declared" for c in calls):
            continue  # ordinary JSON that happens to have a "name" key
        spans.append((m.start(), span_end, calls))
        pos = span_end

    for m in _BRACKET_OPEN.finditer(text):
        if any(s <= m.start() < e for s, e, _ in spans):
            continue
        j = m.end()
        if j >= len(text) or text[j] != "{":
            continue
        try:
            args, end = _DECODER.raw_decode(text, j)
        except ValueError:
            continue
        k = _skip_ws(text, end)
        if text.startswith("}", k):
            k = _skip_ws(text, k + 1)
        if not text.startswith("]", k):
            continue
        norm = _normalize_call({"name": m.group(1), "arguments": args}, valid_names, False)
        spans.append((m.start(), k + 1, [norm]))

    if not spans:
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            try:
                obj, end = _DECODER.raw_decode(stripped)
            except ValueError:
                obj, end = None, 0
            if obj is not None and end == len(stripped):
                calls = _calls_from(obj, valid_names, strict_shape=True)
                if calls and not all(c[0] == "bad" and c[2] == "tool is not declared" for c in calls):
                    spans.append((0, len(text), calls))

    spans.sort(key=lambda s: s[0])
    clean_parts, tool_calls, rejected = [], [], []
    last = 0
    for start, end, calls in spans:
        if start < last:
            continue
        clean_parts.append(text[last:start])
        last = end
        for status, name, payload in calls:
            if status == "ok":
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(payload, ensure_ascii=False)},
                })
            else:
                rejected.append({"name": name, "reason": payload})
    clean_parts.append(text[last:])
    clean = re.sub(r"\n{3,}", "\n\n", "".join(clean_parts)).strip()
    preamble = text[:spans[0][0]].strip() if spans else clean
    return ParseResult(clean, tool_calls, rejected, preamble)


def parse_tool_calls(text: str, valid_names: set = None) -> tuple:
    """Extract tool calls. Returns (clean_text, tool_calls_list)."""
    result = parse_tool_calls_detailed(text, valid_names)
    return result.text, result.tool_calls


# The model sometimes echoes prompt scaffolding at the start of its reply.
_ECHO_RE = re.compile(r"\A\s*(?:\[Assistant\]:[ \t]*|\[(?:Reminder|Correction)\]:[^\n]*(?:\n|\Z)\s*)+")
ECHO_LABELS = ("[Assistant]", "[Reminder]", "[Correction]")


def echo_prefix_len(text: str) -> int:
    """Length of echoed prompt labels ([Assistant]:, [Reminder]: ...) at the start of a reply."""
    m = _ECHO_RE.match(text or "")
    return m.end() if m else 0


# Marker prefixes that may start a tool call in streamed text.
TOOL_CALL_MARKERS = ("```tool_call", "```tool_code", "```function_call", "```tool_use", "```json",
                     "[tool_call")


def find_tool_call_start(text: str, start: int = 0, at_reply_start: bool = None) -> int:
    """Index of the earliest tool-call marker at or after `start`, else -1.
    When `start` is the beginning of the reply, a leading JSON object also
    counts (raw-JSON calls)."""
    if at_reply_start is None:
        at_reply_start = start == 0
    head = text[start:]
    if at_reply_start and head.lstrip()[:1] in ("{", "["):
        return start + len(head) - len(head.lstrip())
    hits = [i for i in (text.find(mk, start) for mk in TOOL_CALL_MARKERS) if i != -1]
    return min(hits) if hits else -1


def safe_emit_boundary(text: str, start: int) -> int:
    """Largest index up to which streamed text can be emitted without
    possibly splitting the beginning of a tool-call marker."""
    longest = max(len(mk) for mk in TOOL_CALL_MARKERS)
    end = len(text)
    for i in range(max(start, end - longest + 1), end):
        tail = text[i:]
        if any(mk.startswith(tail) for mk in TOOL_CALL_MARKERS):
            return i
    return end


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
                try:
                    images.append((
                        base64.b64decode(data["data"], validate=True),
                        data.get("mimeType", "image/png"),
                    ))
                    msg_parts.append("[Image attached]")
                except (KeyError, ValueError, TypeError, binascii.Error):
                    pass
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


def parse_google_function_calls(text: str, valid_names: set = None) -> tuple:
    """Extract function calls for the Google native API.

    Shares the OpenAI parser (fenced function_call / tool_call blocks,
    bare JSON, bracket shorthand). Returns (clean_text, [{"name", "args"}]).
    """
    result = parse_tool_calls_detailed(text, valid_names)
    calls = [{"name": tc["function"]["name"], "args": json.loads(tc["function"]["arguments"])}
             for tc in result.tool_calls]
    return result.text, calls
