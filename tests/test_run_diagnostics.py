import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from erp_harness.erp._odoo_core.odoo_client import OdooClient, READ_CALL_ID
from erp_harness.erp.reads import NativeReads
from erp_harness.tools.run_diagnostics import build_diagnostic_tool, summarize_run


IDENTITY = {"identity_id": "role-a", "credential_scope_sha256": "credential-scope"}


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
    rows(tmp_path, "odoo-native-requests.jsonl", {"tool_call_id": "call-a", "event": "start", "rpc_request_id": "rpc-1"})
    item = diagnose(tmp_path)["items"][0]
    assert item["odoo_request_seen"] is True and item["rpc_incomplete"] is True
    rows(tmp_path, "odoo-native-requests.jsonl",
         {"tool_call_id": "call-a", "event": "start", "rpc_request_id": "rpc-1"},
         {"tool_call_id": "call-b", "event": "end", "rpc_request_id": "rpc-1"})
    assert diagnose(tmp_path)["items"][0]["error_code"] == "correlation_conflict"
    assert diagnose(tmp_path)["items"][0]["odoo_request_seen"] is None


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


def test_rpc_start_end_ids_preserve_attempt_count_and_timeout_unknown(tmp_path):
    path = tmp_path / "rpc.jsonl"
    client = OdooClient.__new__(OdooClient)
    token = READ_CALL_ID.set("call-a")
    try:
        with patch.dict("os.environ", {"ODOO_REQUEST_LOG": str(path)}), patch.object(client, "_json2_call_once", side_effect=TimeoutError):
            try:
                client._json2_call("sale.order", "action_confirm", {})
            except TimeoutError:
                pass
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert [r["event"] for r in events] == ["start", "end"]
        assert events[0]["rpc_request_id"] == events[1]["rpc_request_id"]
        assert events[1]["status"] is None and events[1]["error_type"] == "TimeoutError"
        with patch.dict("os.environ", {"ODOO_REQUEST_LOG": str(path)}):
            evidence = NativeReads.__new__(NativeReads).world_rpc_evidence("call-a")
        assert evidence["refs"][0]["completed_attempts"] == 1
    finally:
        READ_CALL_ID.reset(token)
