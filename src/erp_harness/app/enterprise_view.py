"""新业务的独立回读。动作回执选目标，当前 Odoo 记录证明状态。"""
from decimal import Decimal

MODELS = {
    "inventory": "stock.picking", "manufacturing": "mrp.production",
    "payment": "account.payment", "refund": "account.move", "reconciliation": "account.move.line",
}
FIELDS = {
    "stock.picking": ("name", "state", "company_id", "partner_id", "move_ids", "backorder_ids", "return_id"),
    "stock.move": ("state", "company_id", "product_id", "product_uom_qty", "quantity", "location_id", "location_dest_id", "origin_returned_move_id"),
    "mrp.production": ("name", "state", "company_id", "product_id", "product_qty", "qty_produced", "bom_id", "move_raw_ids", "move_finished_ids", "date_start", "date_finished"),
    "account.payment": ("name", "state", "company_id", "partner_id", "currency_id", "amount", "move_id", "invoice_ids", "is_reconciled", "is_matched", "reconciled_statement_line_ids"),
    "account.move": ("name", "state", "move_type", "company_currency_id", "company_id", "partner_id", "currency_id", "amount_total", "amount_residual", "line_ids", "reversed_entry_id", "statement_line_id"),
    "account.move.line": ("name", "parent_state", "company_id", "partner_id", "currency_id", "account_id", "move_id", "balance", "amount_residual", "reconciled", "full_reconcile_id", "matched_debit_ids", "matched_credit_ids", "payment_id"),
    "account.bank.statement.line": ("payment_ref", "company_id", "amount", "move_id", "is_reconciled"),
    "res.currency": ("name", "rounding"),
}
RELATIONS = {
    "stock.picking": {"move_ids": "stock.move"},
    "mrp.production": {"move_raw_ids": "stock.move", "move_finished_ids": "stock.move"},
    "account.payment": {"move_id": "account.move", "invoice_ids": "account.move", "reconciled_statement_line_ids": "account.bank.statement.line", "currency_id": "res.currency"},
    "account.move": {"line_ids": "account.move.line", "reversed_entry_id": "account.move", "statement_line_id": "account.bank.statement.line", "currency_id": "res.currency", "company_currency_id": "res.currency"},
    "account.move.line": {"move_id": "account.move", "payment_id": "account.payment"},
    "account.bank.statement.line": {"move_id": "account.move"},
}


def ids(value):
    if type(value) is int:
        return [value]
    if isinstance(value, list):
        return value[:1] if len(value) == 2 and isinstance(value[1], str) else [v for v in value if type(v) is int]
    return []


def action_targets(runs):
    """后续账本核对覆盖旧回执。未知写入不会被早先的成功掩盖。"""
    receipts = {}
    for run in runs:
        for tool in run.get("tools", []):
            result = tool.get("result") or {}
            if result.get("action_id"):
                receipts[result["action_id"]] = (result, tool.get("arguments") or {}, run.get("id"))
        for event in run.get("events", []):
            if event.get("type") == "reconciliation" and event.get("action_id"):
                receipts[event["action_id"]] = (event, event, run.get("id"))
    targets, pending = {}, False
    for result, arguments, run_id in receipts.values():
        status = result.get("action_status", result.get("status"))
        pending |= status in {"needs_reconciliation", "executing", "unknown", "pending_approval", "approved"}
        verification = result.get("verification") or {}
        if status != "verified" or verification.get("status") != "satisfied":
            continue
        evidence = verification.get("evidence") or {}
        model = evidence.get("record_model") or result.get("model") or arguments.get("model")
        records = evidence.get("records", evidence.get("record_ids", []))
        related = evidence.get("related_records", []) or [{"model": model, "id": r.get("id") if isinstance(r, dict) else r} for r in records]
        for record in related:
            if record.get("model") in FIELDS and type(record.get("id")) is int:
                targets[(record["model"], record["id"])] = run_id
    return targets, pending


def readback(kind, target, runs, read):
    targets, pending = action_targets(runs)
    if target == "read_only":
        targets = {(d["model"], d["id"]): r.get("id") for r in runs for d in r.get("documents", []) if d.get("model") in FIELDS and type(d.get("id")) is int}
    queue, rows, failures = list(targets), {}, {}
    while queue:
        key = queue.pop()
        if key in rows or key in failures:
            continue
        model, record_id = key
        row, error = read(model, record_id, ("id", *FIELDS[model]))
        if error:
            failures[key] = error
            continue
        if any(f not in row for f in FIELDS[model]):
            failures[key] = "required business fields were not observed"
            continue
        rows[key] = row
        for field, related_model in RELATIONS.get(model, {}).items():
            queue.extend((related_model, i) for i in ids(row.get(field)))
    primary = [row for (model, i), row in rows.items() if model == MODELS[kind] and (model, i) in targets]
    if kind == "refund":
        primary = [r for r in primary if r.get("move_type") in {"out_refund", "in_refund"}]
    checks = []
    def check(name, label, value):
        checks.append({"name": name, "label": label, "status": "unknown" if value is None else "passed" if value else "failed", "detail": label, "source": "native_readback"})
    check("enterprise_observed", "目标单据完整回读", bool(primary) and not failures)
    if target != "read_only":
        check("enterprise_settled", "不存在待核对或未执行的写入", not pending)
        ok = bool(primary)
        if target == "draft":
            ok &= all(r.get("state") == "draft" for r in primary)
        elif kind in {"inventory", "manufacturing"}:
            states = {"done"} if target == "done" else {"confirmed", "assigned", "waiting", "progress", "to_close"}
            ok &= all(r.get("state") in states for r in primary)
            if target == "done":
                moves = [r for (m, _), r in rows.items() if m == "stock.move"]
                ok &= bool(moves) and all(r["state"] == "done" and r["quantity"] >= 0 and r["location_id"] != r["location_dest_id"] for r in moves)
                if kind == "manufacturing":
                    ok &= all(r["bom_id"] and r["qty_produced"] == r["product_qty"] for r in primary)
        else:
            moves = [r for (m, _), r in rows.items() if m == "account.move"]
            lines = {i: r for (m, i), r in rows.items() if m == "account.move.line"}
            def zero(amount, currency):
                rounding = rows.get(("res.currency", next(iter(ids(currency)), 0)), {}).get("rounding")
                return rounding is not None and abs(Decimal(str(amount))) < Decimal(str(rounding)) / 2
            ok &= bool(moves) and all(r["state"] == "posted" and r["line_ids"] and all(i in lines for i in r["line_ids"]) and zero(sum(Decimal(str(lines[i]["balance"])) for i in r["line_ids"]), r["company_currency_id"]) for r in moves)
            if kind == "refund":
                for r in primary:
                    original = rows.get(("account.move", next(iter(ids(r["reversed_entry_id"])), 0)))
                    ok &= bool(original) and all(original[f] == r[f] for f in ("company_id", "partner_id", "currency_id"))
            if target == "reconciled":
                payments = [r for (m, _), r in rows.items() if m == "account.payment"]
                bank = [r for (m, _), r in rows.items() if m == "account.bank.statement.line"]
                ok &= bool(bank) and all(r["is_reconciled"] for r in bank)
                ok &= all(r["is_matched"] and r["is_reconciled"] and r["reconciled_statement_line_ids"] for r in payments)
                if kind == "payment":
                    ok &= bool(payments)
                if kind == "refund":
                    ok &= all(zero(r["amount_residual"], r["currency_id"]) for r in primary)
                if kind == "reconciliation":
                    ok &= all(r["reconciled"] and r["full_reconcile_id"] for r in primary)
        check("enterprise_final", "终态、业务来源与账务关系回读", ok if primary and not failures else None)
    return rows, failures, checks, targets
