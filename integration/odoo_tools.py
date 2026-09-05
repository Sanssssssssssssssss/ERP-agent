"""Keep the advertised AgentTools fixed while selecting and recording their route."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from odoo_mcp.odoo_client import READ_CALL_ID
from pi_agent.messages import TextContent
from pi_agent.tools import AgentToolResult
from pydantic_core import to_json

from odoo_runtime.actions import ACTION_TOOLS, NativeActions
from odoo_runtime.capabilities import CAPABILITY_TOOLS, NativeCapabilities
from odoo_runtime.reads import READ_RESPONSES, NativeReads, normalize_read_arguments
from odoo_runtime.world import SIDE_EFFECT_TOOLS, WorldStore


def _world_failed(world: WorldStore, operation: str, error: BaseException) -> None:
    try:
        world.mark_unhealthy(operation, error)
    except Exception:
        pass
    print(f"World {operation} failed open: {type(error).__name__}", file=sys.stderr)


def route_tools(tools, log_path: Path, native: NativeReads | None = None,
                world: WorldStore | None = None,
                actions: NativeActions | None = None,
                capabilities: NativeCapabilities | None = None,
                next_sequence: Callable[[], int] | None = None):
    """No MCP fallback on a native failure; business errors retain their envelope."""
    routed = []
    for tool in tools:
        name = tool.name.removeprefix("mcp_odoo_")
        direct_read = native is not None and name in READ_RESPONSES
        direct_action = actions is not None and name in ACTION_TOOLS
        capability_ready = capabilities is not None and name in CAPABILITY_TOOLS

        async def execute(call_id, arguments, signal=None, on_update=None,
                          *, tool=tool, name=name, capability_ready=capability_ready,
                          direct_read=direct_read, direct_action=direct_action):
            started = time.monotonic()
            # Knowledge indexing remains on MCP until Stage 6; every other
            # allowlisted background operation is native in Stage 5.
            direct_capability = capability_ready and not (
                name == "submit_async_task"
                and arguments.get("operation") == "index_knowledge"
            )
            direct = direct_read or direct_action or direct_capability
            event = {
                "tool_call_id": call_id, "tool": tool.name,
                "backend": "native" if direct else "mcp", "event": "start",
                "sequence": next_sequence() if next_sequence else None,
            }
            if capability_ready and not direct_capability:
                event["deferred_capability"] = "index_knowledge"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")
            token = READ_CALL_ID.set(call_id)
            observation = None
            side_effect_attempted = name in SIDE_EFFECT_TOOLS
            try:
                if world is not None and name in READ_RESPONSES:
                    requested_instance = arguments.get("instance")
                    identity = None
                    identity_available = True
                    if direct_read and (requested_instance is None or isinstance(requested_instance, str)):
                        try:
                            identity = native.identity_context(requested_instance)
                        except Exception as exc:
                            identity_available = False
                            _world_failed(world, "identity", exc)
                    if identity_available:
                        try:
                            observation = world.begin(call_id, name, dict(arguments), event["backend"], identity=identity)
                        except Exception as exc:
                            _world_failed(world, "begin", exc)
                if direct_read:
                    normalized = normalize_read_arguments(name, dict(arguments))
                    raw = await asyncio.to_thread(native.call, name, normalized)
                    structured = READ_RESPONSES[name].model_validate(raw).model_dump(
                        mode="json", by_alias=True
                    )
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str, indent=2).decode(),
                        details={"structuredContent": structured, "meta": None},
                    )
                elif direct_action:
                    raw = await asyncio.to_thread(actions.call, name, dict(arguments))
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str, indent=2).decode(),
                        details={"structuredContent": raw, "meta": None},
                    )
                elif direct_capability:
                    raw = await asyncio.to_thread(
                        capabilities.call, name, dict(arguments)
                    )
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str, indent=2).decode(),
                        details={"structuredContent": raw, "meta": None},
                    )
                else:
                    result = await tool.execute(call_id, arguments, signal, on_update)
                if name == "health_check":
                    # A0: keep process-local counters in receipts, not model context.
                    # Policy/permission fields remain visible and unchanged in both arms.
                    with log_path.with_name("health-process-telemetry.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"tool_call_id": call_id, "result": result.model_dump(mode="json")}) + "\n")
                    result = result.model_copy(deep=True)
                    payload = json.loads(result.text)
                    for data in (payload, (result.details or {}).get("structuredContent")):
                        if isinstance(data, dict):
                            data.get("runtime", {}).pop("n_plus_one", None)
                            for key in ("busiest", "over_budget_totals"):
                                data.get("rate_limits", {}).pop(key, None)
                    result.content = [TextContent(text=to_json(payload, fallback=str, indent=2).decode())]
                event["result_sha256"] = hashlib.sha256(result.text.encode()).hexdigest()
                if observation is not None:
                    completed = observation
                    observation = None
                    try:
                        metadata = native.world_metadata(name, normalized) if direct else {}
                        evidence = native.world_rpc_evidence(call_id) if direct else None
                        world.finish(
                            completed, result.text, field_metadata=metadata,
                            rpc_evidence=evidence,
                        )
                    except Exception as exc:
                        _world_failed(world, "finish", exc)
                if direct_read:
                    try:
                        event["native_telemetry"] = native.telemetry()
                    except Exception as exc:
                        print(f"Native telemetry unavailable: {type(exc).__name__}", file=sys.stderr)
                if world is not None:
                    try:
                        event["world_telemetry"] = world.telemetry()
                    except Exception as exc:
                        _world_failed(world, "telemetry", exc)
                return result
            except BaseException as exc:
                if observation is not None:
                    failed = observation
                    observation = None
                    try:
                        world.finish(failed, error=exc)
                    except Exception as receipt_exc:
                        _world_failed(world, "finish_error", receipt_exc)
                event["error_type"] = type(exc).__name__
                raise
            finally:
                if side_effect_attempted:
                    invalidated_reads = native or (actions.reads if actions is not None else None)
                    if invalidated_reads is not None:
                        try:
                            for runtime in set(invalidated_reads.instances.values()):
                                runtime.invalidate_schema()
                        except Exception as exc:
                            print(f"Native schema invalidation failed: {type(exc).__name__}", file=sys.stderr)
                    if world is not None:
                        try:
                            world.invalidate(instance=arguments.get("instance"), reason="side-effect attempt or ambiguous result", call_id=call_id)
                        except Exception as exc:
                            _world_failed(world, "invalidation", exc)
                READ_CALL_ID.reset(token)
                event.update(
                    event="end",
                    elapsed_seconds=time.monotonic() - started,
                    end_sequence=next_sequence() if next_sequence else None,
                )
                try:
                    with log_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(event) + "\n")
                except OSError:
                    print("Tool completion receipt could not be saved", file=sys.stderr)

        routed.append(replace(
            tool,
            execute_fn=execute,
            execution_mode="sequential" if name in ACTION_TOOLS else tool.execution_mode,
        ))
    if native is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        if not READ_RESPONSES.keys() <= present:
            raise RuntimeError("MCP discovery is missing a required native read tool")
    if actions is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        if not ACTION_TOOLS <= present:
            raise RuntimeError("MCP discovery is missing a required native action tool")
    if capabilities is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        if not CAPABILITY_TOOLS <= present:
            raise RuntimeError("MCP discovery is missing a required native capability tool")
    return routed
