"""Host facts for capability disclosure. Never an execution authorization."""
from __future__ import annotations

from collections import Counter
import json
import re
from erp_harness.app.laya_state import task_goal, sop_requirements, _json

VERSION = "host_facts_v1"
TERMINAL = frozenset({"verified", "known_failed", "rejected", "expired"})
# Routing needs identities, progress and amounts, not descriptions or attachment bodies.
FACT_FIELDS = frozenset({
    "id", "name", "display_name", "state", "invoice_status", "payment_state", "reconciled",
    "is_matched", "amount_total", "amount_residual", "amount", "debit", "credit",
    "quantity", "product_qty", "product_uom_qty", "qty_producing", "qty_produced",
    "qty_delivered", "qty_received", "picked", "date_start", "date_finished", "origin",
})






def _packed_facts(value, path='result'):
    """Preserve complete small record groups, describing large groups without sampling."""
    if isinstance(value, list):
        return ({'count': len(value), 'complete': False} if len(value) > 16 else
                [_facts(row) for row in value if isinstance(row, dict)])
    if isinstance(value, dict):
        return {k: _packed_facts(v, path+'.'+k) for k, v in value.items()
                if isinstance(v, (dict, list))} | _facts(value)
    return value




def _facts(record):
    result = {}
    for key, value in record.items():
        if key not in FACT_FIELDS and not key.endswith(("_id", "_ids")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value if not isinstance(value, str) else value[:160]
        elif isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value):
            # Preserve complete small relationships. Large sets are explicitly incomplete.
            result[key] = value if len(value) <= 64 else {"count": len(value), "complete": False}
    return result


def ledger_state(rows, *, identity=None, complete=True):
    """Only the host supplies these rows; never infer approval from conversation text."""
    if identity is not None and any(r.get("identity") != identity for r in rows):
        raise ValueError("Routing ledger identity mismatch")
    pending, finished = [], []
    for row in rows:
        payload = row.get("payload") or {}
        kwargs = payload.get("kwargs") or {}
        entry = {"action_id": row.get("action_id"), "status": row.get("status", "unknown"),
                 "model": payload.get("model"), "operation": payload.get("method", payload.get("operation")),
                 "record_ids": payload.get("record_ids", kwargs.get("ids", []))}
        (finished if entry["status"] in TERMINAL else pending).append(entry)
    return {"complete": complete, "status_counts": dict(Counter(r.get("status", "unknown") for r in rows)),
            "unresolved": pending, "recent_finished": finished[-6:],
            "finished_omitted": max(0, len(finished)-6)}


def build_routing_state(request, *, goal=None, stage=None, ledger=None, identity=None,
                        world=None, required=()):
    """Shared live/replay projection; replay must supply an as-of host ledger or mark it unknown."""
    messages = request["messages"]
    users = [m.get('content', '') for m in messages if m.get('role') == 'user']
    first = users[0] if users else ''
    first = first if isinstance(first, str) else json.dumps(first, ensure_ascii=False)
    state = {"version": VERSION, "task": {"goal": task_goal(goal if goal is not None else first),
             "confirmed_stage": stage, "stage_known": stage is not None},
             "action_ledger": ledger if ledger is not None else ledger_state([], complete=False),
             "runtime_retained_capabilities": sorted(required), "business_facts": [], "recent_results": [],
             "evidence_gaps": [], "execution_permission": "not_granted_by_disclosure"}
    # Live runs use the immutable host goal. Legacy replay retains later user direction separately.
    if goal is None:
        state['task']['later_user_instructions'] = [task_goal(v) for v in users[1:]
            if isinstance(v, str) and not v.startswith('The desktop host has completed human approval')]
    calls = {c["id"]: c["function"] for m in messages if m.get("role") == "assistant"
             for c in m.get("tool_calls", [])}
    if world is not None:
        visible_calls = {m.get('tool_call_id') for m in messages if m.get('role') == 'tool'}
        archived = []
        # World outlives compacted chat. Add missing observations only to the selector's private view.
        for receipt in world._authorized_receipts(identity):
            call_id = receipt['call_id']
            if call_id in visible_calls:
                continue
            integrity, payload = world._visible_payload(receipt)
            if integrity != 'verified':
                state['evidence_gaps'].append({'source_call_id':call_id,'reason':'unverified_observation'})
                continue
            calls[call_id] = {'name': receipt['tool'], 'arguments': receipt['request']}
            archived.append({'role':'tool','tool_call_id':call_id,'name':receipt['tool'],'content':payload})
        messages = archived + messages
    seen = set()
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        call = calls.get(call_id, {})
        tool = (message.get("name") or call.get("name", "")).removeprefix("mcp_odoo_")
        if tool in {"configure_odoo_tools", "list_odoo_capabilities"}:
            continue
        args = _json(call.get("arguments", {})) or {}
        payload = _json(message.get("content"))
        if not isinstance(payload, dict):
            continue
        model = args.get("model") or payload.get("model")
        summary = {"tool": tool, "model": model, "success": payload.get("success"),
                   "source_call_id": call_id}
        if payload.get("error"):
            summary["error"] = str(payload["error"])[:350]
        if tool == "get_odoo_sop" and isinstance(payload.get("sop"), dict):
            sop = payload["sop"]
            summary.update(procedure=sop.get("id"), required_tools=sop.get("required_tools", []))
        if len(state["recent_results"]) < 6:
            state["recent_results"].append(summary)
        if tool in {"get_model_fields", "list_models", "schema_catalog", "get_odoo_sop", "list_odoo_sops"}:
            continue
        receipt = world.receipt_for_call(call_id) if world is not None else None
        batch_start = len(state['business_facts'])
        if receipt is not None:
            if receipt.get("identity") != identity:
                raise ValueError("Routing observation identity mismatch")
            # Use the current merged World projection, including stale/conflicting fields.
            targets = receipt.get("targets", [])
            if not targets:
                integrity, visible = world._visible_payload(receipt)
                if integrity == 'verified' and isinstance(visible, dict):
                    payload = visible
                else:
                    state['evidence_gaps'].append({'source_call_id': call_id, 'reason': 'unverified_observation'})
                    continue
            records = [(t["model"], r["id"]) for t in targets for r in t.get("records", [])]
            if len(records) > 64:
                state["evidence_gaps"].append({"source_call_id": call_id, "records": len(records), "reason": "large_record_set"})
                continue
            for model_name, record_id in records:
                key = (model_name, record_id)
                if key in seen:
                    continue
                view = world.record_view(identity["identity_id"], model_name, record_id)
                if view is None:
                    continue
                seen.add(key)
                fields = {f: d.get("value") for f, d in view["fields"].items()
                          if d.get("status") in {"value", "empty"} and not d.get("stale")}
                state["business_facts"].append({"model": model_name, "record_id": record_id,
                    "stale": view.get("stale", True), "values": _facts(fields), "source_call_id": call_id})
        else:
            # Historical prefixes without World remain observations, never freshly verified state.
            result = payload.get("result")
            records = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
            if len(records) > 64:
                state["evidence_gaps"].append({"source_call_id": call_id, "records": len(records), "reason": "large_record_set"})
                continue
            for record in records:
                if not isinstance(record, dict) or type(record.get("id")) is not int or not model:
                    continue
                key = (model, record["id"])
                if key in seen:
                    continue
                seen.add(key)
                state["business_facts"].append({"model": model, "record_id": record["id"],
                    "stale": "unknown", "values": _facts(record), "source_call_id": call_id})
        if (not (receipt and receipt.get('targets')) and isinstance(payload.get('result'), dict)
                and type(payload['result'].get('id')) is not int):
            packed = _packed_facts(payload['result'])
            if packed:
                # Composite reads are historical observations; they do not establish current ERP truth.
                state['business_facts'].append({'tool': tool, 'stale': 'unknown', 'values': packed,
                    'source_call_id': call_id, 'completeness': payload.get('completeness')})
            else:
                state['evidence_gaps'].append({'source_call_id': call_id, 'reason': 'unprojected_result'})
        # Admit whole observation groups. A relation set must not become a misleading prefix.
        if len(json.dumps(state['business_facts'], ensure_ascii=False)) > 10000:
            omitted = len(state['business_facts']) - batch_start
            del state['business_facts'][batch_start:]
            state['evidence_gaps'].append({'source_call_id': call_id, 'records': omitted, 'reason': 'fact_budget'})
    # The selector may disclose tools; nothing here can bypass authorization or fresh readback.
    # Question definitions stay importable by the isolated trainer; only host projection uses World.
    text = re.sub(r"odoo-write:[A-Za-z0-9_-]+", "[redacted]", json.dumps(state, ensure_ascii=False))
    from erp_harness.context.world import _scrub_payload
    return _scrub_payload(json.loads(text), error_strings=True, string_limit=None)


def disclosure_questions(groups, label="A", first=False):
    questions = {}
    for name, group in groups.items():
        choices = [(label, "Needed for the current task, including after prerequisite reads."),
                   ("B" if label == "A" else "A", "Not needed for the remaining task.")]
        if not first:
            choices.reverse()
        questions[name] = {"type": "choice", "instructions":
            "Does the remaining task need this capability available? Use the confirmed task, current ledger and business facts. "
            "A finished action is not a finished task. Publication is not permission. "
            "Runtime-retained capabilities are safety dependencies, not necessarily still needed by the remaining task. "
            + name + ": " + group["description"] + " Tools: " + ", ".join(group["tools"]),
            "criteria": dict(choices)}
    return questions
