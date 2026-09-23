"""Read the same invoiceable quantities used by Odoo's sale invoice wizard."""
from __future__ import annotations

import math

from .business_operations import _Evidence, _number
from .store import ActionStore
from .write_guards import _id

_ORDER_FIELDS = ("name", "state", "company_id", "partner_id", "currency_id", "order_line", "invoice_ids")
_LINE_FIELDS = ("order_id", "name", "display_type", "is_downpayment", "product_id",
                "product_uom_qty", "qty_delivered", "qty_invoiced", "qty_to_invoice")
_NEXT_STEP = {
    "invoiceable_lines_found": "Use the regular sale invoice wizard; execution rechecks these quantities.",
    "order_not_confirmed": "This order is not confirmed. Ask whether to confirm it; do not create a down payment to bypass confirmation.",
    "order_cancelled": "This order is cancelled. Explain its state and ask for the intended order; do not invoice or create a down payment.",
    "already_invoiced": "Read the linked existing invoices and report their state; do not create another invoice or down payment.",
    "awaiting_delivery": "The delivery-based lines have no invoiceable quantity. Ask whether to wait for delivery or authorize a down payment with an explicit amount or percentage. Do not change invoice policy or deliver goods without instruction.",
    "final_adjustment_not_selected": "Only negative adjustment quantities remain and final deduction is not selected. Ask whether final billing is intended; do not change the wizard choice automatically.",
    "final_adjustment_only": "Only negative final adjustment or down-payment deduction lines are invoiceable. This can create a credit note; it is not an ordinary goods invoice. Explain the quantities and obtain the user's explicit final-adjustment/refund decision before proposing or executing it. Do not treat this as permission to issue a normal invoice.",
    "no_invoiceable_lines": "Explain the per-order conditions and existing invoices. Do not retry another API or create a down payment to bypass them. Ask the user for the missing business decision.",
}


class InvoiceEligibilityError(ValueError):
    def __init__(self, report: dict) -> None:
        self.report = report
        super().__init__(report["next_step"])


def _nonzero(quantity: float, digits: int) -> bool:
    # Odoo 19 uses Product Unit decimal precision here, not the line's UOM rounding.
    # Match float_is_zero's HALF-UP epsilon at the half-unit boundary.
    value = abs(quantity) * 10.0 ** digits
    if not value:
        return False
    return not math.isfinite(value) or round(value + 2 ** (math.log2(value) - 50)) != 0


def inspect_invoice_eligibility(runtime, order_ids: list[int], *, instance: str = "default",
                                final: bool = True) -> dict:
    """Complete scoped read; missing evidence raises, never becomes 'nothing to invoice'."""
    if (not isinstance(order_ids, list) or not 1 <= len(order_ids) <= 20
            or any(type(i) is not int or i <= 0 for i in order_ids)
            or type(final) is not bool):
        raise ValueError("invoice eligibility requires 1 to 20 positive sale order IDs and a boolean final")
    refresh = getattr(runtime, "_refresh_scope", None)
    if refresh is not None:
        refresh()
    g = _Evidence(runtime, {"instance": instance})
    orders = g.many("sale.order", order_ids, _ORDER_FIELDS)
    line_ids = {i for order in orders for i in order["order_line"]}
    if any(type(i) is not int or i <= 0 for i in line_ids):
        raise ValueError("invoice eligibility source lines are incomplete")
    lines = g.find("sale.order.line", [["id", "in", sorted(line_ids)]], _LINE_FIELDS) if line_ids else []
    if {row["id"] for row in lines} != line_ids:
        raise ValueError("invoice eligibility source lines are unreadable or incomplete")
    # fields_get resolves this float's Product Unit precision under the existing
    # business identity. Reading decimal.precision records needs administrator ACLs.
    metadata = g.client.execute_method("sale.order.line", "fields_get",
                                       allfields=["qty_to_invoice"], attributes=["type", "digits"])
    precision = metadata.get("qty_to_invoice", {}) if isinstance(metadata, dict) else {}
    declared = precision.get("digits")
    if (precision.get("type") != "float" or not isinstance(declared, (list, tuple))
            or len(declared) != 2 or type(declared[1]) is not int or not 0 <= declared[1] <= 15):
        raise ValueError("invoice eligibility Product Unit precision is unavailable")
    digits = declared[1]
    product_ids = {_id(row["product_id"]) for row in lines if not row["display_type"]}
    product_ids.discard(None)  # Odoo 19 down payments can have no product.
    products = g.find("product.product", [["id", "in", sorted(product_ids)]], ("invoice_policy",)) if product_ids else []
    if {row["id"] for row in products} != product_ids:
        raise ValueError("invoice eligibility product policies are unreadable or incomplete")
    policies = {row["id"]: row["invoice_policy"] for row in products}
    summaries = []
    for order in orders:
        owned = [line for line in lines if line["id"] in order["order_line"]]
        if any(_id(line["order_id"]) != order["id"] for line in owned):
            raise ValueError("invoice eligibility source line belongs to another order")
        facts, eligible_count = [], 0
        for line in owned:
            if line["display_type"]:
                continue
            quantity = _number(line["qty_to_invoice"])
            eligible = _nonzero(quantity, digits) and (quantity > 0 or final)
            eligible_count += int(eligible)
            facts.append({"id": line["id"], "name": line["name"],
                          "invoice_policy": policies.get(_id(line["product_id"])),
                          **{k: line[k] for k in ("product_uom_qty", "qty_delivered", "qty_invoiced", "qty_to_invoice", "is_downpayment")},
                          "eligible": eligible})
        positive_count = sum(row["eligible"] and _number(row["qty_to_invoice"]) > 0 for row in facts)
        negative_count = sum(row["eligible"] and _number(row["qty_to_invoice"]) < 0 for row in facts)
        if eligible_count:
            reason = "invoiceable_lines_found" if positive_count else "final_adjustment_only"
        elif order["state"] in {"draft", "sent"}:
            reason = "order_not_confirmed"
        elif order["state"] == "cancel":
            reason = "order_cancelled"
        elif facts and all(_number(row["qty_invoiced"]) > 0 and _number(row["qty_invoiced"]) >= _number(row["product_uom_qty"])
                           and _number(row["qty_to_invoice"]) == 0 for row in facts) and order["invoice_ids"]:
            reason = "already_invoiced"
        elif not final and any(_number(row["qty_to_invoice"]) < 0 and _nonzero(_number(row["qty_to_invoice"]), digits) for row in facts):
            reason = "final_adjustment_not_selected"
        elif any(row["invoice_policy"] == "delivery" and _number(row["qty_delivered"]) < _number(row["product_uom_qty"]) for row in facts):
            reason = "awaiting_delivery"
        else:
            reason = "no_invoiceable_lines"
        summaries.append({**{key: order[key] for key in ("id", "name", "state", "company_id", "partner_id", "currency_id", "invoice_ids")},
                          "reason_code": reason, "next_step": _NEXT_STEP[reason],
                          "eligible_line_count": eligible_count, "positive_line_count": positive_count,
                          "negative_line_count": negative_count, "line_count": len(facts),
                          "lines": facts[:20], "line_samples_complete": len(facts) <= 20})
    eligible = any(order["eligible_line_count"] for order in summaries)
    positive_count = sum(order["positive_line_count"] for order in summaries)
    negative_count = sum(order["negative_line_count"] for order in summaries)
    reasons = {order["reason_code"] for order in summaries}
    reason = ("invoiceable_lines_found" if positive_count else "final_adjustment_only") if eligible else next(iter(reasons)) if len(reasons) == 1 else "no_invoiceable_lines"
    # A batch may legitimately skip a fully invoiced order and invoice another one.
    return {"status": "eligible" if eligible else "blocked", "complete": True,
            "reason_code": reason,
            "final": final, "order_count": len(orders), "orders": summaries[:20],
            "positive_line_count": positive_count, "negative_line_count": negative_count,
            "order_samples_complete": len(summaries) <= 20,
            "snapshot_sha256": ActionStore.digest([orders, lines, products, precision, final]),
            "requires_business_choice": reason == "final_adjustment_only" or not eligible and reason != "already_invoiced",
            "next_step": _NEXT_STEP[reason]}
