"""Zero-LLM Stage-4 gate against one disposable snapshot-restored Odoo."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def provision(env) -> None:
    if (
        env.cr.dbname != "bench"
        or os.environ.get("PI_ODOO_ACTION_GATE") != "1"
        or not Path("/tmp/saas_setup_complete").is_file()
    ):
        raise RuntimeError("action fixtures may only modify the disposable bench snapshot")
    product = env["product.product"].search(
        [("sale_ok", "=", True), ("purchase_ok", "=", True)], limit=1
    )
    if not product:
        raise RuntimeError("action gate needs one sale/purchase product")
    customer = env["res.partner"].create(
        {"name": "PI_ACTION_GATE_CUSTOMER", "ref": "PI_ACTION_GATE_CUSTOMER", "customer_rank": 1}
    )
    vendor = env["res.partner"].create(
        {"name": "PI_ACTION_GATE_VENDOR", "ref": "PI_ACTION_GATE_VENDOR", "supplier_rank": 1}
    )
    sale = env["sale.order"].create({"partner_id": customer.id})
    env["sale.order.line"].create(
        {
            "order_id": sale.id,
            "product_id": product.id,
            "product_uom_qty": 1,
            "price_unit": 10,
        }
    )
    purchase = env["purchase.order"].create({"partner_id": vendor.id})
    env["purchase.order.line"].create(
        {
            "order_id": purchase.id,
            "product_id": product.id,
            "product_qty": 1,
            "price_unit": 5,
            "date_planned": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    invoice = env["account.move"].create(
        {
            "move_type": "out_invoice",
            "partner_id": customer.id,
            "invoice_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "product_id": product.id,
                        "quantity": 1,
                        "price_unit": 10,
                    },
                )
            ],
        }
    )
    env.cr.commit()
    Path("/tmp/pi-action-gate-fixture.json").write_text(
        json.dumps(
            {
                "customer": customer.id,
                "vendor": vendor.id,
                "product": product.id,
                "sale": sale.id,
                "purchase": purchase.id,
                "invoice": invoice.id,
            }
        )
    )
    print("ACTION_GATE_FIXTURE_READY")


def run_gate() -> None:
    from odoo_runtime.actions import NativeActions
    from odoo_runtime.reads import NativeReads
    from odoo_runtime.store import ActionStore

    root = Path(os.environ.get("ACTION_GATE_ROOT", "/logs/agent"))
    root.mkdir(parents=True, exist_ok=True)
    fixture = json.loads(Path("/tmp/pi-action-gate-fixture.json").read_text())
    os.environ.update(
        {
            "ODOO_MCP_ENABLE_WRITES": "1",
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": (
                "sale.order.action_confirm,purchase.order.button_confirm,"
                "sale.advance.payment.inv.create_invoices,account.move.action_post"
            ),
            "ODOO_MCP_ALLOW_UNKNOWN_METHODS": "0",
            "MCP_CHATTER_DIRECT": "0",
            "ODOO_ACTION_APPROVAL_MODE": "bench-auto",
            "ODOO_MCP_AUDIT_LOG": str(root / "native-write-audit.jsonl"),
            "ODOO_REQUEST_LOG": str(root / "native-requests.jsonl"),
            "ODOO_REQUEST_BACKEND": "native",
            "PI_AGENT_SESSION_ID": "stage4-zero-llm-gate",
        }
    )
    upload = root / "gate-attachment.txt"
    upload.write_bytes(b"PI_ACTION_GATE_ATTACHMENT")
    os.environ["ODOO_MCP_ATTACHMENT_UPLOAD_ROOTS"] = str(root)
    reads = NativeReads.from_environment()
    actions = NativeActions(reads, store=ActionStore(root / "odoo-actions.sqlite3"))

    def write(model, operation, **arguments):
        report = actions.validate_write(model, operation, **arguments)
        assert report.get("success"), report
        result = actions.execute_approved_write(report["approval"], confirm=True)
        assert result.get("success") and result.get("action_status") == "verified", result
        return result

    created = write(
        "res.partner",
        "create",
        values_list=[
            {"name": "PI_ACTION_GATE_BATCH_A"},
            {"name": "PI_ACTION_GATE_BATCH_B"},
        ],
    )
    created_ids = actions._ids(created["result"])
    assert len(created_ids) == 2
    write(
        "res.partner",
        "write",
        record_ids=[created_ids[0]],
        values={"name": "PI_ACTION_GATE_UPDATED"},
    )
    write("res.partner", "unlink", record_ids=[created_ids[1]])
    write(
        "ir.attachment",
        "create",
        values={
            "name": "gate-attachment.txt",
            "datas_from_path": str(upload),
            "res_model": "res.partner",
            "res_id": fixture["customer"],
        },
    )
    malformed_relation = actions.validate_write(
        "sale.order",
        "create",
        values={
            "partner_id": fixture["customer"],
            "order_line": [["0", "false", {"product_id": fixture["product"]}]],
        },
    )
    assert not malformed_relation.get("success"), malformed_relation
    assert any(
        issue.get("code") == "invalid_relational_command"
        for issue in malformed_relation.get("issues", [])
    ), malformed_relation
    write(
        "sale.order",
        "create",
        values={
            "partner_id": fixture["customer"],
            "commitment_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "order_line": [
                [
                    0,
                    0,
                    {
                        "product_id": fixture["product"],
                        "product_uom_qty": 2,
                        "price_unit": 10,
                    },
                ]
            ],
        },
    )
    chatter = actions.chatter_post(
        "res.partner", fixture["customer"], "PI_ACTION_GATE_CHATTER"
    )
    chatter_result = actions.chatter_post(
        "res.partner",
        fixture["customer"],
        "PI_ACTION_GATE_CHATTER",
        approval=chatter["approval"],
        confirm=True,
    )
    assert chatter_result.get("success"), chatter_result

    for model, method, record_id in (
        ("sale.order", "action_confirm", fixture["sale"]),
        ("purchase.order", "button_confirm", fixture["purchase"]),
        ("account.move", "action_post", fixture["invoice"]),
    ):
        result = actions.execute_method(model, method, kwargs={"ids": [record_id]})
        assert result.get("success") and result.get("action_status") == "verified", result

    wizard = write(
        "sale.advance.payment.inv",
        "create",
        values={"advance_payment_method": "delivered", "sale_order_ids": [fixture["sale"]]},
    )
    wizard_ids = actions._ids(wizard["result"])
    assert len(wizard_ids) == 1
    invoiced = actions.execute_method(
        "sale.advance.payment.inv",
        "create_invoices",
        kwargs={"ids": wizard_ids},
    )
    assert invoiced.get("success") and invoiced.get("action_status") == "verified", invoiced

    summary = actions.store.summary()
    assert summary["status_counts"] == {"verified": summary["actions"]}, summary
    audit_text = (root / "native-write-audit.jsonl").read_text()
    assert all("token" not in json.loads(line) for line in audit_text.splitlines())
    assert Path("/etc/odoo/api_key").read_text().strip() not in audit_text
    receipt = {
        "status": "passed",
        "llm_calls": 0,
        "verified_actions": summary["actions"],
        "status_counts": summary["status_counts"],
        "ledger_sha256": summary["sha256"],
    }
    (root / "action-gate-summary.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)
    actions.store.close()


if "env" in globals():
    provision(globals()["env"])
elif __name__ == "__main__":
    run_gate()
