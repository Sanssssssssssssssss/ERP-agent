"""Keep the advertised AgentTools fixed while selecting and recording their route."""

from __future__ import annotations

# 工具边界：保留对模型公布的契约，绑定 NativeReads / NativeActions / Capabilities。
# mcp_odoo_ 是兼容名称。是否走原生实现取决于注入的后端。
# call_id 同时关联工具日志、Odoo RPC 和 World 观察回执。
# 读取结果分成模型可见值与审计证据。_runtime_evidence 不直接送入上下文。
# validate_write 返回持久化动作引用。执行时由账本恢复规范化 payload。
# 原生调用失败不切换 MCP 重做。可能有副作用的调用结束后使旧读取失效。

import asyncio
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from erp_harness.runtime.messages import TextContent
from erp_harness.runtime.tools import AgentTool, AgentToolResult
from pydantic_core import to_json

from erp_harness.erp._odoo_core.odoo_client import READ_CALL_ID
from erp_harness.erp.actions import ACTION_TOOLS, NativeActions
from erp_harness.erp.business_facts import BusinessFacts, attach_business_facts
from erp_harness.erp.capabilities import CAPABILITY_TOOLS, NativeCapabilities
from erp_harness.erp.reads import (
    NATIVE_READ_RESPONSES,
    READ_RESPONSES,
    NativeReads,
    _summarize_field_metadata,
    normalize_read_arguments,
)
from erp_harness.context.world import SIDE_EFFECT_TOOLS, WorldStore


async def _unrouted_native_tool(*_args, **_kwargs):
    raise RuntimeError("Native Odoo tool was not routed to its implementation")


def native_tool_catalog() -> list[AgentTool]:
    """Load the fixed Stage-6 contract without MCP discovery."""
    payload = json.loads(
        Path(__file__).with_name("native_tool_catalog.json").read_text(encoding="utf-8")
    )
    rows = payload.get("tools")
    if not isinstance(rows, list):
        raise RuntimeError("Native Odoo tool catalog is invalid")
    tools = [
        AgentTool(
            name=row["name"],
            label=row["label"],
            description=row["description"],
            parameters=row["parameters"],
            execute_fn=_unrouted_native_tool,
        )
        for row in rows
    ]
    names = [tool.name.removeprefix("mcp_odoo_") for tool in tools]
    expected = set(NATIVE_READ_RESPONSES) | set(ACTION_TOOLS) | set(CAPABILITY_TOOLS)
    if len(names) != len(set(names)) or set(names) != expected:
        raise RuntimeError("Native Odoo tool catalog does not match native capabilities")
    return tools


def _world_failed(world: WorldStore, operation: str, error: BaseException) -> None:
    try:
        world.mark_unhealthy(operation, error)
    except Exception:
        pass
    print(f"World {operation} failed open: {type(error).__name__}", file=sys.stderr)


def _model_visible_native_read(name: str, arguments: dict, raw: object) -> object:
    """Keep schema exploration compact while preserving exact explicit reads."""
    if name != "get_model_fields" or not isinstance(raw, dict):
        return raw
    if arguments.get("field_names"):
        return raw
    result = raw.get("result")
    if not isinstance(result, dict):
        return raw
    visible = dict(raw)
    visible["result"] = {
        field: (
            metadata
            if isinstance(metadata, dict)
            and "selection_count" in metadata
            and "selection" not in metadata
            else _summarize_field_metadata(metadata)
            if isinstance(metadata, dict)
            else metadata
        )
        for field, metadata in result.items()
    }
    if visible["result"] != result:
        visible["summary"] = True
    return visible


def _receipt_unavailable_result(
    result: AgentToolResult, *, fallback_payload: dict | None = None,
) -> AgentToolResult:
    """Tell the model when the World receipt could not be persisted."""
    visible = fallback_payload
    if visible is None:
        try:
            visible = json.loads(result.text)
        except (TypeError, json.JSONDecodeError):
            visible = {"success": False, "error": "World receipt unavailable"}
    visible = dict(visible) if isinstance(visible, dict) else {
        "result": visible,
    }
    visible["receipt_status"] = "unavailable"
    updated = result.model_copy(deep=True)
    updated.content = [TextContent(text=to_json(visible, fallback=str).decode())]
    details = dict(updated.details or {})
    structured = details.get("structuredContent")
    if fallback_payload is not None:
        structured = visible
    elif isinstance(structured, dict):
        structured = dict(structured)
        structured["receipt_status"] = "unavailable"
    else:
        structured = visible
    details["structuredContent"] = structured
    updated.details = details
    return updated


def route_tools(tools, log_path: Path, native: NativeReads | None = None,
                world: WorldStore | None = None,
                actions: NativeActions | None = None,
                capabilities: NativeCapabilities | None = None,
                next_sequence: Callable[[], int] | None = None, *,
                native_health: bool = False):
    """No MCP fallback on a native failure; business errors retain their envelope."""
    routed = []
    business_facts = BusinessFacts(actions) if actions is not None else None
    native_reads = NATIVE_READ_RESPONSES if native_health else READ_RESPONSES
    for tool in tools:
        name = tool.name.removeprefix("mcp_odoo_")
        # mcp_odoo_ 前缀保留固定工具契约；是否直连原生实现由下面的运行时依赖和工具集合决定。
        direct_read = native is not None and name in native_reads
        direct_action = actions is not None and name in ACTION_TOOLS
        capability_ready = capabilities is not None and name in CAPABILITY_TOOLS

        async def execute(call_id, arguments, signal=None, on_update=None,
                          *, tool=tool, name=name, capability_ready=capability_ready,
                          direct_read=direct_read, direct_action=direct_action):
            started = time.monotonic()
            direct_capability = capability_ready
            direct = direct_read or direct_action or direct_capability
            event = {
                "tool_call_id": call_id, "tool": tool.name,
                "backend": "native" if direct else "mcp", "event": "start",
                "sequence": next_sequence() if next_sequence else None,
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")
            token = READ_CALL_ID.set(call_id)
            observation = None
            source_raw = None
            runtime_evidence = None
            world_receipt_unavailable = False
            side_effect_attempted = name in SIDE_EFFECT_TOOLS
            try:
                # World 记录失败不阻断本次工具调用；会标记不健康并停止后续历史投影。
                if world is not None and (
                    name in READ_RESPONSES or (direct_read and name != "health_check")
                ):
                    requested_instance = arguments.get("instance")
                    identity = None
                    identity_available = True
                    if direct_read and (requested_instance is None or isinstance(requested_instance, str)):
                        try:
                            identity = native.identity_context(requested_instance)
                        except Exception as exc:
                            identity_available = False
                            world_receipt_unavailable = True
                            _world_failed(world, "identity", exc)
                    if identity_available:
                        try:
                            observation = world.begin(call_id, name, dict(arguments), event["backend"], identity=identity)
                        except Exception as exc:
                            world_receipt_unavailable = True
                            _world_failed(world, "begin", exc)
                if direct_read:
                    normalized = normalize_read_arguments(name, dict(arguments))
                    raw = await asyncio.to_thread(native.call, name, normalized)
                    source_raw = dict(raw) if isinstance(raw, dict) else raw
                    if isinstance(source_raw, dict):
                        runtime_evidence = source_raw.pop("_runtime_evidence", None)
                    # 字段发现结果可先压缩给模型；source_raw 另存入 receipt 供审计。
                    visible_raw = _model_visible_native_read(name, normalized, source_raw)
                    structured = native_reads[name].model_validate(visible_raw).model_dump(
                        mode="json", by_alias=True
                    )
                    if name == "read_record" and "missing_ids" not in visible_raw:
                        structured.pop("missing_ids", None)
                    result = AgentToolResult(
                        content=to_json(visible_raw, fallback=str).decode(),
                        details={"structuredContent": structured, "meta": None},
                    )
                elif direct_action:
                    raw = await asyncio.to_thread(actions.call, name, dict(arguments))
                    if business_facts is not None and name in {
                        "preview_write", "validate_write"
                    } and raw.get("success"):
                        payload = raw.get("approval")
                        if isinstance(payload, dict):
                            report = await asyncio.to_thread(business_facts.inspect, payload)
                            # 事实报告是诊断信息。是否允许执行仍由动作与审批检查决定。
                            raw = attach_business_facts(raw, report)
                    if name == "validate_write" and raw.get("success"):
                        status = raw.get("approval_status")
                        request = raw.get("execution_request")
                        reference = request.get("approval") if isinstance(request, dict) else None
                        if (
                            isinstance(status, dict)
                            and status.get("stored")
                            and status.get("durable")
                            and isinstance(reference, dict)
                            and isinstance(reference.get("action_id"), str)
                            and isinstance(reference.get("token"), str)
                            and reference["action_id"]
                            and reference["token"]
                        ):
                            raw = dict(raw)
                            raw["approval"] = dict(reference)
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str).decode(),
                        details={"structuredContent": raw, "meta": None},
                    )
                elif direct_capability:
                    raw = await asyncio.to_thread(
                        capabilities.call, name, dict(arguments)
                    )
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str).decode(),
                        details={"structuredContent": raw, "meta": None},
                    )
                else:
                    result = await tool.execute(call_id, arguments, signal, on_update)
                if world_receipt_unavailable:
                    # 业务结果照常返回，但模型必须知道这次不能作为可复用的 World 证据。
                    result = _receipt_unavailable_result(
                        result,
                        fallback_payload=(
                            source_raw if name == "get_model_fields" and isinstance(source_raw, dict)
                            else None
                        ),
                    )
                if name == "health_check":
                    # A0: keep process-local counters in receipts, not model context.
                    # Policy/permission fields remain visible and unchanged in both arms.
                    with log_path.with_name("health-process-telemetry.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"tool_call_id": call_id, "result": result.model_dump(mode="json")}) + "\n")
                    result = result.model_copy(deep=True)
                    payload = json.loads(result.text)
                    for data in (
                        payload,
                        (result.details or {}).get("structuredContent"),
                    ):
                        if isinstance(data, dict):
                            runtime = data.get("runtime")
                            if isinstance(runtime, dict):
                                runtime.pop("n_plus_one", None)
                            rates = data.get("rate_limits")
                            if isinstance(rates, dict):
                                for key in ("busiest", "over_budget_totals"):
                                    rates.pop(key, None)
                    result.content = [
                        TextContent(
                            text=to_json(payload, fallback=str).decode()
                        )
                    ]
                event["result_sha256"] = hashlib.sha256(result.text.encode()).hexdigest()
                if observation is not None:
                    completed = observation
                    observation = None
                    try:
                        metadata = native.world_metadata(name, normalized) if direct else {}
                        rpc_evidence = native.world_rpc_evidence(call_id) if direct else None
                        world.finish(
                            completed, result.text, field_metadata=metadata,
                            rpc_evidence=rpc_evidence,
                            raw_result=source_raw,
                            evidence=runtime_evidence,
                        )
                    except Exception as exc:
                        event["world_receipt_error"] = type(exc).__name__
                        world_receipt_unavailable = True
                        result = _receipt_unavailable_result(
                            result,
                            fallback_payload=(
                                source_raw if name == "get_model_fields" and isinstance(source_raw, dict)
                                else None
                            ),
                        )
                        _world_failed(world, "finish", exc)
                event["result_sha256"] = hashlib.sha256(result.text.encode()).hexdigest()
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
                    if capabilities is not None:
                        try:
                            capabilities.knowledge.invalidate(instance=arguments.get("instance"), reason="side-effect attempt or ambiguous result")
                        except Exception as exc:
                            event["knowledge_invalidation_failed"] = type(exc).__name__
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

        description = tool.description
        if direct_action and name == "validate_write":
            description += (
                " A successful persisted validation returns execution_request with "
                "action_id and token; pass it unchanged to execute_approved_write."
            )
        elif direct_action and name == "execute_approved_write":
            description += (
                " Use execution_request with action_id and token from a successful "
                "persisted validation unchanged; the runtime holds the canonical payload, "
                "so do not reconstruct values or token."
            )
        routed.append(replace(
            tool,
            execute_fn=execute,
            description=description,
            execution_mode="sequential" if name in ACTION_TOOLS else tool.execution_mode,
        ))
    if native is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        required_reads = NATIVE_READ_RESPONSES if native_health else READ_RESPONSES
        if not required_reads.keys() <= present:
            raise RuntimeError("Advertised catalog is missing a required native read tool")
    if actions is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        if not ACTION_TOOLS <= present:
            raise RuntimeError("Advertised catalog is missing a required native action tool")
    if capabilities is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        if not CAPABILITY_TOOLS <= present:
            raise RuntimeError(
                "Advertised catalog is missing a required native capability tool"
            )
    return routed
