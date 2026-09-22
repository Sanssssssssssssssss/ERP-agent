"""Regress the first propagation point and the write boundary, without a model."""
import asyncio
import copy
import json

import pytest

from erp_harness.app import conversation
from erp_harness.app.host import Workbench
from erp_harness.erp.task_evidence import TaskEvidence
from tests.test_actions import _actions


def test_proposal_preserves_original_and_rejects_later_correction(tmp_path):
    host = Workbench(tmp_path, repo=tmp_path)
    host._launch_conversation = lambda run: None
    try:
        sid = host.create_session()["id"]
        original = "只读列出华东机电客户249（苏州）的全部销售单，不要把前五条当全部。"
        result = host.send_message(sid, original)
        run = host.store.data["conversation_runs"][result["run_id"]]
        proposal = {"type": "sale_invoice", "title": "查询", "goal": "查询 ID 249 的订单", "completion_target": "read_only"}
        host._tool_start(run, {"tool_call_id": "p", "tool_name": "propose_business", "args": proposal})
        host._conversation_tool_end(run, {"tool_call_id": "p", "result": {"success": True, "proposal": proposal}})
        saved = host.store.data["messages"][sid][-1]["proposal"]
        assert saved["source_messages"][0]["text"] == original
        business = host.confirm_business(sid, saved["id"], True)
        instruction = host._instruction(business, "test-run")
        assert original in instruction.read_text(encoding="utf8")
        assert "ID 249" not in instruction.read_text(encoding="utf8")
        assert json.loads(instruction.with_name("task-sources.json").read_text())["read_only"]
        saved["status"] = "pending"
        host.store.data["messages"][sid].append({"id": "correction", "role": "user", "text": "改成杭州"})
        with pytest.raises(ValueError, match="new user"):
            host.confirm_business(sid, saved["id"], True)
    finally:
        host.close()


def test_user_quote_must_resolve_to_live_id():
    class Reads:
        def call(self, name, args):
            assert name == "search_records"
            return {"success": True, "result": [{"id": 516, "name": "华东机电客户249（苏州）"}]}
    ref = {"resource": "customer", "id": 249, "quote": "华东机电客户249（苏州）"}
    with pytest.raises(ValueError, match="uniquely"):
        conversation.resolve_references(Reads(), [ref], "查" + ref["quote"])
    ref["id"] = 516
    assert conversation.resolve_references(Reads(), [ref], "查" + ref["quote"])[0]["id"] == 516
    with pytest.raises(ValueError, match="exact"):
        conversation.resolve_references(Reads(), [ref], "另一个人")


def test_sales_readback_checks_original_party_and_optional_payment_term():
    from tests.test_workbench_sale_view import _state, RECORDS, NativeReadFixture
    from erp_harness.app.sale_view import refresh_business
    records = copy.deepcopy(RECORDS)
    records[("sale.order", 7)]["payment_term_id"] = False
    for partner_id, expected in ((10, "passed"), (20, "failed")):
        state = _state()
        state["businesses"]["b1"].update(completion_target="confirmed", references=[{
            "model": "res.partner", "id": partner_id, "quote": "原始客户", "fields": {"id": partner_id}}])
        refresh_business(state, "b1", NativeReadFixture(records))
        readback = state["businesses"]["b1"]["readback"]
        assert readback["outcome"]["status"] == expected
        assert next(c for c in readback["checks"] if c["name"] == "requested_reference_0")["status"] == expected


def test_reference_paging_and_server_count_share_the_filter(monkeypatch):
    calls = []
    class Reads:
        def call(self, name, args):
            calls.append((name, args))
            return {"success": True, "rows": [{"__count": 10}]} if name == "aggregate_records" else {"success": True, "result": [{"id": n, "name": f"S{n}"} for n in range(6)]}
    monkeypatch.setattr(conversation, "_ODOO_READS", Reads())
    result = asyncio.run(conversation._read_odoo_reference("r", {"resource": "sale_order", "domain": [["partner_id", "=", 516]], "fields": ["name"], "include_count": True})).details
    assert result["total_count"] == 10 and result["next_offset"] == 5 and not result["complete"]
    assert calls[0][1]["domain"] == calls[1][1]["domain"] == [["partner_id", "=", 516]]
    assert calls[0][1]["fields"] == ["id", "name"]


@pytest.mark.parametrize("change", ["wrong_target", "wrong_partner", "stale_name", "read_only", "valid"])
def test_target_evidence_prevents_sending_wrong_business(tmp_path, monkeypatch, change):
    actions, writer, runtime = _actions(path=tmp_path / "actions.sqlite3", approval_mode="host")
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "sale.order.action_confirm")
    partner = {"id": 7, "name": "Customer249"}
    order = {"id": 7, "name": "S7", "partner_id": [7, "Customer249"], "state": "draft"}
    runtime.client.records["res.partner"][7] = partner
    runtime.client.records["sale.order"][7] = copy.deepcopy(order)
    runtime.client.records["sale.order"][8] = {**order, "id": 8}
    spec = {"version": 1, "instruction_sha256": "frozen", "read_only": change == "read_only", "references": [
        {"model": "res.partner", "id": 7, "fields": partner},
        {"model": "sale.order", "id": 7, "fields": copy.deepcopy(order)}]}
    try:
        actions.task_evidence = TaskEvidence(actions.reads, spec, tmp_path / "evidence.json")
        if change == "wrong_partner":
            runtime.client.records["sale.order"][7]["partner_id"] = [8, "Different"]
        result = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [8 if change == "wrong_target" else 7]})
        if change in {"valid", "stale_name"}:
            assert result.get("action_status") == "pending_approval", result
            actions.store.approve(result["action_id"], "test-host")
            if change == "stale_name":
                runtime.client.records["res.partner"][7]["name"] = "Changed"
            result = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})
        assert result.get("success") is (change == "valid"), result
        assert len(writer.calls) == (1 if change == "valid" else 0)
    finally:
        actions.store.close()
