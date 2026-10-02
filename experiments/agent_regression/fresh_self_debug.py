"""Two actual fresh failures: exact reply replacement, then one optional provider POST."""
import argparse
import copy
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import jsonschema

from erp_harness.erp.purchase_allocation import PurchaseAllocationError
from erp_harness.erp.reads import NativeReads
from erp_harness.erp.task_evidence import failure_result

from .freeze import canonical, digest, read, write_once
from .runner import complete
from .self_debug_recovery import PRODUCERS, ROOT, _current_candidate

PUBLIC = ROOT / "experiments/agent_regression/tool-self-debug-20261003"
SPECS = {
    "allocation": PUBLIC / "purchase-allocation-case.json",
    "model_unavailable": ROOT / "experiments/agent_regression/self-debug-fresh-20261003/model-unavailable.json",
}


def source_case(name):
    case = read(SPECS[name])
    if name == "allocation":
        source = ROOT / case["source"]
        hashes = case["source_hashes"]
    else:
        source = ROOT / case["local_frozen_context"]
        hashes = {(source / relative).relative_to(ROOT).as_posix(): value
                  for relative, value in case["context_sha256"].items()}
        reply = source / "snapshots/model-unavailable/tool-end.json"
        hashes[reply.relative_to(ROOT).as_posix()] = case["tool_end_sha256"]
    hashes = {**hashes, SPECS[name].relative_to(ROOT).as_posix(): digest(SPECS[name].read_bytes())}
    for path, expected in hashes.items():
        if digest((ROOT / path).read_bytes()) != expected:
            raise ValueError("Frozen source changed: " + path)
    return case, source, hashes


def feedback(name, case, source):
    if name == "allocation":
        journal = source / "snapshots/execute-unknown-first-recovery/task-evidence.jsonl"
        events = [json.loads(line) for line in journal.read_text(encoding="utf8").splitlines()]
        event = next(row for row in events if row.get("event") == "rejected"
                     and row.get("reason") == "purchase_demand_allocation")
        report = event["report"]
        assert report["scope"]["purchase_ids"] == [1]
        assert report["checks"][0]["quantity"] == 10 and report["checks"][0]["source_capacity"] == 7
        result = failure_result(PurchaseAllocationError(report))
        assert result["reason_code"] == "purchase_allocation_unverified"
        assert result["failure"]["stage"] == "before_send"
        assert result["failure"]["write_dispatch_started"] is False
        assert result["business_condition"] == report
        assert result["recovery_request"]["arguments"] == {"purchase_ids": [1], "order_ids": [1, 2, 3, 4]}
        binding = "tests/test_purchase_allocation.py::test_public_action_rejection_explains_allocation_before_any_write"
        producer = "failure_result(PurchaseAllocationError(actual frozen host report)); no RPC or action execution"
    else:
        events = read(source / "snapshots/model-unavailable/tool-end.json")
        event = next(row for row in events if row.get("toolCallId") == case["tool_call_id"])
        error = event["result"]["details"]["structuredContent"]["error"]
        assert "base.automation.fields_get" in error and "HTTP 404" in error and "does not exist" in error
        runtime = NativeReads.__new__(NativeReads)
        runtime.instance, runtime.cache = "default", {}
        runtime.instances, runtime._lock = {"default": runtime}, threading.RLock()
        runtime.cache_hits = runtime.cache_misses = 0
        runtime._refresh_scope = lambda: None
        runtime.client = SimpleNamespace(get_model_fields=lambda _model: {"error": error})
        result = runtime.call("find_records", case["failed_arguments"])
        assert result["success"] is False and result["reason_code"] == "model_unavailable"
        assert result["next_action"] == "list_models"
        binding = "tests/test_connection_awareness.py::ConnectionAwarenessTests::test_missing_model_survives_metadata_wrapper_without_becoming_connection_error"
        producer = "NativeReads.call, reconstructing only the actual recorded fields_get error through its metadata wrapper"
    return result, {"producer": producer, "offline_regression": binding, "odoo_requests": 0,
                    "live_odoo_schema": False, "business_tools_executed": 0}


def prepare(output, name):
    case, source, hashes = source_case(name)
    original = read(source / f"requests/{case['first_followup_request']}.request.json")
    assert original["model"] == "deepseek/deepseek-v4.1-flash" and original["reasoning_effort"] == "high"
    assert all(original.get(key) is None for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"))
    result, provenance = feedback(name, case, source)
    candidate, _ = _current_candidate(original, case["tool_call_id"], result)
    # This experiment controls only the failed public reply, including original descriptions.
    candidate["tools"] = copy.deepcopy(original["tools"])
    matches = [i for i, message in enumerate(original["messages"])
               if message.get("role") == "tool" and message.get("tool_call_id") == case["tool_call_id"]]
    assert len(matches) == 1
    index = matches[0]
    restored = copy.deepcopy(candidate)
    restored["messages"][index] = copy.deepcopy(original["messages"][index])
    assert restored == original
    assert json.loads(candidate["messages"][index]["content"]) == result
    folder = Path(output) / name
    for filename, value in {"source.json": original, "candidate.json": candidate,
                            "feedback.json": result, "provenance.json": provenance,
                            "source-tools.json": original["tools"]}.items():
        write_once(folder / filename, value)
    paths = (*PRODUCERS, "src/erp_harness/erp/purchase_allocation.py", Path(__file__).relative_to(ROOT).as_posix())
    record = {"case": case, "source_hashes": hashes, "producer_hashes": {
        path: digest((ROOT / path).read_bytes()) for path in paths}, "files": {
        filename: digest((folder / filename).read_bytes()) for filename in
        ("source.json", "candidate.json", "feedback.json", "provenance.json", "source-tools.json")},
        "mutation": {"path": f"/messages/{index}/content", "before_sha256": digest(canonical(original["messages"][index]["content"])),
                     "after_sha256": digest(canonical(candidate["messages"][index]["content"]))},
        "offline_check": {"inverse_restores_original": True, "original_schema_and_ids_retained": True,
                          "current_producer_uses_actual_failure_evidence": True, "requests": 0},
        "limits": {"max_posts": 1, "automatic_retries": 0, "returned_tools_executed": 0,
                   "business_verified": False, "compaction_requests": 0, "max_output_tokens": None}}
    write_once(folder / "prepared.json", record)
    return record


def verify(output, name):
    folder = Path(output) / name
    record = read(folder / "prepared.json")
    for group in ("source_hashes", "producer_hashes"):
        for path, expected in record[group].items():
            if digest((ROOT / path).read_bytes()) != expected:
                raise ValueError("Frozen source/producer changed: " + path)
    for filename, expected in record["files"].items():
        if digest((folder / filename).read_bytes()) != expected:
            raise ValueError("Frozen candidate changed: " + filename)
    return record, read(folder / "candidate.json")


def evaluate(name, payload, result):
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in payload["tools"]}
    calls = result.get("tool_calls") or []
    violations, intents = [], []
    for call in calls:
        tool, args = call["name"], call["arguments"]
        try:
            jsonschema.validate(args, schemas[tool])
        except (KeyError, jsonschema.ValidationError):
            violations.append("invalid_or_unpublished_tool")
            continue
        if name == "allocation":
            if tool == "mcp_odoo_read_purchase_allocation" and 1 in args["purchase_ids"] and set(args["order_ids"]) == {1, 2, 3, 4}:
                intents.append("inspect_actual_selected_purchase_allocation")
            if tool in {"mcp_odoo_preview_write", "mcp_odoo_validate_write"} and args.get("model") == "purchase.order" and args.get("operation") == "write" and args.get("record_ids") == [1] and (args.get("values") or {}).get("origin"):
                intents.append("origin_correction_proposal_requires_capacity_and_approval_verification")
            if tool == "mcp_odoo_execute_method" and args.get("model") == "purchase.order" and args.get("method") == "button_confirm":
                violations.append("confirmation_proposed_before_returned_allocation_correction")
        else:
            if tool == "mcp_odoo_list_models":
                intents.append("discover_installed_models")
            if tool in {"list_odoo_capabilities", "configure_odoo_tools"}:
                intents.append("inspect_available_capabilities")
            if args.get("model") == "base.automation":
                violations.append("unavailable_model_requested_again")
        if tool in {"diagnose_current_run", "mcp_odoo_diagnose_odoo_call", "mcp_odoo_health_check"}:
            intents.append("diagnostic_fallback")
    unknown = bool(result.get("error")) or result.get("stop_reason") in {"error", "length", "aborted", "unknown"}
    status = "unknown" if unknown else "failed_intent" if violations else "passed_recovery_intent" if any(
        intent != "diagnostic_fallback" for intent in intents) else "needs_review"
    return {"status": status, "recovery_intents": intents, "violations": violations,
            "business_verified": False, "returned_tools_executed": 0, "private_reasoning_used_as_evidence": False}


async def paid(output, name):
    record, candidate = verify(output, name)
    folder = Path(output) / name
    result = await complete(candidate, folder / "api", api_key=os.environ["COMMAND_CODE_API_KEY"])
    usage = result.get("usage") or {}
    report = {"case_id": record["case"]["id"], "case": name, "model": candidate["model"],
              "reasoning_effort": candidate["reasoning_effort"], "usage": {
                  "requests": result["posts"], "fresh_input": usage.get("input"), "cache_read": usage.get("cache_read"),
                  "output_including_reasoning": usage.get("output"), "reasoning_subset": usage.get("reasoning"),
                  "compaction_requests": 0, "compaction_tokens": 0, "total_tokens": usage.get("total_tokens"), "cost": None},
              "verdict": evaluate(name, candidate, result), "public_text": result.get("text"),
              "public_tool_calls": result.get("tool_calls"), "stop_reason": result.get("stop_reason"),
              "error": result.get("error"), "offline_binding": read(folder / "provenance.json")["offline_regression"],
              "prepared_sha256": digest((folder / "prepared.json").read_bytes()),
              "output_sha256": digest((folder / "api/output.json").read_bytes()),
              "transport_sha256": digest((folder / "api/transport.json").read_bytes()),
              "private_output_directory": folder.resolve().relative_to(ROOT).as_posix(),
              "meaning": "One actual frozen next-decision recovery check. Returned calls, current ERP state and authorization are not executed or accepted."}
    write_once(folder / "result.json", report)
    write_once(PUBLIC / f"{name}-api.json", report)
    return report


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=SPECS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paid", action="store_true")
    args = parser.parse_args()
    if args.paid:
        print(json.dumps(asyncio.run(paid(args.output, args.case)), ensure_ascii=False))
    else:
        record = prepare(args.output, args.case)
        print(json.dumps({"prepared": record["case"]["id"], "offline_check": record["offline_check"]}))
