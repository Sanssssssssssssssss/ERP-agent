"""Bounded execution diagnosis. No business reads, writes, retries or approval decisions."""
from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from pathlib import Path

from erp_harness.erp.read_failures import FAILURE_GUIDANCE
from erp_harness.erp.store import ActionStore
from erp_harness.runtime.tools import AgentTool, AgentToolResult

_LOCAL_GUIDANCE = {
    "invalid_path": ("tool_contract", "read_observation_directory"),
    "invalid_request": ("tool_contract", "correct_observation_request"),
    "unknown_reference": ("tool_contract", "search_observations"),
    "access": ("authorization", "use_current_identity"),
}
_UNCERTAIN = {"sending", "executing", "needs_reconciliation"}


def _guidance(code):
    # Only host-maintained codes supply instructions; receipt text is not authority.
    return (FAILURE_GUIDANCE.get(code) or _LOCAL_GUIDANCE.get(code)) if isinstance(code, str) else None


def _diagnostic_error(code):
    layer, action = _guidance(code)
    return {"success": False, "error_code": code, "likely_failure_layer": layer,
            "next_action": action, "items": []}


def _rows(path: Path):
    rows, complete = [], True
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                    else:
                        complete = False
                except ValueError:
                    complete = False
    except (OSError, UnicodeError):
        complete = False
    return rows, complete


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value) else None


def _payload(message):
    details = message.get("details")
    if isinstance(details, dict):
        structured = details.get("structuredContent", details)
        if isinstance(structured, dict):
            return structured
    for part in message.get("content", []):
        if isinstance(part, dict) and part.get("type") == "text":
            try:
                data = json.loads(part.get("text", ""))
                if isinstance(data, dict):
                    return data
            except (ValueError, TypeError):
                pass
    return {}


def _action_status(action):
    status = action.get("status")
    return status if status in {"pending_approval", "approved", "expired", "executing", "sending",
                               "verified", "known_failed", "needs_reconciliation", "rejected"} else "unknown"


def _error(message, payload):
    if isinstance(payload.get("result"), dict) and isinstance(payload["result"].get("failures"), dict):
        payload = payload["result"]
    if isinstance(payload.get("execution_failure"), dict):
        payload = {**payload["execution_failure"], "success": False}
    elif isinstance(payload.get("failures"), dict) and payload["failures"]:
        codes = {item.get("reason_code") for item in payload["failures"].values()
                 if isinstance(item, dict) and _guidance(item.get("reason_code"))}
        return next(iter(codes)) if len(codes) == 1 and _guidance(next(iter(codes))) else "tool_failed"
    if payload.get("success") is False or message.get("isError"):
        failure = payload.get("failure")
        codes = [failure.get("code") if isinstance(failure, dict) else None,
                 payload.get("reason_code"), payload.get("error_code"), payload.get("error_class")]
        for code in codes:
            if _guidance(code):
                return code
        return "tool_failed"
    return None


def _field_recovery(payload):
    recovery = payload.get("recovery_request")
    if payload.get("success") is not False or payload.get("reason_code") != "query_invalid" or not isinstance(recovery, dict):
        return None
    arguments = recovery.get("arguments")
    fields = recovery.get("unknown_fields")
    if (recovery.get("tool") != "mcp_odoo_get_model_fields" or not isinstance(arguments, dict)
            or not _identifier(arguments.get("model")) or not _identifier(arguments.get("instance"))
            or not isinstance(fields, list) or not fields or not all(_identifier(field) for field in fields)):
        return None
    # Bound diagnostics, retaining the total so truncation cannot look like a complete field list.
    return {"tool": "mcp_odoo_get_model_fields",
            "arguments": {"model": arguments["model"], "instance": arguments["instance"],
                          "query": " ".join(fields[:3])},
            "unknown_fields": fields[:3], "unknown_field_count": len(fields),
            "notice": "当前实例的字段定义中不存在这些字段，可能是版本或命名差异，不能据此认定权限不足。"
                      "先调用本只读字段发现工具，查验名称、类型和关联；候选字段不是语义替代。"
                      "若检索不足，按业务含义扩大查询。必要业务信息仍无法获取时保留未知，不能只删字段就认定核验通过。"}


def _auxiliary_recovery(payload, call):
    arguments = call.get("arguments")
    if payload.get("success") is not False or not isinstance(arguments, dict):
        return {}
    if payload.get("reason_code") == "sop_inputs_invalid" and call.get("name") == "get_odoo_sop":
        from erp_harness.tools.sops import MAX_INPUT_LENGTH, SOPS

        sop_id = arguments.get("sop_id")
        if not isinstance(sop_id, str) or sop_id not in SOPS:
            return {}
        parameters = SOPS[sop_id]["parameters"]
        supplied = arguments.get("inputs")
        supplied = {} if supplied is None else supplied
        metadata = {"sop_id": sop_id, "allowed_inputs": list(parameters),
                    "required_inputs": [name for name, required in parameters.items() if required],
                    "input_constraints": {"type": "string", "max_length": MAX_INPUT_LENGTH}}
        if isinstance(supplied, dict):
            names = {
                "missing": [name for name, required in parameters.items()
                            if required and not str(supplied.get(name, "")).strip()],
                "invalid": [name for name, value in supplied.items()
                            if not isinstance(value, str) or len(value) > MAX_INPUT_LENGTH],
                "unknown": [name for name in supplied if name not in parameters],
            }
            for key, values in names.items():
                safe = [name for name in values if _identifier(name)]
                metadata[key] = safe[:8]
                metadata[key + "_count"] = len(values)
        return metadata
    if payload.get("reason_code") == "observation_path_invalid" and call.get("name") == "read_observation":
        recovery = payload.get("recovery_request")
        candidate = recovery.get("arguments") if isinstance(recovery, dict) else None
        if (not isinstance(candidate, dict) or recovery.get("tool") != "read_observation"
                or candidate.get("path") != "$" or candidate.get("limit") != 100
                or not _identifier(candidate.get("observation_ref"))
                or candidate["observation_ref"] != arguments.get("observation_ref")
                or candidate.get("instance") != arguments.get("instance")
                or "instance" in candidate and not _identifier(candidate["instance"])):
            return {}
        request = {"observation_ref": candidate["observation_ref"], "path": "$", "limit": 100}
        if "instance" in candidate:
            request["instance"] = candidate["instance"]
        return {"recovery_request": {"tool": "read_observation", "arguments": request}}
    return {}


def summarize_run(directory: Path, session_file: Path, identity: dict, *, run_id: str, session_id: str):
    """Only explicit IDs join artifacts; missing evidence never proves no dispatch."""
    result = {"success": True, "scope": "current_run", "business_truth": False,
              "note": "Execution evidence only. Verify business facts using current-permission Odoo reads.",
              "items": [], "evidence_complete": True}
    calls, ambiguous_calls = {}, set()
    # ponytail: scan local receipt metadata; index only if measured run size warrants it.
    for path in sorted((directory / "requests").glob("*.meta.json")):
        rows, complete = _rows(path)
        result["evidence_complete"] &= complete
        for row in rows:
            if row.get("run_id") != run_id or row.get("session_id") != session_id:
                return _diagnostic_error("scope_mismatch")
            for call in row.get("tool_call_ids", []):
                if _identifier(call):
                    if call in calls and calls[call] != row.get("request_id"):
                        ambiguous_calls.add(call)
                    calls[call] = row.get("request_id")
    events, events_ok = _rows(directory / "tool-backends.jsonl")
    rpc, rpc_ok = _rows(directory / "odoo-native-requests.jsonl")
    world, world_ok = _rows(directory / "world-observations.jsonl")
    messages, session_ok = _rows(session_file)
    result["evidence_complete"] &= events_ok and rpc_ok and world_ok and session_ok
    try:
        actions = ActionStore.read_receipts(directory / "odoo-actions.sqlite3")
    except (OSError, sqlite3.Error, ValueError):
        actions = []
        result["evidence_complete"] = False
    if any(row.get("run_id") != run_id or row.get("session_id") != session_id
           or row.get("identity") != identity for row in actions):
        return _diagnostic_error("scope_mismatch")
    if any(row.get("identity") != identity for row in world if row.get("type") == "world_observation"):
        return _diagnostic_error("identity_mismatch")
    by_action = {a["action_id"]: a for a in actions}
    by_call, requested = {}, {}
    for row in messages:
        message = row.get("message", {})
        if not isinstance(message, dict):
            result["evidence_complete"] = False
            continue
        call = message.get("toolCallId")
        if message.get("role") == "assistant":
            for part in message.get("content", []):
                if isinstance(part, dict) and part.get("type") == "toolCall" and _identifier(part.get("id")) in calls:
                    requested_call = {"name": part.get("name"), "arguments": part.get("arguments")}
                    if part["id"] in requested and requested[part["id"]] != requested_call:
                        ambiguous_calls.add(part["id"])
                    requested[part["id"]] = requested_call
        if message.get("role") == "toolResult" and call in calls:
            if call in by_call and by_call[call] != message:
                result["evidence_complete"] = False
                by_call[call] = {"isError": True}  # Conflicting duplicate cannot supply evidence.
            else:
                by_call[call] = message
    for call in reversed(calls):
        message = by_call.get(call, {})
        payload = _payload(message)
        task_failure = payload.get("execution_failure")
        if isinstance(task_failure, dict):
            payload = {**payload, **task_failure, "success": False}
        failure = payload.get("failure") if isinstance(payload.get("failure"), dict) else {}
        if not failure and str(payload.get("error", "")).startswith("Unreviewed side-effect methods are blocked by default."):
            # Legacy policy receipt proves the refusal reason, not missing RPC evidence.
            failure = {"code": "method_not_supported", "layer": "action_policy", "next_action": "request_supported_alternative"}
        code = _error(message, payload)
        if code == "tool_failed" and failure.get("code") == "method_not_supported":
            code = "method_not_supported"
        starts = [e for e in events if e.get("tool_call_id") == call and e.get("event") == "start"]
        ends = [e for e in events if e.get("tool_call_id") == call and e.get("event") == "end"]
        if not code and not (starts and not ends):
            continue
        matching = [r for r in rpc if r.get("tool_call_id") == call]
        rpc_ids = {r.get("rpc_request_id") for r in matching if r.get("rpc_request_id")}
        # One request ID attached to two tool calls is corrupt correlation, not evidence.
        requested_call = requested.get(call, {})
        conflict = (call in ambiguous_calls or bool(requested_call and message.get("toolName")
                    and message["toolName"] != requested_call.get("name")) or any(
            r.get("rpc_request_id") in rpc_ids and r.get("tool_call_id") != call for r in rpc))
        text = "".join(p.get("text", "") for p in message.get("content", []) if isinstance(p, dict))
        before_dispatch = bool(message.get("isError") and re.fullmatch(r"Tool [\w.-]+ not found(?:\..*)?", text, re.DOTALL))
        observed_dispatch = any(r.get("dispatch_started") is True for r in matching)
        explicit_no_dispatch = bool(matching) and all(r.get("dispatch_started") is False for r in matching)
        policy_no_dispatch = _identifier(failure.get("stage")) in {"before_send", "before_dispatch"} and failure.get("odoo_request_seen") is False
        if policy_no_dispatch and observed_dispatch:
            conflict = True
        if before_dispatch and (starts or observed_dispatch):
            conflict = True
        seen = (True if observed_dispatch else False if explicit_no_dispatch or policy_no_dispatch
                or before_dispatch and not matching else None) if not conflict else None
        if conflict:
            result["evidence_complete"] = False
        approval = payload.get("approval_status") or payload.get("approval") or {}
        action_id = _identifier(payload.get("action_id") or (approval.get("action_id") if isinstance(approval, dict) else None))
        action = by_action.get(action_id, {})
        observations = [r for r in world if r.get("type") == "world_observation" and r.get("call_id") == call]
        observation = observations[0] if len(observations) == 1 else {}
        stale = None
        if observation and world_ok:
            stale = any(r.get("type") == "world_invalidation"
                        and identity.get("identity_id") in r.get("identity_ids", [])
                        and r.get("sequence", 0) > observation.get("sequence", 0) for r in world)
        started_rpc = {r.get("rpc_request_id") for r in matching if r.get("event") == "start"}
        ended_rpc = {r.get("rpc_request_id") for r in matching if r.get("event", "end") == "end"}
        incomplete_rpc = started_rpc - ended_rpc
        guidance = _guidance(code) or ("unknown", "fresh_read_or_validate")
        reported_status = _identifier(payload.get("action_status"))
        stage = _identifier(failure.get("stage"))
        uncertain = (action.get("status") in _UNCERTAIN or reported_status in _UNCERTAIN
                     or code in {"needs_reconciliation", "action_outcome_unknown", "action_verification_failed",
                                 "invoice_delivery_reconciliation_required"})
        status = _action_status(action)
        if status == "unknown" and reported_status in _UNCERTAIN:
            # A receipt can conservatively require reconciliation; it cannot authorize a retry.
            status = reported_status
        result["items"].append({
            "tool_call_id": call, "request_id": _identifier(calls[call]) if call not in ambiguous_calls else None,
            "tool_name": _identifier(message.get("toolName")),
            "tool_started": True if starts else False if before_dispatch or policy_no_dispatch and failure.get("stage") == "before_dispatch" else None,
            "tool_ended": True if ends or message else None,
            "odoo_request_seen": seen,
            "rpc_request_ids": sorted(r for r in rpc_ids if _identifier(r))[:3] if not conflict else [],
            "rpc_started": True if started_rpc and not conflict else None,
            "rpc_ended": True if ended_rpc and not conflict else None,
            "rpc_incomplete": bool(incomplete_rpc) if started_rpc and not conflict else None,
            "action_id": _identifier(action_id), "action_status": status,
            "world_observation": _identifier(observation.get("receipt_id")), "world_stale": stale,
            "error_code": "correlation_conflict" if conflict else code or "tool_incomplete",
            "stage": stage if not conflict and stage in {
                "before_send", "before_dispatch", "send", "verification", "prepare", "execute", "after_tool_call"} else "before_dispatch" if seen is False else "unknown",
            "likely_failure_layer": "unknown" if conflict else "tool_dispatch" if before_dispatch
                else "odoo_transport" if incomplete_rpc else guidance[0],
            "next_action": "reconcile_without_replay" if uncertain else "inspect_execution_evidence"
                if conflict or incomplete_rpc else "recover_capabilities" if before_dispatch else guidance[1],
        })
        if not conflict and _guidance(payload.get("reason_code")):
            result["items"][-1]["reason_code"] = payload["reason_code"]
        partial = payload.get("failures")
        if not isinstance(partial, dict) and isinstance(payload.get("result"), dict):
            partial = payload["result"].get("failures")
        if not conflict and isinstance(partial, dict) and partial:
            result["items"][-1]["instance_failures"] = [
                {"instance": _identifier(instance), "reason_code": value["reason_code"],
                 "likely_failure_layer": _guidance(value["reason_code"])[0],
                 "next_action": _guidance(value["reason_code"])[1]}
                for instance, value in list(partial.items())[:8]
                if isinstance(value, dict) and _guidance(value.get("reason_code"))]
            result["items"][-1]["instance_failure_count"] = len(partial)
        if not conflict and not action_id and not uncertain and not incomplete_rpc and code == "query_invalid":
            recovery = _field_recovery(payload)
            if recovery:
                result["items"][-1].update(error_code="unknown_field", reason_code="query_invalid",
                    likely_failure_layer="tool_arguments", next_action="discover_live_fields", recovery_request=recovery)
        if not conflict and not action_id and not uncertain and not incomplete_rpc:
            result["items"][-1].update(_auxiliary_recovery(payload, requested_call))
    unresolved = []
    linked = {item["action_id"] for item in result["items"]}
    for action in reversed(actions):
        if action["action_id"] not in linked and action.get("status") in {
            "pending_approval", "approved", "sending", "executing", "needs_reconciliation"}:
            unresolved.append({"action_id": _identifier(action["action_id"]),
                "action_status": action["status"], "tool_call_id": None, "odoo_request_seen": None,
                "world_stale": None, "error_code": "unresolved_action", "likely_failure_layer": "unknown",
                "next_action": "reconcile_without_replay" if action["status"] in {"sending", "executing", "needs_reconciliation"}
                else "review_existing_approval"})
    items = unresolved + result["items"]
    # Uncertain writes must remain visible even when recent parameter failures fill the limit.
    items.sort(key=lambda item: 0 if item.get("next_action") == "reconcile_without_replay"
               else 1 if item.get("error_code") != "unresolved_action" else 2)
    result["items"] = items[:3]
    if len(json.dumps(result, ensure_ascii=False).encode()) > 8192:
        return _diagnostic_error("diagnostic_size_limit")
    return result


def build_diagnostic_tool(receipt_dir: Path, session_file: Path, identity_context, *, routing_context=None):
    identity = copy.deepcopy(identity_context())
    run_id = os.environ.get("HARBOR_TRIAL_ID") or os.environ.get("PI_AGENT_RUN_ID")
    session_id = os.environ.get("PI_AGENT_SESSION_ID")

    async def execute(_call_id, arguments, _signal=None, _on_update=None):
        if arguments:
            payload = _diagnostic_error("no_arguments_allowed")
        elif not run_id or not session_id or identity_context() != identity:
            payload = _diagnostic_error("identity_or_scope_unavailable")
        else:
            payload = summarize_run(receipt_dir, session_file, identity, run_id=run_id, session_id=session_id)
            if payload.get("success") and routing_context is not None:
                payload["routing"] = routing_context()
                if len(json.dumps(payload, ensure_ascii=False).encode()) > 8192:
                    payload = _diagnostic_error("diagnostic_size_limit")
        return AgentToolResult(content=json.dumps(payload, ensure_ascii=False), details=payload)

    return AgentTool(name="diagnose_current_run", label="Diagnose current run",
        description="Read up to 3 compact execution diagnostics for this run. Missing logs mean unknown. Does not query Odoo, approve, retry or establish current business facts.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute_fn=execute, execution_mode="sequential")
