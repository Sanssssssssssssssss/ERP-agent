"""Run one Pi CodingSession with the fixed Odoo tool contract."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from pi_agent.messages import AssistantMessage, ToolResultMessage
from pi_agent.session import JsonlSessionStorage
from pi_agent.session.entries import CompactionEntry, MessageEntry
from pi_agent.tools import AgentTool, AgentToolResult
from pi_ai.env import OpenAICompatibleConfig
from pi_ai.openai_compatible import OpenAICompatibleProvider
from pi_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderModelMetadata,
    ProviderSettings,
)
from pi_coding.paths import PiPaths
from pi_coding.resources import PiResourcePaths
from pi_coding.session import CodingSession, CodingSessionConfig

from integration.odoo_tools import native_tool_catalog, route_tools
from integration.stream_events import public_events
from integration.world_context import project_messages, project_read_history
from odoo_runtime.actions import NativeActions
from odoo_runtime.capabilities import NativeCapabilities
from odoo_runtime.dynamic_tools import CAPABILITY_GROUPS, DynamicToolController
from odoo_runtime.reads import NativeReads
from odoo_runtime.sops import build_sop_tools
from odoo_runtime.store import ActionStore
from odoo_runtime.task_evidence import TaskEvidence
from odoo_runtime.world import WorldStore
from odoo_runtime.world_tools import build_world_tools

CONTEXT_WINDOW = 128_000
MODEL_COMPAT = {
    "supportsReasoningEffort": True,
    "requiresReasoningContentOnAssistantMessages": True,
}
MCP_ONLY_POLICY = (
    "Use mcp_odoo tools for every Odoo operation. Do not access Odoo through "
    "shell commands, direct HTTP, XML-RPC, JSON-2, PostgreSQL, or Python libraries."
)
SOP_POLICY = (
    " Before the first Odoo mutation, call list_odoo_sops and read the closest "
    "matching procedure with get_odoo_sop. A procedure is guidance, never write "
    "authorization; current tool and host policy still decide."
)
DYNAMIC_TOOL_POLICY = (
    " Use configure_odoo_tools directly when you know the needed capability groups; "
    "call list_odoo_capabilities when you need to discover group availability. "
    "Select the complete optional capability set needed for the task. A configured "
    "tool set appears on the next model turn; a same-response call to a newly "
    "selected tool is rejected."
)
BUSINESS_EXECUTION_POLICY = (
    " Determine the user-required scope and constraints before acting; for fulfillment, "
    "procurement, or manufacturing work, determine the supply-and-demand gap. Do not "
    "reinterpret established facts merely because adjacent records or another tool reveal "
    "more data. Reuse facts already read and their observation receipts when there is no "
    "new evidence; refresh Odoo only when current state is needed."
)
McpToolSet = None


def _usage_message_value(message: AssistantMessage, field: str) -> int | None:
    usage = getattr(message, "usage", None)
    if getattr(message, "stop_reason", None) in {"error", "aborted"}:
        fields = ("input", "cache_read", "cache_write", "output", "total_tokens", "reasoning")
        if usage is None or not any(getattr(usage, name, None) not in (None, 0) for name in fields):
            return None
    return getattr(usage, field, None) if usage is not None else None


def _nullable_usage_sum(messages: list[AssistantMessage], field: str) -> int | None:
    """Sum a reported usage bucket while preserving zero and missing values."""
    if not messages:
        return None
    values = [_usage_message_value(message, field) for message in messages]
    if any(value is None for value in values):
        return None
    return sum(values)


def _nullable_entry_usage_sum(entries: list[CompactionEntry], field: str) -> int | None:
    if not entries:
        return 0
    values = [getattr(entry.usage, field, None) if entry.usage is not None else None for entry in entries]
    if any(value is None for value in values):
        return None
    return sum(values)


def _validated_dynamic_selection(active: object) -> tuple[str, ...] | None:
    """Return a fail-closed, typed capability selection."""
    if not isinstance(active, list) or any(type(item) is not str for item in active):
        return None
    if len(active) != len(set(active)) or any(item not in CAPABILITY_GROUPS for item in active):
        return None
    return tuple(active)


def _tool_result_payload(message: ToolResultMessage) -> dict[str, object] | None:
    details = message.details
    if isinstance(details, dict):
        structured = details.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        return details
    try:
        payload = json.loads(message.text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _history_dynamic_selection(messages: object) -> tuple[str, ...] | None:
    """Recover only the latest typed successful configure result from history.

    A malformed latest successful result returns no selection instead of
    falling back to an older selection; failed configure calls remain
    non-authoritative.
    """
    if not isinstance(messages, (list, tuple)):
        return None
    for message in reversed(messages):
        if not isinstance(message, ToolResultMessage) or message.tool_name != "configure_odoo_tools":
            continue
        if message.is_error:
            continue
        payload = _tool_result_payload(message)
        if not isinstance(payload, dict):
            return None
        if payload.get("success") is False:
            continue
        if payload.get("success") is not True:
            return None
        return _validated_dynamic_selection(payload.get("active"))
    return None


def _receipt_dynamic_selection(path: Path) -> tuple[bool, tuple[str, ...] | None]:
    """Read the latest successful selection from this run's receipt only."""
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return False, None
    except (OSError, json.JSONDecodeError):
        return True, None
    for row in reversed(rows):
        if not isinstance(row, dict) or row.get("event") != "end" or row.get("tool") != "configure_odoo_tools":
            continue
        if row.get("success") is not True:
            continue
        return True, _validated_dynamic_selection(row.get("active"))
    return False, None


def _restore_dynamic_selection(dynamic_tools: DynamicToolController, session: CodingSession, dynamic_log: Path) -> None:
    receipt_found, active = _receipt_dynamic_selection(dynamic_log)
    if not receipt_found:
        # A new run keeps the business session transcript but has a new receipt
        # directory. Recover only a typed, successful configure result from
        # history; never copy approvals, action receipts, or arbitrary prose.
        active = _history_dynamic_selection(session.messages)
    if active is not None:
        dynamic_tools._active = active
        # The session was constructed with the base set before the selection
        # was read; publish it for the next provider turn.
        session.stage_tools_for_next_turn(dynamic_tools.tools)


async def _get_current_time(_call_id, _arguments, _signal=None, _on_update=None):
    local = datetime.now().astimezone()
    payload = {
        "success": True,
        "local_datetime": local.isoformat(),
        "local_date": local.date().isoformat(),
        "timezone": str(local.tzinfo),
        "utc_datetime": local.astimezone(UTC).isoformat(),
    }
    return AgentToolResult(content=json.dumps(payload, indent=2), details=payload)


CURRENT_TIME_TOOL = AgentTool(
    name="get_current_time",
    label="Current Time",
    description=(
        "Read the current runtime date and time, including the local timezone and UTC. "
        "Use this to anchor relative dates and deadlines."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    execute_fn=_get_current_time,
)


@asynccontextmanager
async def _source_tools(args):
    if getattr(args, "runtime_mode", "mcp") == "native":
        yield native_tool_catalog()
        return
    toolset_class = McpToolSet
    if toolset_class is None:
        from pi_agent.mcp import McpToolSet as toolset_class
    async with toolset_class(args.mcp_url) as toolset:
        yield toolset.tools


class RequestReceipts:
    """Keep actual model request bodies locally; never persist auth headers."""

    def __init__(
        self, directory: Path, max_model_requests: int | None = None
    ) -> None:
        if max_model_requests is not None and (
            type(max_model_requests) is not int or max_model_requests < 1
        ):
            raise ValueError("max_model_requests must be a positive integer")
        self.directory = directory
        self.max_model_requests = max_model_requests
        # A resumed worker shares the receipt directory with the initial
        # worker.  Continue numbering so a continuation never overwrites the
        # request that established the pending action.
        existing = [
            int(path.stem.split(".", 1)[0])
            for path in directory.glob("*.request.json")
            if path.stem.split(".", 1)[0].isdigit()
        ]
        self.number = max(existing, default=0)

    async def before_provider_request(self, payload: object) -> object:
        if (
            self.max_model_requests is not None
            and self.number >= self.max_model_requests
        ):
            raise RuntimeError(
                f"max_model_requests ({self.max_model_requests}) exceeded"
            )
        self.number += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.number:04d}.request.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    async def before_provider_headers(self, headers: dict) -> dict:
        return headers

    async def after_provider_response(self, status: int, headers: dict) -> None:
        (self.directory / f"{self.number:04d}.response.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "request_ids": {
                        key: value
                        for key, value in headers.items()
                        if key.lower()
                        in {"x-request-id", "request-id", "x-generation-id"}
                    },
                }
            ),
            encoding="utf-8",
        )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruction-file", type=Path, required=True)
    parser.add_argument("--usage-file", type=Path, required=True)
    parser.add_argument(
        "--session-file",
        type=Path,
        default=Path("/logs/agent/pi-agent-session.jsonl"),
    )
    parser.add_argument("--receipt-dir", type=Path, default=None)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--runtime-mode", choices=("mcp", "native"), default="mcp")
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--max-model-requests", type=int, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--read-backend", choices=("mcp", "native"), default="mcp")
    parser.add_argument("--action-backend", choices=("mcp", "native"), default="mcp")
    parser.add_argument("--capability-backend", choices=("mcp", "native"), default="mcp")
    parser.add_argument("--sop-mode", choices=("off", "controlled"), default="off")
    parser.add_argument("--tool-mode", choices=("static", "dynamic"), default="static")
    parser.add_argument("--world-mode", choices=("off", "record", "project"), default="off")
    parser.add_argument("--continue-run", action="store_true")
    parser.add_argument("--pause-on-approval", action="store_true")
    return parser.parse_args()


def _approval_required(result: object) -> bool:
    """Read the native approval marker without coupling the runner to a tool."""
    def pause_status(value: object) -> bool:
        if isinstance(value, str):
            return value in {"pending_approval", "needs_reconciliation"}
        if isinstance(value, dict):
            return pause_status(value.get("status"))
        return False

    details = getattr(result, "details", None)
    if details is None and isinstance(result, dict):
        details = result.get("details", result)
    if hasattr(details, "model_dump"):
        details = details.model_dump()
    if isinstance(details, dict):
        structured = details.get("structuredContent", details)
        if isinstance(structured, dict):
            if structured.get("approval_required") is True:
                return True
            for key in ("approval_status", "action_status"):
                marker = structured.get(key)
                if pause_status(marker):
                    return True
            if pause_status(structured.get("status")):
                return True
    structured = getattr(result, "structuredContent", None)
    return (
        isinstance(structured, dict)
        and (
            structured.get("approval_required") is True
            or pause_status(structured.get("status"))
            or pause_status(structured.get("action_status"))
            or pause_status(structured.get("approval_status"))
        )
    )


def _next_receipt_sequence(directory: Path):
    """Continue the shared tool chronology across approval worker restarts."""
    latest = 0
    for name in ("tool-backends.jsonl", "sop-events.jsonl", "dynamic-tools.jsonl"):
        path = directory / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for key in ("sequence", "end_sequence"):
                value = row.get(key)
                if type(value) is int:
                    latest = max(latest, value)
    return count(latest + 1).__next__


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL")
    if not api_key or not base_url or not model:
        raise RuntimeError("LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL are required")
    if args.max_turns is not None and args.max_turns < 1:
        raise ValueError("max-turns must be positive")
    max_model_requests = getattr(args, "max_model_requests", None)
    if max_model_requests is not None and (
        type(max_model_requests) is not int or max_model_requests < 1
    ):
        raise ValueError("max-model-requests must be a positive integer")
    max_output_tokens = getattr(args, "max_output_tokens", None)
    if max_output_tokens is not None and (
        type(max_output_tokens) is not int or max_output_tokens < 1
    ):
        raise ValueError("max-output-tokens must be a positive integer")
    budget_enabled = (
        max_model_requests is not None or max_output_tokens is not None
    )
    world_mode = getattr(args, "world_mode", "off")
    if world_mode not in {"off", "record", "project"}:
        raise ValueError("world-mode must be off, record, or project")
    sop_mode = getattr(args, "sop_mode", "off")
    if sop_mode not in {"off", "controlled"}:
        raise ValueError("sop-mode must be off or controlled")
    tool_mode = getattr(args, "tool_mode", "static")
    if tool_mode not in {"static", "dynamic"}:
        raise ValueError("tool-mode must be static or dynamic")
    if tool_mode == "dynamic" and sop_mode != "controlled":
        raise ValueError("dynamic tool mode requires controlled SOP mode")
    runtime_mode = getattr(args, "runtime_mode", "mcp")
    if runtime_mode == "native" and any(
        getattr(args, name, "mcp") != "native"
        for name in ("read_backend", "action_backend", "capability_backend")
    ):
        raise ValueError("native runtime mode requires every Odoo backend to be native")

    args.session_file.parent.mkdir(parents=True, exist_ok=True)
    args.usage_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_dir = getattr(args, "receipt_dir", None) or args.session_file.parent
    receipt_dir.mkdir(parents=True, exist_ok=True)
    provider_name = os.environ.get("LLM_PROVIDER", "openai-compatible")
    thinking = os.environ.get("LLM_THINKING_TYPE", "high")
    receipts = RequestReceipts(
        receipt_dir / "requests",
        max_model_requests=max_model_requests,
    )
    receipt_start_number = receipts.number
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=api_key,
            base_url=base_url,
            reasoning_effort=thinking,
            thinking_format="openai",
            compat=MODEL_COMPAT,
            provider_name=provider_name,
            timeout_seconds=None,
            max_retries=0,
            max_tokens=max_output_tokens,
            infer_api_from_model=False,
            provider_hooks=receipts,
        )
    )
    world = None
    actions = None
    capabilities = None
    dynamic_tools = None
    try:
        async with _source_tools(args) as source_tools:
            next_tool_sequence = _next_receipt_sequence(receipt_dir)
            native_runtime = None
            if (getattr(args, "read_backend", "mcp") == "native"
                    or getattr(args, "action_backend", "mcp") == "native"
                    or getattr(args, "capability_backend", "mcp") == "native"):
                os.environ["ODOO_REQUEST_LOG"] = str(receipt_dir / "odoo-native-requests.jsonl")
                os.environ["ODOO_REQUEST_BACKEND"] = "native"
                native_runtime = NativeReads.from_environment()
            native = native_runtime if getattr(args, "read_backend", "mcp") == "native" else None
            actions = (
                NativeActions(
                    native_runtime,
                    store=ActionStore(receipt_dir / "odoo-actions.sqlite3"),
                    # Bench high-reasoning turns can exceed the safe default between
                    # validation and execution; prestate is still rechecked before send.
                    approval_ttl_seconds=60 * 60,
                )
                if getattr(args, "action_backend", "mcp") == "native"
                else None
            )
            if actions is not None:
                actions.store.recover_interrupted()
                evidence_file = os.environ.get("ODOO_TASK_EVIDENCE_FILE")
                if evidence_file:
                    specification = json.loads(Path(evidence_file).read_text(encoding="utf-8"))
                    instruction_sha256 = hashlib.sha256(args.instruction_file.read_bytes()).hexdigest()
                    if specification.get("instruction_sha256") != instruction_sha256:
                        raise ValueError("host evidence is bound to a different instruction")
                    actions.task_evidence = TaskEvidence(native_runtime, specification, receipt_dir / "task-evidence.json")
            capabilities = (
                NativeCapabilities(
                    native_runtime,
                    task_path=receipt_dir / "capability-tasks.sqlite3",
                )
                if getattr(args, "capability_backend", "mcp") == "native"
                else None
            )
            if world_mode != "off":
                try:
                    world = WorldStore(
                        receipt_dir / "world-observations.jsonl",
                        projection_path=receipt_dir / "world-projections.jsonl",
                    )
                except Exception as exc:  # noqa: BLE001 - optional world state must fail open
                    print(f"World initialization failed open: {type(exc).__name__}", file=sys.stderr)
            full_tools = [
                *route_tools(
                    source_tools, receipt_dir / "tool-backends.jsonl",
                    native, world, actions, capabilities, next_tool_sequence,
                    native_health=runtime_mode == "native",
                ),
                CURRENT_TIME_TOOL,
                *(
                    build_sop_tools(
                        receipt_dir / "sop-events.jsonl",
                        next_tool_sequence,
                        read_locator="find_records" if runtime_mode == "native" else "search_records",
                    )
                    if sop_mode == "controlled"
                    else ()
                ),
                *(build_world_tools(
                    world,
                    identity_context=(native_runtime.identity_context if native_runtime is not None else None),
                ) if world is not None else ()),
            ]
            if tool_mode == "dynamic":
                dynamic_tools = DynamicToolController(
                    full_tools,
                    receipt_dir / "dynamic-tools.jsonl",
                    next_tool_sequence,
                )
                session_tools = list(dynamic_tools.tools)
            else:
                session_tools = full_tools
            cwd = Path.cwd()
            provider_config = OpenAICompatibleProviderConfig(
                name=provider_name,
                base_url=base_url,
                api_key_env="LLM_API_KEY",
                models=(model,),
                default_model=model,
                context_windows={model: CONTEXT_WINDOW},
                compat=MODEL_COMPAT,
                model_metadata={
                    model: ProviderModelMetadata(
                        reasoning=True,
                        context_window=CONTEXT_WINDOW,
                    )
                },
                timeout_seconds=None,
                max_retries=0,
                thinking_levels=(thinking,),
                thinking_models=(model,),
                thinking_default=thinking,
                thinking_parameter="reasoning_effort",
                thinking_defaults={model: thinking},
            )
            runtime_date = datetime.now().astimezone().date().isoformat()
            session = await CodingSession.load(
                CodingSessionConfig(
                    provider=provider,
                    owns_initial_provider=True,
                    model=model,
                    storage=JsonlSessionStorage(args.session_file),
                    cwd=cwd,
                    tools=list(session_tools),
                    max_turns=args.max_turns,
                    resource_paths=PiResourcePaths(
                        root=receipt_dir / ".pi-agent",
                        paths=PiPaths(
                            home=receipt_dir / ".pi-agent",
                            agents_home=receipt_dir / ".agents",
                        ),
                        agents_root=receipt_dir / ".agents",
                        project_resources_enabled=False,
                    ),
                    session_id=os.environ.get("PI_AGENT_SESSION_ID"),
                    provider_name=provider_name,
                    provider_settings=ProviderSettings(providers=(provider_config,)),
                    runtime_provider_config=provider_config,
                    skills_enabled=False,
                    extensions_enabled=False,
                    append_system_prompt=(
                        MCP_ONLY_POLICY
                        + BUSINESS_EXECUTION_POLICY
                        + (SOP_POLICY if sop_mode == "controlled" else "")
                        + (DYNAMIC_TOOL_POLICY if tool_mode == "dynamic" else "")
                    ),
                    auto_compact_enabled=not budget_enabled,
                    # Bench turns end at the final agent stop; avoid paying a
                    # post-stop summarizer call while retaining pre-prompt and
                    # provider-overflow compaction paths.
                    auto_compact_after_prompt_enabled=False,
                    retry_enabled=not budget_enabled,
                    thinking_level=thinking,
                )
            )
            if dynamic_tools is not None:
                # A paused worker is a fresh process.  Restore the controller's
                # last published set from its append-only receipt before the
                # continuation model turn is built.
                dynamic_log = receipt_dir / "dynamic-tools.jsonl"
                _restore_dynamic_selection(dynamic_tools, session, dynamic_log)
                dynamic_tools.bind(session.stage_tools_for_next_turn)
            if getattr(args, "pause_on_approval", False):
                async def stop_after_approval(turn):
                    return any(_approval_required(result) for result in turn.tool_results)

                # The hook runs after the tool result has been persisted and
                # before the next model request.  It therefore pauses at the
                # safe boundary without an extra paid request.
                session._harness.config.should_stop_after_turn = stop_after_approval
            if world is not None and (world_mode == "project" or runtime_mode == "native"):
                existing_transform = session._harness.config.transform_context

                async def project_context(messages, signal):
                    projected = (
                        project_messages(world, messages)
                        if world_mode == "project" else list(messages)
                    )
                    if runtime_mode == "native":
                        projected = project_read_history(world, projected)
                    transformed = existing_transform(projected, signal) if existing_transform else projected
                    return await transformed if inspect.isawaitable(transformed) else transformed

                # The session owns refresh; only compose its existing hook for this run.
                session._harness.config.transform_context = project_context
            try:
                system_prompt_path = args.session_file.with_name(
                    "pi-agent-system-prompt.txt"
                )
                system_prompt_path.write_text(session.system_prompt, encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "type": "run_metadata",
                            "entrant": (
                                "pi-agent-odoo-native"
                                if runtime_mode == "native"
                                else "pi-agent-odoo-mcp"
                            ),
                            "commit_sha": os.environ.get("PI_ODOO_SOURCE_COMMIT"),
                            "model": model,
                            "reasoning": thinking,
                            "mcpToolCount": (
                                0 if runtime_mode == "native" else len(source_tools)
                            ),
                            "odooToolCount": len(source_tools),
                            "runtimeMode": runtime_mode,
                            "readBackend": getattr(args, "read_backend", "mcp"),
                            "actionBackend": getattr(args, "action_backend", "mcp"),
                            "capabilityBackend": getattr(args, "capability_backend", "mcp"),
                            "sopMode": sop_mode,
                            "toolMode": tool_mode,
                            "worldMode": world_mode,
                            "runtimeDate": runtime_date,
                            "toolNames": [tool.name for tool in session.tools],
                            "maxOutputTokens": max_output_tokens,
                            "maxModelRequests": max_model_requests,
                            "requestReceipts": str(
                                receipt_dir / "requests"
                            ),
                            "maxTurns": args.max_turns,
                            "toolContractSha256": hashlib.sha256(json.dumps([
                                {"name": tool.name, "description": tool.description,
                                 "parameters": tool.parameters}
                                for tool in session_tools
                            ], sort_keys=True).encode()).hexdigest(),
                            "runtime": "CodingSession",
                            "sessionFile": str(args.session_file),
                            "systemPromptFile": str(system_prompt_path),
                            "systemPromptSha256": hashlib.sha256(
                                session.system_prompt.encode("utf-8")
                            ).hexdigest(),
                        }
                    ),
                    flush=True,
                )
                entries_before = await session.session_entries()
                entry_ids_before = {entry.id for entry in entries_before}
                if getattr(args, "continue_run", False) and getattr(args, "pause_on_approval", False):
                    # Persist the host notification in the same Pi session. The
                    # native action ledger remains the execution authority.
                    source = session.prompt(
                        "The desktop host has completed human approval of the pending actions. "
                        "Resume the existing business goal. Check durable action status and execute only "
                        "the approved actions; approval itself does not mean execution. "
                        "Do not repeat completed writes or request the same approval again.",
                        source="extension", custom_type="odoo_approval_resume",
                    )
                elif getattr(args, "continue_run", False):
                    source = session.continue_()
                else:
                    source = session.prompt(args.instruction_file.read_text(encoding="utf-8"))
                async for event in public_events(source):
                    print(json.dumps(event, ensure_ascii=False), flush=True)

                new_entries = [entry for entry in await session.session_entries()
                               if entry.id not in entry_ids_before]
                assistant = [entry.message for entry in new_entries
                             if isinstance(entry, MessageEntry)
                             and isinstance(entry.message, AssistantMessage)]
                compactions = [entry for entry in new_entries if isinstance(entry, CompactionEntry)]

                usage = {
                    "input": _nullable_usage_sum(assistant, "input"),
                    "output": _nullable_usage_sum(assistant, "output"),
                    "total": _nullable_usage_sum(assistant, "total_tokens"),
                    "cacheRead": _nullable_usage_sum(assistant, "cache_read"),
                    "cacheWrite": _nullable_usage_sum(assistant, "cache_write"),
                    "cacheWrite1H": _nullable_usage_sum(assistant, "cache_write_1h"),
                    "reasoning": _nullable_usage_sum(assistant, "reasoning"),
                    "compactionTotal": _nullable_entry_usage_sum(compactions, "total_tokens"),
                    "compactionInput": _nullable_entry_usage_sum(compactions, "input"),
                    "compactionCacheRead": _nullable_entry_usage_sum(compactions, "cache_read"),
                    "compactionCacheWrite": _nullable_entry_usage_sum(compactions, "cache_write"),
                    "compactionCacheWrite1H": _nullable_entry_usage_sum(compactions, "cache_write_1h"),
                    "compactionOutput": _nullable_entry_usage_sum(compactions, "output"),
                    "compactionReasoning": _nullable_entry_usage_sum(compactions, "reasoning"),
                    "compactionCalls": len(compactions),
                    "totalScope": "assistant_responses_only",
                    "inputSemantics": "uncached",
                    "modelCalls": receipts.number - receipt_start_number,
                    "maxOutputTokens": max_output_tokens,
                    "maxModelRequests": max_model_requests,
                    "assistantEntries": len(assistant),
                    "lastStopReason": assistant[-1].stop_reason if assistant else None,
                    "errorMessage": assistant[-1].error_message if assistant else None,
                    "unreportedUsageRequests": max(
                        0, receipts.number - receipt_start_number
                        - sum(_usage_message_value(message, "total_tokens") is not None
                              for message in assistant)
                        - sum(entry.usage is not None for entry in compactions)
                    ),
                    "worldMode": world_mode,
                    "actionBackend": getattr(args, "action_backend", "mcp"),
                    "capabilityBackend": getattr(args, "capability_backend", "mcp"),
                    "sopMode": sop_mode,
                    "toolMode": tool_mode,
                    "runtimeMode": runtime_mode,
                    "commitSha": os.environ.get("PI_ODOO_SOURCE_COMMIT"),
                }
                args.usage_file.write_text(json.dumps(usage), encoding="utf-8")
                if assistant and assistant[-1].stop_reason == "error":
                    raise RuntimeError(
                        "Provider run did not complete: "
                        + (assistant[-1].error_message or "unspecified provider error")
                    )
            finally:
                await session.aclose()
    finally:
        if actions is not None:
            try:
                summary = actions.store.summary()
                (receipt_dir / "action-ledger-summary.json").write_text(
                    json.dumps(summary, sort_keys=True), encoding="utf-8"
                )
            except Exception as exc:  # noqa: BLE001 - preserve the agent result on receipt failure
                print(f"Action ledger summary unavailable: {type(exc).__name__}", file=sys.stderr)
            finally:
                actions.store.close()
        if capabilities is not None:
            capabilities.close()
        if world is not None:
            try:
                world.write_summary(receipt_dir / "world-summary.json")
            except Exception as exc:  # noqa: BLE001 - derived receipts must not mask the run result
                print(f"Derived world summary unavailable: {type(exc).__name__}", file=sys.stderr)
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
