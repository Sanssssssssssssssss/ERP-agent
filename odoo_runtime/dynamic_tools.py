"""Deterministic next-turn Odoo tool publication."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from inspect import isawaitable
from pathlib import Path
from typing import Any

from pi_agent.tools import AgentTool, AgentToolResult

BASE_TOOLS = frozenset(
    {
        "health_check",
        "get_odoo_profile",
        "list_instances",
        "list_models",
        "get_model_fields",
        "build_domain",
        "search_records",
        "read_record",
        "aggregate_records",
        "get_current_time",
        "list_odoo_sops",
        "get_odoo_sop",
    }
)

CAPABILITY_GROUPS = {
    "actions": {
        "description": "Preview, validate, execute, and verify Odoo mutations.",
        "tools": [
            "preview_write",
            "validate_write",
            "execute_approved_write",
            "chatter_post",
            "execute_method",
        ],
        "required_models": [],
    },
    "accounting": {
        "description": "Receivable/payable aging and accounting health summaries.",
        "tools": ["receivable_payable_aging", "accounting_health_summary"],
        "required_models": ["account.move"],
    },
    "attachments": {
        "description": "Read bounded Odoo attachment metadata or content.",
        "tools": ["read_attachment"],
        "required_models": [],
    },
    "async_reads": {
        "description": "Submit, inspect, cancel, and list allowlisted read-only background tasks.",
        "tools": [
            "submit_async_task",
            "get_async_task",
            "cancel_async_task",
            "list_async_tasks",
        ],
        "required_models": [],
    },
    "cross_instance": {
        "description": "Search, aggregate, and compare health across configured Odoo instances.",
        "tools": [
            "search_across_instances",
            "aggregate_across_instances",
            "accounting_health_across_instances",
        ],
        "required_models": [],
    },
    "diagnostics": {
        "description": "Diagnose calls, access, relationships, data quality, fit gaps, and business packs.",
        "tools": [
            "data_quality_report",
            "diagnose_odoo_call",
            "inspect_model_relationships",
            "diagnose_access",
            "fit_gap_report",
            "business_pack_report",
            "schema_catalog",
        ],
        "required_models": [],
    },
    "employee": {
        "description": "Find employees with bounded native lookup.",
        "tools": ["search_employee"],
        "required_models": ["hr.employee"],
    },
    "knowledge": {
        "description": "Index, search, and inspect the current MCP-backed knowledge store; native migration is Stage 6.",
        "tools": ["index_knowledge", "search_knowledge", "knowledge_stats"],
        "required_models": [],
    },
    "migration": {
        "description": "Plan JSON-2 migration and inspect upgrade or addon risks.",
        "tools": [
            "generate_json2_payload",
            "upgrade_risk_report",
            "analyze_upgrade_log",
            "lookup_model_history",
            "scan_addons_source",
        ],
        "required_models": [],
    },
    "time_off": {
        "description": "Search employee leave and holiday records.",
        "tools": ["search_holidays"],
        "required_models": ["hr.leave.report.calendar"],
    },
}


def _base_name(name: str) -> str:
    return name.removeprefix("mcp_odoo_")


def tool_contract_sha256(contracts: Sequence[Mapping[str, Any]]) -> str:
    return sha256(
        json.dumps(
            list(contracts), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class DynamicToolController:
    """Keep a small base visible and publish requested groups next turn."""

    def __init__(
        self,
        tools: Sequence[AgentTool],
        log_path: Path,
        next_sequence: Callable[[], int],
    ) -> None:
        self._all = tuple(tools)
        names = [_base_name(tool.name) for tool in self._all]
        available = set(names)
        required = BASE_TOOLS | {
            name for group in CAPABILITY_GROUPS.values() for name in group["tools"]
        }
        missing = sorted(required - available)
        unmanaged = sorted(available - required)
        duplicates = sorted(name for name, total in Counter(names).items() if total > 1)
        if missing or unmanaged or duplicates:
            raise RuntimeError(
                "Invalid dynamic tool inventory: "
                f"missing={missing}, unmanaged={unmanaged}, duplicates={duplicates}"
            )
        self._log_path = log_path
        self._next_sequence = next_sequence
        self._publisher: Callable[[Sequence[AgentTool]], None] | None = None
        self._active: tuple[str, ...] = ()
        self._availability: dict[str, dict[str, Any]] | None = None
        self._controls = self._build_controls()

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        selected = BASE_TOOLS | {
            name
            for group_id in self._active
            for name in CAPABILITY_GROUPS[group_id]["tools"]
        }
        return (
            *(tool for tool in self._all if _base_name(tool.name) in selected),
            *self._controls,
        )

    def bind(self, publisher: Callable[[Sequence[AgentTool]], None]) -> None:
        self._publisher = publisher

    def _log(self, event: dict[str, Any]) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")

    async def _installed_models(self, call_id: str) -> set[str] | None:
        required = sorted(
            {
                model
                for group in CAPABILITY_GROUPS.values()
                for model in group["required_models"]
            }
        )
        search = next(
            tool for tool in self._all if _base_name(tool.name) == "search_records"
        )
        result = await search.execute(
            f"{call_id}:model-probe",
            {
                "model": "ir.model",
                "domain": [["model", "in", required]],
                "fields": ["model"],
                "limit": len(required),
            },
        )
        details = result.details if isinstance(result.details, dict) else {}
        payload = details.get("structuredContent", details)
        if not isinstance(payload, dict) or payload.get("success") is False:
            return None
        rows = payload.get("result")
        if not isinstance(rows, list):
            return None
        return {
            row["model"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("model"), str)
        }

    async def _list(self, call_id: str) -> dict[str, Any]:
        try:
            installed = await self._installed_models(call_id)
        except Exception:  # noqa: BLE001 - availability stays unknown on any probe failure
            installed = None
        groups = []
        availability = {}
        for group_id, spec in CAPABILITY_GROUPS.items():
            required = spec["required_models"]
            missing = [] if installed is None else sorted(set(required) - installed)
            status = (
                "unknown"
                if installed is None and required
                else "module_missing"
                if missing
                else "available"
            )
            row = {
                "id": group_id,
                "description": spec["description"],
                "tools": list(spec["tools"]),
                "required_models": list(required),
                "status": status,
                "missing_models": missing,
                "authorization": "checked_at_call",
                "active": group_id in self._active,
            }
            groups.append(row)
            availability[group_id] = row
        self._availability = availability
        return {
            "success": True,
            "tool": "list_odoo_capabilities",
            "active": list(self._active),
            "capabilities": groups,
            "notice": "Configure the complete desired optional set. Changes appear next model turn.",
        }

    def _configure(self, requested: Any) -> dict[str, Any]:
        if self._availability is None:
            return {"success": False, "error": "Call list_odoo_capabilities first."}
        if not isinstance(requested, list) or any(
            not isinstance(item, str) for item in requested
        ):
            return {
                "success": False,
                "error": "capabilities must be an array of strings.",
            }
        if len(requested) != len(set(requested)):
            return {
                "success": False,
                "error": "capabilities must not contain duplicates.",
            }
        unknown = sorted(set(requested) - set(CAPABILITY_GROUPS))
        blocked = sorted(
            group_id
            for group_id in requested
            if group_id in self._availability
            and self._availability[group_id]["status"] == "module_missing"
        )
        if unknown or blocked:
            return {
                "success": False,
                "error": "Invalid dynamic tool selection.",
                "unknown": unknown,
                "module_missing": blocked,
            }
        if self._publisher is None:
            raise RuntimeError("Dynamic tool controller is not bound to a session")
        before = {tool.name for tool in self.tools}
        self._active = tuple(requested)
        published = self.tools
        self._publisher(published)
        after = {tool.name for tool in published}
        contracts = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            }
            for tool in published
        ]
        return {
            "success": True,
            "tool": "configure_odoo_tools",
            "active": list(self._active),
            "added": sorted(after - before),
            "removed": sorted(before - after),
            "published_tools": [tool.name for tool in published],
            "tool_contract_sha256": tool_contract_sha256(contracts),
            "available_next_turn": True,
        }

    async def _execute(self, name: str, call_id: str, build) -> AgentToolResult:
        started = self._next_sequence()
        self._log(
            {
                "event": "start",
                "tool": name,
                "tool_call_id": call_id,
                "sequence": started,
            }
        )
        try:
            payload = build()
            if isawaitable(payload):
                payload = await payload
        except Exception as exc:
            self._log(
                {
                    "event": "end",
                    "tool": name,
                    "tool_call_id": call_id,
                    "sequence": started,
                    "end_sequence": self._next_sequence(),
                    "success": False,
                    "error_type": type(exc).__name__,
                }
            )
            raise
        self._log(
            {
                "event": "end",
                "tool": name,
                "tool_call_id": call_id,
                "sequence": started,
                "end_sequence": self._next_sequence(),
                "success": payload.get("success") is True,
                "active": payload.get("active"),
                "published_tools": payload.get("published_tools"),
                "tool_contract_sha256": payload.get("tool_contract_sha256"),
            }
        )
        return AgentToolResult(content=json.dumps(payload, indent=2), details=payload)

    def _build_controls(self) -> tuple[AgentTool, AgentTool]:
        async def list_tool(call_id, _arguments, _signal=None, _on_update=None):
            return await self._execute(
                "list_odoo_capabilities", call_id, lambda: self._list(call_id)
            )

        async def configure_tool(call_id, arguments, _signal=None, _on_update=None):
            return await self._execute(
                "configure_odoo_tools",
                call_id,
                lambda: self._configure(arguments.get("capabilities")),
            )

        return (
            AgentTool(
                name="list_odoo_capabilities",
                label="List Odoo capabilities",
                description="Discover deterministic Odoo tool groups and installed-model availability.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                execute_fn=list_tool,
                execution_mode="sequential",
            ),
            AgentTool(
                name="configure_odoo_tools",
                label="Configure Odoo tools",
                description="Replace the optional Odoo capability groups published on the next model turn.",
                parameters={
                    "type": "object",
                    "properties": {
                        "capabilities": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(CAPABILITY_GROUPS),
                            },
                            "uniqueItems": True,
                        },
                    },
                    "required": ["capabilities"],
                    "additionalProperties": False,
                },
                execute_fn=configure_tool,
                execution_mode="sequential",
            ),
        )


__all__ = [
    "BASE_TOOLS",
    "CAPABILITY_GROUPS",
    "DynamicToolController",
    "tool_contract_sha256",
]
