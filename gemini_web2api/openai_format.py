"""OpenAI wire-format helpers: error bodies, usage, and SSE writers.

Kept free of Gemini transport code so both the package server and the
single-file entrypoint can share it.
"""
import json
import time
import uuid


def error_body(message: str, err_type: str = "invalid_request_error", code=None, param=None) -> dict:
    """OpenAI-style error object: {"error": {message, type, param, code}}."""
    return {"error": {"message": message, "type": err_type, "param": param, "code": code}}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token), never 0 for non-empty text."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def chat_usage(prompt: str, completion: str) -> dict:
    p, c = estimate_tokens(prompt), estimate_tokens(completion)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def responses_usage(prompt: str, completion: str) -> dict:
    p, c = estimate_tokens(prompt), estimate_tokens(completion)
    return {"input_tokens": p, "output_tokens": c, "total_tokens": p + c}


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"


def tool_calls_text(tool_calls: list) -> str:
    """Serialized form of tool calls, used for completion-token estimates."""
    return "".join(tc["function"]["name"] + tc["function"]["arguments"] for tc in tool_calls or [])


def start_sse(handler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()


class ChatStream:
    """Writes a spec-compliant chat.completion.chunk SSE stream.

    Sequence: role chunk -> content / tool_call deltas -> finish chunk
    -> optional usage chunk (stream_options.include_usage) -> [DONE].
    """

    def __init__(self, handler, cid: str, model: str, include_usage: bool = False, arg_slice: int = 120):
        self.handler = handler
        self.cid = cid
        self.model = model
        self.created = int(time.time())
        self.include_usage = include_usage
        self.arg_slice = arg_slice
        self.started = False
        self.closed = False

    def _write(self, obj) -> None:
        payload = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        self.handler.wfile.write(f"data: {payload}\n\n".encode())
        self.handler.wfile.flush()

    def _chunk(self, delta: dict, finish_reason=None) -> dict:
        chunk = {"id": self.cid, "object": "chat.completion.chunk", "created": self.created,
                 "model": self.model,
                 "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish_reason}]}
        if self.include_usage:
            chunk["usage"] = None
        return chunk

    def start(self) -> None:
        if self.started:
            return
        start_sse(self.handler)
        self.started = True
        self._write(self._chunk({"role": "assistant"}))

    def content(self, text: str) -> None:
        if not text:
            return
        self.start()
        self._write(self._chunk({"content": text}))

    def tool_calls(self, tool_calls: list) -> None:
        self.start()
        for i, tc in enumerate(tool_calls):
            fn = tc["function"]
            self._write(self._chunk({"tool_calls": [{
                "index": i, "id": tc["id"], "type": "function",
                "function": {"name": fn["name"], "arguments": ""}}]}))
            args = fn.get("arguments") or ""
            for j in range(0, len(args), self.arg_slice):
                self._write(self._chunk({"tool_calls": [{
                    "index": i, "function": {"arguments": args[j:j + self.arg_slice]}}]}))

    def finish(self, finish_reason: str, usage: dict = None) -> None:
        self.start()
        self._write(self._chunk({}, finish_reason))
        if self.include_usage:
            self._write({"id": self.cid, "object": "chat.completion.chunk", "created": self.created,
                         "model": self.model, "choices": [], "usage": usage})
        self._write("[DONE]")
        self.closed = True

    def error(self, message: str, err_type: str = "upstream_error") -> None:
        """Report a failure inside an already-open stream, then terminate it."""
        if not self.started:
            start_sse(self.handler)
            self.started = True
        self._write(error_body(message, err_type))
        self._write("[DONE]")
        self.closed = True


class ResponsesStream:
    """Writes the OpenAI Responses API streaming event sequence."""

    def __init__(self, handler, rid: str, model: str):
        self.handler = handler
        self.seq = 0
        self.base = {"id": rid, "object": "response", "created_at": int(time.time()), "model": model}
        self.output = []
        self._msg = None  # in-progress message item
        self.started = False

    def emit(self, event_type: str, **fields) -> None:
        self.seq += 1
        event = {"type": event_type, "sequence_number": self.seq, **fields}
        self.handler.wfile.write(
            f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
        self.handler.wfile.flush()

    def start(self) -> None:
        if self.started:
            return
        start_sse(self.handler)
        self.started = True
        pending = {**self.base, "status": "in_progress", "output": [], "usage": None}
        self.emit("response.created", response=pending)
        self.emit("response.in_progress", response=pending)

    def _open_message(self) -> None:
        mid = f"msg_{uuid.uuid4().hex[:24]}"
        self._msg = {"id": mid, "index": len(self.output), "text": ""}
        self.output.append(None)  # placeholder, filled on close
        self.emit("response.output_item.added", output_index=self._msg["index"],
                  item={"type": "message", "id": mid, "role": "assistant", "status": "in_progress", "content": []})
        self.emit("response.content_part.added", item_id=mid, output_index=self._msg["index"], content_index=0,
                  part={"type": "output_text", "text": "", "annotations": []})

    def text(self, delta: str) -> None:
        if not delta:
            return
        self.start()
        if self._msg is None:
            self._open_message()
        self._msg["text"] += delta
        self.emit("response.output_text.delta", item_id=self._msg["id"], output_index=self._msg["index"],
                  content_index=0, delta=delta)

    def _close_message(self) -> None:
        m = self._msg
        if m is None:
            return
        part = {"type": "output_text", "text": m["text"], "annotations": []}
        item = {"type": "message", "id": m["id"], "role": "assistant", "status": "completed", "content": [part]}
        self.emit("response.output_text.done", item_id=m["id"], output_index=m["index"], content_index=0, text=m["text"])
        self.emit("response.content_part.done", item_id=m["id"], output_index=m["index"], content_index=0, part=part)
        self.emit("response.output_item.done", output_index=m["index"], item=item)
        self.output[m["index"]] = item
        self._msg = None

    def function_calls(self, tool_calls: list) -> None:
        self.start()
        self._close_message()
        for tc in tool_calls:
            idx = len(self.output)
            fc_id = f"fc_{tc['id'][len('call_'):]}" if tc["id"].startswith("call_") else f"fc_{tc['id']}"
            item = {"type": "function_call", "id": fc_id, "call_id": tc["id"],
                    "name": tc["function"]["name"], "arguments": tc["function"]["arguments"], "status": "completed"}
            self.output.append(item)
            self.emit("response.output_item.added", output_index=idx,
                      item={**item, "arguments": "", "status": "in_progress"})
            self.emit("response.function_call_arguments.delta", item_id=fc_id, output_index=idx,
                      delta=item["arguments"])
            self.emit("response.function_call_arguments.done", item_id=fc_id, output_index=idx,
                      arguments=item["arguments"])
            self.emit("response.output_item.done", output_index=idx, item=item)

    def complete(self, usage: dict) -> None:
        self.start()
        self._close_message()
        self.emit("response.completed",
                  response={**self.base, "status": "completed", "output": self.output, "usage": usage})

    def fail(self, message: str) -> None:
        self.start()
        self._close_message()
        self.emit("response.failed", response={**self.base, "status": "failed", "output": self.output,
                                               "usage": None,
                                               "error": {"code": "server_error", "message": message}})


def responses_output_items(text: str, tool_calls: list) -> list:
    """Non-streaming Responses API output list (message first, then calls)."""
    output = []
    if text or not tool_calls:
        output.append({"type": "message", "id": f"msg_{uuid.uuid4().hex[:24]}", "role": "assistant",
                       "status": "completed",
                       "content": [{"type": "output_text", "text": text or "", "annotations": []}]})
    for tc in tool_calls or []:
        fc_id = f"fc_{tc['id'][len('call_'):]}" if tc["id"].startswith("call_") else f"fc_{tc['id']}"
        output.append({"type": "function_call", "id": fc_id, "call_id": tc["id"],
                       "name": tc["function"]["name"], "arguments": tc["function"]["arguments"],
                       "status": "completed"})
    return output
