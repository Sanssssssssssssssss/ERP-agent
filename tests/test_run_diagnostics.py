import ast
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from erp_harness.erp._odoo_core.odoo_client import READ_CALL_ID, OdooClient
from erp_harness.erp.read_failures import FAILURE_GUIDANCE
from erp_harness.erp.reads import NativeReads
from erp_harness.tools.run_diagnostics import _payload, build_diagnostic_tool, summarize_run

IDENTITY = {"identity_id": "role-a", "credential_scope_sha256": "credential-scope"}
INCIDENTS = json.loads((Path(__file__).resolve().parents[1]
    / "experiments/agent_regression/bench_recovery_cases.json").read_text(encoding="utf-8"))
FIELD_CASES = [case for case in INCIDENTS if case["failure_layer"] == "unknown_field"]
OTHER_CASES = [case for case in INCIDENTS
    if case["failure_layer"] in {"sop_inputs", "empty_domain", "observation_path", "diagnostic_scope"}]


def seed(root):
    (root / "requests").mkdir()
    (root / "requests/0001.meta.json").write_text(json.dumps({
        "run_id": "run", "session_id": "session", "request_id": "request1",
        "tool_call_ids": ["call-a", "call-b"]}), encoding="utf8")
    for name in ("tool-backends.jsonl", "odoo-native-requests.jsonl", "world-observations.jsonl", "session.jsonl"):
        (root / name).write_text("", encoding="utf8")


def rows(root, name, *values):
    (root / name).write_text("".join(json.dumps(v) + "\n" for v in values), encoding="utf8")


def diagnose(root, actions=()):
    with patch("erp_harness.tools.run_diagnostics.ActionStore.read_receipts", return_value=actions):
        return summarize_run(root, root / "session.jsonl", IDENTITY, run_id="run", session_id="session")


def test_correlations_unknown_logs_and_no_business_truth(tmp_path):
    seed(tmp_path)
    rows(tmp_path, "tool-backends.jsonl", {"tool_call_id": "call-a", "event": "start"},
         {"tool_call_id": "call-a", "event": "end"})
    failed = {"message": {"role": "toolResult", "toolCallId": "call-a", "isError": False,
              "details": {"structuredContent": {"success": False, "error": "SECRET-RAW-REASONING",
                  "approval": {"token": "SECRET-APPROVAL"}}}}}
    rows(tmp_path, "session.jsonl", failed, failed)
    result = diagnose(tmp_path)
    assert len(result["items"]) == 1
    assert result["items"][0]["odoo_request_seen"] is None
    assert result["items"][0]["tool_ended"] is True
    assert result["business_truth"] is False and "SECRET" not in json.dumps(result)
    assert len(json.dumps(result).encode()) <= 8192
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-b", "event": "end", "status": 200})
    assert diagnose(tmp_path)["items"][0]["odoo_request_seen"] is None
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "event": "start", "rpc_request_id": "rpc-1", "dispatch_started": True})
    item = diagnose(tmp_path)["items"][0]
    assert item["odoo_request_seen"] is True and item["rpc_incomplete"] is True
    rows(tmp_path, "odoo-native-requests.jsonl",
         {"tool_call_id": "call-a", "event": "start", "rpc_request_id": "rpc-1", "dispatch_started": True},
         {"tool_call_id": "call-b", "event": "end", "rpc_request_id": "rpc-1"})
    assert diagnose(tmp_path)["items"][0]["error_code"] == "correlation_conflict"
    assert diagnose(tmp_path)["items"][0]["odoo_request_seen"] is None


def test_duplicate_request_ids_and_conflicting_dispatch_remain_unknown(tmp_path):
    seed(tmp_path)
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "isError": True, "content": [{"type": "text", "text": "Tool read_record not found"}]}})
    rows(tmp_path, "tool-backends.jsonl", {"tool_call_id": "call-a", "event": "start"})
    item = diagnose(tmp_path)["items"][0]
    assert item["stage"] == "unknown" and item["odoo_request_seen"] is None
    assert item["error_code"] == "correlation_conflict"
    rows(tmp_path, "tool-backends.jsonl")
    rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": "request2", "tool_call_ids": ["call-a"]})
    item = diagnose(tmp_path)["items"][0]
    assert item["request_id"] is None and item["odoo_request_seen"] is None
    assert item["error_code"] == "correlation_conflict"


def test_pre_dispatch_only_and_historical_world_staleness(tmp_path):
    seed(tmp_path)
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "isError": True, "content": [{"type": "text", "text": "Tool read_record not found"}]}})
    assert diagnose(tmp_path)["items"][0]["odoo_request_seen"] is False
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "details": {"success": False}, "isError": True}})
    observation = {"type": "world_observation", "call_id": "call-a", "identity": IDENTITY,
                   "sequence": 2, "receipt_id": "obs-1"}
    rows(tmp_path, "world-observations.jsonl", observation,
         {"type": "world_invalidation", "identity_ids": ["role-a"], "sequence": 3})
    assert diagnose(tmp_path)["items"][0]["world_stale"] is True
    with (tmp_path / "world-observations.jsonl").open("a") as f:
        f.write('{"partial":')
    result = diagnose(tmp_path)
    assert result["items"][0]["world_stale"] is None and not result["evidence_complete"]


def test_identity_scope_and_unresolved_writes_never_replay(tmp_path):
    seed(tmp_path)
    action = {"action_id": "act-1", "run_id": "run", "session_id": "session", "identity": IDENTITY,
              "status": "needs_reconciliation", "payload": {"secret": "DO-NOT-RETURN"}}
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.jsonl")}
    item = diagnose(tmp_path, [action])["items"][0]
    assert item["next_action"] == "reconcile_without_replay" and item["odoo_request_seen"] is None
    assert "DO-NOT-RETURN" not in json.dumps(item)
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob("*.jsonl")}
    assert not diagnose(tmp_path, [{**action, "run_id": "other"}])["success"]
    assert not diagnose(tmp_path, [{**action, "identity": {"identity_id": "other"}}])["success"]
    rows(tmp_path, "world-observations.jsonl", {"type": "world_observation", "identity": {"identity_id": "other"}})
    assert diagnose(tmp_path)["error_code"] == "identity_mismatch"
    with patch.dict("os.environ", {"HARBOR_TRIAL_ID": "run", "PI_AGENT_SESSION_ID": "session"}):
        identity = dict(IDENTITY)
        tool = build_diagnostic_tool(tmp_path, tmp_path / "session.jsonl", lambda: identity)
        assert asyncio.run(tool.execute("x", {"path": "../other"})).details["error_code"] == "no_arguments_allowed"
        identity["identity_id"] = "revoked"
        assert asyncio.run(tool.execute("x", {})).details["error_code"] == "identity_or_scope_unavailable"


def test_live_routing_is_scoped_read_only_and_not_reused_as_current_history(tmp_path):
    from unittest.mock import Mock

    from erp_harness.app.capability_routing import project_routing_result
    seed(tmp_path)
    context=Mock(return_value={'status':'held','reason':'unresolved_write','active':['actions'],
                              'business_truth':False,'automatic_business_retry':False})
    with patch.dict('os.environ',{'HARBOR_TRIAL_ID':'run','PI_AGENT_SESSION_ID':'session'}), \
         patch('erp_harness.tools.run_diagnostics.ActionStore.read_receipts',return_value=[]):
        identity=dict(IDENTITY)
        tool=build_diagnostic_tool(tmp_path,tmp_path/'session.jsonl',lambda:identity,routing_context=context)
        result=asyncio.run(tool.execute('diagnose',{}))
        assert result.details['routing']['reason']=='unresolved_write'
        text=result.content[0].text
        assert len(text.encode())<=8192
        old=json.loads(project_routing_result('diagnose_current_run',text))
        assert 'routing' not in old and old['business_truth'] is False
        identity['identity_id']='other'
        assert not asyncio.run(tool.execute('denied',{})).details['success']
        context.assert_called_once()


def test_rpc_start_end_ids_preserve_attempt_count_and_timeout_unknown(tmp_path):
    path = tmp_path / "rpc.jsonl"
    with patch.object(OdooClient, "_connect"):
        client = OdooClient("http://fixture", "db", "role", "", transport="json2", api_key="secret")
    token = READ_CALL_ID.set("call-a")
    try:
        with patch.dict("os.environ", {"ODOO_REQUEST_LOG": str(path)}), patch("urllib.request.urlopen", side_effect=TimeoutError):
            try:
                client._json2_call("sale.order", "action_confirm", {})
            except TimeoutError:
                pass
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert [r["event"] for r in events] == ["start", "end"]
        assert events[0]["rpc_request_id"] == events[1]["rpc_request_id"]
        assert events[1]["status"] is None and events[1]["error_type"] == "TimeoutError"
        assert all(r["dispatch_started"] for r in events)
        with patch.dict("os.environ", {"ODOO_REQUEST_LOG": str(path)}):
            evidence = NativeReads.__new__(NativeReads).world_rpc_evidence("call-a")
        assert evidence["refs"][0]["completed_attempts"] == 1
    finally:
        READ_CALL_ID.reset(token)


def test_invalid_configuration_has_no_http_start(tmp_path):
    path = tmp_path / "rpc.jsonl"
    with patch.object(OdooClient, "_connect"):
        client = OdooClient("http://fixture", "db", "role", "", transport="json2")
    with patch.dict("os.environ", {"ODOO_REQUEST_LOG": str(path)}), patch("urllib.request.urlopen") as http:
        try:
            client._json2_call("sale.order", "read", {})
        except ValueError:
            pass
        http.assert_not_called()
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(events) == 1 and events[0]["event"] == "end" and not events[0]["dispatch_started"]
        assert NativeReads.__new__(NativeReads).world_rpc_evidence(None)["status"] == "no_completed_attempt"


def test_uncertain_write_is_not_hidden_by_recent_failures(tmp_path):
    seed(tmp_path)
    rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": "request2", "tool_call_ids": ["call-c"]})
    rows(tmp_path, "session.jsonl", *[{"message": {"role": "toolResult", "toolCallId": call,
         "isError": True}} for call in ("call-a", "call-b", "call-c")])
    action = {"action_id": "act-unknown", "run_id": "run", "session_id": "session",
              "identity": IDENTITY, "status": "sending"}
    result = diagnose(tmp_path, [action, *[{**action, "action_id": f"pending-{i}",
                                           "status": "pending_approval"} for i in range(4)]])
    assert len(result["items"]) == 3
    assert result["items"][0]["action_id"] == "act-unknown"
    assert result["items"][0]["next_action"] == "reconcile_without_replay"


@pytest.mark.parametrize("case", FIELD_CASES, ids=lambda case: case["id"])
def test_observed_field_refusals_keep_live_discovery_in_self_debug(tmp_path, case):
    from erp_harness.app.capability_routing import project_routing_result
    from erp_harness.tools.router import native_tool_catalog, route_tools
    from tests.test_supply_context import SupplyClient

    seed(tmp_path)
    client = SupplyClient()
    model = case["failed_arguments"]["model"]
    unknown = ast.literal_eval(case["original_error"].split("Unknown field(s) ", 1)[1].split(" on ", 1)[0])
    client._add(model, [field for field in case["failed_arguments"]["fields"] if field not in unknown], [])
    routed = next(tool for tool in route_tools(native_tool_catalog(), tmp_path / "tool-backends.jsonl",
        native=NativeReads(client), native_health=True) if tool.name == case["tool"])
    with patch.object(client, "read_records", create=True, side_effect=AssertionError("invalid field reached data RPC")):
        reply = asyncio.run(routed.execute("call-a", case["failed_arguments"]))
    failure = reply.details["structuredContent"]
    assert failure["success"] is False and failure["recovery_request"]
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "details": reply.details}})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = diagnose(tmp_path)
    item = result["items"][0]
    assert item["error_code"] == "unknown_field"
    assert item["reason_code"] == "query_invalid"
    assert item["likely_failure_layer"] == "tool_arguments"
    assert item["next_action"] == "discover_live_fields"
    assert item["recovery_request"]["tool"] == "mcp_odoo_get_model_fields"
    assert item["recovery_request"]["arguments"]["model"] == model
    assert item["recovery_request"]["unknown_fields"] == unknown
    assert item["recovery_request"]["arguments"]["instance"] == failure["recovery_request"]["arguments"]["instance"]
    assert "类型" in item["recovery_request"]["notice"] and "必要" in item["recovery_request"]["notice"]
    assert item["odoo_request_seen"] is None  # No RPC evidence cannot prove no dispatch.
    assert result["business_truth"] is False and len(json.dumps(result, ensure_ascii=False).encode()) <= 8192
    projected = json.loads(project_routing_result("diagnose_current_run", json.dumps({**result, "routing": {}})))
    assert projected["items"] == result["items"] and "routing" not in projected
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_field_discovery_does_not_override_conflicts_or_uncertain_writes(tmp_path):
    from erp_harness.erp.reads import UnknownFieldsError

    seed(tmp_path)
    recovery = UnknownFieldsError("sale.order", ["old_field"], ["name"]).recovery
    recovery["arguments"]["instance"] = "default"
    failure = {"success": False, "reason_code": "query_invalid", "recovery_request": recovery,
               "error": "SECRET-RAW", "action_id": "act-unknown"}
    action = {"action_id": "act-unknown", "run_id": "run", "session_id": "session",
              "identity": IDENTITY, "status": "sending"}
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "content": [{"type": "text", "text": json.dumps(failure)}]}})
    item = diagnose(tmp_path, [action])["items"][0]
    assert item["next_action"] == "reconcile_without_replay" and "recovery_request" not in item
    rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": "request2", "tool_call_ids": ["call-a"]})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == "correlation_conflict" and "recovery_request" not in item
    assert "SECRET" not in json.dumps(item)
    rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": "request2", "tool_call_ids": []})
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "event": "start",
         "rpc_request_id": "rpc-incomplete", "dispatch_started": True})
    item = diagnose(tmp_path)["items"][0]
    assert item["likely_failure_layer"] == "odoo_transport" and "recovery_request" not in item


def test_field_discovery_only_exposes_bounded_safe_metadata(tmp_path):
    from erp_harness.erp.reads import UnknownFieldsError

    seed(tmp_path)
    recovery = UnknownFieldsError("sale.order", ["old_field"], ["name"]).recovery
    recovery["arguments"].update(instance="secondary", token="SECRET-TOKEN")
    recovery.update(notice="SECRET-INSTRUCTIONS", candidate_fields=["SECRET-CANDIDATES"])
    failure = {"success": False, "reason_code": "query_invalid", "recovery_request": recovery,
               "error": "SECRET-RAW", "detail": "SECRET-DETAIL"}
    def record(value):
        rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
             "content": [{"type": "text", "text": json.dumps(value)}]}})
        return diagnose(tmp_path)

    result = record(failure)
    hint = result["items"][0]["recovery_request"]
    assert hint["arguments"] == {"model": "sale.order", "instance": "secondary", "query": "old_field"}
    assert "SECRET" not in json.dumps(result)
    for change in ({"tool": "mcp_odoo_write_record"}, {"arguments": []},
                   {"unknown_fields": "old_field"}, {"unknown_fields": ["../SECRET"]}):
        invalid = copy.deepcopy(failure)
        invalid["recovery_request"].update(change)
        assert "recovery_request" not in record(invalid)["items"][0]
    assert "recovery_request" not in record({**failure, "reason_code": "permission_denied"})["items"][0]
    bounded = copy.deepcopy(failure)
    bounded["recovery_request"]["unknown_fields"] = ["x" * 155 + str(i) for i in range(50)]
    result = record(bounded)
    hint = result["items"][0]["recovery_request"]
    assert len(hint["unknown_fields"]) == 3 and hint["unknown_field_count"] == 50
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 8192


@pytest.mark.parametrize("case", OTHER_CASES, ids=lambda case: case["id"])
def test_observed_nonfield_failures_preserve_classification(tmp_path, case):
    from erp_harness.tools.sops import get_sop
    from experiments.agent_regression.bench_recovery import verify_source
    from tests.test_supply_context import SupplyClient

    family = case["failure_layer"]
    if family == "sop_inputs":
        payload = get_sop(**case["failed_arguments"])
        expected = "sop_inputs_invalid"
    elif family == "empty_domain":
        client = SupplyClient()
        with patch.object(client, "search_records", create=True, side_effect=AssertionError("invalid query reached Odoo")):
            payload = NativeReads(client).call("find_records", case["failed_arguments"])
        expected = "query_invalid"
    else:
        # Original replies exercise compatibility, not an invented historical recovery.
        source = Path(__file__).resolve().parents[1] / case["local_frozen_context"] / "agent/requests"
        if not source.exists():
            pytest.skip("Private historical World/scope receipt is unavailable in this checkout")
        directory = verify_source(case)  # Existing snapshots remain subject to strict hash checks.
        messages = [json.loads(line).get("message", {}) for line in
                    (directory.parent / "pi-agent-session.jsonl").read_text(encoding="utf8").splitlines()]
        message = next(message for message in messages if message.get("role") == "toolResult"
                       and message.get("toolCallId") == case["tool_call_id"])
        payload = _payload(message)
        expected = "invalid_path" if family == "observation_path" else "identity_or_scope_unavailable"
    seed(tmp_path)
    rows(tmp_path / "requests", "0001.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": case["causal_request"], "tool_call_ids": [case["tool_call_id"]]})
    rows(tmp_path, "session.jsonl", {"message": {"role": "assistant", "content": [{
         "type": "toolCall", "id": case["tool_call_id"], "name": case["tool"], "arguments": case["failed_arguments"]}]}},
         {"message": {"role": "toolResult", "toolCallId": case["tool_call_id"],
         "toolName": case["tool"], "details": payload}})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == expected and item["tool_name"] == case["tool"]
    assert item["next_action"] != "fresh_read_or_validate" and item["likely_failure_layer"] != "unknown"
    assert item["odoo_request_seen"] is None  # Fixture absence does not prove no dispatch.
    if family == "sop_inputs":
        assert item["missing"] == payload["missing"]
        assert item["allowed_inputs"] == payload["allowed_inputs"]
        assert item["required_inputs"] == payload["required_inputs"]
        assert item["sop_id"] == case["failed_arguments"]["sop_id"]


@pytest.mark.parametrize("tag,code", [("reason_code", "connection_timeout"),
    ("reason_code", "permission_denied"), ("error_code", "identity_or_scope_unavailable"),
    ("error_class", "invalid_path"), ("failure", "method_not_supported")])
def test_structured_failure_guidance_cannot_inject_receipt_text(tmp_path, tag, code):
    seed(tmp_path)
    payload = {"success": False, "error": "SECRET-RAW", "detail": "SECRET-DETAIL",
               "next_action": "SECRET-INSTRUCTIONS", "failure_layer": "SECRET-LAYER"}
    payload[tag] = {"code": code, "layer": "SECRET-LAYER", "next_action": "SECRET-INSTRUCTIONS"} if tag == "failure" else code
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "toolName": "mcp_odoo_read_record", "details": payload}})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == code and "SECRET" not in json.dumps(item)
    expected = FAILURE_GUIDANCE.get(code, ("tool_contract", "read_observation_directory"))
    assert (item["likely_failure_layer"], item["next_action"]) == expected
    payload.update(reason_code=["SECRET"], error_code="SECRET-CODE", error_class={"SECRET": True},
                   failure={"code": "SECRET-CODE", "stage": [], "layer": "SECRET", "next_action": "SECRET"},
                   action_status=["sending"], action_id=["SECRET"])
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": payload}})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == "tool_failed" and "SECRET" not in json.dumps(item)
    payload.update(success=True)
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": payload}})
    assert diagnose(tmp_path)["items"] == []


@pytest.mark.parametrize("status,code,stage", [("sending", "action_outcome_unknown", "send"),
    ("needs_reconciliation", "action_verification_failed", "verification"),
    (None, "invoice_delivery_reconciliation_required", "before_send")])
def test_typed_cause_does_not_authorize_uncertain_write_replay(tmp_path, status, code, stage):
    seed(tmp_path)
    payload = {"success": False, "reason_code": "connection_timeout", "action_status": status,
               "failure": {"code": code, "stage": stage, "layer": "SECRET", "next_action": "retry_write"}}
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": payload}})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == code and item["reason_code"] == "connection_timeout"
    assert item["stage"] == stage and item["next_action"] == "reconcile_without_replay"
    assert "SECRET" not in json.dumps(item) and "retry_write" not in json.dumps(item)
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "event": "start",
         "rpc_request_id": "rpc-incomplete", "dispatch_started": True})
    item = diagnose(tmp_path)["items"][0]
    assert item["rpc_incomplete"] is True and item["next_action"] == "reconcile_without_replay"
    rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": "duplicate", "tool_call_ids": ["call-a"]})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == "correlation_conflict" and "reason_code" not in item
    assert item["stage"] == "unknown" and item["next_action"] == "reconcile_without_replay"


def test_read_failure_classification_preserves_incomplete_rpc_and_ledger_guard(tmp_path):
    seed(tmp_path)
    payload = {"success": False, "reason_code": "permission_denied", "next_action": "retry_write",
               "failure": {"code": "permission_denied", "stage": "before_send", "odoo_request_seen": False}}
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": payload}})
    item = diagnose(tmp_path)["items"][0]
    assert item["odoo_request_seen"] is False and item["next_action"] == "check_permissions"
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "event": "start",
         "rpc_request_id": "rpc-incomplete", "dispatch_started": True})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == "correlation_conflict" and item["next_action"] == "inspect_execution_evidence"
    payload.pop("failure")
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": payload}})
    item = diagnose(tmp_path)["items"][0]
    assert item["error_code"] == "permission_denied" and item["likely_failure_layer"] == "odoo_transport"
    assert item["next_action"] == "inspect_execution_evidence"
    payload["action_id"] = "act-unknown"
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a", "details": payload}})
    action = {"action_id": "act-unknown", "run_id": "run", "session_id": "session",
              "identity": IDENTITY, "status": "sending"}
    item = diagnose(tmp_path, [action])["items"][0]
    assert item["next_action"] == "reconcile_without_replay" and item["reason_code"] == "permission_denied"



def test_older_untracked_reconciliation_receipt_is_not_hidden_by_recent_errors(tmp_path):
    seed(tmp_path)
    calls = ["call-a", "call-b", "call-c", "call-d"]
    rows(tmp_path / "requests", "0001.meta.json", {"run_id": "run", "session_id": "session",
         "request_id": "request1", "tool_call_ids": calls})
    rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
         "details": {"success": False, "failure": {"code": "invoice_delivery_reconciliation_required"}}}},
         *[{"message": {"role": "toolResult", "toolCallId": call,
            "details": {"success": False, "reason_code": "query_invalid"}}} for call in calls[1:]])
    items = diagnose(tmp_path)["items"]
    assert len(items) == 3 and items[0]["tool_call_id"] == "call-a"
    assert items[0]["next_action"] == "reconcile_without_replay"



def test_auxiliary_recovery_uses_bound_read_only_arguments_and_authoritative_sop_names(tmp_path):
    from erp_harness.tools.sops import MAX_INPUT_LENGTH, get_sop

    seed(tmp_path)
    def record(name, arguments, payload, actions=()):
        rows(tmp_path, "session.jsonl", {"message": {"role": "assistant", "content": [{
             "type": "toolCall", "id": "call-a", "name": name, "arguments": arguments}]}},
             {"message": {"role": "toolResult", "toolCallId": "call-a", "toolName": name, "details": payload}})
        return diagnose(tmp_path, actions)["items"][0]

    args = {"sop_id": "po_to_receipt", "inputs": {"model": "SECRET-VALUE"}}
    sop = get_sop(**args)
    sop.update(missing=["SECRET-INSTRUCTIONS"], allowed_inputs=["SECRET-INSTRUCTIONS"],
               input_constraints={"type": "SECRET-INSTRUCTIONS"})
    item = record("get_odoo_sop", args, sop)
    assert item["missing"] == ["purchase_order"] and item["unknown"] == ["model"]
    assert item["input_constraints"] == {"type": "string", "max_length": MAX_INPUT_LENGTH}
    assert "SECRET" not in json.dumps(item)

    args = {"observation_ref": "obs-000001-safe", "path": "$.bad", "instance": "default"}
    payload = {"success": False, "reason_code": "observation_path_invalid", "recovery_request": {
        "tool": "read_observation", "arguments": {"observation_ref": args["observation_ref"],
        "path": "$", "limit": 100, "instance": "default", "token": "SECRET-TOKEN"}, "notice": "SECRET-INSTRUCTIONS"}}
    expected = {"tool": "read_observation", "arguments": {
        "observation_ref": args["observation_ref"], "path": "$", "limit": 100, "instance": "default"}}
    item = record("read_observation", args, payload)
    assert item["recovery_request"] == expected and "SECRET" not in json.dumps(item)
    for change in ({"tool": "mcp_odoo_execute_method"}, {"arguments": {"path": "$.bad"}},
                   {"arguments": {**expected["arguments"], "observation_ref": "other-reference"}},
                   {"arguments": {**expected["arguments"], "instance": "other"}}):
        invalid = copy.deepcopy(payload)
        invalid["recovery_request"].update(change)
        assert "recovery_request" not in record("read_observation", args, invalid)
    assert "recovery_request" not in record("read_observation", args, {**payload, "reason_code": "observation_access_denied"})

    for guarded_args, guarded_payload in ((args, payload), ({"sop_id": "po_to_receipt"}, sop)):
        name = "read_observation" if guarded_args is args else "get_odoo_sop"
        rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "event": "start",
             "rpc_request_id": "rpc-incomplete", "dispatch_started": True})
        item = record(name, guarded_args, guarded_payload)
        assert item["next_action"] == "inspect_execution_evidence"
        assert "recovery_request" not in item and "allowed_inputs" not in item
        rows(tmp_path, "odoo-native-requests.jsonl")
        guarded_payload = {**guarded_payload, "action_id": "act-unknown"}
        action = {"action_id": "act-unknown", "run_id": "run", "session_id": "session",
                  "identity": IDENTITY, "status": "sending"}
        item = record(name, guarded_args, guarded_payload, [action])
        assert item["next_action"] == "reconcile_without_replay"
        assert "recovery_request" not in item and "allowed_inputs" not in item
        rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
             "request_id": "duplicate", "tool_call_ids": ["call-a"]})
        item = record(name, guarded_args, guarded_payload)
        assert item["error_code"] == "correlation_conflict"
        assert "recovery_request" not in item and "allowed_inputs" not in item
        rows(tmp_path / "requests", "0002.meta.json", {"run_id": "run", "session_id": "session",
             "request_id": "duplicate", "tool_call_ids": []})



def test_conflicting_call_arguments_or_tool_names_block_auxiliary_recovery(tmp_path):
    from erp_harness.tools.sops import get_sop

    seed(tmp_path)
    part = {"type": "toolCall", "id": "call-a", "name": "get_odoo_sop",
            "arguments": {"sop_id": "po_to_receipt"}}
    payload = get_sop(**part["arguments"])
    result = {"message": {"role": "toolResult", "toolCallId": "call-a", "toolName": "get_odoo_sop", "details": payload}}
    for content, reply in (([part, {**part, "arguments": {"sop_id": "safe_write_review"}}], result),
                           ([part], {"message": {**result["message"], "toolName": "mcp_odoo_execute_method"}})):
        rows(tmp_path, "session.jsonl", {"message": {"role": "assistant", "content": content}}, reply)
        diagnosed = diagnose(tmp_path)
        item = diagnosed["items"][0]
        assert not diagnosed["evidence_complete"] and item["error_code"] == "correlation_conflict"
        assert item["next_action"] == "inspect_execution_evidence" and "allowed_inputs" not in item


def test_runtime_refusal_and_nested_task_failures_keep_their_causes(tmp_path):
    seed(tmp_path)
    payload = {"success": False, "reason_code": "tool_arguments_invalid", "failure": {
        "code": "tool_arguments_invalid", "stage": "before_dispatch", "odoo_request_seen": False}}
    def record(result):
        rows(tmp_path, "session.jsonl", {"message": {"role": "toolResult", "toolCallId": "call-a",
            "toolName": "test", "details": {"structuredContent": result}}})
        return diagnose(tmp_path)["items"][0]
    item = record(payload)
    assert item["stage"] == "before_dispatch" and item["odoo_request_seen"] is False
    assert item["tool_started"] is False and item["next_action"] == "correct_arguments"
    rows(tmp_path, "odoo-native-requests.jsonl", {"event": "start", "tool_call_id": "call-a",
        "rpc_request_id": "conflict", "dispatch_started": True})
    item = record(payload)
    assert item["error_code"] == "correlation_conflict" and item["odoo_request_seen"] is None
    rows(tmp_path, "odoo-native-requests.jsonl")
    failure = {"reason_code": "permission_denied", "failure_layer": "authorization", "next_action": "check_permissions"}
    for result in ({"success": True, "status": "failed", "execution_failure": failure},
                   {"success": True, "failures": {"default": failure}},
                   {"success": True, "status": "succeeded", "result": {"success": True, "failures": {"default": failure}}}):
        item = record(result)
        assert item["error_code"] == "permission_denied" and item["next_action"] == "check_permissions"
