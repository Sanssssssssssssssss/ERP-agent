"""Phase contracts stop authority gaps without prescribing a golden tool trace."""
import copy
import json
import time

import pytest

from erp_harness.app.host import Workbench, build_business_instruction, build_task_contract
from erp_harness.app.runner import _handoff_required
from erp_harness.erp.task_evidence import TaskEvidence, TaskHandoff, failure_result
from tests.test_actions import _actions
from tests.test_workbench_host import _EventProcess


def bind(actions, path, kind="sale_invoice", target="confirmed", references=()):
    business = {"type": kind, "completion_target": target, "references": list(references)}
    actions.task_evidence = TaskEvidence(actions.reads, build_task_contract(business, "confirmed goal"), path)
    return actions.task_evidence


@pytest.fixture
def action_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "sale.order.action_confirm,account.move.action_post,sale.advance.payment.inv.create_invoices,account.move.message_post")
    actions, writer, runtime = _actions(path=tmp_path / "actions.sqlite", approval_mode="host")
    yield actions, writer, runtime
    actions.store.close()


def test_confirmed_sales_guard_is_used_before_approval_and_after_approval(action_fixture, tmp_path):
    actions, writer, _ = action_fixture
    bind(actions, tmp_path / "posted.json", target="posted")
    approved = actions.execute_method("account.move", "action_post", kwargs={"ids": [10]})
    assert approved.get("approval_required"), approved
    actions.store.approve(approved["action_id"], "test-host")
    bind(actions, tmp_path / "confirmed.json")
    result = actions.execute_method("account.move", "action_post", kwargs={"ids": [10]})
    assert _handoff_required(result) and writer.calls == []
    with pytest.raises(TaskHandoff):
        actions._execute_row(actions.store.get(approved["action_id"]), lambda: writer.calls.append("forbidden"))
    assert writer.calls == []
    assert len(actions.store.summary()["receipts"]) == 1


@pytest.mark.parametrize("model,method", [("stock.picking", "button_validate"), ("sale.advance.payment.inv", "create_invoices")])
def test_confirmed_sales_rejects_shipping_and_invoice_generation(action_fixture, tmp_path, model, method):
    actions, _, _ = action_fixture
    evidence = bind(actions, tmp_path / "evidence.json")
    with pytest.raises(TaskHandoff):
        evidence.check_stage("method", {"model": model, "method": method, "instance": "default", "kwargs": {"ids": [1]}})


def test_confirmed_sales_allows_schedule_support_without_delivery(action_fixture, tmp_path):
    actions, _, _ = action_fixture
    evidence = bind(actions, tmp_path / "evidence.json")
    payload = {"model": "stock.picking", "operation": "write", "values": {"scheduled_date": "2026-09-25"}}
    evidence.check_stage("write", payload)
    with pytest.raises(TaskHandoff):
        evidence.check_stage("write", {**payload, "values": {**payload["values"], "state": "done"}})


def test_scope_allows_supporting_manufacturing_and_pdf_but_not_email(action_fixture, tmp_path):
    actions, _, _ = action_fixture
    evidence = bind(actions, tmp_path / "manufacturing.json", kind="manufacturing", target="done")
    for model, method in [("purchase.order", "button_confirm"), ("stock.picking", "button_validate"), ("mrp.production", "button_mark_done")]:
        evidence.check_stage("method", {"model": model, "method": method})
    evidence = bind(actions, tmp_path / "invoice.json", target="posted")
    evidence.check_stage("write", {"model": "account.move.send.wizard", "values": {"sending_methods": []}})
    with pytest.raises(TaskHandoff):
        evidence.check_stage("write", {"model": "account.move.send.wizard", "values_list": [{"sending_methods": ["email"]}]})
    with pytest.raises(TaskHandoff):
        evidence.check_stage("method", {"model": "account.move", "method": "message_post"})
    evidence.check_stage("chatter", {"model": "account.move", "method": "message_post", "kwargs": {"body": "internal note"}})


def test_missing_mail_authority_is_handoff_but_bad_arguments_are_not(action_fixture, tmp_path):
    actions, writer, _ = action_fixture
    evidence = bind(actions, tmp_path / "mail.json", kind="invoice_delivery", target="sent")
    with pytest.raises(TaskHandoff) as error:
        evidence.reference_check("method", {"model": "account.move", "method": "message_post"})
    result = failure_result(error.value)
    assert _handoff_required(result) and result["retry_safe"] is False
    # Missing host binding wins over absent invoice details; do not keep guessing IDs.
    missing = actions.execute_method("account.move", "message_post", kwargs={"ids": [999], "partner_ids": [999]})
    assert _handoff_required(missing), missing
    invalid = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [0]})
    assert invalid["success"] is False and not _handoff_required(invalid)
    assert writer.calls == []


def test_legacy_evidence_keeps_existing_paths_and_readonly_still_blocks(action_fixture, tmp_path):
    actions, _, _ = action_fixture
    evidence = TaskEvidence(actions.reads, {"version": 1, "instruction_sha256": "legacy"}, tmp_path / "legacy.json")
    evidence.check_stage("method", {"model": "stock.picking", "method": "button_validate"})
    evidence = bind(actions, tmp_path / "readonly.json", target="read_only")
    with pytest.raises(TaskHandoff):
        evidence.prestate("write", {"model": "res.partner"})


def test_normal_purchase_confirmation_remains_allowed(action_fixture, tmp_path, monkeypatch):
    actions, writer, runtime = action_fixture
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "purchase.order.button_confirm")
    runtime.client.records["purchase.order"][20] = {"id": 20, "state": "draft"}
    bind(actions, tmp_path / "purchase.json", kind="purchase", target="confirmed")
    result = actions.execute_method("purchase.order", "button_confirm", kwargs={"ids": [20]})
    assert result.get("approval_required") and not _handoff_required(result), result
    assert writer.calls == []


def test_confirmed_mail_references_keep_the_verified_send_path(tmp_path, monkeypatch):
    from tests.test_invoice_mail import send, setup
    actions, writer, _ = setup(tmp_path, monkeypatch)
    try:
        refs = copy.deepcopy(actions.task_evidence.references)
        bind(actions, tmp_path / "stage.json", kind="invoice_delivery", target="sent", references=refs)
        result = send(actions)
        assert result.get("approval_required") and not _handoff_required(result), result
        assert writer.calls == []
    finally:
        actions.store.close()


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://odoo.test")
    monkeypatch.setenv("ODOO_DB", "test")
    monkeypatch.setenv("ODOO_USERNAME", "tester")
    monkeypatch.setenv("ODOO_API_KEY", "test-only")
    host = Workbench(tmp_path)
    host._launch = lambda run, continue_run: None
    host._launch_conversation = lambda run: None
    yield host
    host.close()


def business(host, *, sources=True):
    session = host.create_session("phase")
    sid = session["id"]
    raw = [{"id": "u1", "text": "开票并发给客户"}, {"id": "u2", "text": "选100%预付款，开票并过账"}]
    host.store.data["messages"][sid].extend({**m, "role": "user", "business_id": None} for m in raw)
    proposal = {"id": "p1", "type": "sale_invoice", "title": "开票", "goal": "为当前订单开100%预付款发票并过账", "completion_target": "posted", "status": "pending"}
    if sources:
        proposal["source_messages"] = raw
    host.store.data["messages"][sid].append({"id": "pmessage", "role": "assistant", "business_id": None, "proposal": proposal})
    return sid, host.confirm_business(sid, "p1", True)


def test_actual_host_instruction_separates_current_goal_and_authorization(host):
    sid, item = business(host)
    assert item["goal"] == "为当前订单开100%预付款发票并过账"
    path = host._instruction(item, "r_fixture")
    text = path.read_text(encoding="utf-8")
    assert "开票并发给客户" not in text
    assert "开票并发给客户" in path.with_name("authorization-sources.json").read_text(encoding="utf-8")
    assert json.loads(path.with_name("task-sources.json").read_text())["stage"]["completion_target"] == "posted"
    # The replay builder is the production writer's source.
    assert text == build_business_instruction({**item, "goal_submitted": False}, material_text=host._material_context(sid, []))


@pytest.mark.parametrize("sources", [False, True])
def test_read_only_initial_sources_or_goal_are_submitted_once(host, sources):
    sid, item = business(host)
    original, goal = "列出华东客户249全部订单", "查询已确认客户的订单"
    item.update(completion_target="read_only", goal=goal,
                source_messages=[{"id": "u-query", "text": original}] if sources else [])
    first = host._instruction(item, "r_query").read_text(encoding="utf-8")
    assert (original if sources else goal) in first
    assert (goal if sources else original) not in first
    assert item["goal_submitted"]
    followup = {"id": "u-next", "role": "user", "business_id": item["id"], "text": "继续查询下一页"}
    host.store.data["messages"][sid].append(followup)
    second = host._instruction(item, "r_next").read_text(encoding="utf-8")
    assert original not in second and goal not in second
    assert followup["text"] in second and followup["submitted_run_id"] == "r_next"
    third = host._instruction(item, "r_resume").read_text(encoding="utf-8")
    assert original not in third and goal not in third and followup["text"] not in third
    assert "Continue the existing confirmed phase" in third


def test_worker_handoff_is_idle_and_requires_new_proposal(host, tmp_path):
    sid, item = business(host)
    run = host.start_run(sid, item["id"])
    payload = failure_result(TaskHandoff("需要确认收件人"))
    process = _EventProcess([
        {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "mcp_odoo_execute_method", "args": {}},
        {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "mcp_odoo_execute_method", "result": {"details": {"structuredContent": payload}}},
    ])
    host._processes[run["id"]] = process
    host._consume_worker(run["id"], process, tmp_path / "absent-usage.json")
    assert run["status"] == item["status"] == "awaiting_input"
    assert run["error"] is None and item["active_run_id"] is None
    assert run["handoff"]["message"] == "需要确认收件人"
    view = host.get_business(sid, item["id"])
    assert view["runs"][0]["handoff"]["message"] == "需要确认收件人"
    assert view["activity"]["phase"] == "awaiting_input"
    with pytest.raises(RuntimeError, match="更新后的业务提案"):
        host.start_run(sid, item["id"])
    result = host.send_message(sid, "发给财务联系人", business_id=item["id"])
    conversation = host.store.data["conversation_runs"][result["run_id"]]
    assert conversation["revision_business_id"] == item["id"]


def test_old_mixed_goal_restores_confirmed_proposal_or_requires_confirmation(host):
    sid, item = business(host)
    item.pop("goal_contract_version")
    item["goal"] = "开票并发给客户\n选100%预付款，开票并过账"
    host._migrate_business_goal_flags()
    assert item["goal"] == "为当前订单开100%预付款发票并过账"
    item.pop("goal_contract_version")
    host.store.data["messages"][sid] = []
    host._migrate_business_goal_flags()
    assert item["requires_goal_confirmation"]
    with pytest.raises(RuntimeError, match="更新后的业务提案"):
        host.start_run(sid, item["id"])


def test_handoff_never_overrides_unresolved_write(host, tmp_path):
    from erp_harness.erp.store import ActionStore
    sid, item = business(host)
    run = host.start_run(sid, item["id"])
    path = host.store.root / "runs" / run["id"] / "odoo-actions.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = ActionStore(path)
    try:
        row = store.register(action_key="uncertain", kind="method", payload={}, identity={}, prestate={}, file_digests={},
            policy_digest="policy", approval_source="host", resource_key="invoice:1",
            run_id=run["id"], session_id=sid, expires_at=time.time() + 900, approved=True)
        assert store.claim(row["action_id"])["claimed"]
        assert store.mark_sending(row["action_id"])
    finally:
        store.close()
    run["handoff"] = {"message": "需要收件人", "code": "scope_handoff_required", "next_action": "renew_proposal"}
    host._finalize_run(run, "awaiting_input")
    assert run["status"] == "needs_reconciliation"
    assert item["status"] == "needs_reconciliation"


def test_handoff_is_durable_before_stop_and_never_requests_a_second_model_turn(tmp_path):
    import asyncio
    from erp_harness.context.paths import RuntimePaths
    from erp_harness.context.resources import ResourcePaths
    from erp_harness.providers import FakeProvider
    from erp_harness.runtime import AgentTool, AgentToolResult, AssistantMessage, TextContent, ToolCall
    from erp_harness.runtime.session import HarnessSession, SessionConfig
    from erp_harness.runtime.storage import JsonlSessionStorage
    from tests.runtime.pi_event_helpers import assistant_done

    async def check():
        path = tmp_path / "session.jsonl"
        payload = failure_result(TaskHandoff("当前阶段不发送邮件。请确认发票、公司和收件人。"))
        async def execute(*args, **kwargs):
            return AgentToolResult(content=[TextContent(text=json.dumps(payload, ensure_ascii=False))],
                                   details={"structuredContent": payload})
        tool = AgentTool(name="send_invoice", label="Send", description="Test handoff", parameters={"type": "object"}, execute_fn=execute)
        provider = FakeProvider([[assistant_done(AssistantMessage(model="fake", stop_reason="toolUse",
                                content=[ToolCall(id="send-1", name=tool.name, arguments={})]))],
                                [assistant_done(AssistantMessage(model="fake", content="This turn must not run."))]])
        def stop(turn):
            # Reopen the real transcript at the stopping boundary, not after close.
            messages = [json.loads(line).get("message", {}) for line in path.read_text(encoding="utf8").splitlines()]
            assert any(m.get("role") == "toolResult" and m.get("toolCallId") == "send-1"
                       and m.get("details", {}).get("structuredContent") == payload for m in messages)
            return any(_handoff_required(result) for result in turn.tool_results)
        session = await HarnessSession.load(SessionConfig(
            provider=provider, model="fake", system="Test", tools=[tool], cwd=tmp_path,
            storage=JsonlSessionStorage(path), should_stop_after_turn=stop,
            skills_enabled=False, extensions_enabled=False,
            resource_paths=ResourcePaths(root=tmp_path, paths=RuntimePaths(home=tmp_path / "home", agents_home=tmp_path / "agents"),
                                         agents_root=tmp_path / "agents", project_resources_enabled=False)))
        try:
            _ = [event async for event in session.prompt("Send the invoice")]
            assert len(provider.calls) == 1
        finally:
            await session.aclose()
    asyncio.run(check())
