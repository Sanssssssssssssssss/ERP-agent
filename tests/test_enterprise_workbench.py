import asyncio
from copy import deepcopy

from erp_harness.app.business import BUSINESS_TARGETS, default_target, valid_target
from erp_harness.app.conversation import _propose_business
from erp_harness.app.enterprise_view import FIELDS, readback
from erp_harness.app.sale_view import refresh_business


def test_business_proposals_preserve_old_targets_and_reject_invalid_pairs():
    for kind in BUSINESS_TARGETS:
        result = asyncio.run(_propose_business("p", {"type": kind, "title": "业务", "goal": "按原单完成指定业务"}))
        assert result.details["success"]
        assert result.details["proposal"]["completion_target"] == default_target(kind)
    assert default_target("purchase") == "confirmed"
    assert default_target("sale_invoice") == "posted"
    assert not valid_target("purchase", "posted")
    assert not valid_target("sale_purchase_invoice", "draft")
    assert not valid_target("inventory", "reconciled")
    assert not valid_target({}, "done")


def payment_fixture():
    def row(model, record_id, **values):
        return {"id": record_id, **dict.fromkeys(FIELDS[model], False), **values}
    rows = {
        ("account.payment", 1): row("account.payment", 1, name="PAY1", state="paid", company_id=[1, "公司"], partner_id=[2, "客户"], currency_id=[1, "CNY"], amount=100, move_id=[10, "PAY1"], invoice_ids=[11], is_reconciled=True, is_matched=False, reconciled_statement_line_ids=[]),
        ("account.move", 10): row("account.move", 10, state="posted", move_type="entry", company_currency_id=[1, "CNY"], line_ids=[100, 101], currency_id=[1, "CNY"]),
        ("account.move", 11): row("account.move", 11, state="posted", move_type="out_invoice", company_currency_id=[1, "CNY"], line_ids=[102, 103], amount_residual=0, currency_id=[1, "CNY"]),
        ("res.currency", 1): row("res.currency", 1, name="CNY", rounding=0.01),
    }
    for i, move, amount in [(100, 10, 100), (101, 10, -100), (102, 11, 100), (103, 11, -100)]:
        rows[("account.move.line", i)] = row("account.move.line", i, parent_state="posted", move_id=[move, "凭证"], balance=amount, reconciled=True, full_reconcile_id=[1, "核销"], amount_residual=0)
    runs = [{"id": "r1", "business_id": "b", "tools": [{"arguments": {"model": "account.payment.register", "method": "action_create_payments"}, "result": {"action_id": "a", "action_status": "verified", "verification": {"status": "satisfied", "evidence": {"record_model": "account.payment", "records": [{"id": 1}]}}}}]}]
    return rows, runs, row


def test_paid_label_needs_actual_bank_evidence_and_unknown_write_blocks_completion():
    rows, runs, row = payment_fixture()
    def read(model, record_id, fields):
        return rows.get((model, record_id)), None if (model, record_id) in rows else "not observed"
    def final():
        return {c["name"]: c["status"] for c in readback("payment", "reconciled", runs, read)[2]}
    assert final()["enterprise_final"] == "failed"
    rows[("account.payment", 1)].update(is_matched=True, reconciled_statement_line_ids=[8])
    rows[("account.bank.statement.line", 8)] = row("account.bank.statement.line", 8, move_id=[10, "银行凭证"], is_reconciled=True, amount=100)
    assert final()["enterprise_final"] == "passed"
    state = {"businesses": {"b": {"id": "b", "session_id": "s", "type": "payment", "completion_target": "reconciled"}}, "runs": {"r1": runs[0]}}
    result = refresh_business(state, "b", lambda tool, args: {"success": True, "result": rows[(args["model"], args["record_id"])]})
    assert result["outcome"]["status"] == "passed"
    assert any(s["id"] == "payment" for s in result["execution"]["stages"])
    runs[0]["events"] = [{"type": "reconciliation", "action_id": "a", "action_status": "needs_reconciliation"}]
    assert final()["enterprise_settled"] == "failed"
    assert final()["enterprise_final"] != "passed"


def test_unbalanced_or_unreadable_accounting_cannot_pass():
    rows, runs, _ = payment_fixture()
    rows[("account.move.line", 100)]["balance"] = 101
    reader = lambda m, i, fields: (deepcopy(rows[(m, i)]), None)
    checks = readback("payment", "posted", runs, reader)[2]
    assert next(c for c in checks if c["name"] == "enterprise_final")["status"] == "failed"
    del rows[("account.move.line", 100)]["balance"]
    assert readback("payment", "posted", runs, reader)[1]
