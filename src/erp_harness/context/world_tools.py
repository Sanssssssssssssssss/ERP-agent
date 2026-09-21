"""Bounded, identity-scoped recall of WorldStore observation receipts."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from erp_harness.runtime.tools import AgentTool, AgentToolResult

from erp_harness.context.world import WorldStore


def _result(payload: dict[str, Any]) -> AgentToolResult:
    return AgentToolResult(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")), details=payload)


def build_world_tools(
    world: WorldStore, *, identity_context: Callable[[str | None], dict[str, Any]] | None = None,
) -> tuple[AgentTool, AgentTool]:
    """Expose only the current credential scope; receipts remain local artifacts."""

    async def search(call_id, arguments: dict[str, Any], _signal=None, _on_update=None):
        try:
            identity = (identity_context or world.identity)(arguments.get("instance"))
            page = world.search_observations(
                identity, query=arguments.get("query"), tool=arguments.get("tool"),
                model=arguments.get("model"), cursor=arguments.get("cursor", 0),
                limit=arguments.get("limit", 20),
            )
            return _result({
                "success": True, "tool": "search_observations",
                "acl": {"identity_id": identity["identity_id"], "instance": identity["instance"]},
                **page,
            })
        except (TypeError, ValueError) as exc:
            return _result({"success": False, "tool": "search_observations", "error": str(exc)})

    async def read(call_id, arguments: dict[str, Any], _signal=None, _on_update=None):
        try:
            identity = (identity_context or world.identity)(arguments.get("instance"))
            page = world.read_observation(
                identity, arguments["observation_ref"], path=arguments.get("path"),
                query=arguments.get("query"), cursor=arguments.get("cursor", 0),
                limit=arguments.get("limit", 20), fields=arguments.get("fields"),
            )
            return _result({"success": True, "tool": "read_observation", **page})
        except KeyError as exc:
            return _result({"success": False, "tool": "read_observation", "error": str(exc), "error_class": "unknown_reference"})
        except PermissionError as exc:
            return _result({"success": False, "tool": "read_observation", "error": str(exc), "error_class": "access"})
        except (TypeError, ValueError) as exc:
            kind = "invalid_path" if "path" in str(exc).lower() else "invalid_request"
            return _result({"success": False, "tool": "read_observation", "error": str(exc), "error_class": kind})

    common = {
        "instance": {"type": "string", "description": "Configured Odoo instance; defaults to the current instance."},
    }
    search_paging = {
        "cursor": {"type": "integer", "minimum": 0, "description": "Zero-based observation offset."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Observation page size; defaults to 20."},
    }
    read_paging = {
        "cursor": {"type": "integer", "minimum": 0, "description": "Zero-based page offset, or character offset for strings."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 4096, "description": "Rows or object fields accept 1..100; strings accept a 1..4096 character chunk. Defaults to 20."},
    }
    return (
        AgentTool(
            name="search_observations", label="Search stored observations",
            description="Search bounded summaries of prior successful Odoo reads for the current identity. Returns receipt references; does not query Odoo.",
            parameters={
                "type": "object", "properties": {
                    **common, **search_paging,
                    "query": {"type": "string", "description": "Literal keyword over observation metadata and stored result text."},
                    "tool": {"type": "string", "description": "Original read tool name without the mcp_odoo_ prefix."},
                    "model": {"type": "string", "description": "Original Odoo model."},
                }, "additionalProperties": False,
            }, execute_fn=search, execution_mode="sequential",
        ),
        AgentTool(
            name="read_observation", label="Read stored observation",
            description="Read a bounded, identity-scoped portion of a stored model-visible Odoo read. Historical data may be stale after writes; refresh Odoo when current state matters.",
            parameters={
                "type": "object", "properties": {
                    **common, **read_paging,
                    "observation_ref": {"type": "string", "description": "Reference returned by search_observations."},
                    "path": {"type": "string", "description": "JSON path such as $.result or $.result.0.name. Defaults to $.result; $ returns only a child directory."},
                    "query": {"type": "string", "description": "Keyword filter before pagination."},
                    "fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to retain from object rows."},
                }, "required": ["observation_ref"], "additionalProperties": False,
            }, execute_fn=read, execution_mode="sequential",
        ),
    )
