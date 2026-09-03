from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.report import load_entries, main, report_trial, write_index


class ReportingTest(unittest.TestCase):
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
            original = source.read_bytes()
            output = Path(directory) / "report"
            summary = report_trial(trial, output)
            self.assertEqual(summary["agent_termination"]["kind"], "TURN_LIMIT")
            self.assertEqual(summary["actions"]["model_calls"], 1)
            self.assertEqual(summary["actions"]["assistant_entries"], 2)
            self.assertEqual(summary["usage"]["unreported_error_calls"], 0)
            self.assertEqual(summary["identity"]["provider"], "test")
            self.assertEqual(len(json.loads((output / "requests.json").read_text())), 1)
            self.assertIn(
                "Agent stopped after max_turns=1",
                (output / "session.html").read_text(encoding="utf-8"),
            )
            self.assertEqual(source.read_bytes(), original)

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
            self.assertIn("Native Pi", write_index(root / "reports").read_text())
            (trial / "result.json").unlink()
            report_trial(trial, output)
            self.assertIn(
                "INCOMPLETE / last response:", write_index(root / "reports").read_text()
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
