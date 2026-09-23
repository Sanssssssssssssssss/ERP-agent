"""Approval continuation carries the bound phase without changing authorization."""
import copy

import pytest

from erp_harness.app.business import completion_target_instruction
from erp_harness.app.host import build_business_instruction, build_task_contract
from erp_harness.app.runner import build_approval_resume_message
from erp_harness.erp.task_evidence import TaskEvidence
from tests.test_actions import _actions


@pytest.mark.parametrize("kind,target,expected", [
    ("sale_invoice", "confirmed", "do not invoice or deliver goods"),
    ("sale_invoice", "posted", "do not send mail here"),
    ("invoice_delivery", "sent", "account.move.message_post"),
    ("purchase", "confirmed", "verify the confirmed state"),
    ("manufacturing", "confirmed", "verify the confirmed state"),
    ("manufacturing", "done", "source links and quantities"),
    ("inventory", "done", "partial deliveries and backorders"),
    ("payment", "posted", "requested documents are posted"),
    ("refund", "reconciled", "bank matching"),
    ("sale_invoice", "draft", "do not confirm or post"),
    ("sale_invoice", "read_only", "do not create or modify"),
])
def test_resume_and_initial_instruction_share_bound_phase(tmp_path, kind, target, expected):
    business = {"type": kind, "completion_target": target, "goal": "当前确认的业务", "references": []}
    initial = build_business_instruction(business)
    spec = build_task_contract(business, initial)
    actions, _, _ = _actions(path=tmp_path / "actions.sqlite")
    try:
        evidence = TaskEvidence(actions.reads, spec, tmp_path / "task-evidence.json")
        before = copy.deepcopy(evidence.spec)
        resumed = build_approval_resume_message(evidence.stage)
        assert f"{kind}; completion target: {target}" in resumed
        assert expected in resumed
        assert completion_target_instruction(kind, target) in initial
        assert completion_target_instruction(kind, target) in resumed
        assert "Earlier source messages and assistant plans do not expand this phase" in resumed
        assert "Read-only checks remain allowed" in resumed
        assert "approval itself does not mean execution" in resumed
        assert "Do not repeat completed writes" in resumed
        assert evidence.spec == before
        assert "Attached material" not in resumed and "Observed user references" not in resumed
        if kind in {"purchase", "manufacturing"}:
            assert "do not invoice or deliver goods" not in resumed
        if target == "sent":
            assert "do not send mail here" not in resumed
            assert "does not prove the recipient opened" in resumed
    finally:
        actions.store.close()


@pytest.mark.parametrize("stage", [None, {}, {"version": 2}, {"version": True},
    {"version": 1, "business_type": "unknown", "completion_target": "posted"},
    {"version": 1, "business_type": "purchase", "completion_target": "sent"}])
def test_legacy_or_unknown_stage_keeps_previous_resume_text(stage):
    assert build_approval_resume_message(stage) == (
        "The desktop host has completed human approval of the pending actions. "
        "Resume the existing business goal. Check durable action status and execute only "
        "the approved actions; approval itself does not mean execution. "
        "Do not repeat completed writes or request the same approval again."
    )


def test_stage_extras_cannot_reintroduce_historical_plan():
    stage = {"version": 1, "business_type": "sale_invoice", "completion_target": "posted",
             "source_messages": ["接着开票再发邮件"], "assistant_plan": "偷偷扩大任务"}
    before = copy.deepcopy(stage)
    resumed = build_approval_resume_message(stage)
    assert "接着开票再发邮件" not in resumed and "偷偷扩大任务" not in resumed
    assert stage == before
