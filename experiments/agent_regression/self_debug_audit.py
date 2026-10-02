"""Offline failure-envelope inventory. No model requests or Odoo operations."""
import argparse
import ast
import hashlib
import json
import socket
import ssl
import tempfile
import threading
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from erp_harness.erp._odoo_core.odoo_client import OdooJson2Error
from erp_harness.erp.actions import NativeActions
from erp_harness.erp.capabilities import CAPABILITY_TOOLS, NativeCapabilities
from erp_harness.erp.reads import NATIVE_READ_RESPONSES, NativeReads
from erp_harness.runtime.loop import _error_result
from erp_harness.runtime.messages import ToolCall
from erp_harness.runtime.validation import validate_tool_arguments
from erp_harness.tools.router import native_tool_catalog
from erp_harness.tools.run_diagnostics import _payload, summarize_run

from .bench_recovery import INDEX, verify_source

ROOT = Path(__file__).resolve().parents[2]
TAGS = ("reason_code", "error_code", "error_class")
FAULTS = (
    ("timeout", TimeoutError("offline fault"), "connection_timeout"),
    ("connection_refused", ConnectionRefusedError("offline fault"), "connection_refused"),
    ("network", ConnectionError("offline fault"), "connection_unavailable"),
    ("dns", socket.gaierror("offline fault"), "dns_failed"),
    ("tls", ssl.SSLError("offline fault"), "tls_error"),
    ("authentication", OdooJson2Error("offline fault", status_code=401), "authentication_failed"),
    ("permission", PermissionError("offline fault"), "permission_denied"),
    ("field_acl", ValueError("Field policy denies access: offline fault"), "field_policy_denied"),
    ("rate_limit", OdooJson2Error("offline fault", status_code=429), "rate_limited"),
    ("endpoint", OdooJson2Error("offline fault", status_code=404), "endpoint_not_found"),
    ("database", ValueError("database offline does not exist"), "database_unavailable"),
    ("server", OdooJson2Error("offline fault", status_code=500), "server_error"),
    ("query", ValueError("Invalid domain: offline fault"), "query_invalid"),
    ("record", OdooJson2Error("offline fault", odoo_error={"name": "odoo.exceptions.MissingError"}), "record_unavailable"),
    ("response", ValueError("invalid JSON: offline fault"), "invalid_response"),
    ("response_size", ValueError("response byte limit: offline fault"), "response_too_large"),
)


def reason(payload):
    failure = payload.get("failure")
    return (failure.get("code") if isinstance(failure, dict) else None) or next(
        (payload[key] for key in TAGS if payload.get(key)), None)


def minimal_arguments(schema):
    """Valid transport arguments; tool bodies are replaced before execution."""
    if "anyOf" in schema:
        return minimal_arguments(schema["anyOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        required = [*schema.get("required", []), *schema.get("oneOf", [{}])[0].get("required", [])]
        return {key: minimal_arguments(schema["properties"][key]) for key in required}
    if kind == "array":
        return [minimal_arguments(schema.get("items", {})) for _ in range(schema.get("minItems", 0))]
    return {"string": "fixture", "integer": schema.get("minimum", 1),
            "number": schema.get("minimum", 1), "boolean": False, "null": None}.get(kind)


def diagnosis(message, call_id="offline-call", request_id="offline-request", rpc=()):
    """Explicit fixture scope tests classification, never reconstructs business truth."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "requests").mkdir()
        (root / "requests/0001.meta.json").write_text(json.dumps({
            "run_id": "offline-run", "session_id": "offline-session", "request_id": request_id,
            "tool_call_ids": [call_id]}), encoding="utf-8")
        for name, values in (("session.jsonl", [{"message": message}]),
                             ("odoo-native-requests.jsonl", rpc),
                             ("tool-backends.jsonl", []), ("world-observations.jsonl", [])):
            (root / name).write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
        with patch("erp_harness.tools.run_diagnostics.ActionStore.read_receipts", return_value=[]):
            result = summarize_run(root, root / "session.jsonl", {}, run_id="offline-run", session_id="offline-session")
        assert result.get("success") and result["business_truth"] is False and len(result["items"]) == 1
        return result["items"][0]


def contract_probes():
    """Known failures in real shared wrappers, with all business bodies replaced."""
    probes = []
    reads = NativeReads.__new__(NativeReads)
    reads.instance, reads.instances, reads._lock = "default", {"default": reads}, threading.RLock()
    reads._refresh_scope = lambda: None
    capabilities, actions = NativeCapabilities.__new__(NativeCapabilities), NativeActions.__new__(NativeActions)
    for tool in native_tool_catalog():
        name = tool.name.removeprefix("mcp_odoo_")
        group = "read" if name in NATIVE_READ_RESPONSES else "capability" if name in CAPABILITY_TOOLS else "action"
        runtime = {"read": reads, "capability": capabilities, "action": actions}[group]
        args = validate_tool_arguments(tool, ToolCall(id="offline-call", name=tool.name,
                                                      arguments=minimal_arguments(tool.parameters)))
        for family, error, expected in FAULTS:
            with patch.object(runtime, name, side_effect=error) as body:
                try:
                    payload = runtime.call(name, args)
                    message = {"role": "toolResult", "toolCallId": "offline-call",
                               "details": {"structuredContent": payload}}
                except Exception as exc:  # noqa: BLE001 - reproduce the runtime's exception boundary
                    result = _error_result(str(exc))  # Actual runtime exception envelope.
                    payload = {}
                    message = {"role": "toolResult", "toolCallId": "offline-call",
                               "isError": True, "content": [part.model_dump() for part in result.content]}
                body.assert_called_once()  # Argument preparation must not masquerade as an injected fault.
            item = diagnosis(message)
            probes.append({"tool": tool.name, "group": group, "family": family, "expected_reason": expected,
                "direct_reason": reason(payload), "direct_next_action": payload.get("next_action"),
                "diagnostic_reason": item.get("error_code"), "diagnostic_layer": item.get("likely_failure_layer"),
                "diagnostic_next_action": item.get("next_action"), "source": "offline_shared_exception_probe"})
    return probes


def historical_inventory():
    records, sessions = [], {}
    for case in json.loads(INDEX.read_text(encoding="utf-8")):
        if not case.get("tool") or case["failure_layer"] == "normal_control":
            continue
        directory = verify_source(case)
        session = directory.parent / "pi-agent-session.jsonl"
        if session not in sessions:
            sessions[session] = [json.loads(line).get("message", {}) for line in session.read_text(encoding="utf-8").splitlines()]
        message = next(m for m in sessions[session] if m.get("role") == "toolResult" and m.get("toolCallId") == case["tool_call_id"])
        payload = _payload(message)
        metadata = json.loads((directory / f"{case['causal_request']}.meta.json").read_text(encoding="utf-8"))
        item = diagnosis(message, case["tool_call_id"], metadata["request_id"])
        records.append({"case": case["id"], "tool": case["tool"], "family": case["failure_layer"],
            "causal_request": case["causal_request"], "tool_call_id": case["tool_call_id"],
            "direct_reason": reason(payload), "direct_next_action": payload.get("next_action"),
            "diagnostic_reason": item.get("error_code"), "diagnostic_layer": item.get("likely_failure_layer"),
            "offline_test": case["offline_test"], "api_entry": case["api_entry"],
            "source": case["local_frozen_context"], "source_sha256": case["context_sha256"],
            "scope": "classification replay in explicit fixture scope; original replies unchanged"})
    return records


def source_inventory():
    """Count literal failed-return contracts, not speculative model behavior."""
    records = []
    for name in ("erp/reads.py", "erp/capabilities.py", "erp/actions.py", "erp/task_evidence.py",
                 "tools/sops.py", "tools/dynamic_tools.py", "context/world_tools.py", "app/conversation.py"):
        path = ROOT / "src/erp_harness" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            values = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
            if not isinstance(values.get("success"), ast.Constant) or values["success"].value is not False:
                continue
            records.append({"file": path.relative_to(ROOT).as_posix(), "line": node.lineno,
                "reason_tag": bool(set(values) & {*TAGS, "failure"}),
                "next_action": "next_action" in values or "failure" in values,
                "note": "Literal return only; inherited or subsequently added fields require source review."})
    return records


def run():
    probes = contract_probes()
    actual = historical_inventory()
    missing = sorted({p["tool"] for p in probes if not p["direct_reason"]})
    lost = sorted({p["tool"] for p in probes if p["direct_reason"]
                   and p["diagnostic_reason"] != p["expected_reason"]})
    return {"date": "2026-10-03", "native_tool_count": len(native_tool_catalog()),
        "groups": dict(Counter(row["group"] for row in probes[::len(FAULTS)])),
        "fault_families": [family for family, _, _ in FAULTS], "probe_count": len(probes),
        "status": "coverage_gaps" if missing or lost else "passed_offline_contracts",
        "direct_missing_reason_tools": missing,
        "diagnostic_lost_reason_tools": lost,
        "historical_failed_tool_count": len({row["tool"] for row in actual}),
        "historical_failed_nodes": len(actual),
        "historical_by_tool": dict(Counter(row["tool"] for row in actual)),
        "historical_with_reason": sum(bool(row["direct_reason"]) for row in actual),
        "historical_with_next_action": sum(bool(row["direct_next_action"]) for row in actual),
        "probes": probes, "historical": actual, "literal_failure_returns": source_inventory(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
            ("src/erp_harness/tools/run_diagnostics.py", "src/erp_harness/erp/reads.py",
             "src/erp_harness/erp/capabilities.py", "src/erp_harness/erp/actions.py",
             "src/erp_harness/erp/read_failures.py", "src/erp_harness/erp/task_evidence.py",
             "src/erp_harness/erp/_odoo_core/tool_helpers.py", "src/erp_harness/tools/router.py",
             "src/erp_harness/tools/native_tool_catalog.json", "src/erp_harness/runtime/loop.py",
             "src/erp_harness/runtime/validation.py",
             "experiments/agent_regression/bench_recovery_cases.json",
             "experiments/agent_regression/self_debug_audit.py", "tests/test_self_debug_audit.py")},
        "usage": {"requests": 0, "fresh_input": 0, "cache_read": 0, "output_including_reasoning": 0,
                  "reasoning_subset": 0, "compaction": 0, "total_tokens": 0, "cost": 0},
        "odoo_calls": 0, "business_writes": 0,
        "limits": ["Injected fallback failures measure envelope coverage, not real incident frequencies or model recovery.",
                   "Historical replies predate current repairs; replay does not assign historical runs a new business outcome.",
                   "Conversation and auxiliary literal returns are source evidence, not complete fault coverage."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("native_tool_count", "groups", "historical_failed_tool_count",
        "historical_failed_nodes", "historical_by_tool")}, ensure_ascii=False))
    print("Direct missing reason:", len(result["direct_missing_reason_tools"]))
    print("Known reason lost by self-debug:", len(result["diagnostic_lost_reason_tools"]))
