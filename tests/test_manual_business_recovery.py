"""Observed manual failures: cancellation, partial allocation and derived identity."""
import copy
import json
from unittest.mock import Mock, patch

import pytest

from erp_harness.erp.business_operations import method_prestate, method_verify
from erp_harness.erp.store import ActionStore
from erp_harness.erp.task_evidence import TaskEvidence, TaskHandoff
from erp_harness.app.approval_display import enrich_approval
from tests.test_enterprise_actions import _batch_business_case, Client
from tests.test_run_diagnostics import seed, rows, diagnose


def test_partial_allocation_is_known_effect_not_completed_production():
    runtime, payload = _batch_business_case("manufacturing")
    payload["method"] = "action_assign"
    raw_id = runtime.client.records["mrp.production"][7]["move_raw_ids"][0]
    runtime.client.records["stock.move"][raw_id].update(state="partially_available", quantity=1)
    before = method_prestate(runtime, payload)
    result = method_verify(runtime, payload, before, True)
    assert result["status"] == "satisfied"
    assert result["evidence"]["allocation_status"] in {"partial", "waiting_materials"}
    assert result["evidence"]["production_completed"] is False
    for lost_ack in (None, False):
        assert method_verify(runtime, payload, before, lost_ack)["status"] != "satisfied"


def test_purchase_cancel_reads_related_effects_before_dispatch():
    from types import SimpleNamespace
    c = Client()
    c.add("purchase.order", 1, name="P1", state="purchase", locked=False, company_id=1, partner_id=2, currency_id=6, amount_total=10, picking_ids=[3], invoice_ids=[], order_line=[4])
    c.add("stock.picking", 3, state="assigned", company_id=1, move_ids=[])
    c.add("purchase.order.line", 4, qty_received=0, qty_invoiced=0, move_dest_ids=[], order_id=1,
          product_id=5, product_uom_id=1, price_unit=10, tax_ids=[], product_qty=1, date_planned="2026-10-01 00:00:00")
    runtime = SimpleNamespace(client=c)
    payload = {"instance": "default", "model": "purchase.order", "method": "button_cancel", "kwargs": {"ids": [1]}}
    before = method_prestate(runtime, payload)
    assert method_verify(runtime, payload, before, None)["status"] != "satisfied"
    c.records["purchase.order"][1]["state"] = "cancel"
    c.records["stock.picking"][3]["state"] = "cancel"
    assert method_verify(runtime, payload, before, None)["status"] == "satisfied"
    for field, value in (("qty_received", 1), ("move_dest_ids", [88])):
        c.records["purchase.order.line"][4][field] = value
        with pytest.raises(TaskHandoff):
            method_prestate(runtime, payload)
        c.records["purchase.order.line"][4][field] = [] if field == "move_dest_ids" else 0


def test_verified_creation_requires_role_session_and_current_relations(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_AGENT_SESSION_ID", "session")
    reads = Mock()
    reads.instance = "default"
    reads.identity_context.return_value = {"identity_id": "manager"}
    spec = {"version": 1, "instruction_sha256": "host"}
    evidence = TaskEvidence(reads, spec, tmp_path/"evidence.json", ledger_path=tmp_path/"actions.sqlite3")
    evidence._search = Mock(return_value=[{"id": 2, "name": "MO2", "company_id": [1, "公司"], "product_id": [3, "产品"]}])
    row = {"action_id": "created", "status": "verified", "kind": "write", "session_id": "session", "identity_sha256": ActionStore.digest(evidence.identity), "payload": {"operation": "create", "model": "mrp.production", "values": {"company_id": 1, "product_id": 3}}, "verification": {"evidence": {"record_ids": [2]}}}
    with patch.object(ActionStore, "read_receipts", return_value=[row]):
        assert evidence._created_targets("mrp.production", {2})[0]["action_id"] == "created"
        for key, wrong in (("status", "needs_reconciliation"), ("session_id", "other"), ("identity_sha256", "other")):
            original = row[key]; row[key] = wrong
            assert evidence._created_targets("mrp.production", {2}) == []
            row[key] = original
        evidence._search.return_value[0]["company_id"] = [9, "其他公司"]
        assert evidence._created_targets("mrp.production", {2}) == []


def test_policy_diagnosis_retains_no_send_reason_and_conflicts_stay_unknown(tmp_path):
    seed(tmp_path)
    failure = {"code": "method_not_supported", "stage": "before_send", "layer": "action_policy", "odoo_request_seen": False, "next_action": "request_supported_alternative"}
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": {"success": False, "failure": failure}}})
    item = diagnose(tmp_path)["items"][0]
    assert item["odoo_request_seen"] is False and item["likely_failure_layer"] == "action_policy"
    assert item["next_action"] == "request_supported_alternative"
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "rpc_request_id": "rpc-1", "dispatch_started": True, "event": "start"})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == "correlation_conflict" and item["odoo_request_seen"] is None


def test_approval_names_are_fresh_model_scoped_and_do_not_mutate_payload():
    approval = {"model": "purchase.order", "operation": "create", "values": {"company_id": 1, "currency_id": 1, "order_line": [[0, 0, {"product_id": 1}]]}}
    before = copy.deepcopy(approval)
    reads = Mock()
    def read(tool, args):
        assert tool == "read_record"
        return {"success": True, "result": [{"id": 1, "display_name": args["model"]}]}
    reads.call.side_effect = read
    enrich_approval(approval, reads)
    assert approval["approval_display"]["ready"] is True
    assert len(approval["display_references"]) == 3
    assert approval["values"] == before["values"]
    reads.call.side_effect = lambda *_: {"success": False, "error": "permission denied"}
    enrich_approval(approval, reads)
    assert approval["approval_display"]["ready"] is False
    assert all(r["status"] == "unavailable" for r in approval["display_references"])


def test_replacement_date_blocks_expired_copy_before_binding(tmp_path):
    from datetime import datetime, timedelta
    reads = Mock(); reads.instance = "default"; reads.identity_context.return_value = {"identity_id": "manager"}
    spec = {"version": 1, "instruction_sha256": "host", "references": [{"model": "purchase.order", "purpose": "source", "id": 1}]}
    evidence = TaskEvidence(reads, spec, tmp_path/"date.json", session_id="host-session")
    evidence.bind = Mock(return_value=[]); evidence.reference_check = Mock(return_value=[])
    payload = {"model": "purchase.order", "operation": "create", "values": {"date_planned": "2020-01-01 01:00:00"}}
    with pytest.raises(TaskHandoff, match="交期"):
        evidence.prestate("write", payload)
    evidence.bind.assert_not_called()
    payload["values"]["date_planned"] = (datetime.now()+timedelta(days=3)).isoformat()
    evidence.prestate("write", payload)
    assert evidence.session_id == "host-session"


def test_confirmation_binds_order_lines_and_rejects_changes(monkeypatch):
    from tests.test_actions import _actions
    actions, writer, runtime = _actions(approval_mode="host")
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "sale.order.action_confirm")
    result = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})
    row = actions.store.get(result["action_id"])
    assert row["prestate"]["order_lines"][0]["product_uom_qty"] == 3
    assert actions._current_prestate_matches(row)
    runtime.client.records["sale.order.line"][71]["product_uom_qty"] = 4
    assert not actions._current_prestate_matches(row)
    runtime.client.records["sale.order.line"][71]["order_id"] = [8, "Other"]
    assert not actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]}).get("approval_required")
    runtime.client.records["sale.order"][7].pop("order_line")
    assert not actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]}).get("approval_required")
    assert not writer.calls


def test_unsupported_picking_cancel_guides_only_reviewed_purchase_route(monkeypatch):
    from tests.test_actions import _actions
    from erp_harness.app.runner import _handoff_required
    from erp_harness.tools.sops import build_sop_payload
    actions, writer, _ = _actions(approval_mode="host")
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "purchase.order.button_cancel")
    result = actions.execute_method("stock.picking", "action_cancel", args=[[2]])
    assert result["failure"]["odoo_request_seen"] is False and not result["retry_safe"]
    assert not _handoff_required(result)
    assert "purchase_id" in result["failure"]["next_action"]
    sop = build_sop_payload("safe_write_review", {"model": "purchase.order", "operation": "button_cancel"})
    assert "do not call stock.picking.action_cancel" in str(sop)
    assert "mcp_odoo_execute_method" in str(sop)
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "sale.order.action_confirm")
    assert _handoff_required(actions.execute_method("stock.picking", "action_cancel", args=[[2]]))
    assert not writer.calls


def test_completed_state_method_reuses_fresh_receipt_without_another_approval(monkeypatch):
    from tests.test_actions import _actions
    actions, writer, runtime = _actions(approval_mode="host")
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "sale.order.action_confirm")
    pending = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})
    actions.store.approve(pending["action_id"], "test-host")
    assert actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})["success"]
    repeat = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})
    assert repeat["success"] and repeat["action_id"] == pending["action_id"]
    assert len(actions.store.read_receipts(actions.store.path)) == 1 and len(writer.calls) == 1
    runtime.client.records["sale.order"][7]["state"] = "draft"
    assert actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})["action_status"] == "needs_reconciliation"
    assert len(writer.calls) == 1
