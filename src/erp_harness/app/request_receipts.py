"""Local request evidence. Correlations come from live objects, never file order."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from erp_harness.app.stream_events import public_events
from erp_harness.runtime.provider import provider_request_kind


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RequestReceipts:
    def __init__(self, directory: Path, max_model_requests: int | None = None):
        if max_model_requests is not None and (type(max_model_requests) is not int or max_model_requests < 1):
            raise ValueError("max_model_requests must be a positive integer")
        self.directory, self.max_model_requests = directory, max_model_requests
        self.number = max((int(p.name.split(".", 1)[0]) for p in directory.glob("*.json")
                           if p.name.split(".", 1)[0].isdigit()), default=0)
        self._scope = ContextVar("request_receipt_scope", default=None)
        self._rows = {}
        self._messages = {}  # Hold identity and object until the public terminal arrives.
        self._tools = {}
        self._round_id = None
        self._unwrapped = None
        self._warned = set()

    def _write(self, stem, suffix, value):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{stem}.{suffix}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except (OSError, TypeError, ValueError) as exc:
            filename = f"{stem}.{suffix}.json"
            if filename not in self._warned:
                self._warned.add(filename)
                with suppress(OSError):
                    print(json.dumps({"type": "receipt_warning", "file": filename, "error_type": type(exc).__name__}), flush=True)

    def _publish(self, event_type, row):
        with suppress(OSError, TypeError, ValueError):
            public = {key: value for key, value in row.items() if not key.startswith("_")}
            self._write(row["request_file"], "meta", public)
            print(json.dumps({"type": event_type, **public}, ensure_ascii=False), flush=True)

    def _allocate(self, payload):
        self.number += 1
        scope = self._scope.get()
        row = {"schema_version": 1, "request_id": "req_" + uuid4().hex,
               "call_id": scope["call_id"] if scope else "call_" + uuid4().hex,
               "request_file": f"{self.number:04d}", "request_kind": provider_request_kind.get(),
               "status": "prepared", "run_id": os.environ.get("HARBOR_TRIAL_ID") or os.environ.get("PI_AGENT_RUN_ID"),
               "session_id": os.environ.get("PI_AGENT_SESSION_ID")}
        if scope is not None:
            scope["row"] = row
        else:
            self._unwrapped = row
        self._rows[row["request_id"]] = row
        self._write(row["request_file"], "request", payload)
        return row

    def _current(self):
        scope = self._scope.get()
        return scope.get("row") if scope else self._unwrapped

    async def before_provider_request(self, payload):
        if self.max_model_requests is not None and self.number >= self.max_model_requests:
            raise RuntimeError(f"max_model_requests ({self.max_model_requests}) exceeded")
        self._allocate(payload)
        return payload

    async def before_provider_headers(self, headers):
        return headers

    async def before_provider_attempt(self, payload, attempt):
        row = self._current() if attempt == 1 else self._allocate(payload)
        if row is not None:
            row.update(attempt=attempt, started_at=_now(), _started=monotonic(), status="running")
            self._publish("request_started", row)

    async def after_provider_response(self, status, headers):
        row = self._current()
        if row is None:
            return
        ids = {key: value for key, value in headers.items()
               if key.lower() in {"x-request-id", "request-id", "x-generation-id"}}
        row.update(http_status=status, headers_received_at=_now(), request_ids=ids)
        self._write(row["request_file"], "response", {"status": status, "request_ids": ids})
        self._publish("request_headers", row)

    def _finish(self, row, status):
        if row is None or row.get("finished_at"):
            return
        row.update(status=status, finished_at=_now())
        if "_started" in row:
            row["duration_ms"] = max(0, (monotonic() - row["_started"]) * 1000)
        self._publish("request_finished", row)

    async def after_provider_attempt(self, status):
        self._finish(self._current(), status)

    async def wrap_provider_stream(self, source, *, raw=None):
        scope = {"call_id": "call_" + uuid4().hex}
        try:
            while True:
                token = self._scope.set(scope)
                try:
                    event = await anext(source)
                except StopAsyncIteration:
                    self._finish(scope.get("row"), "unknown")
                    return
                except (asyncio.CancelledError, GeneratorExit):
                    self._finish(scope.get("row"), "aborted")
                    raise
                except Exception:
                    self._finish(scope.get("row"), "error")
                    raise
                finally:
                    self._scope.reset(token)
                row = scope.get("row")
                if row is not None:
                    with suppress(Exception):
                        message = getattr(event, "message", None) or getattr(event, "error", None) or getattr(event, "partial", None)
                        if message is not None and row["request_kind"] == "normal" and event.type in {"start", "done", "error"}:
                            self._messages[id(message)] = (message, row)
                        if "first_output_at" not in row and event.type in {"text_delta", "thinking_delta", "toolcall_start", "toolcall_delta", "toolcall_end"}:
                            row["first_output_at"] = _now()
                        if event.type in {"done", "error"}:
                            public = {"text": message.text, "stop_reason": message.stop_reason,
                                      "error": message.error_message, "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in message.tool_calls],
                                      "usage": self._usage(message)}
                            self._write(row["request_file"], "output", public)
                            row.update(stop_reason=message.stop_reason, error=message.error_message[:500] if message.error_message else None,
                                       usage=public["usage"], output_file=row["request_file"] + ".output.json", tool_call_ids=[call.id for call in message.tool_calls])
                            self._tools.update({call.id: row for call in message.tool_calls})
                            self._finish(row, "completed" if event.type == "done" else "aborted" if event.reason == "aborted" else "error")
                yield event
        finally:
            self._finish(scope.get("row"), "aborted")
            token = self._scope.set(scope)
            try:
                for stream in (source, raw):
                    close = getattr(stream, "aclose", None)
                    if close is not None:
                        with suppress(Exception):
                            await close()
            finally:
                self._scope.reset(token)

    @staticmethod
    def _usage(message):
        usage = message.usage
        fields = ("input", "cache_read", "cache_write", "cache_write_1h", "output", "reasoning", "total_tokens")
        if usage is None or not usage.model_fields_set or message.stop_reason in {"error", "aborted"} and not any(getattr(usage, name, None) for name in fields):
            return {name: None for name in fields}
        return {name: getattr(usage, name, None) for name in fields}

    async def events(self, source):
        async def correlated():
            async for raw in source:
                kind = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
                if kind not in {"turn_start", "turn_end", "message_start", "message_end", "tool_execution_start", "tool_execution_end"}:
                    yield raw
                    continue
                event = dict(raw) if isinstance(raw, dict) else raw.model_dump(mode="json", by_alias=True)
                if kind == "turn_start":
                    self._round_id = "round_" + uuid4().hex
                if self._round_id and kind in {"turn_start", "turn_end", "message_start", "message_end", "tool_execution_start", "tool_execution_end"}:
                    event["round_id"] = self._round_id
                message = getattr(raw, "message", None)
                pair = self._messages.get(id(message))
                row = pair[1] if pair and pair[0] is message else None
                call_id = event.get("toolCallId", event.get("tool_call_id"))
                row = row or self._tools.get(call_id)
                if row is not None:
                    event["request_id"] = row["request_id"]
                yield event

        async for event in public_events(correlated()):
            row = self._rows.get(event.get("request_id"))
            if row is not None and event.get("type") == "message_end":
                row.update(message_id=event["message_id"], round_id=event.get("round_id"))
                self._publish("request_linked", row)
                self._messages = {key: pair for key, pair in self._messages.items() if pair[1] is not row}
            yield event
