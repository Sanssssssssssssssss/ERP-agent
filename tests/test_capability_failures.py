"""Background and partial-result failures through real local execution boundaries."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from erp_harness.erp._odoo_core.cross_instance import Selection
from erp_harness.erp.capabilities import NativeCapabilities, _TaskStore


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
