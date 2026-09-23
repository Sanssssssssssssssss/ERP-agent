"""Scoped conversation readback. Old records identify targets; only fresh reads supply facts."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

from . import sale_view


def _pick(row, keys):
    return {key: copy.deepcopy(row[key]) for key in keys if key in row}


def _action(row):
    result = _pick(row, ("action_id", "action_status", "status", "model", "operation", "type", "kind"))
    verification = row.get("verification") or {}
    evidence = verification.get("evidence") or {}
    result["verification"] = {"status": verification.get("status"), "evidence": {
        key: ([{"id": r.get("id")} if isinstance(r, dict) else r for r in value] if key == "records"
              else [_pick(r, ("model", "id")) for r in value if isinstance(r, dict)] if key == "related_records"
              else copy.deepcopy(value))
        for key, value in evidence.items() if key in {"records", "record_ids", "record_model", "related_records"}
    }}
    return result


def build_status_context(state, business_id, session_id, connection):
    """Host-only snapshot: no model-selected IDs, old field values or trace bodies."""
    business = state.get("businesses", {}).get(business_id)
    if not business or business.get("session_id") != session_id:
        raise ValueError("business does not belong to session")
    if business.get("odoo_connection") != connection or not all(connection.get(k) for k in ("url", "database", "principal")):
        raise ValueError("business connection mismatch")
    selected = _pick(business, ("id", "session_id", "type", "completion_target", "odoo_connection"))
    selected["references"] = [{**_pick(r, ("model", "id", "purpose")), "quote": r.get("model", "record"),
        "fields": dict.fromkeys(r.get("fields", {})),
        "expected_relations": {key: sale_view._relation_ids(value) for key, value in r.get("fields", {}).items()
            if key in {"company_id", "partner_id", "commercial_partner_id", "parent_id", "currency_id"}}}
        for r in business.get("references", [])]
    runs = {}
    for key, run in state.get("runs", {}).items():
        if run.get("business_id") != business_id:
            continue
        slim = _pick(run, ("id", "business_id", "session_id", "started_at", "ended_at", "status"))
        slim["documents"] = []
        slim["tools"] = [{"name": t.get("name"), "arguments": _pick(t.get("arguments") or {}, ("model", "method")),
                           "result": _action(t.get("result") or {})} for t in run.get("tools", [])
                          if str(t.get("name", "")).removeprefix("mcp_odoo_") in {"execute_method", "execute_approved_write"}
                          or (isinstance(t.get("result"), dict) and t["result"].get("action_id"))]
        slim["events"] = [_action(e) for e in run.get("events", []) if e.get("type") == "reconciliation"]
        runs[key] = slim
    # Search hits only helped exploration. Re-read targets selected by the existing
    # receipt rules or explicitly bound by the user, then follow live relationships.
    targets = {(r["model"], r["id"]) for r in selected["references"] if r.get("purpose") != "source"}
    kind = business.get("type", "sale_invoice")
    if kind in sale_view.ENTERPRISE_TYPES:
        action_targets, _ = sale_view.enterprise_view.action_targets(sorted(runs.values(), key=sale_view._run_sort_key))
        targets.update(action_targets)
    else:
        for model, target_kind in (("sale.order", "sale_invoice"), ("purchase.order", "purchase")):
            if target_kind == kind or kind == "sale_purchase_invoice":
                targets.update((model, i) for i in sale_view._target_order_ids_for_runs(list(runs.values()), target_kind))
    if targets and kind != "invoice_delivery":
        if not runs:
            # An unexecuted workspace may already reference an existing document.
            runs["readback_targets"] = {"id": None, "business_id": business_id, "documents": []}
        newest = max(runs.values(), key=sale_view._run_sort_key)
        newest["documents"] = [{"model": model, "id": record_id} for model, record_id in sorted(targets)]
    approvals = {}
    for key, approval in state.get("approvals", {}).items():
        evidence = (approval.get("prestate") or {}).get("invoice_mail")
        if approval.get("business_id") == business_id and isinstance(evidence, dict):
            frozen = _pick(evidence, ("subject", "body", "email_to", "last_message_id"))
            frozen.update(invoice=_pick(evidence.get("invoice", {}), ("id",)),
                          recipient=_pick(evidence.get("recipient", {}), ("id",)),
                          attachment=_pick(evidence.get("attachment", {}), ("checksum", "file_size", "mimetype")))
            approvals[key] = {**_pick(approval, ("action_id", "run_id", "status", "model", "operation")),
                              "action_id": approval.get("action_id", key), "business_id": business_id,
                              "prestate": {"invoice_mail": frozen}}
    return {"session_id": session_id, "business_id": business_id, "connection": copy.deepcopy(connection),
            "state": {"businesses": {business_id: selected}, "runs": runs, "approvals": approvals}}


def _failure(text):
    return "permission_denied" if any(word in str(text).lower() for word in
        ("accesserror", "access denied", "permission", "forbidden", "无权", "权限", "restricted")) else "unavailable"


def read_business_status(context, reads, *, session_id, connection):
    """Use current credentials and the existing verifier; never return cached green checks."""
    result = {"success": False, "status": "unknown", "source": "native_business_readback",
              "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
              "verification_status": "unknown", "documents": [], "checks": [], "delivery_receipts": []}
    business_id = context.get("business_id")
    state = copy.deepcopy(context.get("state") or {})
    business = state.get("businesses", {}).get(business_id)
    if (context.get("session_id") != session_id or context.get("connection") != connection
            or not business or business.get("session_id") != session_id
            or business.get("odoo_connection") != connection):
        return {**result, "status": "scope_mismatch"}
    result.update(business_id=business_id, business_type=business.get("type"), completion_target=business.get("completion_target"))
    changed_bindings = {}
    try:
        # Refresh before invoice_mail.requested: that helper validates reference fields.
        # Cached fields must not validate a revoked role or an old customer relationship.
        for index, reference in enumerate(business.get("references", [])):
            fields = set(reference.get("fields", {})) | {"id"}
            if business.get("type") == "invoice_delivery":
                from erp_harness.erp import invoice_mail
                fields.update({"account.move": invoice_mail.INVOICE_FIELDS,
                               "res.partner": invoice_mail.CONTACT_FIELDS}.get(reference["model"], ()))
            payload = reads.call("read_record", {"model": reference["model"], "record_id": reference["id"], "fields": sorted(fields)})
            if fields.intersection(payload.get("redacted_fields", [])):
                return {**result, "status": "permission_denied"}
            row, error = sale_view._read_result(payload, reference["id"])
            if error or not fields.issubset(row or {}):
                return {**result, "status": _failure(error or "required fields unavailable")}
            changed = [key for key, ids in reference.get("expected_relations", {}).items()
                       if ids != sale_view._relation_ids(row.get(key))]
            if changed and reference.get("purpose") != "source":
                changed_bindings[f"requested_reference_{index}"] = changed
            reference["fields"] = row
        sale_view.refresh_business(state, business_id, reads)
    except Exception as exc:  # noqa: BLE001 - the read boundary fails closed for every backend error.
        return {**result, "status": _failure(f"{type(exc).__name__}: {exc}")}
    readback = business.get("readback") or {}
    checks = readback.get("checks", [])
    # Fresh facts and the original request binding are separate checks. Updating
    # both invoice and contact to a new customer must not redefine the request.
    for check in checks:
        if check.get("name") in changed_bindings:
            check.update(status="failed", detail="原始引用的业务关系已变化：" + ", ".join(changed_bindings[check["name"]]))
    if changed_bindings:
        readback["outcome"] = sale_view._outcome(checks, business.get("type", "sale_invoice"), business.get("completion_target", "posted"))
        readback["verification_status"] = readback["outcome"]["status"]
    errors = [c for c in checks if str(c.get("name", "")).startswith("read_")]
    if business.get("type") == "invoice_delivery":
        errors.extend(c for c in checks if c.get("name") == "invoice_recipient_verified" and c.get("status") != "passed")
    if errors or readback.get("stale"):
        status = "permission_denied" if any(_failure(c.get("detail", "")) == "permission_denied" for c in errors) else "unavailable"
        return {**result, "status": status}
    result.update(success=True, status="ok", verification_status=readback.get("verification_status", "unknown"),
                  observed_at=readback.get("observed_at", result["observed_at"]), outcome=readback.get("outcome"),
                  latest_run_id=readback.get("latest_run_id"))
    documents = [d for d in readback.get("documents", []) if d.get("source") == "refresh_native_read"]
    result["documents"] = [_pick(d, ("model", "id", "name", "state", "fields", "observed_at")) for d in documents[:20]]
    result["checks"] = [_pick(c, ("name", "label", "status", "detail")) for c in checks[:40]]
    if business.get("type") == "invoice_delivery":
        result["receipt_evidence_scope"] = "Live recheck of matching recorded delivery; this does not prove latest_run_id sent or resent it."
    for receipt in business.get("delivery_receipts", []):
        evidence = receipt.get("evidence") or {}
        result["delivery_receipts"].append({"status": receipt.get("status"), **_pick(evidence,
            ("delivery", "invoice_id", "recipient_id", "email_to", "notice")),
            "message_ids": [m.get("message_id") for m in evidence.get("messages", [])]})
    result["truncated"] = len(documents) > 20 or len(checks) > 40
    result["next_read"] = "read_odoo_reference for exact document fields; this status query never authorizes a write or resend"
    # Keep full source evidence in host logs. The model can request exact record fields next.
    for key in ("documents", "checks", "delivery_receipts"):
        while result[key] and len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 16_384:
            result[key].pop()
            result["truncated"] = True
    return result
