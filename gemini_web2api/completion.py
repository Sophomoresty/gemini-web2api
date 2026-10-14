"""Transport-agnostic completion logic shared by all OpenAI endpoints.

Wraps a Gemini text generator with:
- retries when Gemini returns a canned failure/refusal reply instead of an answer
- tool-call extraction, with one corrective retry when the model calls an
  undeclared tool, sends broken arguments, or ignores a forced tool_choice
- response_format (JSON mode) extraction and validation
- stop-sequence truncation
- incremental streaming that forwards text live and holds back only the
  tool-call block (and, for tool requests, the first few characters so a
  canned failure can still be retried)
"""
import json
import re
import time

from .config import CONFIG
from .tools import (ECHO_LABELS, echo_prefix_len, find_tool_call_start, parse_tool_calls_detailed,
                    safe_emit_boundary, tool_choice_name, tool_names, tools_active)


def _log(msg: str):
    # Imported lazily so this module has no hard dependency on the transport.
    from .gemini import log
    log(msg)


# Gemini Web replaces the answer with one of these when generation fails
# server-side. They are transient: the same prompt usually succeeds on retry.
_FAILURE_PATTERNS = [
    r"i'?m having a hard time fulfilling your request",
    r"sorry,? something went wrong",
    r"something went wrong\.? please try",
    r"i seem to be encountering an error",
    r"i encountered an error doing what you asked",
    r"an error occurred\.? please try",
    r"i'?m unable to (?:process|complete) (?:that|this|your) request right now",
]
# Canned refusals that are frequently false positives on agent prompts.
# Retried, but returned as-is if they persist.
_REFUSAL_PATTERNS = [
    r"i'?m (?:just )?a language model,? (?:and|so) (?:i )?can'?t help",
    r"i'?m not able to help with that,? as i'?m only a language model",
    r"i'?m a text-based ai,? (?:and|so) that is outside of my capabilities",
    r"i can'?t assist (?:you )?with that,? as i'?m only a language model",
]
# Replies that open with one of these are failures even when the model keeps
# writing advice afterwards (observed with opencode: "I cannot execute tools
# directly because the tool call limit has been reached. However, ...").
_LEAD_FAILURE_PATTERNS = [
    r"\bi (?:cannot|can'?t|am unable to|'?m unable to|am not able to|'?m not able to) "
    r"(?:execute|make|call|run|perform|use|access)(?: any| the)?(?: additional| more| further| file)?"
    r" (?:tool|function)",
    r"\b(?:tool|function)[ -]call(?:ing)?s? (?:limit (?:has been |was )?reached|are exhausted|have been exhausted)",
    r"\bmaximum (?:number of )?(?:tool calls|tool call limit|call limit|limit) (?:has been |was )?reached",
    r"\bi(?: am|'m)? (?:unable|not able|cannot|can'?t)\b.{0,120}\b(?:tools?|tool calls?) (?:are|is) "
    r"(?:currently |temporarily )?(?:unavailable|disabled|not available)",
    r"\b(?:tools?|tool calls?) (?:are|is) (?:currently |temporarily )?(?:unavailable|disabled|not available)"
    r"\b.{0,120}\bi(?: am|'m)? (?:unable|not able|cannot|can'?t)\b",
    r"\bi(?: am|'m)? (?:unable|not able|cannot|can'?t) to (?:run|use|access|call|execute)\b[^.]{0,60}\btools?\b",
    r"\bwithout (?:tool|file) access\b",
]

# A reply that explains how to make the change (code blocks plus
# "here are the changes" phrasing) instead of calling the tools. Gets one
# corrective retry when tools are available.
_TUTORIAL_PATTERNS = [
    r"\byou can (?:use|make|apply|follow|add|update)\b",
    r"\bhere (?:are|is) (?:the )?(?:changes|updates|code|steps)\b",
    r"\bfollow(?:ing)? (?:these )?steps\b",
    r"\b(?:add|include|use) the following\b",
    r"\bto (?:complete|fulfill|accomplish) (?:your|this|the) request\b",
]


def tutorial_phrasing(text: str) -> bool:
    t = (text or "").lower()[:600]
    return any(re.search(p, t) for p in _TUTORIAL_PATTERNS)


def looks_like_tutorial(text: str) -> bool:
    return "```" in (text or "") and tutorial_phrasing(text)
_LEAD_CHARS = 200
_CANNED_MAX_CHARS = 300
# For tool requests, this many leading characters are held back while
# streaming so a canned reply can still be detected and retried.
CANNED_PROBE_CHARS = 160


def classify_canned(text: str):
    """Return "failure", "refusal" or None for a Gemini reply."""
    if not text:
        return None
    t = text.strip().lower().replace("\u2019", "'")
    if any(re.search(p, t[:_LEAD_CHARS]) for p in _LEAD_FAILURE_PATTERNS):
        # Retried, but returned as-is if it persists (may be a real answer).
        return "refusal"
    if len(t) > _CANNED_MAX_CHARS:
        return None
    if any(re.search(p, t) for p in _FAILURE_PATTERNS):
        return "failure"
    if any(re.search(p, t) for p in _REFUSAL_PATTERNS):
        return "refusal"
    return None


class UpstreamFailure(RuntimeError):
    """Gemini kept returning canned failures or empty replies."""


class CompletionRequest:
    def __init__(self, prompt: str, tools: list = None, tool_choice="auto",
                 response_format=None, stop=None):
        self.prompt = prompt
        self.tools = tools
        self.tool_choice = tool_choice if tool_choice is not None else "auto"
        self.response_format = response_format if isinstance(response_format, dict) else None
        if isinstance(stop, str):
            stop = [stop]
        self.stop = [s for s in (stop or []) if isinstance(s, str) and s][:4]
        self.use_tools = tools_active(tools, self.tool_choice)
        self.valid_names = tool_names(tools) if self.use_tools else set()
        self.forced_name = tool_choice_name(self.tool_choice) if self.use_tools else None
        self.require_call = self.use_tools and (self.tool_choice == "required" or bool(self.forced_name))

    @property
    def json_mode(self) -> bool:
        return bool(self.response_format) and self.response_format.get("type") in ("json_object", "json_schema")


class CompletionResult:
    def __init__(self, text: str, tool_calls: list, finish_reason: str):
        self.text = text
        self.tool_calls = tool_calls or []
        self.finish_reason = finish_reason


def _attempts() -> int:
    return max(1, int(CONFIG.get("retry_attempts", 3)))


def _pause():
    delay = CONFIG.get("retry_delay_sec", 2)
    if delay:
        time.sleep(min(delay, 5))


def apply_stop(text: str, stop: list):
    """Truncate at the earliest stop sequence. Returns (text, hit)."""
    cut = min((i for i in (text.find(s) for s in stop) if i != -1), default=-1)
    return (text[:cut], True) if cut != -1 else (text, False)


def _extract_json(text: str):
    """Return compact JSON text if `text` is (or fences) a JSON value."""
    candidates = [text.strip()]
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.S)
    if m:
        candidates.insert(0, m.group(1).strip())
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start != -1:
        candidates.append(text[start:])
    decoder = json.JSONDecoder(strict=False)
    for c in candidates:
        try:
            obj, _ = decoder.raw_decode(c)
            return json.dumps(obj, ensure_ascii=False)
        except ValueError:
            continue
    return None


def _correction(req: CompletionRequest, parsed) -> str:
    """Build a corrective note for a retry, or "" when no retry is needed."""
    notes = []
    if req.use_tools and parsed is not None:
        if parsed.rejected and not parsed.tool_calls:
            reasons = "; ".join(sorted({f'{r["name"]}: {r["reason"]}' for r in parsed.rejected}))
            notes.append(f"Your previous reply contained tool calls that could not be used ({reasons}). "
                         f"Only these tools exist: {', '.join(sorted(req.valid_names))}. "
                         "Call one of them with valid JSON arguments, or answer in plain text.")
        if (not parsed.tool_calls and not parsed.rejected and not req.require_call
                and looks_like_tutorial(parsed.text)):
            notes.append("You described the changes instead of making them. You have working tools: "
                         "call them to read, edit or create files and run commands. If the request is "
                         "really finished, reply in plain text again.")
        if req.require_call:
            ok_names = {tc["function"]["name"] for tc in parsed.tool_calls}
            if req.forced_name and req.forced_name not in ok_names:
                notes.append(f'You must call the tool "{req.forced_name}". Reply with only its tool_call block.')
            elif not req.forced_name and not ok_names:
                notes.append("You must call at least one of the listed tools. Reply with only tool_call blocks.")
    return " ".join(notes)


def _finalize(text: str, req: CompletionRequest):
    """Turn raw model text into (CompletionResult, correction_note)."""
    text = text[echo_prefix_len(text):]
    if req.use_tools:
        parsed = parse_tool_calls_detailed(text, req.valid_names)
        if parsed.rejected:
            _log(f"Dropped tool calls: {parsed.rejected}")
        note = _correction(req, parsed)
        if req.forced_name:
            parsed.tool_calls = [tc for tc in parsed.tool_calls
                                 if tc["function"]["name"] == req.forced_name] or parsed.tool_calls
        if parsed.tool_calls:
            return CompletionResult(parsed.preamble, parsed.tool_calls, "tool_calls"), note
        body = parsed.text if parsed.text or not parsed.rejected else text
    else:
        body, note = text, ""
    if req.json_mode:
        extracted = _extract_json(body)
        if extracted is None:
            note = (note + " " if note else "") + "Your previous reply was not valid JSON. Reply with JSON only."
        else:
            body = extracted
    body, _ = apply_stop(body, req.stop)
    return CompletionResult(body, [], "stop"), note


def complete(generate_fn, req: CompletionRequest) -> CompletionResult:
    """Non-streaming completion. generate_fn(prompt) -> str."""
    prompt = req.prompt
    result = None
    canned_text, canned_kind = None, None
    for attempt in range(_attempts()):
        last = attempt == _attempts() - 1
        text = generate_fn(prompt) or ""
        kind = classify_canned(text)
        if kind or not text.strip():
            canned_text, canned_kind = text, kind or "failure"
            _log(f"Gemini returned {'an empty reply' if not kind else 'canned ' + kind}"
                 f" (attempt {attempt + 1}/{_attempts()}): {text[:80]!r}")
            if not last:
                _pause()
            continue
        result, note = _finalize(text, req)
        if note and not last:
            _log(f"Retrying with correction: {note}")
            prompt = f"{req.prompt}\n\n[Correction]: {note}"
            continue
        return result
    if result is not None:
        return result
    if canned_kind == "refusal":
        return CompletionResult(canned_text.strip(), [], "stop")
    raise UpstreamFailure("Gemini returned a transient error reply"
                          + (f": {canned_text.strip()[:120]}" if canned_text and canned_text.strip() else
                             " (empty response)"))


def _maybe_echo(head: str) -> bool:
    """True if `head` is, or may still become, an echoed prompt label."""
    return bool(head) and any(label.startswith(head) or head.startswith(label) for label in ECHO_LABELS)


def complete_stream(stream_fn, req: CompletionRequest):
    """Streaming completion. stream_fn(prompt) yields text deltas.

    Yields ("text", str), then optionally ("tool_calls", list), then
    ("finish", reason). Raises UpstreamFailure / transport errors only
    before anything was yielded; later errors propagate as-is.
    """
    if req.json_mode:
        # JSON must be validated as a whole; emit it in one piece.
        result = complete(lambda p: "".join(stream_fn(p)), req)
        if result.text:
            yield ("text", result.text)
        if result.tool_calls:
            yield ("tool_calls", result.tool_calls)
        yield ("finish", result.finish_reason)
        return

    prompt = req.prompt
    stop_hold = max((len(s) for s in req.stop), default=1) - 1
    for attempt in range(_attempts()):
        last = attempt == _attempts() - 1
        full, emitted, tool_start = "", 0, -1
        stopped = False
        try:
            for delta in stream_fn(prompt):
                if not delta:
                    continue
                full += delta
                if req.stop:
                    cut, hit = apply_stop(full, req.stop)
                    if hit and (tool_start == -1 or len(cut) <= tool_start):
                        full, stopped = cut, True
                if emitted == 0:
                    head = full.lstrip()
                    if _maybe_echo(head) and "\n" not in head and not stopped:
                        continue  # possibly an echoed prompt label; wait for the full line
                    skip = echo_prefix_len(full)
                    if skip and full[skip:].strip():
                        full = full[skip:]
                if req.use_tools:
                    if emitted == 0 and len(full) < CANNED_PROBE_CHARS and not stopped:
                        continue  # hold back the start: it may be a canned failure
                    if emitted == 0 and not last and classify_canned(full[:CANNED_PROBE_CHARS]):
                        break  # long canned failure/refusal: stop reading, retry below
                    if emitted == 0 and not last and tutorial_phrasing(full) and tool_start == -1 \
                            and find_tool_call_start(full, 0) == -1:
                        continue  # may be "here are the changes": hold so it can be retried
                    if tool_start == -1:
                        tool_start = find_tool_call_start(full, emitted, at_reply_start=emitted == 0)
                    limit = tool_start if tool_start != -1 else safe_emit_boundary(full, emitted)
                else:
                    limit = len(full)
                if not stopped:
                    limit = min(limit, max(emitted, len(full) - stop_hold)) if req.stop else limit
                if limit > emitted:
                    yield ("text", full[emitted:limit])
                    emitted = limit
                if stopped:
                    break
        except Exception:
            if emitted or last:
                raise
            _log(f"Stream attempt {attempt + 1}/{_attempts()} failed before output, retrying")
            _pause()
            continue

        if emitted == 0:
            full = full[echo_prefix_len(full):]
            kind = classify_canned(full)
            if not kind and req.use_tools:
                kind = classify_canned(full[:CANNED_PROBE_CHARS])
            if kind or not full.strip():
                _log(f"Gemini returned {'an empty reply' if not kind else 'canned ' + kind}"
                     f" (attempt {attempt + 1}/{_attempts()}): {full[:80]!r}")
                if not last:
                    _pause()
                    continue
                if kind == "refusal":
                    yield ("text", full.strip())
                    yield ("finish", "stop")
                    return
                raise UpstreamFailure("Gemini returned a transient error reply"
                                      + (f": {full.strip()[:120]}" if full.strip() else " (empty response)"))

        rest = full[emitted:]
        if not req.use_tools:
            if rest:
                yield ("text", rest)
            yield ("finish", "stop")
            return

        result, note = _finalize(rest, req)
        if note and emitted == 0 and not last:
            _log(f"Retrying with correction: {note}")
            prompt = f"{req.prompt}\n\n[Correction]: {note}"
            continue
        if not result.tool_calls:
            # Nothing was a usable call: pass the held-back text through verbatim.
            out = result.text if emitted == 0 else rest
        else:
            out = ("\n\n" if emitted and result.text else "") + result.text
        if out:
            yield ("text", out)
        if result.tool_calls:
            yield ("tool_calls", result.tool_calls)
        yield ("finish", result.finish_reason)
        return
