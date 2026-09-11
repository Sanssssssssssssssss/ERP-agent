"""Read-only snapshot capture and fail-closed checks for the 8-item draft target.

The live capture path is used only inside the disposable isolated container.  The
pure ``check_snapshots`` function is exercised locally without Odoo or a model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

CUSTOMER_REF = "rF3119DF4C3_c01"
CUSTOMER = "Nimbus Bureau"
PRODUCT_CODE = "PF3119DF4C3-SPP-NBW-010"
PRODUCT = "Open-Plan Noise Barrier Wall"
PAYMENT_TERM = "30 Days"
PRICE = 695.22
QTY = 8.0
BUDGET = 5927.0
TRACKED_FIELDS = {
    "sale.order": [
        "id", "state", "partner_id", "payment_term_id", "order_line", "invoice_ids",
        "amount_untaxed", "amount_total", "client_order_ref", "commitment_date", "write_date",
    ],
    "sale.order.line": ["id", "order_id", "product_id", "product_uom_qty", "price_unit", "price_subtotal", "write_date"],
    "account.move": ["id", "move_type", "state", "partner_id", "invoice_line_ids", "amount_untaxed", "amount_total", "write_date"],
    "purchase.order": ["id", "state", "partner_id", "order_line", "amount_total", "write_date"],
    "mrp.production": ["id", "state", "product_id", "product_qty", "write_date"],
    "account.payment": ["id", "state", "amount", "partner_id", "memo", "write_date"],
    "stock.picking": ["id", "state", "origin", "sale_id", "move_ids", "write_date"],
    "stock.move": ["id", "state", "picking_id", "sale_line_id", "product_id", "product_uom_qty", "quantity", "write_date"],
}


def _plain(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if hasattr(value, "item"):
        return value.item()
    return value


def _id(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return int(value[0]) if value else 0
    if isinstance(value, dict):
        return int(value.get("id") or 0)
    return int(value or 0)


def _number(value: Any) -> float:
    return float(value or 0)


def _connection() -> Any:
    import odoolib

    key_file = Path(os.environ.get("ODOO_API_KEY_FILE", "/etc/odoo/api_key"))
    return odoolib.get_connection(
        hostname=os.environ.get("ODOO_HOST", "127.0.0.1"),
        protocol="json2",
        port=int(os.environ.get("ODOO_PORT", "8069")),
        database=os.environ.get("ODOO_DB", "bench"),
        login=os.environ.get("ODOO_USERNAME", "admin"),
        password=key_file.read_text(encoding="utf-8").strip(),
    )


def _read_model(conn: Any, model_name: str, ids: list[int], fields: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = conn.get_model(model_name).read(ids=ids, fields=fields) or []
    if isinstance(rows, dict):
        rows = [rows]
    return {str(int(row["id"])): _plain(row) for row in rows if isinstance(row, dict) and row.get("id")}


def capture(conn: Any) -> dict[str, Any]:
    models = {name: list(conn.get_model(name).search(domain=[]) or []) for name in TRACKED_FIELDS}
    customer_ids = list(conn.get_model("res.partner").search(domain=[("ref", "=", CUSTOMER_REF)]) or [])
    product_ids = list(conn.get_model("product.product").search(domain=[("default_code", "=", PRODUCT_CODE)]) or [])
    term_ids = list(conn.get_model("account.payment.term").search(domain=[("name", "=", PAYMENT_TERM)]) or [])
    customer = _read_model(conn, "res.partner", customer_ids, ["id", "name", "ref"])
    product = _read_model(conn, "product.product", product_ids, ["id", "name", "default_code", "list_price"])
    terms = _read_model(conn, "account.payment.term", term_ids, ["id", "name"])
    return {
        "schema": 1,
        "customer_ids": [int(value) for value in customer_ids],
        "product_ids": [int(value) for value in product_ids],
        "payment_term_ids": [int(value) for value in term_ids],
        "customer": customer,
        "product": product,
        "payment_term": terms,
        "ids": {name: [int(value) for value in values] for name, values in models.items()},
        "records": {
            name: _read_model(conn, name, [int(value) for value in models[name]], fields)
            for name, fields in TRACKED_FIELDS.items()
        },
    }


def _delta(before: dict[str, Any], after: dict[str, Any], model_name: str) -> set[int]:
    return set(after.get("ids", {}).get(model_name, [])) - set(before.get("ids", {}).get(model_name, []))


def _relation_ids(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    return {_id(item) for item in value if _id(item)}


def _identity_unchanged(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return (
        after.get("customer_ids") == before.get("customer_ids")
        and after.get("product_ids") == before.get("product_ids")
        and after.get("payment_term_ids") == before.get("payment_term_ids")
        and after.get("customer") == before.get("customer")
        and after.get("product") == before.get("product")
        and after.get("payment_term") == before.get("payment_term")
    )


def _same_existing_records(before: dict[str, Any], after: dict[str, Any]) -> bool:
    for model_name in TRACKED_FIELDS:
        before_ids = set(before.get("ids", {}).get(model_name, []))
        after_ids = set(after.get("ids", {}).get(model_name, []))
        if not before_ids <= after_ids:
            return False
        old = before.get("records", {}).get(model_name, {})
        current = after.get("records", {}).get(model_name, {})
        if any(current.get(str(record_id)) != row for record_id, row in old.items()):
            return False
    return True


def check_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "identity": False,
        "identity_after_unchanged": False,
        "existing_records_unchanged": False,
        "one_new_draft_sales_order": False,
        "sales_order_values": False,
        "one_new_sales_line": False,
        "no_other_tracked_business_writes": False,
    }
    customer_ok = (
        before.get("schema") == 1
        and before.get("customer_ids") and len(before["customer_ids"]) == 1
        and before.get("product_ids") and len(before["product_ids"]) == 1
        and before.get("payment_term_ids") and len(before["payment_term_ids"]) == 1
        and before.get("customer", {}).get(str(before["customer_ids"][0]), {}).get("name") == CUSTOMER
        and before.get("product", {}).get(str(before["product_ids"][0]), {}).get("name") == PRODUCT
        and before.get("product", {}).get(str(before["product_ids"][0]), {}).get("default_code") == PRODUCT_CODE
        and abs(_number(before.get("product", {}).get(str(before["product_ids"][0]), {}).get("list_price")) - PRICE) < 0.01
        and before.get("payment_term", {}).get(str(before["payment_term_ids"][0]), {}).get("name") == PAYMENT_TERM
    )
    checks["identity"] = bool(customer_ok)
    checks["identity_after_unchanged"] = bool(customer_ok and _identity_unchanged(before, after))
    checks["existing_records_unchanged"] = _same_existing_records(before, after)
    new_orders = _delta(before, after, "sale.order")
    new_lines = _delta(before, after, "sale.order.line")
    checks["one_new_draft_sales_order"] = len(new_orders) == 1
    checks["one_new_sales_line"] = len(new_lines) == 1
    if len(new_orders) == 1:
        order_id = next(iter(new_orders))
        order = after.get("records", {}).get("sale.order", {}).get(str(order_id), {})
        checks["sales_order_values"] = (
            order.get("state") == "draft"
            and _id(order.get("partner_id")) == int(before["customer_ids"][0])
            and _id(order.get("payment_term_id")) == int(before["payment_term_ids"][0])
            and not order.get("invoice_ids")
            and len(_relation_ids(order.get("order_line"))) == 1
            and _number(order.get("amount_untaxed")) <= BUDGET + 0.01
            and abs(_number(order.get("amount_untaxed")) - PRICE * QTY) < 0.01
            and abs(_number(order.get("amount_total")) - PRICE * QTY) < 0.01
        )
        if len(new_lines) == 1:
            line = after.get("records", {}).get("sale.order.line", {}).get(str(next(iter(new_lines))), {})
            checks["one_new_sales_line"] = (
                _id(line.get("order_id")) == order_id
                and _id(line.get("product_id")) == int(before["product_ids"][0])
                and abs(_number(line.get("product_uom_qty")) - QTY) < 0.01
                and abs(_number(line.get("price_unit")) - PRICE) < 0.01
                and abs(_number(line.get("price_subtotal")) - PRICE * QTY) < 0.01
                and _relation_ids(order.get("order_line")) == {next(iter(new_lines))}
            )
    checks["no_other_tracked_business_writes"] = all(
        not _delta(before, after, model_name)
        for model_name in TRACKED_FIELDS
        if model_name not in {"sale.order", "sale.order.line"}
    )
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failed,
        "checks": checks,
        "failed": failed,
        "new_ids": {name: sorted(_delta(before, after, name)) for name in TRACKED_FIELDS},
        "target": {"customer": CUSTOMER, "product": PRODUCT, "qty": QTY, "price": PRICE, "payment_term": PAYMENT_TERM, "state": "draft"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture")
    parser.add_argument("--check", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if bool(args.capture) == bool(args.check):
        parser.error("choose exactly one of --capture or --check")
    if args.capture:
        output = Path(args.capture)
        output.write_text(json.dumps(capture(_connection()), indent=2, sort_keys=True), encoding="utf-8")
        return 0
    before = json.loads(Path(args.check[0]).read_text(encoding="utf-8"))
    after = json.loads(Path(args.check[1]).read_text(encoding="utf-8"))
    result = check_snapshots(before, after)
    print(json.dumps(result, indent=2, sort_keys=True))
    reward_path = Path("/logs/verifier/reward.json")
    reward_path.parent.mkdir(parents=True, exist_ok=True)
    reward_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
