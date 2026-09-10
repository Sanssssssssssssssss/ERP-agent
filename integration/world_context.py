"""Pi message adapter for the MCP-independent Odoo World core."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any, Iterable

from pi_agent.messages import AssistantMessage, TextContent, ToolResultMessage

from odoo_runtime.world import READ_TOOLS, WorldStore

_TABLE_MARKERS = {"__world_table__", "__world_schema_table__"}


def _native_tool_name(name: str) -> str:
    return name.removeprefix("mcp_odoo_")


def _contains_table_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_TABLE_MARKERS.intersection(value)) or any(
            _contains_table_marker(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_table_marker(item) for item in value)
    return False


def _encode_tables(value: Any) -> tuple[Any, bool]:
    if isinstance(value, list):
        if _contains_table_marker(value):
            return value, False
        children = [_encode_tables(item) for item in value]
        encoded = [item for item, _changed in children]
        if (len(value) >= 2 and all(isinstance(item, dict) for item in encoded)
                and all("__world_table__" not in item for item in encoded)):
            columns = list(encoded[0])
            if columns and all(set(item) == set(columns) for item in encoded):
                table = {
                    "__world_table__": True,
                    "columns": columns,
                    "rows": [[item[column] for column in columns] for item in encoded],
                }
                if len(json.dumps(table, ensure_ascii=False, separators=(",", ":")).encode()) < len(
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
                ):
                    return table, True
        return encoded, any(changed for _item, changed in children)
    if isinstance(value, dict):
        result = {}
        changed = False
        for key, item in value.items():
            result[key], item_changed = _encode_tables(item)
            changed |= item_changed
        return result, changed
    return value, False


def _schema_table(value: Any) -> tuple[Any, bool]:
    if _contains_table_marker(value):
        return value, False
    if not isinstance(value, dict) or len(value) < 2 or not all(
        isinstance(metadata, dict) for metadata in value.values()
    ):
        return value, False
    names = list(value)
    metadata = [value[name] for name in names]
    columns = [key for key in metadata[0] if all(key in item for item in metadata)]
    if not columns:
        return value, False
    table = {
        "__world_schema_table__": True,
        "fields": names,
        "columns": columns,
        "rows": [[item[column] for column in columns] for item in metadata],
        "extras": [
            {key: item[key] for key in item if key not in columns}
            for item in metadata
        ],
    }
    if len(json.dumps(table, ensure_ascii=False, separators=(",", ":")).encode()) >= len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ):
        return value, False
    return table, True


def expand_lossless_tables(value: Any) -> Any:
    """Decode provider-view tables for deterministic round-trip verification."""
    if isinstance(value, list):
        return [expand_lossless_tables(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("__world_table__") is True:
        columns, rows = value.get("columns"), value.get("rows")
        if (isinstance(columns, list) and isinstance(rows, list)
                and all(isinstance(row, list) and len(row) == len(columns) for row in rows)):
            return [
                {column: expand_lossless_tables(row[index]) for index, column in enumerate(columns)}
                for row in rows
            ]
    if value.get("__world_schema_table__") is True:
        names, columns, rows, extras = (
            value.get("fields"), value.get("columns"), value.get("rows"), value.get("extras")
        )
        if (isinstance(names, list) and isinstance(columns, list) and isinstance(rows, list)
                and isinstance(extras, list) and len(names) == len(rows) == len(extras)
                and all(isinstance(row, list) and len(row) == len(columns) for row in rows)
                and all(isinstance(extra, dict) for extra in extras)):
            return {
                name: {
                    **{column: expand_lossless_tables(row[index]) for index, column in enumerate(columns)},
                    **expand_lossless_tables(extra),
                }
                for name, row, extra in zip(names, rows, extras)
            }
    return {key: expand_lossless_tables(item) for key, item in value.items()}


def _table_projection(
    payload: Any, *, source_text: str, receipt_id: str, result_sha256: str,
    schema: bool = False,
) -> str | None:
    """Encode only result rows, preserving the complete response envelope."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    if _contains_table_marker(payload):
        return None
    result, changed = (
        _schema_table(payload.get("result")) if schema else _encode_tables(payload.get("result"))
    )
    if not changed:
        return None
    projected = dict(payload)
    projected["result"] = result
    projected["world_projection"] = {
        "kind": "lossless_table",
        "receipt_id": receipt_id,
        "result_sha256": result_sha256,
        "result_encoding": "columns_and_rows_values",
        "rows_are_in_original_order": True,
        "full_result_retained": True,
    }
    text = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    return text if len(text.encode()) < len(source_text.encode()) else None


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


def project_read_history(world: WorldStore, messages: Iterable[Any]) -> list[Any]:
    """Project old consumed reads while retaining two recent observation rounds."""
    original = list(messages)
    try:
        if not world.telemetry().get("projection_enabled", True):
            return original
        output = list(original)
        consumed_after = [0] * len(original)
        assistant_seen = 0
        for index in range(len(original) - 1, -1, -1):
            message = original[index]
            if (isinstance(message, AssistantMessage)
                    and message.stop_reason not in {"error", "aborted"}):
                assistant_seen += 1
            elif isinstance(message, ToolResultMessage):
                consumed_after[index] = assistant_seen
        compacted: list[str] = []
        original_bytes = projected_bytes = 0
        for index, message in enumerate(original):
            if (not consumed_after[index] or not isinstance(message, ToolResultMessage)
                    or message.is_error or _native_tool_name(message.tool_name) not in READ_TOOLS):
                continue
            receipt = world.receipt_for_call(message.tool_call_id)
            if (not receipt or not receipt.get("outcome", {}).get("success")
                    or receipt.get("result_sha256") != hashlib.sha256(
                        message.text.encode()).hexdigest()):
                continue
            try:
                payload = json.loads(message.text)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or "world_projection" in payload:
                continue
            compact = None
            # Keep recently consumed observations in the provider context.  Older
            # large reads can be recalled by identity-scoped reference; this is a
            # display policy, not a limit on tools or model turns.
            if consumed_after[index] > 2:
                reference = world.artifact_reference(message.tool_call_id, message.text)
                if reference is not None:
                    candidate = {
                        "success": True,
                        "world_observation": {
                            "kind": "externalized_read",
                            **reference,
                            "recall_tools": ["read_observation", "search_observations"],
                            "historical_notice": (
                                "This is a historical read snapshot. Refresh Odoo when "
                                "current state matters after a write."
                            ),
                        },
                    }
                    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                    if len(encoded.encode()) < len(message.text.encode()):
                        compact = encoded
                elif world.observation_integrity(message.tool_call_id) == "corrupt":
                    # The receipt cannot prove the stored model-visible payload;
                    # retain the original provider message rather than substituting it.
                    continue
            if compact is None:
                compact = _table_projection(
                    payload, source_text=message.text,
                    receipt_id=receipt["receipt_id"], result_sha256=receipt["result_sha256"],
                    schema=_native_tool_name(message.tool_name) == "get_model_fields",
                )
            if compact is None:
                continue
            output[index] = message.model_copy(update={"content": [TextContent(text=compact)]}, deep=True)
            original_bytes += len(message.text.encode())
            projected_bytes += len(compact.encode())
            compacted.append(message.tool_call_id)
        world.record_projection(compacted, original_bytes, projected_bytes)
        return output
    except Exception as exc:  # observation must not break the provider request
        try:
            world.mark_unhealthy("history_projection", exc)
        except Exception:
            try:
                world.disable_projection()
            except Exception:
                pass
        print(f"World history projection disabled after receipt failure: {type(exc).__name__}", file=sys.stderr)
        return original
