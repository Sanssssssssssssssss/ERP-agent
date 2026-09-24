"""Finance and inventory evidence must survive adversarial state changes."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from erp_harness.erp.actions import NativeActions
from erp_harness.erp.business_operations import (
    _INVOICE,
    _LINE,
    _MOVE,
    _PAYMENT,
    _PICKING,
    _bom_consumption,
    _Evidence,
    execution_kwargs,
    method_prestate,
    method_verify,
)
from erp_harness.erp.store import ActionStore


class Client:
    def __init__(self):
        self.context = {}
        self.records = {}

    def add(self, model, record_id, fields=(), **values):
        row = {field: False for field in fields}
        row.update(id=record_id, **values)
        self.records.setdefault(model, {})[record_id] = row
        return row

    def read_records(self, model, ids, fields):
        return [{field: copy.deepcopy(row[field]) for field in fields if field in row}
                for record_id in ids if (row := self.records.get(model, {}).get(record_id)) is not None]

    def search_read(self, model, domain, fields, limit=0, order="id"):
        rows = list(self.records.get(model, {}).values())
        for field, operator, value in domain:
            if operator == "in":
                rows = [r for r in rows if bool(set(r[field] if isinstance(r[field], list) else [r[field]]) & set(value))]
            elif operator in {"=", "child_of"}:
                rows = [r for r in rows if r[field] == value]
            else:
                raise AssertionError(operator)
        return self.read_records(model, sorted(r["id"] for r in rows)[:limit or None], fields)


def payment():
    c = Client()
    c.add("res.currency", 1, rounding=0.01)
    c.add("account.journal", 2, company_id=1, currency_id=1, type="bank")
    c.add("account.payment.method.line", 3, journal_id=2, payment_account_id=200, code="manual")
    c.add("account.payment.register", 4, company_id=1, partner_id=8, currency_id=1, journal_id=2,
          line_ids=[11], amount=40, payment_type="inbound", partner_type="customer", group_payment=False,
          payment_difference_handling="open", payment_method_line_id=3, partner_bank_id=False)
    c.add("account.move", 10, _INVOICE, company_id=1, partner_id=8, currency_id=1, state="posted",
          move_type="out_invoice", amount_total=100, amount_residual=100, line_ids=[11], reversal_move_ids=[])
    c.add("account.move.line", 11, _LINE, company_id=1, partner_id=8, currency_id=1, account_id=200,
          move_id=10, parent_state="posted", amount_residual=100, amount_residual_currency=100,
          balance=100, matched_debit_ids=[], matched_credit_ids=[])
    return SimpleNamespace(client=c), {"instance": "default", "model": "account.payment.register", "method": "action_create_payments", "kwargs": {"ids": [4]}}


def complete_payment(runtime):
    c = runtime.client
    c.add("account.payment", 20, _PAYMENT, company_id=1, partner_id=8, currency_id=1, journal_id=2,
          amount=40, payment_type="inbound", partner_type="customer", state="paid", move_id=21,
          invoice_ids=[10], is_matched=False, reconciled_statement_line_ids=[])
    c.add("account.move", 21, state="posted", line_ids=[22, 23])
    c.records["account.move.line"][11].update(amount_residual=60, amount_residual_currency=60, matched_credit_ids=[30])
    c.add("account.partial.reconcile", 30, debit_move_id=11, credit_move_id=22)


def test_payment_checks_source_and_actual_reconciliation_not_window_action():
    runtime, payload = payment()
    before = method_prestate(runtime, payload)
    action = {"type": "ir.actions.act_window", "res_model": "account.payment", "res_id": 20}
    assert method_verify(runtime, payload, before, action)["status"] == "not_satisfied"
    complete_payment(runtime)
    result = method_verify(runtime, payload, before, action)
    assert result["status"] == "satisfied"
    assert result["evidence"]["bank_reconciliation_verified"] is False
    assert result["evidence"]["related_records"] == [{"model": "account.payment", "id": 20}]
    runtime.client.records["account.partial.reconcile"][30]["credit_move_id"] = 999
    assert method_verify(runtime, payload, before, action)["status"] == "not_satisfied"


@pytest.mark.parametrize("field,value,message", [
    ("amount", 101, "residual"), ("currency_id", 2, "currency_id"),
    ("partner_id", 9, "partner_id"), ("company_id", 2, "company_id"),
    ("payment_type", "outbound", "direction"), ("payment_difference_handling", "reconcile", "write-offs"),
])
def test_payment_rejects_invalid_business_relationships(field, value, message):
    runtime, payload = payment()
    runtime.client.records["account.payment.register"][4][field] = value
    with pytest.raises(ValueError, match=message):
        method_prestate(runtime, payload)


def test_changed_residual_invalidates_approval_and_acl_cannot_be_bypassed():
    runtime, payload = payment()
    before = method_prestate(runtime, payload)
    runtime.client.records["account.move.line"][11]["amount_residual_currency"] = 80
    assert before != method_prestate(runtime, payload)
    runtime.policy = Mock()
    runtime.policy.restricted_fields.return_value = {"amount_residual"}
    with pytest.raises(ValueError, match="evidence unavailable"):
        method_prestate(runtime, payload)
    payload["kwargs"]["context"] = {"skip_account_move_synchronization": True}
    with pytest.raises(ValueError, match="kwargs.ids only"):
        method_prestate(runtime, payload)


def test_different_wizards_share_finance_resource_lock():
    first = {"instance": "a", "model": "account.payment.register", "kwargs": {"ids": [4]}}
    other = {"instance": "a", "model": "account.move.reversal", "kwargs": {"ids": [90]}}
    assert NativeActions._resource_key("method", first) == NativeActions._resource_key("method", other)
    assert NativeActions._resource_key("method", first) != NativeActions._resource_key("method", {**other, "instance": "b"})


@pytest.mark.parametrize("lose_response", [False, True])
def test_repeating_consumed_payment_wizard_never_sends_twice(tmp_path, monkeypatch, lose_response):
    runtime, payload = payment()
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "account.payment.register.action_create_payments")
    monkeypatch.setattr("erp_harness.erp.actions.record_write_event", lambda *args, **kwargs: None)
    identity = {"instance": "default", "url": "http://127.0.0.1:18079", "database": "enterprise", "credential_scope_sha256": "accountant"}
    reads = SimpleNamespace(instance="default", instances={"default": runtime}, identity_context=lambda _: identity)
    writer = Mock()

    def send(*args, **kwargs):
        complete_payment(runtime)
        if lose_response:
            raise TimeoutError("response lost after commit")
        return {"res_model": "account.payment", "res_id": 20}

    writer.execute_method.side_effect = send
    store = ActionStore(tmp_path / "actions.sqlite3")
    try:
        actions = NativeActions(reads, store=store, clients={"default": writer}, approval_mode="host")
        args = {k: v for k, v in payload.items() if k != "instance"}
        pending = actions.execute_method(**args)
        assert pending["approval_required"]
        assert store.approve(pending["action_id"], "desktop-test-user")
        first = actions.execute_method(**args)
        assert first["action_status"] == ("needs_reconciliation" if lose_response else "verified")
        second = actions.execute_method(**args)
        assert second["action_status"] == "verified"
        assert second["action_id"] == first["action_id"]
        assert writer.execute_method.call_count == 1
    finally:
        store.close()


def test_reconcile_requires_balanced_lines_and_does_not_invent_bank_evidence():
    runtime, _ = payment()
    c = runtime.client
    c.add("account.account", 200, reconcile=True)
    c.records["account.move"][10]["statement_line_id"] = False
    c.add("account.move", 12, company_id=1, statement_line_id=False)
    c.add("account.move.line", 13, _LINE, company_id=1, partner_id=8, currency_id=1, account_id=200,
          move_id=12, parent_state="posted", amount_residual=-100, amount_residual_currency=-100,
          balance=-100, matched_debit_ids=[], matched_credit_ids=[])
    payload = {"instance": "default", "model": "account.move.line", "method": "reconcile", "kwargs": {"ids": [11, 13]}}
    before = method_prestate(runtime, payload)
    c.records["account.move.line"][13]["account_id"] = 999
    with pytest.raises(ValueError, match="account_id"):
        method_prestate(runtime, payload)
    c.records["account.move.line"][13]["account_id"] = 200
    for record_id in (11, 13):
        c.records["account.move.line"][record_id].update(amount_residual=0, amount_residual_currency=0, reconciled=True, full_reconcile_id=31)
    result = method_verify(runtime, payload, before, None)
    assert result["status"] == "satisfied"
    assert result["evidence"]["bank_reconciliation_verified"] is False


def test_partial_transfer_preserves_remainder_and_checks_actual_quantity():
    c = Client()
    c.add("stock.picking", 1, _PICKING, company_id=1, partner_id=8, state="assigned", move_ids=[2],
          backorder_ids=[], return_ids=[], picking_type_id=3)
    c.add("stock.move", 2, _MOVE, company_id=1, product_id=4, product_uom=1, product_uom_qty=10,
          quantity=4, state="assigned", location_id=8, location_dest_id=9, move_orig_ids=[])
    c.add("stock.picking.type", 3, create_backorder="ask")
    c.add("stock.location", 8, usage="supplier", company_id=False)
    runtime = SimpleNamespace(client=c)
    payload = {"instance": "default", "model": "stock.picking", "method": "button_validate", "kwargs": {"ids": [1]}}
    with pytest.raises(ValueError, match="keep-backorder"):
        method_prestate(runtime, payload)
    c.records["stock.picking.type"][3]["create_backorder"] = "always"
    before = method_prestate(runtime, payload)
    c.records["stock.picking"][1]["state"] = "done"
    c.records["stock.move"][2].update(state="done", quantity=10)
    assert method_verify(runtime, payload, before, True)["status"] == "not_satisfied"


def test_backorder_context_comes_from_approved_relationship():
    payload = {"model": "stock.backorder.confirmation", "method": "process", "kwargs": {"ids": [8]}}
    assert execution_kwargs(payload, {"enterprise": {"records": [{"id": 91}]}}) == {
        "ids": [8], "context": {"button_validate_picking_ids": [91]},
    }
    assert payload["kwargs"] == {"ids": [8]}


def consumption_fixture():
    c = Client()
    c.add("uom.uom", 1, factor=1, rounding=0.01)
    c.add("uom.uom", 2, factor=2, rounding=0.01)
    c.add("product.product", 3, product_tmpl_id=30, uom_id=1)
    c.add("product.product", 4, product_tmpl_id=40, uom_id=1)
    c.add("mrp.bom", 5, type="normal", consumption="strict", product_id=3, product_tmpl_id=30,
          product_qty=1, product_uom_id=1, bom_line_ids=[6])
    c.add("mrp.bom.line", 6, product_id=4, product_qty=2, product_uom_id=1,
          bom_product_template_attribute_value_ids=[], child_bom_id=False)
    runtime = SimpleNamespace(client=c)
    production = {"id": 7, "bom_id": 5, "product_id": 3, "product_qty": 2, "product_uom_id": 1, "consumption": "strict"}
    move = {"product_id": 4, "product_uom": 2, "product_uom_qty": 1, "quantity": 1}
    return runtime, production, move


def test_strict_bom_detects_tampered_demand_while_flexible_policy_reports_deviation():
    runtime, production, move = consumption_fixture()
    c, payload = runtime.client, {"instance": "default"}
    with pytest.raises(ValueError, match="strict BOM"):
        _bom_consumption(_Evidence(runtime, payload), production, [move], True)
    production["consumption"] = "flexible"
    result = _bom_consumption(_Evidence(runtime, payload), production, [move], True)
    assert result["component_variances"] == [{"product_id": 4, "bom_quantity": 4, "planned_quantity": 2, "actual_quantity": 2}]
    production["consumption"] = "strict"
    move.update(product_uom_qty=2, quantity=2)
    assert _bom_consumption(_Evidence(runtime, payload), production, [move], True)["component_variances"] == []
    c.records["mrp.bom.line"][6]["bom_product_template_attribute_value_ids"] = [42]
    with pytest.raises(ValueError, match="native explosion"):
        _bom_consumption(_Evidence(runtime, payload), production, [move], True)


@pytest.mark.parametrize("mo_policy,bom_policy,error", [
    ("strict", "flexible", "strict BOM"),
    ("warning", "flexible", "human review"),
    ("flexible", "strict", None),
    ("flexible", "warning", None),
])
def test_consumption_uses_confirmed_mo_policy(mo_policy, bom_policy, error):
    runtime, production, move = consumption_fixture()
    production["consumption"] = mo_policy
    runtime.client.records["mrp.bom"][5]["consumption"] = bom_policy
    guard = _Evidence(runtime, {"instance": "default"})
    if error:
        with pytest.raises(ValueError, match=error):
            _bom_consumption(guard, production, [move], True)
    else:
        evidence = _bom_consumption(guard, production, [move], True)
        assert evidence["consumption"] == mo_policy
        assert evidence["bom_consumption"] == bom_policy
        assert evidence["component_variances"]
    move.update(product_uom_qty=2, quantity=2)
    assert _bom_consumption(guard, production, [move], True)["component_variances"] == []


def test_warning_deviation_rejected_before_approval_or_write_rpc(tmp_path, monkeypatch):
    runtime, production, move = consumption_fixture()
    c = runtime.client
    production.update(consumption="warning", company_id=1, qty_producing=2, qty_produced=0,
                      state="progress", date_start="2026-01-01 00:00:00", date_deadline=False,
                      move_raw_ids=[8], move_finished_ids=[9], workorder_ids=[])
    c.add("mrp.production", 7, **{k: v for k, v in production.items() if k != "id"})
    c.records["mrp.bom"][5].update(company_id=1, operation_ids=[], consumption="flexible")
    c.add("stock.move", 8, _MOVE, **move, company_id=1)
    c.add("stock.move", 9, _MOVE, company_id=1)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "mrp.production.button_mark_done")
    identity = {"instance": "default", "credential_scope_sha256": "production"}
    reads = SimpleNamespace(instance="default", instances={"default": runtime}, identity_context=lambda _: identity)
    writer, store = Mock(), ActionStore(tmp_path / "actions.sqlite3")
    try:
        actions = NativeActions(reads, store=store, clients={"default": writer}, approval_mode="host")
        result = actions.execute_method("mrp.production", "button_mark_done", kwargs={"ids": [7]})
        assert result["success"] is False and "human review" in result["error"]
        assert not result.get("approval_required")
        assert store.summary()["receipts"] == []
        writer.execute_method.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("model,child", [("sale.order", "sale.order.line"), ("purchase.order", "purchase.order.line"), ("account.move", "account.move.line")])
def test_relation_commands_verify_updated_fields_deleted_children_and_final_membership(model, child):
    c = Client()
    c.add(child, 1, quantity=2)
    c.add(child, 2, quantity=9)
    c.add(child, 3, quantity=4)
    runtime = SimpleNamespace(client=c, _metadata=lambda m: {"lines": {"relation": child}, "quantity": {"type": "float"}})
    actions = object.__new__(NativeActions)
    actions.reads = SimpleNamespace(instances={"default": runtime})
    check = lambda actual, expected, previous=None: actions._created_relation_matches("default", model, "lines", actual, expected, previous)
    commands = [[1, 1, {"quantity": 2}], [2, 2, 0], [4, 3, 0]]
    assert check([1, 3], commands, [1, 2]) is False  # Child 2 still exists.
    del c.records[child][2]
    assert check([1, 3], commands, [1, 2]) is True
    c.records[child][1]["quantity"] = 99
    assert check([1, 3], commands, [1, 2]) is False
    assert check([3], [[1, 1, {"quantity": 2}], [3, 1, 0]], [1, 3]) is False
    c.records[child][1]["quantity"] = 2
    assert check([3], [[1, 1, {"quantity": 2}], [3, 1, 0]], [1, 3]) is True
    assert check([3], [[6, 0, [3]]], [1]) is True
    assert check([], [[5, 0, 0]], [1]) is True
    assert check([3], [[0, 0, {"quantity": 4}]], []) is True
    assert check([1, 3], [[0, 0, {"quantity": 4}]], [1]) is True
    assert check([], [[0, 0, {"quantity": 4}], [5, 0, 0]], []) is True
