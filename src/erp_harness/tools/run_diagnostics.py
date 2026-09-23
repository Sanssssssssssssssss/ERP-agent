"""Bounded execution diagnosis. No business reads, writes, retries or approval decisions."""
from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from pathlib import Path

from erp_harness.erp.store import ActionStore
from erp_harness.runtime.tools import AgentTool, AgentToolResult


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
    failure = payload.get("failure") or {}
    code = failure.get("code") if isinstance(failure, dict) else None
    if code in {"scope_handoff_required", "business_choice_required", "stale_approval",
                "permission_denied", "needs_reconciliation", "scope_reconfirmation_required"}:
        return code
    if payload.get("success") is False or message.get("isError"):
        return "tool_failed"
    return None


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
                return {"success": False, "error_code": "scope_mismatch", "items": []}
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
        return {"success": False, "error_code": "scope_mismatch", "items": []}
    if any(row.get("identity") != identity for row in world if row.get("type") == "world_observation"):
        return {"success": False, "error_code": "identity_mismatch", "items": []}
    by_action = {a["action_id"]: a for a in actions}
    by_call = {}
    for row in messages:
        message = row.get("message", {})
        if not isinstance(message, dict):
            result["evidence_complete"] = False
            continue
        call = message.get("toolCallId")
        if message.get("role") == "toolResult" and call in calls:
            if call in by_call and by_call[call] != message:
                result["evidence_complete"] = False
                by_call[call] = {"isError": True}  # Conflicting duplicate cannot supply evidence.
            else:
                by_call[call] = message
    for call in reversed(calls):
        message = by_call.get(call, {})
        payload = _payload(message)
        code = _error(message, payload)
        starts = [e for e in events if e.get("tool_call_id") == call and e.get("event") == "start"]
        ends = [e for e in events if e.get("tool_call_id") == call and e.get("event") == "end"]
        if not code and not (starts and not ends):
            continue
        matching = [r for r in rpc if r.get("tool_call_id") == call]
        rpc_ids = {r.get("rpc_request_id") for r in matching if r.get("rpc_request_id")}
        # One request ID attached to two tool calls is corrupt correlation, not evidence.
        conflict = call in ambiguous_calls or any(
            r.get("rpc_request_id") in rpc_ids and r.get("tool_call_id") != call for r in rpc)
        text = "".join(p.get("text", "") for p in message.get("content", []) if isinstance(p, dict))
        before_dispatch = bool(message.get("isError") and re.fullmatch(r"Tool [\w.-]+ not found(?:\..*)?", text, re.S))
        observed_dispatch = any(r.get("dispatch_started") is True for r in matching)
        explicit_no_dispatch = bool(matching) and all(r.get("dispatch_started") is False for r in matching)
        if before_dispatch and (starts or observed_dispatch):
            conflict = True
        seen = (True if observed_dispatch else False if explicit_no_dispatch
                or before_dispatch and not matching else None) if not conflict else None
        if conflict:
            result["evidence_complete"] = False
        approval = payload.get("approval_status") or payload.get("approval") or {}
        action_id = payload.get("action_id") or (approval.get("action_id") if isinstance(approval, dict) else None)
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
        result["items"].append({
            "tool_call_id": call, "request_id": _identifier(calls[call]) if call not in ambiguous_calls else None,
            "tool_started": True if starts else False if before_dispatch else None,
            "tool_ended": True if ends or message else None,
            "odoo_request_seen": seen,
            "rpc_request_ids": sorted(r for r in rpc_ids if _identifier(r))[:3] if not conflict else [],
            "rpc_started": True if started_rpc and not conflict else None,
            "rpc_ended": True if ended_rpc and not conflict else None,
            "rpc_incomplete": bool(incomplete_rpc) if started_rpc and not conflict else None,
            "action_id": _identifier(action_id), "action_status": _action_status(action),
            "world_observation": _identifier(observation.get("receipt_id")), "world_stale": stale,
            "error_code": "correlation_conflict" if conflict else code or "tool_incomplete",
            "stage": "before_dispatch" if seen is False else "unknown",
            "likely_failure_layer": "unknown" if conflict else "tool_dispatch" if before_dispatch else "handoff" if code in {
                "scope_handoff_required", "business_choice_required", "scope_reconfirmation_required"}
                else "odoo_transport" if incomplete_rpc else "unknown",
            "next_action": "request_user_input" if code in {
                "scope_handoff_required", "business_choice_required", "scope_reconfirmation_required"}
                else "reconcile_without_replay" if action.get("status") in {"sending", "executing", "needs_reconciliation"}
                else "fresh_read_or_validate",
        })
        if len(result["items"]) == 3:
            break
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
    items.sort(key=lambda item: 0 if item.get("action_status") in {
        "sending", "executing", "needs_reconciliation"} else 1 if item.get("error_code") != "unresolved_action" else 2)
    result["items"] = items[:3]
    if len(json.dumps(result, ensure_ascii=False).encode()) > 8192:
        return {"success": False, "error_code": "diagnostic_size_limit", "items": []}
    return result


def build_diagnostic_tool(receipt_dir: Path, session_file: Path, identity_context):
    identity = copy.deepcopy(identity_context())
    run_id = os.environ.get("HARBOR_TRIAL_ID") or os.environ.get("PI_AGENT_RUN_ID")
    session_id = os.environ.get("PI_AGENT_SESSION_ID")

    async def execute(_call_id, arguments, _signal=None, _on_update=None):
        if arguments:
            payload = {"success": False, "error_code": "no_arguments_allowed", "items": []}
        elif not run_id or not session_id or identity_context() != identity:
            payload = {"success": False, "error_code": "identity_or_scope_unavailable", "items": []}
        else:
            payload = summarize_run(receipt_dir, session_file, identity, run_id=run_id, session_id=session_id)
        return AgentToolResult(content=json.dumps(payload, ensure_ascii=False), details=payload)

    return AgentTool(name="diagnose_current_run", label="Diagnose current run",
        description="Read up to 3 compact execution diagnostics for this run. Missing logs mean unknown. Does not query Odoo, approve, retry or establish current business facts.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute_fn=execute, execution_mode="sequential")
