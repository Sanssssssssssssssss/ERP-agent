import sys
from pathlib import Path

sys.path.insert(0, "/tmp/pi-odoo-mvp" if Path("/tmp/pi-odoo-mvp/verifier.py").exists() else str(Path(__file__).parents[1]))
from verifier import exact_delta, score_payload, sales_order_valid


def main() -> None:
    empty = {"all_ids": {"sale.order": [], "account.move": [], "purchase.order": [], "mrp.production": []}}
    assert not exact_delta(empty, empty, "sale.order", 1)
    duplicate = {"all_ids": {"sale.order": [1, 2]}}
    assert not exact_delta(empty, duplicate, "sale.order", 1)
    checks = {name: False for name in ("seed_identity_frozen", "one_new_sales_order", "sales_order_valid", "one_new_customer_invoice", "invoice_payment_and_amount", "invoice_line_link_unique", "no_new_purchase_or_manufacturing_order")}
    assert score_payload(checks, empty, None)["overall_score"] == 0
    assert not sales_order_valid({"state": "draft"}, [], {}, 1, 2, 3, None)


if __name__ == "__main__":
    main()
