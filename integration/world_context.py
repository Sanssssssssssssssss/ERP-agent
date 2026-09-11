"""Pi message adapter for the MCP-independent Odoo World core."""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable

from pi_agent.messages import TextContent, ToolResultMessage

from odoo_runtime.world import WorldStore


def project_messages(world: WorldStore, messages: Iterable[Any]) -> list[Any]:
    """Keep tool pairing and the newest full result; fail open if projection receipts fail."""
    try:
        output = list(messages)
        groups: dict[str, list[tuple[int, ToolResultMessage, dict[str, Any]]]] = {}
        for index, message in enumerate(output):
            if not isinstance(message, ToolResultMessage):
                continue
            candidate = world.projection_candidate(message.tool_call_id, message.text)
            if candidate is not None:
                groups.setdefault(candidate["group_key"], []).append((index, message, candidate))
        compacted = []
        original_bytes = projected_bytes = 0
        for entries in groups.values():
            if len(entries) < 2:
                continue
            latest = entries[-1][2]
            for index, message, candidate in entries[:-1]:
                compact = json.dumps({
                    "success": True,
                    "world_projection": {
                        "kind": "identical_repeated_read",
                        "receipt_id": candidate["receipt_id"],
                        "same_as": latest["receipt_id"],
                        "result_sha256": candidate["result_sha256"],
                        "full_result_retained": True,
                    },
                }, ensure_ascii=False, separators=(",", ":"))
                output[index] = message.model_copy(update={"content": [TextContent(text=compact)]}, deep=True)
                original_bytes += len(message.text.encode())
                projected_bytes += len(compact.encode())
                compacted.append(message.tool_call_id)
        world.record_projection(compacted, original_bytes, projected_bytes)
    except Exception as exc:  # observation must not break the provider request
        try:
            world.mark_unhealthy("projection", exc)
        except Exception:
            try:
                world.disable_projection()
            except Exception:
                pass
        print(f"World projection disabled after receipt failure: {type(exc).__name__}", file=sys.stderr)
        return list(messages)
    return output
