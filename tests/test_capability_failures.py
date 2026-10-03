"""Background and partial-result failures through real local execution boundaries."""
import asyncio
import io
import json
import threading
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pydantic_core import to_json

from erp_harness.context.compaction import (
    build_compaction_summary_prompt,
    build_turn_prefix_summary_prompt,
)
from erp_harness.erp._odoo_core.access_helpers import (
    _acl_row_applies,
    _m2m_ids,
    _m2o_id,
    _rule_applies,
)
from erp_harness.erp._odoo_core.cross_instance import Selection
from erp_harness.erp._odoo_core.field_policy import FieldPolicy
from erp_harness.erp.capabilities import (
    NativeCapabilities,
    _CapabilityReadFailure,
    _PolicyReadClient,
    _TaskStore,
)
from erp_harness.erp.gateway import Json2ReadClient
from erp_harness.erp.reads import NativeReads
from erp_harness.runtime.loop import AgentContext, _execute_and_finalize, _PreparedToolCall
from erp_harness.runtime.messages import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from erp_harness.tools.router import native_tool_catalog, route_tools


@pytest.mark.parametrize("error,reason", [
    (PermissionError("private-odoo-error"), "permission_denied"),
    (TimeoutError("private-odoo-error"), "connection_timeout"),
])
def test_background_exceptions_are_persisted_publicly_and_polling_never_reexecutes(tmp_path, error, reason):
    store = _TaskStore(tmp_path / "tasks.sqlite3", max_workers=1)
    worker = Mock(side_effect=error)
    try:
        task = store.submit("index_knowledge", worker)
        store._executor.shutdown(wait=True)
        for result in (store.status(task["task_id"]), store.status(task["task_id"]), store.list()[0]):
            assert result["status"] == "failed"
            assert result["execution_failure"]["reason_code"] == reason
            assert result["execution_failure"]["next_action"]
            assert "private" not in json.dumps(result)
        assert store.status(task["task_id"])["success"] is True
        persisted = store._db.execute("SELECT result_json,error FROM capability_tasks").fetchone()
        assert "private" not in " ".join(value or "" for value in persisted)
        worker.assert_called_once()
    finally:
        store.close()


def test_returned_failure_marks_background_task_failed_without_changing_poll_contract(tmp_path):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.tasks = _TaskStore(tmp_path / "tasks.sqlite3", max_workers=1)
    capabilities.index_knowledge = Mock(return_value={
        "success": False, "reason_code": "permission_denied", "error": "private-source-error",
    })
    try:
        submitted = capabilities.call("submit_async_task", {
            "operation": "index_knowledge", "params": {"model": "res.partner"},
        })
        capabilities.tasks._executor.shutdown(wait=True)
        result = capabilities.call("get_async_task", {"task_id": submitted["task_id"]})
        assert result["success"] is True and result["status"] == "failed"
        assert result["execution_failure"]["reason_code"] == "permission_denied"
        assert "private" not in json.dumps(result)
        listed = capabilities.call("list_async_tasks", {})
        assert listed["success"] is True
        assert listed["tasks"][0]["execution_failure"] == result["execution_failure"]
        capabilities.index_knowledge.assert_called_once()
    finally:
        capabilities.close()


def test_legacy_background_errors_are_unknown_and_never_return_external_text(tmp_path):
    store = _TaskStore(tmp_path / "tasks.sqlite3", max_workers=1)
    try:
        task = store.submit("index_knowledge", lambda: {"success": False})
        store._executor.shutdown(wait=True)
        with store._db:
            store._db.execute("UPDATE capability_tasks SET result_json=NULL,error=?", ("legacy-private-error",))
        result = store.status(task["task_id"])
        assert result["execution_failure"]["reason_code"] == "tool_failed_unknown"
        assert "private" not in json.dumps(result)
    finally:
        store.close()


def test_successful_background_task_preserves_result_and_list_contract(tmp_path):
    store = _TaskStore(tmp_path / "tasks.sqlite3", max_workers=1)
    payload = {"success": True, "result": [{"id": 7}]}
    try:
        task = store.submit("index_knowledge", lambda: payload)
        store._executor.shutdown(wait=True)
        result = store.status(task["task_id"])
        assert result["success"] is True and result["status"] == "succeeded"
        assert result["result"] == payload and "execution_failure" not in result
        assert "result" not in store.list()[0]
    finally:
        store.close()


@pytest.mark.parametrize("name,arguments", [
    ("search_across_instances", {"model": "res.partner"}),
    ("aggregate_across_instances", {"model": "res.partner", "group_by": ["name"], "measures": ["amount:sum"]}),
    ("accounting_health_across_instances", {}),
])
def test_cross_instance_partial_failures_keep_successful_results_and_typed_causes(monkeypatch, name, arguments):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities._selection = lambda requested: Selection(["good", "denied"], [], [])
    rows = [{"id": 1, "name": "Ada", "amount": 10, "__count": 1}]
    client = SimpleNamespace(search_read=lambda *args, **kwargs: rows,
                             execute_method=lambda *args, **kwargs: rows)

    def selected_client(instance):
        if instance == "denied":
            raise PermissionError("private-rpc-error")
        return client

    capabilities._client = selected_client
    monkeypatch.setattr("erp_harness.erp.capabilities.fetch_aging_lines", lambda *args: [])
    monkeypatch.setattr("erp_harness.erp.capabilities.build_aging_report", lambda *args: {
        "buckets": {"current": 10}, "total_outstanding": 10,
    })
    result = capabilities.call(name, arguments)
    assert result["success"] is True and result["instance_count"] == 2
    assert set(result["results"]) == {"good"}
    assert isinstance(result["errors"]["denied"], str)
    assert result["failures"]["denied"]["reason_code"] == "permission_denied"
    assert result["failures"]["denied"]["next_action"] == "check_permissions"
    assert "private" not in json.dumps(result)
    if name == "search_across_instances":
        assert result["merged_count"] == 1
    elif name == "aggregate_across_instances":
        assert result["combined_measures"]["amount"] == 10
    else:
        assert result["combined_total_outstanding"] == 10


def test_returned_cross_instance_failure_is_not_merged_as_a_successful_record():
    def worker(instance):
        return {"success": False, "reason_code": "connection_timeout", "error": "private-rpc-error"} \
            if instance == "failed" else [{"id": 1}]

    results, errors, failures = NativeCapabilities._fan_out(["good", "failed"], worker)
    assert results == {"good": [{"id": 1}]}
    assert failures["failed"]["reason_code"] == "connection_timeout"
    assert "private" not in json.dumps(errors)


def test_non_object_capability_result_is_a_response_failure():
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.lookup_model_history = Mock(return_value=None)
    result = capabilities.call("lookup_model_history", {"name": "sale.order"})
    assert result["success"] is False
    assert result["reason_code"] == "invalid_response"
    assert result["next_action"] == "check_service_logs"


@pytest.mark.parametrize("payload,reason", [
    ({"success": False, "error": "private-rpc-error"}, "tool_failed_unknown"),
    ({"success": False, "error": "private-rpc-error", "failure": {
        "code": "permission_denied", "next_action": "external-instruction",
    }}, "permission_denied"),
])
def test_returned_untyped_capability_failure_does_not_publish_external_error(payload, reason):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.lookup_model_history = Mock(return_value=payload)
    result = capabilities.call("lookup_model_history", {"name": "sale.order"})
    assert result["success"] is False and result["reason_code"] == reason
    assert "private" not in result["error"]
    assert result["next_action"] != "external-instruction"


@pytest.mark.parametrize("status,reason", [
    ("index_missing", "knowledge_index_required"),
    ("refresh_required", "knowledge_index_required"),
    ("capacity_exceeded", "knowledge_capacity_exceeded"),
    ("identity_changed", "knowledge_identity_changed"),
])
def test_legacy_local_knowledge_refusals_keep_explanatory_message(status, reason):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.knowledge = SimpleNamespace(search_knowledge=Mock(return_value={
        "success": False, "status": status, "error": "Local knowledge index needs repair.",
    }))
    result = capabilities.call("search_knowledge", {"query": "Ada", "model": "res.partner"})
    assert result["status"] == status and result["reason_code"] == reason
    assert result["error"] == "Local knowledge index needs repair."


@pytest.mark.parametrize("name", ["get_async_task", "cancel_async_task"])
def test_unknown_background_task_has_stable_recovery_guidance(tmp_path, name):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.tasks = _TaskStore(tmp_path / "tasks.sqlite3")
    try:
        result = capabilities.call(name, {"task_id": "missing"})
        assert result["success"] is False and result["task_id"] == "missing"
        assert result["reason_code"] == "task_not_found"
        assert result["next_action"] == "list_async_tasks"
        assert result["error"] == "Unknown task_id: missing"
    finally:
        capabilities.close()


@pytest.mark.parametrize("payload,status", [
    ({"success": True}, "succeeded"), ({"success": False}, "failed"),
])
def test_finished_task_cannot_be_cancelled_or_replayed(tmp_path, payload, status):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.tasks = _TaskStore(tmp_path / "tasks.sqlite3")
    worker = Mock(return_value=payload)
    try:
        task = capabilities.tasks.submit("index_knowledge", worker)
        capabilities.tasks._executor.shutdown(wait=True)
        result = capabilities.call("cancel_async_task", {"task_id": task["task_id"]})
        assert result["success"] is False and result["task_id"] == task["task_id"]
        assert result["status"] == status and result["reason_code"] == "task_not_cancellable"
        assert result["next_action"] == "inspect_task_status"
        assert capabilities.tasks.status(task["task_id"])["status"] == status
        worker.assert_called_once()
    finally:
        capabilities.close()


def test_task_capacity_refusal_reports_live_count_and_does_not_enqueue_work(tmp_path):
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.tasks = _TaskStore(tmp_path / "tasks.sqlite3", max_workers=1, max_tasks=1)
    release, started = threading.Event(), threading.Event()

    def work(**kwargs):
        started.set()
        release.wait(10)
        return {"success": True}

    capabilities.index_knowledge = Mock(side_effect=work)
    arguments = {"operation": "index_knowledge", "params": {"model": "res.partner"}}
    try:
        first = capabilities.call("submit_async_task", arguments)
        assert first["success"] is True and started.wait(2)
        result = capabilities.call("submit_async_task", arguments)
        assert result["success"] is False and result["reason_code"] == "task_limit_reached"
        assert result["next_action"] == "wait_then_recheck"
        assert result["live_tasks"] == result["max_tasks"] == 1
        assert len(capabilities.tasks.list()) == 1
        capabilities.index_knowledge.assert_called_once()
    finally:
        release.set()
        capabilities.close()


@pytest.mark.parametrize("response,reason", [
    ({"success": False, "reason_code": "connection_timeout", "error": "private-rpc-error"}, "connection_timeout"),
    (None, "invalid_response"),
    ({"success": True}, "invalid_response"),
    ({"success": True, "result": None}, "invalid_response"),
    ({"success": True, "result": [None]}, "invalid_response"),
])
@pytest.mark.parametrize("boundary", ["direct", "fan_out", "async"])
def test_policy_adapter_failure_never_becomes_empty_success(tmp_path, response, reason, boundary):
    reads = SimpleNamespace(
        client=SimpleNamespace(uid=1), _lock=threading.RLock(), instance="main",
        _refresh_scope=Mock(), _require_fields=Mock(), search_records=Mock(return_value=response),
    )
    reads.instances = {"main": reads}
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.reads = reads
    capabilities.tasks = _TaskStore(tmp_path / "tasks.sqlite3")
    capabilities._selection = lambda requested: Selection(["main"], [], [])
    try:
        if boundary == "async":
            task = capabilities.call("submit_async_task", {"operation": "receivable_payable_aging"})
            capabilities.tasks._executor.shutdown(wait=True)
            result = capabilities.call("get_async_task", {"task_id": task["task_id"]})
            assert result["success"] is True and result["status"] == "failed"
            failure = result["execution_failure"]
        elif boundary == "fan_out":
            result = capabilities.call("search_across_instances", {"model": "res.partner"})
            assert result["success"] is True and result["merged_count"] == 0
            assert result["results"] == {}
            failure = result["failures"]["main"]
        else:
            result = capabilities.call("receivable_payable_aging", {})
            assert result["success"] is False
            failure = result
        assert failure["reason_code"] == reason
        assert failure["next_action"] == ("check_connection" if reason == "connection_timeout" else "check_service_logs")
        assert "private" not in json.dumps(result)
        reads.search_records.assert_called_once()
    finally:
        capabilities.close()


def _native_diagnostics():
    with patch.object(Json2ReadClient, "_json2_call_once", return_value={}):
        client = Json2ReadClient(url="http://offline.fixture", db="fixture", username="reader", api_key="fixture")
    reads = NativeReads(client, policy=FieldPolicy({}))
    capabilities = NativeCapabilities.__new__(NativeCapabilities)
    capabilities.reads = reads
    return reads, capabilities


def _diagnostic_model_reply(tmp_path, reads, capabilities, name, arguments):
    tool = next(row for row in route_tools(native_tool_catalog(), tmp_path / "backends.jsonl",
                                           native=reads, capabilities=capabilities, native_health=True)
                if row.name == "mcp_odoo_" + name)
    call = ToolCall(id="offline-" + name, name=tool.name, arguments=arguments)
    final = asyncio.run(_execute_and_finalize(
        AgentContext("", [], [tool]), AssistantMessage(model="offline", content=[call]),
        _PreparedToolCall(0, call, tool, arguments), None, None, lambda _: None))
    payload = json.loads(final.result.text)
    assert final.result.details["structuredContent"] == payload
    return payload, final.result.text


def _http_diagnostic_failure(status):
    # HTTP status must beat unrelated words in external exception bodies.
    body = {"name": "odoo.exceptions.AccessError",
            "message": "PRIVATE_EXTERNAL_MESSAGE timed out permission denied",
            "debug": "PRIVATE_TRACE", "context": {"token": "PRIVATE_TOKEN"}}
    return urllib.error.HTTPError("http://offline.fixture", status, "backend", {},
                                  io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize("name", ["inspect_model_relationships", "diagnose_access", "data_quality_report"])
@pytest.mark.parametrize("status,reason,action", [(403, "permission_denied", "check_permissions"),
                                                (429, "rate_limited", "wait_then_recheck")])
def test_native_partial_diagnostics_retain_http_causes_in_model_reply(tmp_path, name, status, reason, action):
    reads, capabilities = _native_diagnostics()
    arguments = {"model": "sale.order"}
    if name == "data_quality_report":
        reads.cache["sale.order"] = {"id": {"type": "integer"}, "name": {"type": "char"}}
        arguments.update(checks=["duplicates", "missing_required"], key_fields=["name"])
    with patch("urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_diagnostic_failure(status))) as sender:
        result, text = _diagnostic_model_reply(tmp_path, reads, capabilities, name, arguments)
    assert "PRIVATE" not in text
    if name == "inspect_model_relationships":
        assert result["success"] is False
        assert result["metadata_used"]["fields_get"] is False
        failures = [result]
    elif name == "diagnose_access":
        assert result["success"] is True
        assert result["model_metadata"]["record"] is None and result["actual_count"] is None
        assert any(code["code"] == "metadata_access_unavailable" and code["severity"] == "warning"
                   for code in result["diagnosis"]["codes"])
        failures = result["metadata_errors"]
        assert {row["stage"] for row in failures} == {"ir.model", "res.users.context_get"}
    else:
        assert result["success"] is True and result["summary"]["clean"] is False
        assert result["summary"]["checks_errored"] == ["duplicates"]
        assert result["results"][1]["ok"] is True
        assert result["read_failure_count"] == 1 and not result["read_failures_truncated"]
        failures = result["read_failure_details"]
    assert failures
    for failure in failures:
        assert failure["reason_code"] == reason and failure["next_action"] == action
        assert failure["http_status"] == status
    # Diagnostics remain read only; no RPC retries or extra metadata correction is automatic.
    assert all("/json/2/" in call.args[0].full_url for call in sender.call_args_list)
    assert not any(call.args[0].full_url.endswith(("/create", "/write", "/unlink")) for call in sender.call_args_list)


@pytest.mark.parametrize("group,grants,invalid", [(False, 1, False), (None, 1, False), (True, 0, True),
                                                ([True, "Bad"], 0, True), ([], 0, True),
                                                ({}, 0, True), ("invalid", 0, True), ([7, "Group"], 0, False)])
def test_native_access_partial_failure_preserves_successful_metadata_and_unknown_count(tmp_path, group, grants, invalid):
    reads, capabilities = _native_diagnostics()
    reads.cache["ir.model"] = {name: {"type": "char"} for name in ["id", "name", "model"]}
    acl_fields = ["id", "name", "model_id", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"]
    reads.cache["ir.model.access"] = {name: {"type": "char"} for name in acl_fields}
    model = {"id": 12, "name": "Sales order", "model": "sale.order"}
    acl = {"id": 1, "name": "Reader", "model_id": [12, "sale.order"], "group_id": group,
           "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False}

    def response(request, **kwargs):
        if request.full_url.endswith("/sale.order/search_count"):
            raise _http_diagnostic_failure(429)
        value = [model] if request.full_url.endswith("/ir.model/search_read") else [acl] \
            if request.full_url.endswith("/ir.model.access/search_read") else {}
        return io.BytesIO(json.dumps(value).encode())

    with patch("urllib.request.urlopen", side_effect=response) as sender:
        result, text = _diagnostic_model_reply(tmp_path, reads, capabilities, "diagnose_access",
                                              {"model": "sale.order", "expected_count": 1, "include_rules": False})
    assert result["success"] is True and result["model_metadata"]["record"] == model
    assert result["access"]["rows"] == [acl] and result["access"]["granting_count"] == grants
    assert result["actual_count"] is None
    failures = {row["stage"]: row for row in result["metadata_errors"]}
    assert failures["sale.order.search_count"]["reason_code"] == "rate_limited"
    if invalid:
        assert failures["ir.model.access.group_id"]["reason_code"] == "invalid_response"
    else:
        assert "ir.model.access.group_id" not in failures
    assert result["metadata_used"]["rules"] is False and result["rules"]["included"] is False
    assert any(row["code"] == "metadata_access_unavailable" for row in result["diagnosis"]["codes"])
    assert "PRIVATE" not in text and sender.call_count == 4


@pytest.mark.parametrize("group,parsed,applies", [(False, None, True), (None, None, True),
                                               (True, None, False), (0, None, False), (-1, None, False),
                                               ([], None, False), ({}, None, False), ("invalid", None, False),
                                               ([True, "Bad"], None, False), ([7, "Group", "extra"], None, False),
                                               ([7, False], None, False), (7, 7, True),
                                               ([7, "Group"], 7, True), ((7, "Group"), 7, True), (8, 8, False)])
def test_access_acl_groups_distinguish_global_empty_values_from_unknown_shapes(group, parsed, applies):
    assert _m2o_id(group) == parsed
    assert _acl_row_applies({"group_id": group}, {7}) is applies
    if parsed:
        assert not _acl_row_applies({"group_id": group}, None)


@pytest.mark.parametrize("state,read", [("uncalled", False), ("failed", False), ("empty", True)])
def test_native_access_rules_metadata_reports_actual_read_not_requested_configuration(tmp_path, state, read):
    reads, capabilities = _native_diagnostics()
    for model, fields in {
        "ir.model": ["id", "name", "model"],
        "ir.model.access": ["id", "name", "model_id", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"],
        "ir.rule": ["id", "name", "model_id", "domain_force", "groups", "active", "perm_read", "perm_write", "perm_create", "perm_unlink"],
    }.items():
        reads.cache[model] = {field: {"type": "char"} for field in fields}

    def response(request, **kwargs):
        if state == "uncalled" and request.full_url.endswith("/ir.model/search_read") \
                or state == "failed" and request.full_url.endswith("/ir.rule/search_read"):
            raise _http_diagnostic_failure(403)
        value = [{"id": 12, "name": "Sales order", "model": "sale.order"}] \
            if request.full_url.endswith("/ir.model/search_read") else {} \
            if request.full_url.endswith("/res.users/context_get") else []
        return io.BytesIO(json.dumps(value).encode())

    with patch("urllib.request.urlopen", side_effect=response) as sender:
        result, text = _diagnostic_model_reply(tmp_path, reads, capabilities, "diagnose_access", {"model": "sale.order"})
    assert result["success"] is True and result["rules"]["included"] is True
    assert result["metadata_used"]["rules"] is read
    rule_calls = [call for call in sender.call_args_list if call.args[0].full_url.endswith("/ir.rule/search_read")]
    assert len(rule_calls) == (0 if state == "uncalled" else 1)
    assert "PRIVATE" not in text


@pytest.mark.parametrize("name", ["inspect_model_relationships", "diagnose_access", "data_quality_report"])
def test_native_diagnostics_bad_backend_shapes_are_unknown_reads_not_empty_success(tmp_path, name):
    reads, capabilities = _native_diagnostics()
    arguments = {"model": "sale.order"}
    body = []
    if name == "data_quality_report":
        reads.cache["sale.order"] = {"id": {"type": "integer"}, "name": {"type": "char"}}
        arguments.update(checks=["duplicates"], key_fields=["name"])
        body = {"invalid": "shape"}
    with patch("urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(json.dumps(body).encode())):
        result, _text = _diagnostic_model_reply(tmp_path, reads, capabilities, name, arguments)
    failures = [result] if name == "inspect_model_relationships" else result["metadata_errors"] \
        if name == "diagnose_access" else result["read_failure_details"]
    assert failures and all(row["reason_code"] == "invalid_response" for row in failures)
    assert all(row["next_action"] == "check_service_logs" for row in failures)
    if name == "data_quality_report":
        assert result["summary"]["clean"] is False and result["summary"]["checks_errored"] == ["duplicates"]


def test_native_data_quality_failure_details_are_bounded_without_hiding_failed_checks(tmp_path):
    reads, capabilities = _native_diagnostics()
    reads.cache["sale.order"] = {"id": {"type": "integer"}, "name": {"type": "char"}}
    with patch("urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_diagnostic_failure(403))) as sender:
        result, text = _diagnostic_model_reply(tmp_path, reads, capabilities, "data_quality_report",
                                              {"model": "sale.order", "checks": ["duplicates"] * 10, "key_fields": ["name"]})
    assert result["success"] is True and result["summary"]["clean"] is False
    assert result["summary"]["checks_errored"] == ["duplicates"] * 10
    assert result["read_failure_count"] == sender.call_count == 10
    assert len(result["read_failure_details"]) == 8 and result["read_failures_truncated"]
    assert all(row["reason_code"] == "permission_denied" for row in result["read_failure_details"])
    assert "PRIVATE" not in text


def test_large_native_partial_diagnostics_survive_both_compaction_inputs(tmp_path):
    reads, capabilities = _native_diagnostics()
    reads.cache["sale.order"] = {"id": {"type": "integer"}, "name": {"type": "char"}}
    with patch("urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_diagnostic_failure(403))) as sender:
        result, text = _diagnostic_model_reply(tmp_path, reads, capabilities, "data_quality_report",
                                              {"model": "sale.order", "checks": ["missing_required"] * 10 + ["duplicates"],
                                               "key_fields": ["name"]})
    assert sender.call_count == 1 and len(text) > 2000
    assert len(result["results"]) == 11 and all(row["ok"] for row in result["results"][:10])
    assert result["summary"]["clean"] is False and result["summary"]["checks_errored"] == ["duplicates"]
    message = ToolResultMessage(tool_call_id="offline-data_quality_report", tool_name="mcp_odoo_data_quality_report",
                                content=[TextContent(text=text)])
    for prompt in (build_compaction_summary_prompt((message,)), build_turn_prefix_summary_prompt((message,))):
        assert '"clean":false' in prompt and "checks_errored" in prompt
        assert "permission_denied" in prompt and "check_permissions" in prompt
        assert '"http_status":403' in prompt and "PRIVATE" not in prompt


@pytest.mark.parametrize("name", ["inspect_model_relationships", "data_quality_report"])
def test_native_diagnostic_normal_paths_preserve_input_metadata_and_clean_report(tmp_path, name):
    reads, capabilities = _native_diagnostics()
    metadata = {"id": {"type": "integer"}, "name": {"type": "char"}}
    reads.cache["sale.order"] = metadata
    arguments = {"model": "sale.order"}
    if name == "inspect_model_relationships":
        arguments["fields_metadata"] = metadata
    else:
        arguments.update(checks=["missing_required"])
    with patch("urllib.request.urlopen", side_effect=AssertionError("no RPC needed")) as sender:
        expected = capabilities.call(name, arguments)
        result, _text = _diagnostic_model_reply(tmp_path, reads, capabilities, name, arguments)
    assert _text == to_json(expected).decode()
    assert result["success"] is True
    if name == "inspect_model_relationships":
        assert result["metadata_used"]["source"] == "input"
        assert result["summary"]["field_count"] == 2
    else:
        assert result["summary"]["clean"] is True and not result["summary"]["checks_errored"]
        assert "read_failure_details" not in result
    assert "reason_code" not in result
    sender.assert_not_called()


@pytest.mark.parametrize("groups,parsed,applies", [
    (None, set(), True), (False, set(), True), ([], set(), True),
    ([1, 7], {1, 7}, True), ([[1, "Group"], (7, "Group")], {1, 7}, True),
    ([8], {8}, False), (True, None, False), ({}, None, False),
    ("invalid", None, False), ([True], None, False), ([0], None, False),
    ([-1], None, False), ([7, True], None, False), ([[7]], None, False),
    ([[7, False]], None, False), ([[7, "Group", "extra"]], None, False),
])
def test_access_many_to_many_groups_preserve_unknown_instead_of_global(groups, parsed, applies):
    assert _m2m_ids(groups) == parsed
    assert _rule_applies({"groups": groups}, {1}) is applies
    if parsed:
        assert not _rule_applies({"groups": groups}, None)


@pytest.mark.parametrize("target,groups,valid", [
    ("direct", [1], True), ("direct", [], True), ("direct", [True], False),
    ("direct", {}, False), ("all", [True], False), ("all", [[1]], False),
    ("rule", [], True), ("rule", [1], True), ("rule", [True], False),
    ("rule", {}, False), ("rule", "invalid", False),
])
def test_native_access_unknown_m2m_metadata_never_grants_or_becomes_global(tmp_path, target, groups, valid):
    reads, capabilities = _native_diagnostics()
    metadata = {
        "ir.model": ["id", "name", "model"],
        "ir.model.access": ["id", "name", "model_id", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"],
        "res.users": ["id", "name", "groups_id", "all_group_ids"],
        "ir.rule": ["id", "name", "model_id", "domain_force", "groups", "active", "perm_read", "perm_write", "perm_create", "perm_unlink"],
    }
    for model, names in metadata.items():
        reads.cache[model] = {name: {"type": "char"} for name in names}
    user = {"id": 2, "name": "User", "groups_id": groups if target == "direct" else [1], "all_group_ids": []}
    if target == "all":
        user["all_group_ids"] = groups
    acl = {"id": 1, "name": "Reader", "model_id": [12, "Sale"], "group_id": [1, "Reader"],
           "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False}
    rule = {"id": 1, "name": "Rule", "model_id": [12, "Sale"], "domain_force": "[]", "active": True,
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
            "groups": groups if target == "rule" else []}

    def response(request, **kwargs):
        if request.full_url.endswith("/ir.model/search_read"):
            value = [{"id": 12, "name": "Sale", "model": "sale.order"}]
        elif request.full_url.endswith("/res.users/context_get"):
            value = {"uid": 2}
        elif request.full_url.endswith("/res.users/read"):
            value = [user]
        elif request.full_url.endswith("/ir.model.access/search_read"):
            value = [acl]
        elif request.full_url.endswith("/ir.rule/search_read"):
            value = [rule]
        else:
            raise AssertionError("Unexpected read-only RPC")
        return io.BytesIO(json.dumps(value).encode())

    with patch("urllib.request.urlopen", side_effect=response) as sender:
        result, text = _diagnostic_model_reply(tmp_path, reads, capabilities, "diagnose_access", {"model": "sale.order"})
    assert result["success"] is True and result["actual_count"] is None
    assert result["current_user"]["record"] == user
    expected_groups = groups if target == "direct" and valid else [1] if valid or target == "rule" else None
    assert result["current_user"]["group_ids"] == expected_groups
    assert result["access"]["granting_count"] == (1 if expected_groups else 0)
    assert result["rules"]["global"] == ([] if target == "rule" and groups != [] else [rule])
    assert result["rules"]["applicable"] == ([] if target == "rule" and not valid else [rule])
    if valid:
        assert not result["metadata_errors"]
    else:
        stage = "ir.rule.groups" if target == "rule" else "res.users.groups"
        failure = next(row for row in result["metadata_errors"] if row["stage"] == stage)
        assert failure["reason_code"] == "invalid_response"
        assert any(row["code"] == "metadata_access_unavailable" for row in result["diagnosis"]["codes"])
        assert not any(row["code"] == "no_access_issue_detected" for row in result["diagnosis"]["codes"])
    assert "PRIVATE" not in text and sender.call_count == 5


def test_capability_policy_exception_preserves_safe_field_diagnostics_once():
    error = ValueError("Field policy denies access to ['amount_residual'] on account.move.line; aggregation on restricted fields is blocked to prevent inference.")
    runtime = SimpleNamespace(client=SimpleNamespace(uid=None), _lock=threading.RLock(), _refresh_scope=lambda: None)
    client = _PolicyReadClient(runtime)
    with pytest.raises(_CapabilityReadFailure) as caught:
        client._locked(lambda: (_ for _ in ()).throw(error))
    failure = caught.value.failure
    assert failure["restricted_fields"] == ["amount_residual"] and failure["model"] == "account.move.line"
    assert failure["reason_code"] == "field_policy_denied" and failure["next_action"] == "check_field_policy"
    assert "amount_residual" in failure["error"]
    assert client.read_failures == [failure] and client.read_failure_count == 1
    forged = _CapabilityReadFailure({**failure, "restricted_fields": ["PRIVATE_BODY"],
                                    "model": "PRIVATE_TOKEN", "next_action": "unsafe", "error": "PRIVATE_BODY"})
    assert "PRIVATE" not in str(forged.failure)
    assert "restricted_fields" not in forged.failure and "model" not in forged.failure
    assert forged.failure["next_action"] == "check_field_policy"
