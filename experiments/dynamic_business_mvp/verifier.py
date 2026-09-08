"""Independent read-only business checks for the dynamic MVP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

REF = "PI-DYNAMIC-MVP-20260908"
CUSTOMER_REF = "rF3119DF4C3_c01"
CUSTOMER = "Nimbus Bureau"
PRODUCT_CODE = "PF3119DF4C3-SPP-NBW-010"
PRODUCT = "Open-Plan Noise Barrier Wall"
QTY = 1.0
PAYMENT_TERM = "30 Days"
MODEL_NAMES = ("sale.order", "account.move", "purchase.order", "mrp.production")
RULE_NAMES = (
    "seed_identity_frozen", "one_new_sales_order", "sales_order_valid",
    "one_new_customer_invoice", "invoice_payment_and_amount",
    "invoice_line_link_unique", "no_new_purchase_or_manufacturing_order",
)


def connection() -> Any:
    import odoolib

    return odoolib.get_connection(
        hostname=os.environ.get("ODOO_HOST", "127.0.0.1"), protocol="json2",
        port=int(os.environ.get("ODOO_PORT", "8069")),
        database=os.environ.get("ODOO_DB", "bench"),
        login=os.environ.get("ODOO_USERNAME", "admin"),
        password=Path(os.environ.get("ODOO_API_KEY_FILE", "/etc/odoo/api_key")).read_text().strip(),
    )


def model(conn: Any, name: str) -> Any:
    return conn.get_model(name)


def search(conn: Any, name: str, domain: list[Any]) -> list[int]:
    return list(model(conn, name).search(domain=domain) or [])


def read_one(conn: Any, name: str, record_id: int, fields: list[str]) -> dict[str, Any]:
    rows = model(conn, name).read(ids=[record_id], fields=fields)
    return ((rows[0] if isinstance(rows, list) else rows) or {}) if record_id else {}


def rid(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return int(value[0]) if value else 0
    if isinstance(value, dict):
        return int(value.get("id") or 0)
    return int(value or 0)


def snapshot(conn: Any) -> dict[str, Any]:
    customer_ids = search(conn, "res.partner", [("ref", "=", CUSTOMER_REF)])
    product_ids = search(conn, "product.product", [("default_code", "=", PRODUCT_CODE)])
    term_ids = search(conn, "account.payment.term", [("name", "=", PAYMENT_TERM)])
    term = read_one(conn, "account.payment.term", term_ids[0], ["name", "line_ids"]) if len(term_ids) == 1 else {}
    term_lines = [read_one(conn, "account.payment.term.line", int(line_id), ["value", "value_amount", "delay_type", "nb_days"]) for line_id in (term.get("line_ids") or [])]
    customer = read_one(conn, "res.partner", customer_ids[0], ["name", "ref", "comment"]) if len(customer_ids) == 1 else {}
    product = read_one(conn, "product.product", product_ids[0], ["name", "default_code", "list_price"]) if len(product_ids) == 1 else {}
    all_ids = {name: search(conn, name, []) for name in MODEL_NAMES}
    return {"customer_ids": customer_ids, "product_ids": product_ids, "customer": customer, "product": product,
            "payment_term": {"ids": term_ids, "record": term, "lines": term_lines}, "all_ids": all_ids}


def same_term(baseline: dict[str, Any], term_id: Any, conn: Any) -> bool:
    frozen = baseline.get("payment_term", {})
    if frozen.get("ids") != [rid(term_id)] or not frozen.get("record"):
        return False
    current = read_one(conn, "account.payment.term", rid(term_id), ["name", "line_ids"])
    lines = [read_one(conn, "account.payment.term.line", int(line_id), ["value", "value_amount", "delay_type", "nb_days"]) for line_id in (current.get("line_ids") or [])]
    return current == frozen["record"] and lines == frozen.get("lines", []) and current.get("name") == PAYMENT_TERM


def exact_delta(baseline: dict[str, Any], after: dict[str, Any], model_name: str, expected: int) -> bool:
    return len(set(after.get("all_ids", {}).get(model_name, [])) - set(baseline.get("all_ids", {}).get(model_name, []))) == expected


def sales_order_valid(so: dict[str, Any], lines: list[dict[str, Any]], baseline: dict[str, Any], customer_id: int, product_id: int, list_price: float, conn: Any) -> bool:
    return (rid(so.get("partner_id")) == customer_id and so.get("state") in {"sale", "done"}
            and so.get("client_order_ref") == REF and bool(so.get("commitment_date"))
            and same_term(baseline, so.get("payment_term_id"), conn) and len(lines) == 1
            and rid(lines[0].get("product_id")) == product_id
            and abs(float(lines[0].get("product_uom_qty") or 0) - QTY) < 0.01
            and abs(float(lines[0].get("price_unit") or 0) - list_price) < 0.01)


def score_payload(checks: dict[str, bool], after: dict[str, Any], so_id: int | None, note: str = "") -> dict[str, Any]:
    passed = sum(1 for name in RULE_NAMES if checks.get(name) is True)
    failed = [name for name in RULE_NAMES if checks.get(name) is not True]
    return {"overall_score": round(100 * passed / len(RULE_NAMES), 2), "passed": passed == len(RULE_NAMES),
            "sales_order_id": so_id, "checks": checks, "note": note, "after": after,
            "rules": {"total": len(RULE_NAMES), "applicable": len(RULE_NAMES), "passed": passed,
                      "failed": len(failed), "not_applicable": 0, "failed_rules": failed}}


def check(conn: Any, baseline: dict[str, Any]) -> dict[str, Any]:
    after = snapshot(conn)
    checks = {name: False for name in RULE_NAMES}
    frozen_customer, frozen_product = baseline.get("customer", {}), baseline.get("product", {})
    frozen_term = baseline.get("payment_term", {})
    identity_ok = (len(after["customer_ids"]) == 1 and len(after["product_ids"]) == 1
                   and after["customer"] == frozen_customer and after["customer"].get("name") == CUSTOMER
                   and after["product"] == frozen_product and after["product"].get("name") == PRODUCT
                   and after["product"].get("default_code") == PRODUCT_CODE
                   and len(frozen_term.get("ids", [])) == 1 and len(frozen_term.get("lines", [])) == 1
                   and frozen_term.get("record", {}).get("name") == PAYMENT_TERM
                   and frozen_term["lines"][0].get("value") == "percent"
                   and abs(float(frozen_term["lines"][0].get("value_amount") or 0) - 100) < 0.01
                   and frozen_term["lines"][0].get("delay_type") == "days_after"
                   and int(frozen_term["lines"][0].get("nb_days") or 0) == 30)
    checks["seed_identity_frozen"] = identity_ok
    new_so_ids = sorted(set(after["all_ids"]["sale.order"]) - set(baseline.get("all_ids", {}).get("sale.order", [])))
    checks["one_new_sales_order"] = len(new_so_ids) == 1 and exact_delta(baseline, after, "sale.order", 1)
    if not identity_ok or len(new_so_ids) != 1:
        return score_payload(checks, after, None, "baseline identity or exact sales-order delta failed")

    so_id = new_so_ids[0]
    so = read_one(conn, "sale.order", so_id, ["partner_id", "state", "client_order_ref", "commitment_date", "payment_term_id", "invoice_ids"])
    line_ids = search(conn, "sale.order.line", [("order_id", "=", so_id)])
    lines = [read_one(conn, "sale.order.line", line_id, ["product_id", "product_uom_qty", "price_unit", "price_subtotal"]) for line_id in line_ids]
    list_price = float(frozen_product.get("list_price") or 0)
    checks["sales_order_valid"] = sales_order_valid(so, lines, baseline, after["customer_ids"][0], after["product_ids"][0], list_price, conn)
    linked_ids = [int(invoice_id) for invoice_id in (so.get("invoice_ids") or [])]
    invoices = [read_one(conn, "account.move", invoice_id, ["move_type", "state", "partner_id", "invoice_payment_term_id", "amount_untaxed", "amount_total", "invoice_line_ids"]) for invoice_id in linked_ids]
    posted = [row for row in invoices if row.get("move_type") == "out_invoice" and row.get("state") == "posted"]
    new_invoice_ids = set(after["all_ids"]["account.move"]) - set(baseline.get("all_ids", {}).get("account.move", []))
    checks["one_new_customer_invoice"] = len(new_invoice_ids) == 1 and len(invoices) == 1 and len(posted) == 1 and int(next(iter(new_invoice_ids))) in linked_ids
    invoice_lines = []
    if len(posted) == 1:
        invoice_lines = [read_one(conn, "account.move.line", int(line_id), ["sale_line_ids", "product_id", "quantity", "price_subtotal", "display_type", "tax_ids"]) for line_id in (posted[0].get("invoice_line_ids") or [])]
    zero_tax = len(invoice_lines) == 1 and not (invoice_lines[0].get("tax_ids") or [])
    checks["invoice_payment_and_amount"] = (zero_tax and len(posted) == 1 and rid(posted[0].get("partner_id")) == after["customer_ids"][0]
                                              and same_term(baseline, posted[0].get("invoice_payment_term_id"), conn)
                                              and abs(float(posted[0].get("amount_untaxed") or 0) - list_price) < 0.01
                                              and abs(float(posted[0].get("amount_total") or 0) - list_price) < 0.01)
    # The frozen fixture is expected to be tax-free; a product invoice line must be the single linked line.
    checks["invoice_line_link_unique"] = (len(invoice_lines) == 1 and len(line_ids) == 1
                                           and invoice_lines[0].get("display_type") in (None, False, "product")
                                           and [int(x) for x in (invoice_lines[0].get("sale_line_ids") or [])] == [line_ids[0]]
                                           and rid(invoice_lines[0].get("product_id")) == after["product_ids"][0]
                                           and abs(float(invoice_lines[0].get("quantity") or 0) - QTY) < 0.01
                                           and abs(float(invoice_lines[0].get("price_subtotal") or 0) - list_price) < 0.01)
    checks["no_new_purchase_or_manufacturing_order"] = exact_delta(baseline, after, "purchase.order", 0) and exact_delta(baseline, after, "mrp.production", 0)
    return score_payload(checks, after, so_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--baseline", type=Path)
    group.add_argument("--check", type=Path)
    args = parser.parse_args()
    conn = connection()
    if args.baseline:
        args.baseline.write_text(json.dumps(snapshot(conn), indent=2))
        return
    payload = check(conn, json.loads(args.check.read_text()))
    out = Path("/logs/verifier")
    out.mkdir(parents=True, exist_ok=True)
    (out / "verifier_details.json").write_text(json.dumps(payload, indent=2))
    (out / "reward.txt").write_text(f"{payload['overall_score']:.2f}\n")
    print(json.dumps({"independent_business_checks": f"{payload['rules']['passed']}/{payload['rules']['total']}", "score": payload["overall_score"]}))
    raise SystemExit(0 if payload["passed"] else 1)


if __name__ == "__main__":
    main()
