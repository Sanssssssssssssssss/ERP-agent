from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.report import (
    _world_receipts,
    load_entries,
    main,
    report_path,
    report_trial,
    tool_failed,
    write_index,
)
from integration.trial_summary import _failure, _redact
from odoo_runtime.dynamic_tools import tool_contract_sha256


class ReportingTest(unittest.TestCase):
    def test_recovered_intermediate_error_is_not_a_terminal_failure(self) -> None:
        failure = _failure(
            [
                {"status": "error", "error": "Invalid SOP inputs"},
                {"role": "assistant", "stopReason": "stop"},
            ],
            {},
        )
        self.assertIsNone(failure["layer"])
        self.assertIsNone(failure["fingerprint"])

    def test_stale_generation_target_is_not_counted_as_world_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory)
            (trial / "agent").mkdir()
            observation = {
                "type": "world_observation",
                "receipt_id": "obs-1",
                "call_id": "call-1",
                "identity": {"identity_id": "identity-1"},
                "outcome": {"success": True},
                "merge": {"status": "stale_generation"},
                "targets": [{
                    "model": "res.company", "records": [{"id": 1}], "relations": [],
                }],
            }
            (trial / "agent/world-observations.jsonl").write_text(
                json.dumps(observation) + "\n", encoding="utf-8"
            )
            declared = {
                "observations": 1,
                "successful_observations": 1,
                "failed_observations": 0,
                "records": 0,
                "relations": 0,
                "projection_calls": 0,
                "projected_messages": 0,
                "projection_original_bytes": 0,
                "projection_bytes": 0,
            }
            computed, integrity = _world_receipts(trial, declared, {"call-1"})
            self.assertEqual(computed["records"], 0)
            self.assertTrue(integrity["valid"])

    def test_redaction_preserves_json_tool_results_and_business_errors(self):
        for success in (False, True):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as directory:
                payload = {
                    "success": success,
                    "error": "api_key:missing",
                    "result": [{"api_key": "private-value", "text": '中文 "quoted" \\ path'}],
                    "nested": json.dumps({"password": "nested-private", "success": False}),
                }
                row = {
                    "type": "message", "id": "tool", "timestamp": 1.0,
                    "message": {"role": "toolResult", "toolCallId": "call1",
                                "toolName": "mcp_odoo_generate_json2_payload", "isError": False,
                                "content": [{"type": "text", "text": json.dumps(payload)}]},
                }
                source = Path(directory) / "session.jsonl"
                source.write_text(json.dumps(row), encoding="utf-8")
                original = source.read_bytes()
                message = load_entries(source)[0].message
                redacted = json.loads(message.content[0].text)
                self.assertEqual(tool_failed(message), not success)
                self.assertIs(redacted["success"], success)
                self.assertEqual(redacted["error"], "[REDACTED]")
                self.assertEqual(redacted["result"][0]["api_key"], "[REDACTED]")
                self.assertEqual(redacted["result"][0]["text"], payload["result"][0]["text"])
                self.assertEqual(json.loads(redacted["nested"]),
                                 {"password": "[REDACTED]", "success": False})
                self.assertEqual(_redact(message.content[0].text), message.content[0].text)
                self.assertEqual(source.read_bytes(), original)

    def test_setup_only_failure_is_visible_without_a_fake_model_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "job" / "setup-failure"
            (trial / "agent").mkdir(parents=True)
            (trial / "config.json").write_text('{"agent":{"kwargs":{"read_backend":"mcp"}}}')
            (trial / "result.json").write_text('{"agent_execution":null,"exception_info":{"exception_type":"RuntimeError"}}')
            output = root / "report"
            page = report_path(trial, output).read_text(encoding="utf-8")
            errors = json.loads((output / "report_errors.json").read_text())
            self.assertEqual(errors[0]["model_http_request_records"], 0)
            self.assertIn("HTTP 请求记录：0", page)
            self.assertIn("RuntimeError", page)
            self.assertFalse((output / trial.name / "trial_summary.json").exists())
            (trial / "result.json").write_text("{")
            report_path(trial, output)
            damaged = json.loads((output / "report_errors.json").read_text())[0]
            self.assertEqual(damaged["receipt_errors"], {"result": "JSONDecodeError"})
            self.assertIsNone(damaged["model_http_request_records"])

    def test_local_turn_guard_is_visible_but_not_a_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "trial"
            source = trial / "agent/pi-agent-session.jsonl"
            source.parent.mkdir(parents=True)
            rows = [
                {"type": "session_info", "id": "s", "timestamp": 1.0},
                {
                    "type": "message",
                    "id": "response",
                    "parent_id": "s",
                    "timestamp": 2.0,
                    "message": {
                        "role": "assistant",
                        "provider": "test",
                        "model": "test",
                        "content": [],
                        "stopReason": "toolUse",
                        "usage": {"input": 10, "output": 2, "totalTokens": 12},
                    },
                },
                {
                    "type": "message",
                    "id": "guard",
                    "parent_id": "response",
                    "timestamp": 3.0,
                    "message": {
                        "role": "assistant",
                        "model": "test",
                        "content": [],
                        "stopReason": "error",
                        "errorMessage": "Agent stopped after max_turns=1",
                    },
                },
            ]
            source.write_text("\n".join(json.dumps(row) for row in rows))
            (trial / "agent/pi-agent-odoo-mcp.jsonl").write_text(
                "non-json runner preamble\n"
                + json.dumps(
                    {
                        "type": "run_metadata",
                        "commit_sha": "fixture-commit",
                    }
                )
                + "\n"
            )
            original = source.read_bytes()
            output = Path(directory) / "report"
            summary = report_trial(trial, output)
            self.assertEqual(summary["agent_termination"]["kind"], "TURN_LIMIT")
            self.assertEqual(summary["actions"]["model_calls"], 1)
            self.assertEqual(summary["actions"]["assistant_entries"], 2)
            self.assertEqual(summary["usage"]["unreported_error_calls"], 0)
            self.assertEqual(summary["identity"]["provider"], "test")
            self.assertEqual(summary["identity"]["commit_sha"], "fixture-commit")
            self.assertTrue(summary["receipts"]["world_integrity"]["valid"])
            self.assertEqual(len(json.loads((output / "requests.json").read_text())), 1)
            self.assertIn(
                "Agent stopped after max_turns=1",
                (output / "session.html").read_text(encoding="utf-8"),
            )
            self.assertEqual(source.read_bytes(), original)

            (trial / "agent/tool-backends.jsonl").write_text(
                json.dumps({"event": "start", "backend": "native", "tool_call_id": "call1", "tool": "mcp_odoo_read_record"}) + "\n"
                + json.dumps({"event": "end", "backend": "native", "tool_call_id": "call1", "elapsed_seconds": 0.01,
                              "native_telemetry": {"cache_hits": 2, "cache_misses": 1, "n_plus_one": [], "rate_limits": {"mode": "off"}}}) + "\n"
                + json.dumps({"event": "start", "backend": "native", "tool_call_id": "action1", "tool": "mcp_odoo_execute_method"}) + "\n"
                + json.dumps({"event": "end", "backend": "native", "tool_call_id": "action1", "elapsed_seconds": 0.01}) + "\n"
            )
            (trial / "agent/odoo-native-requests.jsonl").write_text(
                json.dumps({"backend": "native", "model": "res.partner", "method": "read", "error_type": None}) + "\n"
            )
            observation = {
                "type": "world_observation", "receipt_id": "obs-1", "call_id": "call1",
                "identity": {"identity_id": "identity-1"}, "outcome": {"success": True},
                "targets": [{"model": "res.partner", "records": [{"id": 1}], "relations": []}],
            }
            projection = {
                "type": "world_projection", "compacted_messages": 1,
                "compacted_call_ids": ["call1"],
                "original_bytes": 100, "projected_bytes": 40,
            }
            (trial / "agent/world-observations.jsonl").write_text(json.dumps(observation) + "\n")
            (trial / "agent/world-projections.jsonl").write_text(json.dumps(projection) + "\n")
            (trial / "agent/world-summary.json").write_text(json.dumps({
                "observations": 1, "successful_observations": 1, "failed_observations": 0,
                "records": 1, "relations": 0, "projection_calls": 1,
                "projected_messages": 1, "projection_original_bytes": 100,
                "projection_bytes": 40, "healthy": True,
            }))
            ledger_path = trial / "agent/odoo-actions.sqlite3"
            database = sqlite3.connect(ledger_path)
            try:
                database.execute(
                    "CREATE TABLE action_ledger (action_id TEXT, kind TEXT, "
                    "status TEXT, approval_source TEXT, created_at REAL)"
                )
                database.execute(
                    "INSERT INTO action_ledger VALUES (?, ?, ?, ?, ?)",
                    ("action1", "method", "verified", "bench_auto_disposable", 1.0),
                )
                database.commit()
            finally:
                database.close()
            ledger = ledger_path.read_bytes()
            (trial / "agent/action-ledger-summary.json").write_text(json.dumps({
                "database": "odoo-actions.sqlite3",
                "sha256": hashlib.sha256(ledger).hexdigest(),
                "actions": 1,
                "status_counts": {"verified": 1},
                "receipts": [{
                    "action_id": "action1", "kind": "method", "status": "verified",
                    "approval_source": "bench_auto_disposable",
                }],
            }))
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "native", "action_backend": "native",
            }}}))
            (trial / "agent/snapshot-receipt.json").write_text(json.dumps({"status": "verified", "snapshot_sha256": "fixture"}))
            reports = Path(directory) / "routed-reports"
            routed = report_trial(trial, reports / "native")
            self.assertEqual(routed["actions"]["mcp_calls"], 0)
            self.assertEqual(routed["actions"]["native_read_calls"], 1)
            self.assertEqual(routed["actions"]["native_action_calls"], 1)
            self.assertEqual(
                routed["actions"]["action_backend_closure"]["execute_method"],
                {"native": 1},
            )
            self.assertEqual(routed["actions"]["action_backend_mismatches"], [])
            self.assertEqual(routed["actions"]["odoo_json2_attempts"], 1)
            self.assertEqual(routed["actions"]["native_cache_hits"], 2)
            self.assertEqual(routed["actions"]["world_observations"], 1)
            self.assertEqual(routed["actions"]["world_projected_messages"], 1)
            self.assertTrue(routed["receipts"]["world_integrity"]["valid"])
            self.assertTrue(routed["receipts"]["action_ledger_integrity"]["valid"])
            self.assertEqual(routed["actions"]["action_ledger_status_counts"], {"verified": 1})
            self.assertEqual(routed["identity"]["read_backend"], "native")
            self.assertEqual(routed["identity"]["action_backend"], "native")
            self.assertEqual(routed["identity"]["runtime_mode"], "mcp")
            self.assertEqual(routed["identity"]["world_mode"], "off")
            self.assertFalse(routed["receipts"]["native_runtime_integrity"]["required"])
            self.assertTrue(routed["receipts"]["native_runtime_integrity"]["valid"])
            self.assertIn("world_observations", routed["receipts"])
            self.assertEqual(routed["receipts"]["snapshot"]["snapshot_sha256"], "fixture")
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "runtime_mode": "native", "read_backend": "native",
                "action_backend": "native", "capability_backend": "native",
            }}}))
            native_receipt = {
                "status": "verified",
                "installed_or_importable": {
                    "mcp": False,
                    "mcp_types": False,
                    "odoo_mcp": False,
                },
                "installed_mcp_distributions": [],
                "mcp_process": False,
                "mcp_port_8000_open": False,
                "mcp_pid_file": False,
            }
            (trial / "agent/native-runtime-receipt.json").write_text(
                json.dumps(native_receipt)
            )
            native = report_trial(trial, reports / "native-runtime")
            self.assertTrue(native["receipts"]["native_runtime_integrity"]["required"])
            self.assertTrue(native["receipts"]["native_runtime_integrity"]["valid"])
            native_receipt["status"] = "failed"
            (trial / "agent/native-runtime-receipt.json").write_text(
                json.dumps(native_receipt)
            )
            rejected_native = report_trial(trial, reports / "rejected-native-runtime")
            self.assertFalse(
                rejected_native["receipts"]["native_runtime_integrity"]["valid"]
            )
            self.assertIsNot(rejected_native["agent_termination"]["natural_end"], True)
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "native", "action_backend": "native",
            }}}))
            blocked_ledger = json.loads(
                (trial / "agent/action-ledger-summary.json").read_text()
            )
            blocked_ledger["status_counts"] = {"needs_reconciliation": 1}
            (trial / "agent/action-ledger-summary.json").write_text(
                json.dumps(blocked_ledger)
            )
            blocked_action = report_trial(trial, reports / "blocked-action")
            self.assertIsNot(blocked_action["agent_termination"]["natural_end"], True)
            blocked_ledger["status_counts"] = {"verified": 1}
            (trial / "agent/action-ledger-summary.json").write_text(
                json.dumps(blocked_ledger)
            )
            bad_summary = json.loads((trial / "agent/world-summary.json").read_text())
            bad_summary["observations"] = 99
            (trial / "agent/world-summary.json").write_text(json.dumps(bad_summary))
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "native", "world_mode": "record",
            }}}))
            damaged_world = report_trial(trial, reports / "damaged-world")
            self.assertEqual(damaged_world["actions"]["world_observations"], 1)
            self.assertFalse(damaged_world["receipts"]["world_integrity"]["valid"])
            self.assertEqual(
                damaged_world["receipts"]["world_integrity"]["mismatches"]["observations"]["computed"], 1
            )
            bad_summary["observations"] = 1
            (trial / "agent/world-summary.json").write_text(json.dumps(bad_summary))
            broken_observation = {**observation, "call_id": "not-dispatched"}
            (trial / "agent/world-observations.jsonl").write_text(json.dumps(broken_observation) + "\n")
            unclosed_world = report_trial(trial, reports / "unclosed-world")
            self.assertEqual(unclosed_world["receipts"]["world_integrity"]["missing_dispatch_call_ids"], ["call1"])
            self.assertEqual(unclosed_world["receipts"]["world_integrity"]["orphan_observation_call_ids"], ["not-dispatched"])
            self.assertIsNot(unclosed_world["agent_termination"]["natural_end"], True)
            (trial / "agent/world-observations.jsonl").write_text(json.dumps(observation) + "\n")
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {"read_backend": "mcp"}}}))
            report_trial(trial, reports / "mcp")
            page = write_index(reports).read_text(encoding="utf-8")
            self.assertIn("读取：原生", page)
            self.assertIn("读取：MCP", page)
            self.assertIn("JSON-2 尝试", page)
            # Wrong completion IDs must not cancel out a still-running call.
            with (trial / "agent/tool-backends.jsonl").open("a") as stream:
                stream.write(json.dumps({"event": "start", "backend": "native", "tool_call_id": "pending"}) + "\n")
                stream.write(json.dumps({"event": "end", "backend": "native", "tool_call_id": "wrong", "error_type": "CancelledError"}) + "\n")
            requests = trial / "agent/requests"
            requests.mkdir()
            (requests / "0001.request.json").write_text("{}")
            (requests / "0002.request.json").write_text("{}")
            (requests / "0001.response.json").write_text('{"status":200}')
            partial = report_trial(trial, reports / "partial")
            self.assertEqual(partial["actions"]["unfinished_tool_dispatches"], 1)
            self.assertEqual(partial["actions"]["unmatched_tool_completions"], 1)
            self.assertEqual(partial["actions"]["backend_errors"], {"CancelledError": 1})
            self.assertEqual(partial["actions"]["model_http_request_records"], 2)
            self.assertEqual(partial["actions"]["model_http_response_headers"], 1)
            self.assertEqual(partial["actions"]["requests_without_response_headers"], 1)
            self.assertEqual(partial["actions"]["request_response_entry_gap"], 1)
            self.assertTrue(partial["usage"]["unmatched_request_usage_unknown"])
            self.assertIn("下列 token 不完整", write_index(reports).read_text(encoding="utf-8"))
            self.assertFalse(partial["agent_termination"]["natural_end"])

            # A host result or verifier timestamp alone never proves success.
            rows = rows[:2]
            rows[-1]["message"]["stopReason"] = "stop"
            source.write_text("\n".join(json.dumps(row) for row in rows))
            (trial / "agent/tool-backends.jsonl").write_text("")
            (requests / "0002.request.json").unlink()
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "mcp", "snapshot_sha256": "fixture"}}}))
            (trial / "agent/pi-agent-usage.json").write_text('{"modelCalls":1}')
            host_result = {"agent_execution": {"finished_at": "2026-09-03T00:00:00Z"},
                           "verifier": {"started_at": "2026-09-03T00:00:01Z"},
                           "exception_info": {"exception_type": "VerifierError"}}
            (trial / "result.json").write_text(json.dumps(host_result))
            unknown = report_trial(trial, reports / "unknown")
            self.assertIsNone(unknown["agent_termination"]["natural_end"])
            host_result["agent_result"] = {"metadata": {
                "model_calls": 1, "read_backend": "mcp", "snapshot_sha256": "fixture"}}
            (trial / "result.json").write_text(json.dumps(host_result))
            (trial / "verifier").mkdir()
            (trial / "verifier/verifier_details.json").write_text(json.dumps({"overall_score": 100,
                "rules": {"total": 68, "applicable": 62, "passed": 62, "failed": 0, "not_applicable": 6}}))
            complete = report_trial(trial, reports / "complete")
            self.assertTrue(complete["agent_termination"]["natural_end"])
            self.assertEqual(complete["harbor_failure"]["exception_type"], "VerifierError")
            self.assertEqual(complete["verifier_rules"]["passed"], 62)

            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "mcp", "snapshot_sha256": "fixture",
                "sop_mode": "controlled",
            }}}))
            host_result["agent_result"]["metadata"]["sop_mode"] = "controlled"
            (trial / "result.json").write_text(json.dumps(host_result))
            (trial / "agent/tool-backends.jsonl").write_text(
                json.dumps({"event": "start", "backend": "mcp", "tool_call_id": "action",
                            "tool": "mcp_odoo_execute_method", "sequence": 5}) + "\n"
                + json.dumps({"event": "end", "backend": "mcp", "tool_call_id": "action",
                              "tool": "mcp_odoo_execute_method", "sequence": 5,
                              "end_sequence": 6}) + "\n"
            )
            sop_events = [
                {"event": "start", "tool": "list_odoo_sops", "tool_call_id": "list",
                 "sop_id": None, "sequence": 1},
                {"event": "end", "tool": "list_odoo_sops", "tool_call_id": "list",
                 "sop_id": None, "sequence": 1, "end_sequence": 2, "success": True},
                {"event": "start", "tool": "get_odoo_sop", "tool_call_id": "sop",
                 "sop_id": "safe_write_review", "sequence": 3},
                {"event": "end", "tool": "get_odoo_sop", "tool_call_id": "sop",
                 "sop_id": "safe_write_review", "sequence": 3,
                 "end_sequence": 4, "success": True},
            ]
            (trial / "agent/sop-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in sop_events) + "\n"
            )
            sop_ok = report_trial(trial, reports / "sop-ok")
            self.assertTrue(sop_ok["actions"]["sop_receipt_valid"])
            self.assertTrue(sop_ok["actions"]["sop_list_before_get"])
            self.assertTrue(sop_ok["actions"]["sop_read_before_first_mutation"])
            self.assertTrue(sop_ok["agent_termination"]["natural_end"])
            (trial / "agent/sop-events.jsonl").unlink()
            sop_missing = report_trial(trial, reports / "sop-missing")
            self.assertFalse(sop_missing["actions"]["sop_receipt_valid"])
            self.assertFalse(sop_missing["agent_termination"]["natural_end"])
            no_list = sop_events[2:]
            (trial / "agent/sop-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in no_list) + "\n"
            )
            sop_no_list = report_trial(trial, reports / "sop-no-list")
            self.assertFalse(sop_no_list["actions"]["sop_list_before_get"])
            self.assertFalse(sop_no_list["agent_termination"]["natural_end"])
            sop_events[-1]["end_sequence"] = 6
            (trial / "agent/sop-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in sop_events) + "\n"
            )
            sop_late = report_trial(trial, reports / "sop-late")
            self.assertFalse(sop_late["actions"]["sop_read_before_first_mutation"])
            self.assertFalse(sop_late["agent_termination"]["natural_end"])
            sop_events[-1]["end_sequence"] = 4
            for name, bad_event in (
                ("pending", {"event": "start", "tool": "get_odoo_sop",
                             "tool_call_id": "pending", "sequence": 7}),
                ("orphan", {"event": "end", "tool": "get_odoo_sop",
                            "tool_call_id": "orphan", "sequence": 7,
                            "end_sequence": 8, "success": True}),
                ("missing-id", {"event": "start", "tool": "get_odoo_sop",
                                "tool_call_id": None, "sequence": 7}),
            ):
                with self.subTest(sop_receipt=name):
                    damaged = [*sop_events, bad_event]
                    (trial / "agent/sop-events.jsonl").write_text(
                        "\n".join(json.dumps(event) for event in damaged) + "\n"
                    )
                    sop_bad = report_trial(trial, reports / f"sop-{name}")
                    self.assertFalse(sop_bad["actions"]["sop_receipt_valid"])
                    self.assertFalse(sop_bad["agent_termination"]["natural_end"])

            (trial / "agent/sop-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in sop_events) + "\n"
            )
            published = [
                "mcp_odoo_search_records",
                "list_odoo_capabilities",
                "configure_odoo_tools",
                "mcp_odoo_execute_approved_write",
            ]
            published_contracts = [
                {
                    "name": name,
                    "description": f"{name} description",
                    "parameters": {"type": "object", "properties": {}},
                }
                for name in published
            ]
            withdrawn = published[:-1]
            withdrawn_contracts = published_contracts[:-1]
            dynamic_events = [
                {"event": "start", "tool": "list_odoo_capabilities",
                 "tool_call_id": "dynamic-list", "sequence": 7},
                {"event": "end", "tool": "list_odoo_capabilities",
                 "tool_call_id": "dynamic-list", "sequence": 7,
                 "end_sequence": 8, "success": True},
                {"event": "start", "tool": "configure_odoo_tools",
                 "tool_call_id": "dynamic-configure", "sequence": 9},
                {"event": "end", "tool": "configure_odoo_tools",
                 "tool_call_id": "dynamic-configure", "sequence": 9,
                 "end_sequence": 10, "success": True, "active": ["actions"],
                 "published_tools": published,
                 "tool_contract_sha256": tool_contract_sha256(published_contracts)},
                {"event": "start", "tool": "configure_odoo_tools",
                 "tool_call_id": "dynamic-withdraw", "sequence": 11},
                {"event": "end", "tool": "configure_odoo_tools",
                 "tool_call_id": "dynamic-withdraw", "sequence": 11,
                 "end_sequence": 12, "success": True, "active": [],
                 "published_tools": withdrawn,
                 "tool_contract_sha256": tool_contract_sha256(withdrawn_contracts)},
            ]
            (trial / "agent/dynamic-tools.jsonl").write_text(
                "\n".join(json.dumps(event) for event in dynamic_events) + "\n"
            )

            def request_tools(names, tool_result_ids=()):
                return {
                    "messages": [
                        {"role": "tool", "tool_call_id": call_id, "content": "ok"}
                        for call_id in tool_result_ids
                    ],
                    "tools": [
                        {"type": "function", "function": {
                            "name": name,
                            "description": f"{name} description",
                            "parameters": {"type": "object", "properties": {}},
                        }}
                        for name in names
                    ],
                }

            (requests / "0001.request.json").write_text(json.dumps(request_tools(
                ["mcp_odoo_search_records", "list_odoo_capabilities", "configure_odoo_tools"]
            )))
            (requests / "0002.request.json").write_text(
                json.dumps(request_tools(published, ["dynamic-configure"]))
            )
            (requests / "0002.response.json").write_text('{"status":200}')
            (requests / "0003.request.json").write_text(
                json.dumps(request_tools(
                    withdrawn, ["dynamic-configure", "dynamic-withdraw"]
                ))
            )
            (requests / "0003.response.json").write_text('{"status":200}')
            (trial / "agent/pi-agent-usage.json").write_text('{"modelCalls":3}')
            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "mcp", "snapshot_sha256": "fixture",
                "sop_mode": "controlled", "tool_mode": "dynamic",
            }}}))
            host_result["agent_result"]["metadata"]["model_calls"] = 3
            host_result["agent_result"]["metadata"]["tool_mode"] = "dynamic"
            (trial / "result.json").write_text(json.dumps(host_result))
            dynamic_ok = report_trial(trial, reports / "dynamic-ok")
            self.assertTrue(dynamic_ok["actions"]["dynamic_receipt_valid"])
            self.assertTrue(dynamic_ok["actions"]["dynamic_publish_verified"])
            self.assertEqual(dynamic_ok["actions"]["initial_tool_count"], 3)
            self.assertEqual(dynamic_ok["actions"]["maximum_tool_count"], 4)
            self.assertTrue(dynamic_ok["agent_termination"]["natural_end"])
            tampered = request_tools(published, ["dynamic-configure"])
            tampered["tools"][-1]["function"]["parameters"] = {"type": "array"}
            (requests / "0002.request.json").write_text(json.dumps(tampered))
            dynamic_bad = report_trial(trial, reports / "dynamic-bad")
            self.assertFalse(dynamic_bad["actions"]["dynamic_publish_verified"])
            self.assertFalse(dynamic_bad["agent_termination"]["natural_end"])

            (trial / "config.json").write_text(json.dumps({"agent": {"kwargs": {
                "read_backend": "mcp", "snapshot_sha256": "fixture", "world_mode": "record",
            }}}))
            host_result["agent_result"]["metadata"].pop("tool_mode")
            host_result["agent_result"]["metadata"]["world_mode"] = "record"
            (trial / "result.json").write_text(json.dumps(host_result))
            incomplete_world = report_trial(trial, reports / "incomplete-world")
            self.assertFalse(incomplete_world["agent_termination"]["natural_end"])
            self.assertFalse(incomplete_world["receipts"]["world_integrity"]["valid"])

    def test_python_and_native_receipts_are_read_only_and_keep_failure_layers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for native in (False, True):
                with self.subTest(native=native):
                    trial = root / ("native" if native else "python")
                    source = (
                        trial
                        / "agent"
                        / (
                            "pi/sessions/example.jsonl"
                            if native
                            else "pi-agent-session.jsonl"
                        )
                    )
                    source.parent.mkdir(parents=True)
                    messages = [
                        {"role": "user", "content": "<img src=x onerror=alert(1)>"},
                        {
                            "role": "assistant",
                            "provider": "custom",
                            "model": "test",
                            "stopReason": "toolUse",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call1",
                                    "name": "mcp_odoo_search_records",
                                    "arguments": {},
                                }
                            ],
                            "usage": {
                                "input": 12,
                                "cacheRead": 30,
                                "output": 8,
                                "reasoning": 5,
                                "totalTokens": 50,
                            },
                        },
                        {
                            "role": "toolResult",
                            "toolCallId": "call1",
                            "toolName": "mcp_odoo_search_records",
                            "isError": False,
                            "content": [
                                {
                                    "type": "text",
                                    "text": '{"success":false,"error":"bad field"}',
                                }
                            ],
                        },
                        {
                            "role": "assistant",
                            "provider": "custom",
                            "model": "test",
                            "content": [],
                            "stopReason": "error",
                            "errorMessage": "400 Content Exists Risk; api_key=do-not-export",
                            "diagnostics": [
                                {"type": "gateway", "details": {"attempts": 2}}
                            ],
                        },
                    ]
                    if native:
                        messages[-1] = {
                            "role": "assistant",
                            "provider": "custom",
                            "model": "test",
                            "content": [
                                {"type": "thinking", "thinking": "thinking only"}
                            ],
                            "stopReason": "length",
                            "rawStopReason": "length",
                            "usage": {
                                "output": 16384,
                                "reasoning": 16384,
                                "totalTokens": 16384,
                            },
                        }
                    stamp = "2026-09-03T08:00:00Z" if native else 1788422400.0
                    rows = [
                        {
                            "type": "session" if native else "session_info",
                            "id": "s",
                            "timestamp": stamp,
                        }
                    ]
                    for number, message in enumerate(messages):
                        rows.append(
                            {
                                "type": "message",
                                "id": f"m{number}",
                                "timestamp": stamp,
                                "parentId" if native else "parent_id": f"m{number - 1}"
                                if number
                                else "s",
                                "message": message,
                            }
                        )
                    source.write_text(
                        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
                    )
                    if native:
                        (trial / "agent/pi-mcp-baseline.txt").write_text(
                            '{"interrupted":'
                        )
                    original = source.read_bytes()
                    (trial / "result.json").write_text(
                        '{"exception_info":{"exception_type":"ValidationError","exception_message":"reward shape"}}'
                    )
                    (trial / "verifier").mkdir()
                    (trial / "verifier/reward.json").write_text('{"overall_score":0.0}')
                    output = root / "reports" / trial.name
                    summary = report_trial(trial, output)
                    calls = json.loads((output / "requests.json").read_text())
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual(summary["actions"]["model_calls"], 2)
                    self.assertEqual(summary["actions"]["mcp_calls"], 1)
                    self.assertEqual(summary["actions"]["tool_errors"], 1)
                    self.assertEqual(summary["actions"]["protocol_tool_errors"], 0)
                    self.assertEqual(summary["usage"]["uncached_input_tokens"], 12)
                    self.assertEqual(summary["usage"]["cached_input_tokens"], 30)
                    self.assertEqual(
                        summary["usage"]["output_tokens"], 16392 if native else 8
                    )
                    self.assertEqual(
                        summary["usage"]["reasoning_tokens"], 16389 if native else 5
                    )
                    self.assertIsNone(summary["usage"]["cost"])
                    self.assertEqual(
                        summary["agent_termination"]["kind"],
                        "OUTPUT_LIMIT" if native else "PROVIDER_CONTENT_REJECTION",
                    )
                    self.assertIn(
                        "ValidationError", json.dumps(summary["harbor_failure"])
                    )
                    self.assertEqual(
                        summary["usage"]["unreported_error_calls"], 0 if native else 1
                    )
                    self.assertTrue(calls[-1]["diagnostics"])
                    if native:
                        self.assertIn("event_warning", summary["receipts"])
                    page = (output / "session.html").read_text(encoding="utf-8")
                    self.assertIn("Usage", page)
                    self.assertNotIn("<img src=x onerror=alert(1)>", page)
                    self.assertNotIn("do-not-export", page)
                    self.assertNotIn("do-not-export", json.dumps(calls))
                    self.assertEqual((output / "harbor/reward.txt").read_text(), "0\n")
            self.assertIn(
                "Native Pi", write_index(root / "reports").read_text(encoding="utf-8")
            )
            (trial / "result.json").unlink()
            report_trial(trial, output)
            self.assertIn(
                "INCOMPLETE / last response:", write_index(root / "reports").read_text(encoding="utf-8")
            )
            rows.append({"type": "unexpected", "id": "x", "timestamp": stamp})
            source.write_text("\n".join(json.dumps(row) for row in rows))
            with self.assertRaisesRegex(ValueError, "Unsupported native entry"):
                load_entries(source)

    def test_run_wrapper_reports_even_when_harbor_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "job.json"
            config.write_text(json.dumps({"jobs_dir": directory, "job_name": "job"}))
            with (
                patch.object(sys, "argv", ["report", "--run-config", str(config)]),
                patch(
                    "integration.report.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 2),
                ) as run,
                patch("integration.report.report_path") as report,
            ):
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(stopped.exception.code, 2)
                run.assert_called_once()
                report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
