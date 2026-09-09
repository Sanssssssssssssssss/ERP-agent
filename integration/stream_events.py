"""Public text-delta projection of a Pi event stream.

The host still receives normal terminal and tool events for its durable
worker semantics.  Only assistant ``message_update`` events are projected:
text deltas cross the public boundary; thinking and cumulative partials do
not.  The host may build its conversation projection from the deltas.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Mapping
from typing import Any
from uuid import uuid4


def _dump(event: object) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json", by_alias=True)
        if isinstance(value, dict):
            return value
    raise TypeError(f"unsupported Pi event: {type(event).__name__}")


def _role(message: object) -> str | None:
    if isinstance(message, Mapping):
        value = message.get("role")
    else:
        value = getattr(message, "role", None)
    return value if isinstance(value, str) else None


def _field(value: object, name: str, alias: str | None = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, value.get(alias) if alias else None)
    return getattr(value, name, None)


async def public_events(source: AsyncIterable[object]) -> AsyncIterator[dict[str, Any]]:
    """Yield JSON-compatible events safe for the desktop/public stream.

    Incremental thinking events and cumulative partial snapshots never cross
    this boundary.  The terminal ``message_end`` remains available to the
    host for its durable terminal projection.  A message id is assigned at
    assistant ``message_start`` and reused by all public deltas and that end.
    """
    message_id: str | None = None
    sequence = 0
    async for raw in source:
        kind = _field(raw, "type")
        message = _field(raw, "message")
        is_assistant = _role(message) == "assistant"

        if kind == "message_start" and is_assistant:
            message_id = str(uuid4())
            sequence = 0
            event = _dump(raw)
            event["message_id"] = message_id
            yield event
            continue

        if kind == "message_update" and is_assistant:
            nested = _field(raw, "assistant_message_event", "assistantMessageEvent")
            nested_type = _field(nested, "type")
            if nested_type == "text_delta":
                if message_id is None:
                    message_id = str(uuid4())
                    sequence = 0
                sequence += 1
                yield {
                    "type": "message_delta",
                    "message_id": message_id,
                    "text": _field(nested, "delta") or "",
                    "sequence": sequence,
                }
            continue

        if kind == "message_end" and is_assistant:
            event = _dump(raw)
            event["message_id"] = message_id or str(uuid4())
            yield event
            message_id = None
            sequence = 0
            continue

        yield _dump(raw)
