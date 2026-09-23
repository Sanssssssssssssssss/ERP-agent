"""Trace inspection stays scoped, lazy and read-only across the real RPC boundary."""
from __future__ import annotations

import copy
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from erp_harness.app.host import Workbench
from erp_harness.app import trace_inspector
from erp_harness.erp.store import ActionStore
from erp_harness.runtime.session_events import AutoRetryEndEvent, AutoRetryStartEvent, CompactionEndEvent, CompactionStartEvent


@pytest.fixture
def trace(tmp_path):
    host = Workbench(tmp_path, repo=Path.cwd())
    session = host.create_session("Trace fixture")
    sid, bid, rid = session["id"], "b_trace", "r_trace"
    host.store.data["businesses"][bid] = {
        "id": bid, "session_id": sid, "type": "sale_invoice", "title": "确认 S01499",
        "goal": "只确认订单", "completion_target": "confirmed", "status": "completed",
    }
    run = {
        "id": rid, "session_id": sid, "business_id": bid, "status": "completed",
        "started_at": "2026-09-23T00:00:00Z", "ended_at": "2026-09-23T00:01:00Z",
        "elapsed_seconds": 60, "model_rounds": 1, "tool_count": 2,
        "summary": "已确认", "usage": {},
        "rounds": [{"index": 1, "round_id": "round_one", "request_id": "req_one",
                    "status": "completed", "text": "公开输出", "stop_reason": "tool_use",
                    "elapsed_seconds": 1.5, "usage": {"input": 10, "cache_read": 5,
                    "output": 7, "reasoning": 3, "total": 22}, "tool_ids": ["t1", "t2"]}],
        "tools": [{"id": "t1", "name": "search", "round": 1, "status": "error",
                   "arguments": {"model": "sale.order", "query": "S01499", "values": {"memo": "large" * 1000}},
                   "result": {"error": "record_not_found", "password": "secret-result"},
                   "started_at": "2026-09-23T00:00:01Z", "ended_at": "2026-09-23T00:00:02Z", "elapsed_seconds": 1},
                  {"id": "t2", "name": "search", "round": 1, "status": "completed",
                   "arguments": {"model": "sale.order", "query": "S01499", "values": {"memo": "large" * 1000}},
                   "result": {"records": [1499]}}],
        "events": [{"type": "tool_end", "at": "2026-09-23T00:00:02Z", "tool_call_id": "t1",
                    "is_error": True, "result": {"error": "record_not_found", "large": "x" * 10_000}},
                   {"type": "tool_progress", "at": "2026-09-23T00:00:03Z", "tool_call_id": "t2", "text": "读取中"}],
    }
    host.store.data["runs"][rid] = run
    directory = tmp_path / "runs" / rid
    (directory / "requests").mkdir(parents=True)
    (directory / "instruction.txt").write_text("仅确认 S01499", encoding="utf-8")
    yield host, run, directory
    host.close()


def write_request(directory, stem="0001", body=None, meta=None, output=None):
    values = {"request": body or {"messages": [{"role": "user", "content": "确认 S01499"}]},
              "response": {"status": 200, "request_ids": {"x-request-id": "provider-one"}}}
    if meta is not None:
        values["meta"] = meta
    if output is not None:
        values["output"] = output
    for suffix, value in values.items():
        (directory / "requests" / f"{stem}.{suffix}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def rpc(trace, kind=None, id=None, **overrides):
    host, run, _ = trace
    params = {"session_id": run["session_id"], "business_id": run["business_id"], "run_id": run["id"]}
    if kind:
        params.update(kind=kind, id=id)
    else:
        params["summary_only"] = True
    params.update(overrides)
    return host.call("get_trace_detail" if kind else "get_trace", params)


def register_action(trace):
    _, run, directory = trace
    store = ActionStore(directory / "odoo-actions.sqlite3")
    try:
        action = store.register(action_key="a1", kind="write", payload={"model": "sale.order", "record_ids": [1499], "values": {"name": "S01499"}},
                                identity={"username": "fixture", "password": "ledger-secret"}, prestate={"state": "draft"},
                                file_digests={}, policy_digest="fixture", approval_source="desktop", resource_key="sale:1499",
                                run_id=run["id"], session_id=run["session_id"], expires_at=4_000_000_000, approved=True)
        store.finish(action["action_id"], "verified", result={"id": 1499}, verification={"state": "sale"})
    finally:
        store.close()
    run["tools"][1]["action_id"] = action["action_id"]
    return action["action_id"]


def test_summary_is_lazy_small_and_does_not_mutate(trace):
    host, _, directory = trace
    write_request(directory, body={"messages": [{"role": "user", "content": "x" * 100_000}]})
    before = copy.deepcopy(host.store.data)
    with patch.object(trace_inspector, "_read_object", side_effect=AssertionError("summary opened a body")):
        result = rpc(trace)
    assert host.store.data == before
    assert result["business"]["goal"] == "只确认订单"
    assert "text" not in result["rounds"][0]
    assert not ({"arguments", "result"} & result["tools"][0].keys())
    assert "S01499" in result["tools"][0]["search_text"]
    assert result["tools"][0]["error"] == "record_not_found"
    assert "result" not in result["events"][0]
    assert result["requests"][0]["status"] == "recorded"
    assert result["warnings"] and not (directory / "odoo-actions.sqlite3").exists()
    assert len(json.dumps(result)) < 6000


def test_legacy_payload_remains_available(trace):
    result = rpc(trace, summary_only=False)
    assert result["rounds"][0]["text"] == "公开输出"
    assert result["tools"][0]["arguments"]["query"] == "S01499"
    assert result["tools"][0]["result"]["password"] == "<redacted>"
    assert "summary_only" not in result


def test_actual_metadata_scope_and_stat_cache_invalidation(trace):
    _, run, directory = trace
    meta = {"request_id": "req_one", "run_id": run["id"], "session_id": run["session_id"],
            "round_id": "round_one", "request_kind": "normal", "status": "running", "started_at": "2026-09-23T00:00:00Z"}
    write_request(directory, meta=meta)
    first = rpc(trace)["requests"][0]
    assert first["association"] == "linked" and first["round"] == 1
    meta.update(status="completed", finished_at="2026-09-23T00:00:01Z", usage={"input": 10, "total_tokens": 18, "reasoning": 3})
    write_request(directory, meta=meta)
    second = rpc(trace)["requests"][0]
    assert second["status"] == "completed" and second["ended_at"] == meta["finished_at"]
    assert second["usage"] == {"input": 10, "cache_read": None, "output": None, "reasoning": 3, "total": 18}


def test_legacy_request_never_links_by_position_or_http_success(trace):
    _, _, directory = trace
    write_request(directory)
    summary = rpc(trace)["requests"][0]
    assert summary["association"] == "unlinked" and summary["status"] == "recorded"
    assert not ({"round", "started_at", "ended_at", "duration_ms"} & summary.keys())
    detail = rpc(trace, "request", "0001")["data"]
    assert detail["response"]["status"] == 200
    assert detail["response"]["output"] is None
    assert detail["association"] == "unlinked"


def test_conflicting_explicit_ids_do_not_select_a_round(trace):
    _, run, directory = trace
    run["rounds"].append({"index": 2, "round_id": "round_two", "request_id": "req_two"})
    write_request(directory, meta={"request_id": "req_two", "round_id": "round_one"})
    assert rpc(trace)["requests"][0]["association"] == "unlinked"
    assert rpc(trace, "request", "0001")["data"]["response"]["output"] is None


def test_round_detail_keeps_failed_placeholder_usage_unknown(trace):
    _, run, directory = trace
    run["rounds"][0].update(status="error", stop_reason="error", usage={"input": 0, "output": 0, "reasoning": 0, "total": 0})
    write_request(directory, meta={"request_id": "req_one", "round_id": "round_one"})
    assert rpc(trace, "round", "1")["data"]["usage"]["total"] is None
    assert rpc(trace, "request", "0001")["data"]["response"]["output"]["usage"]["total"] is None
    assert run["rounds"][0]["usage"]["total"] == 0


def private_request():
    return {"model": "model-test", "max_tokens": 8192, "reasoning_effort": "high", "api_key": "api-secret",
            "messages": [{"role": "system", "content": "你是业务助手"},
                         {"role": "assistant", "content": [{"type": "text", "text": "公开"}, {"type": "thinking", "thinking": "private-chain"}], "reasoning_content": "private-reasoning"},
                         {"role": "tool", "content": json.dumps({"password": "inner-secret", "reasoning_content": "inner-reasoning", "total_tokens": 15, "ok": True})}],
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]}


def assert_redacted(result):
    encoded = json.dumps(result, ensure_ascii=False)
    for private in ("api-secret", "inner-secret", "inner-reasoning", "private-chain", "private-reasoning", "response-private"):
        assert private not in encoded
    assert result["data"]["input"]["max_tokens"] == 8192
    assert result["data"]["input"]["reasoning_effort"] == "high"
    assert json.loads(result["data"]["input"]["messages"][2]["content"])["total_tokens"] == 15
    assert result["redaction"]["hidden_chars"] > 0


def test_request_redaction_metrics_and_context_stats(trace):
    write_request(trace[2], body=private_request(), output={"text": "公开回复", "reasoning_content": "response-private", "usage": {"reasoning": 5, "total_tokens": 28}})
    result = rpc(trace, "request", "0001")
    assert_redacted(result)
    assert result["data"]["response"]["output"]["usage"]["reasoning"] == 5
    stats = result["data"]["content_stats"]
    assert stats["unit"] == "characters" and stats["tools_schema_chars"] > 0
    assert set(stats["messages_by_role"]) == {"system", "assistant", "tool"}
    assert stats["hidden_chars"] > 0 and "不是 token" in stats["method"]


def test_context_comparison_uses_exact_sanitized_prefix_and_not_schema(trace):
    directory = trace[2]
    initial = {"messages": [{"role": "system", "content": "规则"}, {"role": "user", "content": "旧指令"}], "tools": []}
    current = {"messages": [{"role": "system", "content": "规则"}, {"role": "user", "content": "新指令"}, {"role": "tool", "content": "结果"}], "tools": ["big schema" * 1000]}
    write_request(directory, "0001", initial)
    write_request(directory, "0002", current)
    comparison = rpc(trace, "request", "0002")["data"]["comparison"]
    assert comparison["unchanged_messages"] == 1
    assert comparison["added_messages"] == 2 and comparison["removed_messages"] == 1
    assert comparison["added_chars"] == sum(len(trace_inspector._json(m)) for m in current["messages"][1:])
    assert comparison["unit"] == "characters" and "非 token" in comparison["method"]


@pytest.mark.parametrize("params", [{"session_id": "s_other"}, {"business_id": "b_other"}, {"run_id": "r_other"}])
def test_scope_rejected(trace, params):
    with pytest.raises(KeyError):
        rpc(trace, "request", "0001", **params)


def test_bad_path_meta_scope_and_request_shape(trace):
    directory = trace[2]
    with pytest.raises(ValueError):
        rpc(trace, "request", "../0001")
    write_request(directory, meta={"run_id": "r_foreign"})
    assert rpc(trace)["requests"][0]["status"] == "unavailable"
    with pytest.raises(ValueError, match="outside this run"):
        rpc(trace, "request", "0001")
    write_request(directory, body={"messages": None}, meta={})
    assert rpc(trace, "request", "0001")["data"]["content_stats"]["messages_by_role"] == {}


def test_detail_readback_and_original_arguments_read_only(trace):
    host, _, directory = trace
    action = register_action(trace)
    before, ledger = copy.deepcopy(host.store.data), (directory / "odoo-actions.sqlite3").read_bytes()
    tool = rpc(trace, "tool", "t2")["data"]
    assert tool["arguments"]["query"] == "S01499"
    assert tool["normalized_arguments"]["record_ids"] == [1499]
    assert tool["action"]["verification"] == {"state": "sale"}
    assert tool["action"]["identity"]["password"] == "<redacted>"
    assert tool["progress"][0]["text"] == "读取中"
    detail = rpc(trace, "action", action)["data"]
    assert detail["tool_calls"][0]["tool_id"] == "t2"
    assert detail["status"] == "verified"
    assert host.store.data == before and (directory / "odoo-actions.sqlite3").read_bytes() == ledger
    assert rpc(trace)["actions"][0]["started_at"] is None


def test_broken_ledger_does_not_claim_no_writes_or_repair_schema(trace):
    _, run, directory = trace
    run["tools"][0]["action_id"] = "a_missing"
    path = directory / "odoo-actions.sqlite3"
    database = sqlite3.connect(path)
    database.execute("CREATE TABLE unrelated (id INTEGER)")
    database.close()
    before = path.read_bytes()
    assert rpc(trace)["warnings"]
    assert rpc(trace, "action", "a_missing")["data"]["status"] == "unknown"
    assert path.read_bytes() == before


def test_diagnostics_do_not_infer_root_cause_or_zero_usage(trace):
    result = rpc(trace)
    diagnostics = result["diagnostics"]
    assert diagnostics["first_observed_error"]["id"] == "0"
    assert "不等于根因" in diagnostics["first_observed_error"]["notice"]
    assert diagnostics["repeated_tools"] == [{"name": "search", "tool_ids": ["t1", "t2"], "count": 2}]
    assert diagnostics["slow_tools"] == [{"kind": "tool", "id": "t1", "value": 1}]
    assert diagnostics["approval_wait"]["known_seconds"] is None
    assert diagnostics["approval_wait"]["execution_seconds"] is None
    assert rpc(trace, "round", "1")["data"]["stop_reason"] == "tool_use"
    assert rpc(trace, "run", "r_trace")["data"]["instruction"] == "仅确认 S01499"


def test_approval_wait_merges_overlap_and_missing_stays_unknown(trace):
    host, run, _ = trace
    run["events"] += [{"type": "approval_required", "action_id": "a", "at": "2026-09-23T00:00:10Z"},
                      {"type": "approval_required", "action_id": "b", "at": "2026-09-23T00:00:20Z"}]
    host.store.data["approvals"] = {key: {"run_id": run["id"], "action_id": key, "decided_at": stamp} for key, stamp in
                                    (("a", "2026-09-23T00:00:30Z"), ("b", "2026-09-23T00:00:40Z"))}
    wait = rpc(trace)["diagnostics"]["approval_wait"]
    assert wait["known_seconds"] == 30 and wait["execution_seconds"] == 30 and wait["complete"]
    del host.store.data["approvals"]["b"]["decided_at"]
    wait = rpc(trace)["diagnostics"]["approval_wait"]
    assert wait["known_seconds"] == 20 and wait["execution_seconds"] is None and not wait["complete"]


def test_true_stdio_preserves_metrics_and_removes_nested_secrets(trace):
    host, run, directory = trace
    write_request(directory, body=private_request())
    host.store.save()
    host.close()
    request = {"id": "detail", "method": "get_trace_detail", "params": {
        "session_id": run["session_id"], "business_id": run["business_id"], "run_id": run["id"], "kind": "request", "id": "0001"}}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-m", "erp_harness.app.host", "--data-dir", str(host.store.root), "--repo", str(Path.cwd())],
                            input=json.dumps(request) + "\n", capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    response = next(json.loads(line) for line in result.stdout.splitlines() if json.loads(line).get("id") == "detail")
    assert "error" not in response, response
    assert_redacted(response["result"])


def test_worker_retry_compaction_and_receipt_warning_remain_in_trace(trace):
    host, run, directory = trace
    run["events"] = []
    events = [
        AutoRetryStartEvent(attempt=1, max_attempts=3, delay_ms=1000, error_message="busy" * 200).model_dump(mode="json", by_alias=True),
        AutoRetryEndEvent(attempt=1, success=True).model_dump(mode="json", by_alias=True),
        CompactionStartEvent(reason="threshold").model_dump(mode="json", by_alias=True),
        CompactionEndEvent(reason="threshold", will_retry=True, result={"summary": "must-not-copy" * 1000}).model_dump(mode="json", by_alias=True),
        {"type": "receipt_warning", "file": "0001.output.json", "error_type": "OSError"},
    ]
    process = SimpleNamespace(stdout=io.StringIO("\n".join(json.dumps(row) for row in events)), stderr=io.StringIO(), wait=lambda: 0, poll=lambda: 0)
    host._consume_worker(run["id"], process, directory / "missing-usage.json")
    result = rpc(trace)
    assert [event["type"] for event in result["events"]] == [event["type"] for event in events]
    assert result["events"][0]["attempt"] == 1 and result["events"][0]["delay_ms"] == 1000
    assert result["events"][1]["success"] is True
    assert result["events"][3]["will_retry"] is True
    assert len(rpc(trace, "event", "0")["data"]["error"]) == 400
    assert "result" not in rpc(trace, "event", "3")["data"]
    assert "must-not-copy" not in json.dumps(result)
    assert result["events"][4]["file"] == "0001.output.json"
    assert result["events"][4]["error_type"] == "OSError"
    assert "请求日志 0001.output.json 留档失败，证据不完整。" in result["warnings"]
    assert run["status"] == "completed" and not run.get("error")
