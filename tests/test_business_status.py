import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from erp_harness.app.business_status import build_status_context, read_business_status
from erp_harness.erp import invoice_mail
from tests.test_invoice_mail import Reader
from tests.test_workbench_sale_view import RECORDS, NativeReadFixture, _state

CONNECTION = {"url": "http://example.test", "database": "test", "principal": "finance"}


class Reads:
    instance = "default"

    def __init__(self):
        self.client = Reader()
        self.instances = {"default": SimpleNamespace(client=self.client)}
        self.denied = set()

    def call(self, name, args):
        assert name == "read_record"
        if args["model"] in self.denied:
            return {"success": False, "error": "AccessError: permission denied"}
        rows = self.client.read_records(args["model"], [args["record_id"]], args["fields"])
        return {"success": True, "result": rows[0] if rows else None}


def mail_state():
    reads = Reads()
    records = reads.client.records
    records["account.move"][10] = {"id": 10, "name": "INV/001", "state": "posted", "move_type": "out_invoice", "company_id": [1, "公司"],
        "partner_id": [7, "客户"], "commercial_partner_id": [7, "客户"], "currency_id": [6, "CNY"], "amount_total": 200, "invoice_pdf_report_id": [30, "invoice.pdf"]}
    records["res.partner"][8] = {"id": 8, "name": "财务", "email": "finance@example.test", "active": True, "company_id": False,
        "parent_id": [7, "客户"], "commercial_partner_id": [7, "客户"], "type": "invoice", "function": "财务"}
    records["res.company"] = {1: {"id": 1, "name": "公司", "email": "billing@example.test"}}
    records["res.currency"] = {6: {"id": 6, "name": "CNY"}}
    records["ir.attachment"][30] = {"id": 30, "name": "invoice.pdf", "mimetype": "application/pdf", "checksum": "abc", "file_size": 99, "res_model": "account.move", "res_id": 10}
    refs = [{"model": m, "id": i, "purpose": p, "fields": copy.deepcopy(records[m][i])} for m, i, p in
            [("account.move", 10, "target"), ("res.company", 1, "target"), ("res.partner", 8, "recipient")]]
    payload = {"instance": "default", "kwargs": {"ids": [10], "partner_ids": [8]}}
    evidence = invoice_mail.prestate(reads.instances["default"], payload)
    records["mail.message"][201] = {"id": 201, "model": "account.move", "res_id": 10, "message_type": "comment", "subject": evidence["subject"], "body": evidence["body"],
        "outgoing_email_to": evidence["email_to"], "partner_ids": [], "attachment_ids": [30], "notification_ids": [301]}
    records["mail.notification"] = {301: {"id": 301, "notification_type": "email", "notification_status": "sent",
        "mail_email_address": evidence["email_to"], "res_partner_id": False}}
    business = {"id": "b1", "session_id": "s1", "type": "invoice_delivery", "completion_target": "sent", "references": refs,
        "odoo_connection": CONNECTION, "readback": {"verification_status": "passed", "documents": [{"secret": "old"}]}}
    state = {"businesses": {"b1": business}, "runs": {"r1": {"id": "r1", "business_id": "b1", "status": "completed"}},
             "approvals": {"a1": {"action_id": "a1", "business_id": "b1", "run_id": "r1", "prestate": {"invoice_mail": evidence}}}}
    return state, reads


def read(context, reads, **kwargs):
    return read_business_status(context, reads, session_id=kwargs.get("session_id", "s1"), connection=kwargs.get("connection", CONNECTION))


def test_mail_live_missing_and_revoked_never_uses_old_green():
    state, reads = mail_state()
    context = build_status_context(state, "b1", "s1", CONNECTION)
    assert "old" not in json.dumps(context)
    result = read(context, reads)
    assert result["status"] == "ok" and result["verification_status"] == "passed", result
    assert result["delivery_receipts"][0]["delivery"] == "smtp_accepted"
    assert result["delivery_receipts"][0]["message_ids"] == [201]
    assert "does not prove latest_run_id" in result["receipt_evidence_scope"]
    # Reusing the frozen context must execute another live read, not retain green.
    assert "readback" not in context["state"]["businesses"]["b1"]
    reads.client.records["mail.notification"][301]["notification_status"] = "exception"
    result = read(context, reads)
    assert result["verification_status"] == "unknown"
    assert result["delivery_receipts"][0]["delivery"] == "unconfirmed"
    reads.denied.add("account.move")
    result = read(context, reads)
    assert result["status"] == "permission_denied"
    assert result["documents"] == result["checks"] == result["delivery_receipts"] == []


def test_scope_and_changed_customer_fail_closed():
    state, reads = mail_state()
    context = build_status_context(state, "b1", "s1", CONNECTION)
    assert read(context, reads, session_id="another")["status"] == "scope_mismatch"
    assert read(context, reads, connection={**CONNECTION, "principal": "sales"})["status"] == "scope_mismatch"
    with pytest.raises(ValueError):
        build_status_context(state, "b1", "another", CONNECTION)
    reads.client.records["res.partner"][8]["commercial_partner_id"] = [77, "别的公司"]
    result = read(context, reads)
    assert result["status"] == "unavailable" and not result["delivery_receipts"]


def test_historical_mail_and_missing_mail_stay_distinct():
    state, reads = mail_state()
    state["approvals"] = {}
    context = build_status_context(state, "b1", "s1", CONNECTION)
    result = read(context, reads)
    assert result["delivery_receipts"][0]["delivery"] == "smtp_accepted"
    reads.client.records["mail.message"].clear()
    result = read(context, reads)
    assert result["status"] == "ok" and result["verification_status"] == "unknown"
    assert result["delivery_receipts"][0]["delivery"] == "unconfirmed"


def test_redacted_reference_is_permission_denied_not_missing_record():
    state, reads = mail_state()
    original = reads.call
    def redacted(name, args):
        payload = original(name, args)
        if args["model"] == "res.partner":
            payload["result"].pop("email")
            payload["redacted_fields"] = ["email"]
        return payload
    reads.call = redacted
    result = read(build_status_context(state, "b1", "s1", CONNECTION), reads)
    assert not result["success"] and result["status"] == "permission_denied"
    assert not result["delivery_receipts"]


def test_consistently_changed_invoice_and_contact_do_not_redefine_original_customer():
    state, reads = mail_state()
    context = build_status_context(state, "b1", "s1", CONNECTION)
    reads.client.records["account.move"][10].update(partner_id=[9, "另一个客户"], commercial_partner_id=[9, "另一个客户"])
    reads.client.records["res.partner"][8].update(parent_id=[9, "另一个客户"], commercial_partner_id=[9, "另一个客户"])
    result = read(context, reads)
    assert result["success"] and result["verification_status"] == "failed", result
    assert any(c["name"].startswith("requested_reference_") and c["status"] == "failed" for c in result["checks"])


def test_generic_order_reads_current_facts_and_drops_failed_history():
    state = _state()
    state["businesses"]["b1"].update(odoo_connection=CONNECTION, completion_target="posted",
        references=[{"model": "sale.order", "id": 7, "fields": {"id": 7, "name": "SO001"}}])
    for run in state["runs"].values():
        run["documents"] = [d for d in run["documents"] if d["model"] == "sale.order"]
    state["runs"]["r2"].update(summary="SECRET", rounds=[{"reasoning": "SECRET"}], tools=[{"name": "search_records", "result": "SECRET"}])
    context = build_status_context(state, "b1", "s1", CONNECTION)
    assert "SECRET" not in json.dumps(context)
    native = NativeReadFixture(copy.deepcopy(RECORDS))
    reads = SimpleNamespace(call=native)
    result = read(context, reads)
    assert result["success"] and result["status"] == "ok" and result["verification_status"] == "passed", result
    native.failures.add(("sale.order", 7))
    result = read(context, reads)
    assert not result["success"] and result["status"] == "unavailable" and not result["documents"]
    assert len(json.dumps(result).encode()) < 16_384


def test_customer_reference_does_not_restore_exploratory_orders_to_chat_scope():
    state = _state()
    state["businesses"]["b1"].update(odoo_connection=CONNECTION, completion_target="confirmed",
        references=[{"model": "res.partner", "id": 10, "fields": {"id": 10}}])
    context = build_status_context(state, "b1", "s1", CONNECTION)
    assert all(d["model"] == "res.partner" for r in context["state"]["runs"].values() for d in r["documents"])
    native = NativeReadFixture(copy.deepcopy(RECORDS))
    result = read(context, SimpleNamespace(call=native))
    assert result["success"] and result["verification_status"] == "unknown", result
    assert {(args["model"], args["record_id"]) for _, args in native.calls} == {("res.partner", 10)}
    assert not any(d["model"] == "sale.order" for d in result["documents"])


def test_business_status_bounds_large_read_results():
    state = _state()
    state["businesses"]["b1"].update(odoo_connection=CONNECTION, completion_target="posted",
        references=[{"model": "sale.order", "id": 7, "fields": {"id": 7}}])
    for run in state["runs"].values():
        run["documents"] = [d for d in run["documents"] if d["model"] == "sale.order"]
    records = copy.deepcopy(RECORDS)
    records[("sale.order", 7)]["client_order_ref"] = "很长的客户参考" * 20_000
    result = read(build_status_context(state, "b1", "s1", CONNECTION), SimpleNamespace(call=NativeReadFixture(records)))
    assert result["truncated"] and len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 16_384


def test_pending_validation_survives_context_projection():
    from erp_harness.app.enterprise_view import action_targets

    state = _state()
    state["businesses"]["b1"].update(odoo_connection=CONNECTION, type="payment")
    state["runs"]["r2"]["tools"] = [{"name": "validate_write", "result": {
        "action_id": "unresolved", "action_status": "pending_approval"}}]
    context = build_status_context(state, "b1", "s1", CONNECTION)
    _, pending = action_targets(list(context["state"]["runs"].values()))
    assert pending, "A pending write must not disappear and turn settlement green."


@pytest.mark.parametrize("binding", ["reference", "verified_action"])
def test_exploratory_denied_order_cannot_hide_accessible_target(binding):
    state = _state()
    state["businesses"]["b1"].update(odoo_connection=CONNECTION, completion_target="posted")
    state["runs"]["r2"]["documents"].append({"model": "sale.order", "id": 999, "name": "Do not disclose"})
    if binding == "reference":
        state["businesses"]["b1"]["references"] = [{"model": "sale.order", "id": 7, "fields": {"id": 7}}]
    else:
        state["runs"]["r2"]["tools"] = [{"name": "execute_method", "arguments": {"model": "sale.order", "method": "action_confirm"},
            "result": {"action_id": "a1", "action_status": "verified", "verification": {"status": "satisfied", "evidence": {"records": [{"id": 7}]}}}}]
    native = NativeReadFixture(copy.deepcopy(RECORDS), failures={("sale.order", 999)})
    context = build_status_context(state, "b1", "s1", CONNECTION)
    result = read(context, SimpleNamespace(call=native))
    assert result["success"] and result["verification_status"] == "passed", result
    assert not any(args["record_id"] == 999 for _, args in native.calls)
    assert "Do not disclose" not in json.dumps(context)


@pytest.mark.parametrize("field,value", [("partner_id", [99, "Changed"]), ("currency_id", [2, "EUR"])])
def test_order_relation_changes_return_current_facts_but_fail_original_binding(field, value):
    state = _state()
    state["businesses"]["b1"].update(odoo_connection=CONNECTION, completion_target="posted",
        references=[{"model": "sale.order", "id": 7, "fields": {"id": 7, field: RECORDS[("sale.order", 7)][field]}}])
    context = build_status_context(state, "b1", "s1", CONNECTION)
    records = copy.deepcopy(RECORDS)
    records[("sale.order", 7)][field] = value
    records[("res.partner", 99)] = {"id": 99, "name": "Changed"}
    result = read(context, SimpleNamespace(call=NativeReadFixture(records)))
    assert result["success"] and result["verification_status"] == "failed", result
    order = next(d for d in result["documents"] if d["model"] == "sale.order")
    assert order["fields"][field] == value


@pytest.mark.parametrize("setting", ["ODOO_MCP_FIELD_POLICY_FILE", "ODOO_MCP_POLICY_FILE"])
def test_worker_environment_keeps_field_policy_without_expanding_write_methods(tmp_path, monkeypatch, setting):
    from erp_harness.app.worker import child_environment, conversation_environment
    from erp_harness.erp._odoo_core.write_policy import allowed_side_effect_methods
    from erp_harness.erp.reads import NativeReads

    monkeypatch.chdir(tmp_path)
    for key in ("ODOO_MCP_FIELD_POLICY_FILE", "ODOO_MCP_POLICY_FILE"):
        monkeypatch.delenv(key, raising=False)
    policy = tmp_path / "acl.json"
    policy.write_text(json.dumps({"field_acl": {"default": {"res.partner": {"deny": ["email"]}}},
                                 "allowed_side_effect_methods": ["sale.order.unlink"]}))
    monkeypatch.setenv(setting, "acl.json")
    for build_env in (child_environment, conversation_environment):
        env = build_env("s1", "r1")
        assert env["ODOO_MCP_FIELD_POLICY_FILE"] == str(policy)
        assert "ODOO_MCP_POLICY_FILE" not in env
        client = Reader()
        client.records["res.partner"][7]["email"] = "private@example.test"
        client.get_model_fields = lambda _model: {k: {"type": "char"} for k in ("id", "name", "email")}
        with patch.dict(os.environ, env, clear=True):
            payload = NativeReads(client).call("read_record", {"model": "res.partner", "record_id": 7, "fields": ["id", "name", "email"]})
            assert "sale.order.unlink" not in allowed_side_effect_methods()
        assert payload["success"] and payload["redacted_fields"] == ["email"], payload
        assert "email" not in payload["result"]
