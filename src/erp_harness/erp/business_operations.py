"""Odoo 19 business method contracts. Receipts describe effects, never whole-task success."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from erp_harness.erp.store import ActionStore
from erp_harness.erp.write_guards import _Guard, _id

ENTERPRISE_METHODS = (
    "stock.picking.action_confirm", "stock.picking.action_assign", "stock.picking.button_validate",
    "stock.backorder.confirmation.process", "stock.return.picking.action_create_returns",
    "mrp.production.action_confirm", "mrp.production.action_assign",
    "mrp.production.set_qty_producing", "mrp.production.button_mark_done",
    "account.payment.register.action_create_payments", "account.move.reversal.reverse_moves",
    "account.move.line.reconcile",
)
_METHODS = frozenset(ENTERPRISE_METHODS) - {"mrp.production.action_confirm"}
FINANCE_MODELS = frozenset({"account.payment.register", "account.move.reversal", "account.move.line"})
ONE_SHOT_METHODS = frozenset({"account.payment.register.action_create_payments", "account.move.reversal.reverse_moves", "stock.return.picking.action_create_returns"})
_MOVE = ("company_id", "product_id", "product_uom", "product_uom_qty", "quantity", "state",
         "location_id", "location_dest_id", "origin_returned_move_id", "move_orig_ids", "date", "picked")
_PICKING = ("company_id", "partner_id", "state", "move_ids", "backorder_ids", "return_ids",
            "return_id", "picking_type_id", "location_id", "location_dest_id", "date_done")
_INVOICE = ("company_id", "partner_id", "currency_id", "state", "move_type", "amount_total",
            "amount_residual", "line_ids", "reversal_move_ids", "reversed_entry_id")
_LINE = ("company_id", "partner_id", "currency_id", "account_id", "move_id", "parent_state",
         "amount_residual", "amount_residual_currency", "balance", "reconciled", "full_reconcile_id",
         "matched_debit_ids", "matched_credit_ids", "payment_id")
_PAYMENT = ("company_id", "partner_id", "currency_id", "journal_id", "amount", "payment_type",
            "partner_type", "state", "move_id", "invoice_ids", "is_matched", "reconciled_statement_line_ids")


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("business quantity or amount is not finite")
    return float(value)


def _date(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("manufacturing start and finish dates require explicit evidence")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _same(rows: list[dict], field: str, expected: int | None = None) -> int:
    values = {_id(row.get(field)) for row in rows}
    if len(values) != 1 or None in values or (expected is not None and values != {expected}):
        raise ValueError(f"business records must have the same {field}")
    return next(iter(values))


class _Evidence(_Guard):
    def many(self, model: str, ids: list[int], fields: tuple[str, ...]) -> list[dict]:
        return [self.read(model, item, fields) for item in sorted(set(ids))]

    def find(self, model: str, domain: list, fields: tuple[str, ...]) -> list[dict]:
        policy = getattr(self.runtime, "policy", None)
        if policy is not None and policy.restricted_fields(self.payload["instance"], model, {"id", *fields}):
            raise ValueError(f"business guard evidence unavailable for {model}")
        rows = self.client.search_read(model, domain, fields=["id", *fields], limit=0, order="id")
        for row in rows:
            if not _id(row.get("id")) or any(field not in row for field in fields):
                raise ValueError(f"business guard evidence incomplete for {model}")
        return rows

    def rounding(self, currency: int) -> Decimal:
        value = _number(self.read("res.currency", currency, ("rounding",))["rounding"])
        if value <= 0:
            raise ValueError("currency rounding must be positive")
        return Decimal(str(value))


def handles(payload: dict) -> bool:
    return f"{payload.get('model')}.{payload.get('method')}" in _METHODS


def execution_kwargs(payload: dict, prestate: dict) -> dict:
    """Derive UI-only wizard context from the already approved Odoo relationship."""
    kwargs = dict(payload.get("kwargs", {}))
    if (payload.get("model"), payload.get("method")) == ("stock.backorder.confirmation", "process"):
        kwargs["context"] = {"button_validate_picking_ids": [r["id"] for r in prestate["enterprise"]["records"]]}
    return kwargs


def _available(g: _Evidence, moves: list[dict], quantity_field: str) -> None:
    needs: dict[tuple[int, int, int], float] = {}
    for move in moves:
        if _number(move[quantity_field]) <= 0:
            continue
        location = g.read("stock.location", _id(move["location_id"]), ("usage", "company_id"))
        if location["usage"] != "internal":
            continue
        company = _id(move["company_id"])
        if _id(location["company_id"]) not in {None, company}:
            raise ValueError("stock source belongs to another company")
        product = g.read("product.product", _id(move["product_id"]), ("is_storable", "uom_id"))
        if not product["is_storable"]:
            continue
        unit = g.read("uom.uom", _id(move["product_uom"]), ("factor",))
        product_unit = g.read("uom.uom", _id(product["uom_id"]), ("factor",))
        factor, base_factor = _number(unit["factor"]), _number(product_unit["factor"])
        if factor <= 0 or base_factor <= 0:
            raise ValueError("stock unit conversion must be positive")
        key = (company, _id(move["product_id"]), _id(move["location_id"]))
        needs[key] = needs.get(key, 0) + _number(move[quantity_field]) * factor / base_factor
    for (company, product, location), demand in needs.items():
        quants = g.find("stock.quant", [["company_id", "=", company], ["product_id", "=", product], ["location_id", "child_of", location]], ("quantity", "reserved_quantity"))
        if sum(_number(q["quantity"]) for q in quants) + 1e-8 < demand:
            raise ValueError("stock is not physically available; procure or transfer it first")
        for quant in quants:
            g.read("stock.quant", quant["id"], ("quantity", "reserved_quantity"))


def _picking(g: _Evidence, ids: list[int], method: str) -> dict:
    rows = g.many("stock.picking", ids, _PICKING)
    company = _same(rows, "company_id")
    moves = g.many("stock.move", [i for row in rows for i in row["move_ids"]], _MOVE)
    if not moves:
        raise ValueError("transfer has no stock moves")
    _same(moves, "company_id", company)
    if method in {"button_validate", "process"}:
        if all(row["state"] == "done" for row in rows):
            return {"kind": "picking", "records": rows, "moves": moves, "method": method}
        if any(row["state"] not in {"confirmed", "waiting", "assigned"} for row in rows):
            raise ValueError("confirm the transfer before validating actual quantities")
        if not any(_number(move["quantity"]) > 0 for move in moves):
            raise ValueError("transfer requires explicit non-zero actual quantities")
        for move in moves:
            qty, demand = _number(move["quantity"]), _number(move["product_uom_qty"])
            if qty < 0 or qty > demand + 1e-8:
                raise ValueError("actual transfer quantity must be within its requested quantity")
        _available(g, moves, "quantity")
        if method == "button_validate":
            for row in rows:
                partial = any(m["id"] in row["move_ids"] and _number(m["quantity"]) < _number(m["product_uom_qty"]) - 1e-8 for m in moves)
                policy = g.read("stock.picking.type", _id(row["picking_type_id"]), ("create_backorder",))
                if partial and policy["create_backorder"] != "always":
                    raise ValueError("partial transfer needs an explicit keep-backorder decision: create stock.backorder.confirmation with pick_ids and process it, or use an operation type configured to always keep backorders")
    return {"kind": "picking", "records": rows, "moves": moves, "method": method}


def _bom_consumption(g: _Evidence, production: dict, moves: list[dict], completing: bool) -> dict:
    bom_id = _id(production["bom_id"])
    bom = g.read("mrp.bom", bom_id, ("type", "consumption", "product_id", "product_tmpl_id", "product_qty", "product_uom_id", "bom_line_ids"))
    # Odoo copies this policy onto the MO; later BOM edits do not change it.
    consumption = production["consumption"]
    if consumption not in {"strict", "warning", "flexible"}:
        raise ValueError("manufacturing consumption policy is unavailable; requires human review")
    product = g.read("product.product", _id(production["product_id"]), ("product_tmpl_id",))
    if bom["type"] != "normal" or _id(bom["product_id"]) not in {None, _id(production["product_id"])} or _id(bom["product_tmpl_id"]) != _id(product["product_tmpl_id"]):
        raise ValueError("production must use the matching normal BOM; complex explosion requires explicit native evidence")

    def factor(unit_id):
        value = _number(g.read("uom.uom", unit_id, ("factor",))["factor"])
        if value <= 0:
            raise ValueError("BOM unit factor must be positive")
        return value

    if _number(bom["product_qty"]) <= 0:
        raise ValueError("BOM output quantity must be positive")
    ratio = _number(production["product_qty"]) * factor(_id(production["product_uom_id"])) / factor(_id(bom["product_uom_id"])) / _number(bom["product_qty"])
    expected, planned, actual, tolerances = {}, {}, {}, {}

    def quantity(product_id, unit_id, amount):
        product = g.read("product.product", product_id, ("uom_id",))
        base = g.read("uom.uom", _id(product["uom_id"]), ("factor", "rounding"))
        tolerances[product_id] = max(_number(base["rounding"]) / 2, 1e-8)
        return _number(amount) * factor(unit_id) / factor(_id(product["uom_id"]))

    lines = g.many("mrp.bom.line", bom["bom_line_ids"], ("product_id", "product_qty", "product_uom_id", "bom_product_template_attribute_value_ids", "child_bom_id"))
    for line in lines:
        if line["bom_product_template_attribute_value_ids"] or (_id(line["child_bom_id"]) and g.read("mrp.bom", _id(line["child_bom_id"]), ("type",))["type"] == "phantom"):
            raise ValueError("variant-filtered or phantom BOM requires native explosion evidence before completion")
        product_id = _id(line["product_id"])
        expected[product_id] = expected.get(product_id, 0) + ratio * quantity(product_id, _id(line["product_uom_id"]), line["product_qty"])
    for move in moves:
        product_id = _id(move["product_id"])
        planned[product_id] = planned.get(product_id, 0) + quantity(product_id, _id(move["product_uom"]), move["product_uom_qty"])
        actual[product_id] = actual.get(product_id, 0) + quantity(product_id, _id(move["product_uom"]), move["quantity"])
    variances = [{"product_id": p, "bom_quantity": expected.get(p, 0), "planned_quantity": planned.get(p, 0), "actual_quantity": actual.get(p, 0)}
                 for p in sorted(set(expected) | set(planned) | set(actual))
                 if abs(expected.get(p, 0) - planned.get(p, 0)) > tolerances[p] or (completing and abs(expected.get(p, 0) - actual.get(p, 0)) > tolerances[p])]
    if consumption == "strict" and variances:
        raise ValueError("strict BOM quantities disagree with component demand or consumption; restore quantities derived from the BOM before completing")
    if consumption == "warning" and variances:
        raise ValueError("manufacturing consumption deviation requires human review; the consumption-warning wizard is not supported, so no method was sent")
    return {"production_id": production["id"], "bom_id": bom_id, "consumption": consumption,
            "bom_consumption": bom["consumption"], "component_variances": variances}


def _production(g: _Evidence, ids: list[int], method: str) -> dict:
    fields = ("company_id", "product_id", "product_qty", "product_uom_id", "qty_producing", "qty_produced", "state",
              "bom_id", "consumption", "date_start", "date_deadline", "move_raw_ids", "move_finished_ids", "workorder_ids")
    rows = g.many("mrp.production", ids, fields)
    company = _same(rows, "company_id")
    raw = g.many("stock.move", [i for row in rows for i in row["move_raw_ids"]], _MOVE)
    finished = g.many("stock.move", [i for row in rows for i in row["move_finished_ids"]], _MOVE)
    if method == "button_mark_done" and all(row["state"] == "done" for row in rows):
        return {"kind": "production", "records": rows, "moves": raw, "finished": finished, "method": method}
    contracts = []
    if method != "action_assign":
        for row in rows:
            if row["state"] not in {"confirmed", "progress", "to_close"}:
                raise ValueError("production must be confirmed before producing")
            g.window(row)
            if not _id(row["bom_id"]):
                raise ValueError("production requires an explicit BOM")
            bom = g.read("mrp.bom", _id(row["bom_id"]), ("company_id", "operation_ids"))
            if _id(bom["company_id"]) not in {None, company}:
                raise ValueError("production BOM belongs to another company")
            if _number(row["qty_producing"]) <= 0 or _number(row["qty_producing"]) > _number(row["product_qty"]):
                raise ValueError("set an explicit producing quantity within the production demand")
            if method == "button_mark_done" and abs(_number(row["qty_producing"]) + _number(row["qty_produced"]) - _number(row["product_qty"])) > 1e-8:
                raise ValueError("partial manufacturing requires a separately reviewed backorder; this completion method requires the full remaining quantity")
            if method == "button_mark_done" and _date(row["date_start"]) > datetime.now(UTC):
                raise ValueError("production is scheduled to start in the future; approve a reschedule or wait before marking it done")
            contracts.append(_bom_consumption(g, row, [m for m in raw if m["id"] in row["move_raw_ids"]], method == "button_mark_done"))
            operations = set()
            for wo_id in row["workorder_ids"]:
                wo = g.read("mrp.workorder", wo_id, ("production_id", "operation_id", "workcenter_id", "state"))
                g.qualify(wo, row)
                operations.add(_id(wo["operation_id"]))
                if _id(wo["production_id"]) != row["id"]:
                    raise ValueError("workorder belongs to another production")
                center = g.read("mrp.workcenter", _id(wo["workcenter_id"]), ("company_id", "active"))
                if not center["active"] or _id(center["company_id"]) not in {None, company}:
                    raise ValueError("workcenter is inactive or belongs to another company")
                operation = g.read("mrp.routing.workcenter", _id(wo["operation_id"]), ("bom_id", "workcenter_id"))
                default_center = g.read("mrp.workcenter", _id(operation["workcenter_id"]), ("alternative_workcenter_ids",))
                if _id(operation["bom_id"]) != _id(row["bom_id"]) or _id(wo["workcenter_id"]) not in {_id(operation["workcenter_id"]), *default_center["alternative_workcenter_ids"]}:
                    raise ValueError("workorder must use its BOM operation's workcenter or an approved alternative")
            if operations != set(bom["operation_ids"]):
                raise ValueError("production workorders do not cover the BOM operations; regenerate or review the operation plan")
        if not raw or not finished:
            raise ValueError("production requires component and finished stock moves")
        _same(raw + finished, "company_id", company)
        for move in raw:
            product = g.read("product.product", _id(move["product_id"]), ("is_storable", "uom_id"))
            if not product["is_storable"]:
                continue
            production = next(row for row in rows if move["id"] in row["move_raw_ids"])
            for origin in g.many("stock.move", move["move_orig_ids"], ("state", "date", "company_id")):
                if origin["state"] != "done" or _id(origin["company_id"]) != company or (origin["date"] and production["date_start"] and origin["date"] > production["date_start"]):
                    raise ValueError("component source must be completed by the planned production start")
        _available(g, raw, "quantity" if method == "button_mark_done" else "product_uom_qty")
    return {"kind": "production", "records": rows, "moves": raw, "finished": finished, "method": method, "consumption_contracts": contracts}


def _payment(g: _Evidence, ids: list[int]) -> dict:
    if len(ids) != 1:
        raise ValueError("payment requires one register wizard")
    wizard = g.read("account.payment.register", ids[0], ("company_id", "partner_id", "currency_id", "journal_id", "line_ids", "amount", "payment_type", "partner_type", "group_payment", "payment_difference_handling", "payment_method_line_id", "partner_bank_id"))
    lines = g.many("account.move.line", wizard["line_ids"], _LINE)
    if not lines:
        raise ValueError("payment wizard has no payable source lines")
    company = _same(lines + [wizard], "company_id")
    _same(lines + [wizard], "partner_id")
    currency = _same(lines + [wizard], "currency_id")
    _same(lines, "account_id")
    invoice_ids = sorted({_id(line["move_id"]) for line in lines})
    invoices = g.many("account.move", invoice_ids, _INVOICE)
    _same(invoices + [wizard], "company_id")
    _same(invoices + [wizard], "partner_id")
    _same(invoices + [wizard], "currency_id")
    expected = {"out_invoice": ("inbound", "customer"), "in_invoice": ("outbound", "supplier"), "out_refund": ("outbound", "customer"), "in_refund": ("inbound", "supplier")}
    if any(row["state"] != "posted" or expected.get(row["move_type"]) != (wizard["payment_type"], wizard["partner_type"]) for row in invoices):
        raise ValueError("payment direction or partner type disagrees with the posted source invoices")
    for credit in (row for row in invoices if row["move_type"] in {"out_refund", "in_refund"}):
        original_id = _id(credit["reversed_entry_id"])
        if original_id is None:
            raise ValueError("cash refund requires an explicit original invoice relationship")
        original = g.read("account.move", original_id, _INVOICE)
        for field in ("company_id", "partner_id", "currency_id"):
            _same([credit, original], field)
        if original["state"] != "posted" or original["move_type"] != {"out_refund": "out_invoice", "in_refund": "in_invoice"}[credit["move_type"]]:
            raise ValueError("credit note is not linked to a compatible posted original invoice")
        credits = g.many("account.move", original["reversal_move_ids"], ("state", "amount_total"))
        if sum(Decimal(str(_number(r["amount_total"]))) for r in credits if r["state"] == "posted") > Decimal(str(_number(original["amount_total"]))) + g.rounding(currency) / 2:
            raise ValueError("posted credit notes exceed the original invoice total")
    if any(line["parent_state"] != "posted" or line["reconciled"] for line in lines):
        raise ValueError("source payment lines must be posted and still open")
    if len(invoice_ids) > 1 and not wizard["group_payment"]:
        raise ValueError("multi-invoice payment requires explicit group_payment")
    amount = Decimal(str(_number(wizard["amount"])))
    residual = sum((abs(Decimal(str(_number(line["amount_residual_currency"])))) for line in lines), Decimal(0))
    rounding = g.rounding(currency)
    if amount <= 0 or amount > residual + rounding / 2:
        raise ValueError("payment amount exceeds the currently open source residual or is not positive")
    if wizard["payment_difference_handling"] != "open":
        raise ValueError("payment write-offs require a separately approved accounting adjustment")
    journal = g.read("account.journal", _id(wizard["journal_id"]), ("company_id", "currency_id", "type"))
    if _id(journal["company_id"]) != company or journal["type"] not in {"bank", "cash"} or _id(journal["currency_id"]) not in {None, currency}:
        raise ValueError("payment journal company, type, or currency is incompatible")
    payment_method = g.read("account.payment.method.line", _id(wizard["payment_method_line_id"]), ("journal_id", "payment_account_id", "code"))
    if _id(payment_method["journal_id"]) != _id(wizard["journal_id"]) or not _id(payment_method["payment_account_id"]) or payment_method["code"] != "manual":
        raise ValueError("local payments require a manual journal method with an explicit outstanding account")
    if _id(wizard["partner_bank_id"]):
        bank = g.read("res.partner.bank", _id(wizard["partner_bank_id"]), ("partner_id", "company_id"))
        if _id(bank["partner_id"]) != _id(wizard["partner_id"]) or _id(bank["company_id"]) not in {None, company}:
            raise ValueError("payment bank account is not owned by the source partner and company")
    existing = g.find("account.payment", [["invoice_ids", "in", invoice_ids]], _PAYMENT)
    if any(row["state"] not in {"canceled", "rejected"} and row["state"] == "draft" for row in existing):
        raise ValueError("a draft payment already references these invoices; reconcile its status before paying again")
    return {"kind": "payment", "wizard": wizard, "lines": lines, "invoices": invoices, "existing": existing, "rounding": str(rounding)}


def method_prestate(runtime: Any, payload: dict) -> dict | None:
    if not handles(payload):
        return None
    if set(payload.get("kwargs", {})) != {"ids"}:
        raise ValueError("reviewed enterprise methods accept kwargs.ids only; business choices belong in the approved records")
    g = _Evidence(runtime, payload)
    ids, model, method = payload["kwargs"]["ids"], payload["model"], payload["method"]
    if model == "stock.picking":
        value = _picking(g, ids, method)
    elif model == "mrp.production":
        value = _production(g, ids, method)
    elif model == "stock.backorder.confirmation":
        wizards = g.many(model, ids, ("pick_ids", "backorder_confirmation_line_ids"))
        lines = g.many("stock.backorder.confirmation.line", [i for w in wizards for i in w["backorder_confirmation_line_ids"]], ("picking_id", "to_backorder"))
        if any(line["to_backorder"] is not True for line in lines):
            raise ValueError("canceling remaining quantities requires a separate reviewed cancellation")
        value = _picking(g, [i for w in wizards for i in w["pick_ids"]], "process")
        value["wizards"] = wizards
    elif model == "stock.return.picking":
        if len(ids) != 1:
            raise ValueError("stock return requires one source picking")
        wizard = g.read(model, ids[0], ("picking_id", "product_return_moves"))
        picking = g.read("stock.picking", _id(wizard["picking_id"]), _PICKING)
        if picking["state"] != "done":
            raise ValueError("returns require a completed original transfer")
        lines = g.many("stock.return.picking.line", wizard["product_return_moves"], ("move_id", "product_id", "quantity"))
        if len({_id(line["move_id"]) for line in lines}) != len(lines):
            raise ValueError("combine duplicate return lines for the same original stock move")
        moves = g.many("stock.move", picking["move_ids"], (*_MOVE, "returned_move_ids"))
        by_id = {row["id"]: row for row in moves}
        if not any(_number(line["quantity"]) > 0 for line in lines):
            raise ValueError("return requires a positive quantity")
        for line in lines:
            source = by_id.get(_id(line["move_id"]))
            if source is None or _id(source["product_id"]) != _id(line["product_id"]):
                raise ValueError("return line does not belong to the original product movement")
            returned = g.many("stock.move", source["returned_move_ids"], ("state", "product_uom_qty"))
            remaining = _number(source["quantity"]) - sum(_number(m["product_uom_qty"]) for m in returned if m["state"] != "cancel")
            if not 0 <= _number(line["quantity"]) <= remaining + 1e-8:
                raise ValueError("return quantity exceeds the original quantity minus existing returns")
        value = {"kind": "return", "wizard": wizard, "picking": picking, "lines": lines, "moves": moves}
    elif model == "account.payment.register":
        value = _payment(g, ids)
    elif model == "account.move.reversal":
        if len(ids) != 1:
            raise ValueError("reversal requires one wizard")
        wizard = g.read(model, ids[0], ("move_ids", "new_move_ids", "company_id", "journal_id", "date", "reason"))
        invoices = g.many("account.move", wizard["move_ids"], _INVOICE)
        if not invoices or wizard["new_move_ids"]:
            raise ValueError("reversal wizard has no originals or was already executed")
        _same(invoices + [wizard], "company_id")
        _same(invoices, "partner_id")
        _same(invoices, "currency_id")
        journal = g.read("account.journal", _id(wizard["journal_id"]), ("company_id", "type"))
        if _id(journal["company_id"]) != _id(wizard["company_id"]) or any(row["state"] != "posted" or row["move_type"] not in {"out_invoice", "in_invoice"} for row in invoices):
            raise ValueError("refund requires posted source invoices and a journal in their company")
        if not wizard["reason"]:
            raise ValueError("refund requires an explicit reason")
        for original in invoices:
            existing = g.many("account.move", original["reversal_move_ids"], ("state",))
            if any(row["state"] != "cancel" for row in existing):
                raise ValueError("an existing credit note already reverses this invoice; review it before creating another full reversal")
        value = {"kind": "reversal", "wizard": wizard, "invoices": invoices}
    else:
        lines = g.many("account.move.line", ids, _LINE)
        if len(lines) < 2:
            raise ValueError("reconciliation requires both sides of an accounting relationship")
        company = _same(lines, "company_id")
        currency = _same(lines, "currency_id")
        account = _same(lines, "account_id")
        partners = {_id(row["partner_id"]) for row in lines} - {None}
        if len(partners) > 1 or any(row["parent_state"] != "posted" or row["reconciled"] for row in lines):
            raise ValueError("reconciliation requires posted open lines for one partner")
        rounding = g.rounding(currency)
        residual = sum((Decimal(str(_number(row["amount_residual_currency"]))) for row in lines), Decimal(0))
        company_residual = sum((Decimal(str(_number(row["amount_residual"]))) for row in lines), Decimal(0))
        account_row = g.read("account.account", account, ("reconcile",))
        if not account_row["reconcile"] or abs(residual) > rounding / 2 or abs(company_residual) > rounding / 2:
            raise ValueError("manual full reconciliation requires a reconcilable account and balanced currency residuals")
        moves = g.many("account.move", sorted({_id(row["move_id"]) for row in lines}), ("company_id", "statement_line_id"))
        _same(moves, "company_id", company)
        value = {"kind": "reconcile", "lines": lines, "moves": moves, "rounding": str(rounding)}
    return {"enterprise": value, "enterprise_dependencies": [[m, i, list(f), ActionStore.digest(row)] for (m, i, f), row in sorted(g.rows.items())]}


def method_verify(runtime: Any, payload: dict, prestate: dict, result: Any) -> dict | None:
    if not handles(payload):
        return None
    g, before = _Evidence(runtime, payload), prestate["enterprise"]
    kind = before["kind"]
    evidence: dict = {}
    satisfied = False
    if kind == "picking":
        rows = g.many("stock.picking", [r["id"] for r in before["records"]], _PICKING)
        states = ({"confirmed", "waiting", "assigned"} if before["method"] == "action_confirm" else {"assigned"}) if before["method"] in {"action_confirm", "action_assign"} else {"done"}
        satisfied = bool(rows) and all(r["state"] in states for r in rows)
        if states == {"done"}:
            actual = g.many("stock.move", [m["id"] for m in before["moves"] if _number(m["quantity"]) > 0], _MOVE)
            expected = {m["id"]: m for m in before["moves"]}
            satisfied &= all(m["state"] == "done" and abs(_number(m["quantity"]) - _number(expected[m["id"]]["quantity"])) < 1e-8 for m in actual)
            remaining: dict[tuple, float] = {}
            for move in before["moves"]:
                key = tuple(_id(move[f]) for f in ("company_id", "product_id", "product_uom", "location_id", "location_dest_id"))
                remaining[key] = remaining.get(key, 0) + _number(move["product_uom_qty"]) - _number(move["quantity"])
            if any(q > 1e-8 for q in remaining.values()):
                original = {r["id"]: r for r in before["records"]}
                backorder_ids = [i for r in rows for i in r["backorder_ids"] if i not in original[r["id"]]["backorder_ids"]]
                backorders = g.many("stock.picking", backorder_ids, _PICKING)
                kept: dict[tuple, float] = {}
                for move in g.many("stock.move", [i for r in backorders for i in r["move_ids"]], _MOVE):
                    key = tuple(_id(move[f]) for f in ("company_id", "product_id", "product_uom", "location_id", "location_dest_id"))
                    if move["state"] != "cancel":
                        kept[key] = kept.get(key, 0) + _number(move["product_uom_qty"])
                satisfied &= all(abs(kept.get(key, 0) - quantity) <= 1e-8 for key, quantity in remaining.items()) and not (set(kept) - set(remaining))
        evidence = {"record_model": "stock.picking", "records": rows}
    elif kind == "production":
        workorders = []
        rows = g.many("mrp.production", [r["id"] for r in before["records"]], ("state", "qty_producing", "qty_produced", "product_qty", "move_raw_ids", "move_finished_ids", "workorder_ids", "date_start", "date_finished"))
        if before["method"] == "button_mark_done":
            satisfied = bool(rows) and all(r["state"] == "done" and abs(_number(r["qty_produced"]) - _number(r["product_qty"])) < 1e-8 for r in rows)
            satisfied &= all(r["date_start"] and r["date_finished"] and _date(r["date_start"]) <= _date(r["date_finished"]) for r in rows)
            raw = g.many("stock.move", [m["id"] for m in before["moves"]], _MOVE)
            expected = {m["id"]: m for m in before["moves"]}
            satisfied &= all(m["state"] == "done" and abs(_number(m["quantity"]) - _number(expected[m["id"]]["quantity"])) < 1e-8 for m in raw)
            original_workorders = {i for row in before["records"] for i in row["workorder_ids"]}
            current_workorders = {i for row in rows for i in row["workorder_ids"]}
            workorders = g.many("mrp.workorder", sorted(current_workorders), ("state", "production_id"))
            satisfied &= original_workorders == current_workorders and all(w["state"] == "done" and _id(w["production_id"]) in {r["id"] for r in rows} for w in workorders)
        elif before["method"] == "set_qty_producing":
            raw = g.many("stock.move", [m["id"] for m in before["moves"]], _MOVE)
            satisfied = bool(raw) and all(m["picked"] is True and _number(m["quantity"]) > 0 for m in raw)
        else:
            raw = g.many("stock.move", [m["id"] for m in before["moves"]], ("state",))
            satisfied = bool(raw) and all(m["state"] in {"assigned", "done"} for m in raw)
        evidence = {"record_model": "mrp.production", "records": rows, "workorder_records": workorders, "consumption_contracts": before.get("consumption_contracts", [])}
    elif kind == "return":
        original = before["picking"]
        current = g.read("stock.picking", original["id"], _PICKING)
        ids = sorted(set(current["return_ids"]) - set(original["return_ids"]))
        rows = g.many("stock.picking", ids, _PICKING)
        moves = g.many("stock.move", [i for r in rows for i in r["move_ids"]], _MOVE)
        expected = {_id(line["move_id"]): _number(line["quantity"]) for line in before["lines"] if _number(line["quantity"]) > 0}
        actual = {_id(m["origin_returned_move_id"]): _number(m["product_uom_qty"]) for m in moves}
        satisfied = len(rows) == 1 and _id(rows[0]["return_id"]) == original["id"] and _id(rows[0]["company_id"]) == _id(original["company_id"]) and actual == expected
        evidence = {"record_model": "stock.picking", "records": rows, "effect": "return transfer created; transfer validation is a separate action"}
    elif kind == "payment":
        wizard = before["wizard"]
        invoice_ids = [r["id"] for r in before["invoices"]]
        prior = {p["id"] for p in before["existing"]}
        payments = [p for p in g.find("account.payment", [["invoice_ids", "in", invoice_ids]], _PAYMENT) if p["id"] not in prior]
        current = g.many("account.move.line", [r["id"] for r in before["lines"]], _LINE)
        delta = sum(abs(Decimal(str(r["amount_residual_currency"]))) for r in before["lines"]) - sum(abs(Decimal(str(r["amount_residual_currency"]))) for r in current)
        amount, epsilon = Decimal(str(wizard["amount"])), Decimal(before["rounding"]) / 2
        satisfied = len(payments) == 1 and abs(delta - amount) <= epsilon
        if payments:
            p = payments[0]
            satisfied &= all(_id(p[f]) == _id(wizard[f]) for f in ("company_id", "partner_id", "currency_id", "journal_id")) and p["payment_type"] == wizard["payment_type"] and p["partner_type"] == wizard["partner_type"] and abs(Decimal(str(p["amount"])) - amount) <= epsilon and p["state"] in {"in_process", "paid"}
            move_id = _id(p["move_id"])
            if move_id:
                posted = g.read("account.move", move_id, ("state", "line_ids"))
                prior_links = {i for r in before["lines"] for f in ("matched_debit_ids", "matched_credit_ids") for i in r[f]}
                new_links = {i for r in current for f in ("matched_debit_ids", "matched_credit_ids") for i in r[f]} - prior_links
                links = g.many("account.partial.reconcile", sorted(new_links), ("debit_move_id", "credit_move_id"))
                source_ids, payment_lines = {r["id"] for r in current}, set(posted["line_ids"])
                linked_sources = set()
                for link in links:
                    pair = {_id(link["debit_move_id"]), _id(link["credit_move_id"])}
                    if pair & payment_lines:
                        linked_sources.update(pair & source_ids)
                changed_sources = {r["id"] for r in current if abs(Decimal(str(r["amount_residual_currency"]))) < abs(Decimal(str(next(b for b in before["lines"] if b["id"] == r["id"])["amount_residual_currency"])))}
                satisfied &= posted["state"] == "posted" and bool(changed_sources) and changed_sources <= linked_sources
            else:
                satisfied = False
        evidence = {"record_model": "account.payment", "records": payments, "source_residual_reduction": str(delta), "bank_reconciliation_verified": False}
    elif kind == "reversal":
        wizard = g.read("account.move.reversal", before["wizard"]["id"], ("new_move_ids",))
        rows = g.many("account.move", wizard["new_move_ids"], _INVOICE)
        originals = {r["id"]: r for r in before["invoices"]}
        satisfied = len(rows) == len(originals) and {_id(r["reversed_entry_id"]) for r in rows} == set(originals)
        for row in rows:
            original = originals.get(_id(row["reversed_entry_id"]))
            satisfied &= bool(original) and all(_id(row[f]) == _id(original[f]) for f in ("company_id", "partner_id", "currency_id")) and row["move_type"] == {"out_invoice": "out_refund", "in_invoice": "in_refund"}.get(original["move_type"])
        evidence = {"record_model": "account.move", "records": rows, "effect": "credit note created; posting and cash refund are separate actions"}
    else:
        rows = g.many("account.move.line", [r["id"] for r in before["lines"]], _LINE)
        epsilon = Decimal(before["rounding"]) / 2
        satisfied = bool(rows) and all(r["reconciled"] and _id(r["full_reconcile_id"]) and abs(Decimal(str(r["amount_residual_currency"]))) <= epsilon for r in rows)
        satisfied &= len({_id(r["full_reconcile_id"]) for r in rows}) == 1
        statement_ids = sorted({_id(m["statement_line_id"]) for m in before["moves"]} - {None})
        statements = g.many("account.bank.statement.line", statement_ids, ("is_reconciled",))
        evidence = {"record_model": "account.move.line", "records": rows, "bank_reconciliation_verified": bool(statements) and all(s["is_reconciled"] for s in statements), "statement_lines": statements}
    evidence["related_records"] = [{"model": evidence["record_model"], "id": row["id"]} for row in evidence.get("records", [])]
    evidence["related_records"].extend({"model": "mrp.workorder", "id": row["id"]} for row in evidence.get("workorder_records", []))
    return {"status": "satisfied" if satisfied else "not_satisfied", "evidence": evidence}
