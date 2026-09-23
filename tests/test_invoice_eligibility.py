"""Frozen invoice conditions: block before approval; preserve Odoo billing branches."""
import os
from unittest.mock import patch

import pytest
from test_actions import _actions, _Runtime

from erp_harness.erp._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from erp_harness.erp.invoice_eligibility import inspect_invoice_eligibility


@pytest.mark.parametrize("quantity,final,expected", [
    (0, True, "blocked"), (0.004, True, "blocked"), (0.005, True, "eligible"),
    (2, True, "eligible"), (-1, False, "blocked"), (-1, True, "eligible"),
])
def test_odoo_precision_and_final_adjustments(quantity, final, expected):
    runtime = _Runtime()
    line = runtime.client.records["sale.order.line"][71]
    line.update(qty_to_invoice=quantity, is_downpayment=quantity < 0)
    result = inspect_invoice_eligibility(runtime, [7], final=final)
    assert result["status"] == expected
    assert result["orders"][0]["lines"][0]["qty_to_invoice"] == quantity


def test_sections_do_not_make_invoice_eligible_and_order_policy_does():
    runtime = _Runtime()
    line = runtime.client.records["sale.order.line"][71]
    line.update(qty_to_invoice=0, qty_delivered=0)
    runtime.client.records["sale.order"][7]["order_line"].append(72)
    runtime.client.records["sale.order.line"][72] = {**line, "id": 72, "display_type": "line_note", "qty_to_invoice": 1}
    assert inspect_invoice_eligibility(runtime, [7])["status"] == "blocked"
    runtime.client.records["product.product"][5]["invoice_policy"] = "order"
    line["qty_to_invoice"] = 3
    assert inspect_invoice_eligibility(runtime, [7])["status"] == "eligible"


def test_missing_rows_fields_and_permission_are_not_no_answer():
    runtime = _Runtime()
    with (patch.object(runtime.client, "search_read", side_effect=PermissionError("denied")),
          pytest.raises(PermissionError)):
        inspect_invoice_eligibility(runtime, [7])
    saved = runtime.client.records["sale.order.line"].pop(71)
    with pytest.raises(ValueError, match="unreadable or incomplete"):
        inspect_invoice_eligibility(runtime, [7])
    runtime.client.records["sale.order.line"][71] = saved
    del saved["qty_to_invoice"]
    with pytest.raises(ValueError, match="incomplete"):
        inspect_invoice_eligibility(runtime, [7])


def test_field_policy_blocks_evidence_before_read():
    runtime = _Runtime()
    runtime.policy = FieldPolicy({"default": {"sale.order.line": ModelFieldRule("deny", frozenset({"qty_to_invoice"}))}})
    with pytest.raises(ValueError, match="unavailable"):
        inspect_invoice_eligibility(runtime, [7])


def test_guard_blocks_before_approval_and_detects_changed_invoiceable_quantity():
    actions, writer, runtime = _actions(approval_mode="host")
    try:
        line = runtime.client.records["sale.order.line"][71]
        runtime.client.records["sale.order"][7]["state"] = "sale"
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.advance.payment.inv.create_invoices"}):
            line.update(qty_to_invoice=0, qty_delivered=0)
            blocked = actions.execute_method("sale.advance.payment.inv", "create_invoices", kwargs={"ids": [9]})
            assert blocked["business_condition"]["reason_code"] == "awaiting_delivery"
            assert not blocked["approval_required"]
            assert actions.store.summary()["actions"] == 0
            line.update(qty_to_invoice=2, qty_delivered=2)
            pending = actions.execute_method("sale.advance.payment.inv", "create_invoices", kwargs={"ids": [9]})
            assert pending["resume"]["tool"] == "mcp_odoo_execute_method"
            row = actions.store.get(pending["action_id"])
            assert actions.store.approve(pending["action_id"], "desktop_host")
            line["qty_to_invoice"] = 1  # Still eligible, but approved quantities changed.
            assert not actions._current_prestate_matches(row)
            failed = actions._execute_row(actions.store.get(row["action_id"]), lambda: pytest.fail("must not send"))
            assert not failed["success"]
            assert writer.calls == []
    finally:
        actions.store.close()


def test_explicit_downpayment_keeps_original_method_and_amount():
    actions, writer, runtime = _actions(approval_mode="host")
    try:
        runtime.client.records["sale.order.line"][71]["qty_to_invoice"] = 0
        wizard = runtime.client.records["sale.advance.payment.inv"][9]
        wizard.update(advance_payment_method="percentage", amount=30.0)
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.advance.payment.inv.create_invoices"}):
            result = actions.execute_method("sale.advance.payment.inv", "create_invoices", kwargs={"ids": [9]})
        row = actions.store.get(result["action_id"])
        assert row["prestate"]["wizard"][0]["amount"] == 30
        assert result["approval_required"] and writer.calls == []
        wizard["amount"] = 100
        assert not actions._current_prestate_matches(row)
    finally:
        actions.store.close()


@pytest.mark.parametrize("state,invoiced,invoices,reason", [
    ("draft", 0, [], "order_not_confirmed"), ("cancel", 0, [], "order_cancelled"),
    ("sale", 3, [301], "already_invoiced"), ("sale", 0, [], "awaiting_delivery"),
])
def test_blocked_reason_matches_business_state(state, invoiced, invoices, reason):
    runtime = _Runtime()
    runtime.client.records["sale.order"][7].update(state=state, invoice_ids=invoices)
    runtime.client.records["sale.order.line"][71].update(qty_to_invoice=0, qty_delivered=0, qty_invoiced=invoiced)
    result = inspect_invoice_eligibility(runtime, [7])
    assert result["reason_code"] == reason
    if reason == "already_invoiced":
        assert not result["requires_business_choice"]
        assert result["orders"][0]["invoice_ids"] == [301]


def test_negative_final_adjustment_is_not_verified_as_an_ordinary_invoice():
    actions, _, runtime = _actions()
    try:
        runtime.client.records["sale.order.line"][71].update(qty_to_invoice=-1, is_downpayment=True)
        payload = {"model": "sale.advance.payment.inv", "method": "create_invoices", "kwargs": {"ids": [9]}, "instance": "default"}
        row = {"kind": "method", "payload": payload, "prestate": actions._prestate("method", payload)}
        report = row["prestate"]["invoice_eligibility"][0]
        assert report["reason_code"] == "final_adjustment_only" and report["requires_business_choice"]
        assert report["positive_line_count"] == 0 and report["negative_line_count"] == 1
        runtime.client.records["sale.order"][7]["invoice_ids"] = [301]
        runtime.client.records["account.move"][301] = {"id": 301, "state": "draft", "move_type": "out_refund"}
        assert actions._verify(row, None)["status"] == "unconfirmed"
    finally:
        actions.store.close()


def test_read_scope_and_invalid_precision_fail_closed():
    runtime = _Runtime()
    with pytest.raises(ValueError, match="1 to 20"):
        inspect_invoice_eligibility(runtime, list(range(1, 22)))
    runtime.client.metadata["qty_to_invoice"]["digits"] = [16, 308]
    with pytest.raises(ValueError, match="precision is unavailable"):
        inspect_invoice_eligibility(runtime, [7])
