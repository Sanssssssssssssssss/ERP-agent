from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from bench.adapters import harbor_agent
from erp_harness.app import runner as pi_odoo_runner
from erp_harness.runtime.messages import AssistantMessage, ToolResultMessage, UserMessage
from erp_harness.runtime.tools import AgentTool, AgentToolResult
from erp_harness.runtime.session import HarnessSession
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.tools.dynamic_tools import BASE_TOOLS, CAPABILITY_GROUPS


class RunnerBudgetTest(unittest.TestCase):
    def test_harbor_usage_receipt_boundaries(self):
        async def run_case(receipt):
            with patch.object(harbor_agent.BaseInstalledAgent, "__init__", return_value=None):
                agent = harbor_agent.PiAgentMcpBaseline(max_model_requests=1, max_output_tokens=64)
            agent.model_name = "provider/model"
            agent.model_connection = SimpleNamespace(configured_base_url="https://example.invalid/v1", base_url="https://example.invalid/v1", api_key="test-only")
            agent.context_id = agent.session_id = "test"
            agent.exec_as_agent = AsyncMock(side_effect=lambda _environment, *, command, **_kwargs: SimpleNamespace(stdout=json.dumps(receipt) if command == "cat /logs/agent/pi-agent-usage.json" else ""))
            agent._upload_config_text = AsyncMock()
            context = SimpleNamespace()
            with patch.object(harbor_agent, "_start_task_mcp", new=AsyncMock()):
                await agent._run("do work", object(), context)
            return context

        unknown = asyncio.run(run_case({}))
        self.assertIsNone(unknown.n_input_tokens)
        self.assertIsNone(unknown.n_output_tokens)
        self.assertIsNone(unknown.metadata["compaction_total_tokens"])
        zero = asyncio.run(run_case({"input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "compactionCalls": 0}))
        self.assertEqual((zero.n_input_tokens, zero.n_output_tokens, zero.n_cache_tokens), (0, 0, 0))
        measured = asyncio.run(run_case({"input": 10, "cacheRead": 3, "cacheWrite": 2, "output": 4,
                                         "compactionCalls": 1, "compactionInput": 6,
                                         "compactionCacheRead": 1, "compactionCacheWrite": 2,
                                         "compactionOutput": 5}))
        self.assertEqual((measured.n_input_tokens, measured.n_output_tokens, measured.n_cache_tokens), (24, 9, 4))
        self.assertEqual(measured.metadata["cache_write_tokens"], 2)
        self.assertEqual(measured.metadata["compaction_output_tokens"], 5)

    def test_usage_aggregation_preserves_zero_and_missing_buckets(self):
        zero = SimpleNamespace(input=0, cache_read=0, cache_write=0, output=0, total_tokens=0, reasoning=0)
        partial = SimpleNamespace(input=4, output=2, total_tokens=6, reasoning=1)
        self.assertEqual(pi_odoo_runner._nullable_usage_sum(
            [SimpleNamespace(stop_reason="stop", usage=zero)], "input"
        ), 0)
        self.assertIsNone(pi_odoo_runner._nullable_usage_sum(
            [SimpleNamespace(stop_reason="stop", usage=zero),
             SimpleNamespace(stop_reason="stop", usage=partial)], "cache_read"
        ))
        self.assertEqual(pi_odoo_runner._nullable_usage_sum(
            [SimpleNamespace(stop_reason="stop", usage=zero),
             SimpleNamespace(stop_reason="stop", usage=partial)], "output"
        ), 2)
        self.assertIsNone(pi_odoo_runner._nullable_usage_sum(
            [SimpleNamespace(stop_reason="error", usage=zero)], "total_tokens"
        ))

        compact = SimpleNamespace(usage=SimpleNamespace(input=20, cache_read=3, cache_write=2, output=1, total_tokens=26, reasoning=0))
        self.assertEqual(pi_odoo_runner._nullable_entry_usage_sum([compact], "total_tokens"), 26)
        self.assertEqual(pi_odoo_runner._nullable_entry_usage_sum([], "total_tokens"), 0)
        self.assertIsNone(pi_odoo_runner._nullable_entry_usage_sum(
            [compact, SimpleNamespace(usage=None)], "total_tokens"
        ))

    def test_pause_on_approval_and_resume_keep_requests_and_usage_scoped(self):
        async def check(root: Path):
            requests = []

            async def execute(*_args, **_kwargs):
                return AgentToolResult(
                    content=json.dumps({"success": False, "action_status": "needs_reconciliation"}),
                    details={"success": False, "action_status": "needs_reconciliation"},
                )

            class ToolSet:
                def __init__(self, _url):
                    self.tools = [AgentTool(
                        name="execute_method", label="Execute", description="write",
                        parameters={"type": "object", "properties": {}}, execute_fn=execute,
                    )]

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return None

            def handler(request):
                requests.append(json.loads(request.content))
                if len(requests) == 1:
                    body = {
                        "choices": [{"delta": {"tool_calls": [{
                            "index": 0, "id": "write-1", "type": "function",
                            "function": {"name": "execute_method", "arguments": "{}"},
                        }]}, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                    }
                else:
                    body = {
                        "choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    }
                return httpx.Response(
                    200, text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                    headers={"content-type": "text/event-stream"},
                )

            instruction = root / "instruction.txt"
            instruction.write_text("Execute the write once.", encoding="utf-8")
            args = SimpleNamespace(
                instruction_file=instruction, session_file=root / "session.jsonl",
                usage_file=root / "usage.json", receipt_dir=root / "run",
                mcp_url="http://unused.invalid", max_turns=3, max_model_requests=3,
                max_output_tokens=None, runtime_mode="mcp", read_backend="mcp",
                action_backend="mcp", capability_backend="mcp", sop_mode="off",
                tool_mode="static", world_mode="off", continue_run=False,
                pause_on_approval=True,
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with (
                    patch.object(pi_odoo_runner, "McpToolSet", ToolSet),
                    patch.object(
                        pi_odoo_runner, "OpenAICompatibleProvider",
                        side_effect=lambda config: OpenAICompatibleProvider(config, client=client),
                    ),
                    patch.dict(os.environ, {
                        "LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://unused.invalid/v1",
                        "LLM_MODEL": "deepseek/test", "LLM_PROVIDER": "openai-compatible",
                        "LLM_THINKING_TYPE": "high",
                    }),
                ):
                    await pi_odoo_runner.run(args)
                    self.assertEqual(len(requests), 1)
                    initial_usage = json.loads(args.usage_file.read_text())
                    self.assertEqual(initial_usage["modelCalls"], 1)
                    self.assertEqual(initial_usage["input"], 10)
                    self.assertEqual(initial_usage["output"], 2)
                    args.continue_run = True
                    args.usage_file = root / "resumed-usage.json"
                    await pi_odoo_runner.run(args)

            self.assertEqual(len(requests), 2)
            resumed_usage = json.loads(args.usage_file.read_text())
            self.assertEqual(resumed_usage["modelCalls"], 1)
            self.assertEqual(resumed_usage["assistantEntries"], 1)
            self.assertEqual(resumed_usage["input"], 0)
            self.assertEqual(resumed_usage["output"], 0)
            self.assertEqual(resumed_usage["compactionCalls"], 0)
            self.assertEqual(resumed_usage["unreportedUsageRequests"], 0)
            self.assertEqual(json.loads((args.receipt_dir / "requests" / "0001.request.json").read_text()), requests[0])
            self.assertTrue((args.receipt_dir / "requests" / "0002.request.json").is_file())
            rows = [json.loads(line) for line in args.session_file.read_text().splitlines()]
            self.assertTrue(any(
                row.get("type") == "message"
                and row.get("message", {}).get("role") == "toolResult"
                and "needs_reconciliation" in json.dumps(row.get("message", {}))
                for row in rows
            ))

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_pause_marker_handles_nested_and_malformed_status_values(self):
        nested = ToolResultMessage(
            tool_call_id="x", tool_name="x", content="", is_error=False,
            details={"structuredContent": {"action_status": {"status": "needs_reconciliation"}}},
        )
        malformed = ToolResultMessage(
            tool_call_id="x", tool_name="x", content="", is_error=False,
            details={"structuredContent": {"status": ["needs_reconciliation"], "action_status": {}}},
        )
        self.assertTrue(pi_odoo_runner._approval_required(nested))
        self.assertFalse(pi_odoo_runner._approval_required(malformed))

    def test_current_action_state_overrides_legacy_approval_flag(self):
        for status, expected in (("verified", False), ("known_failed", False), ("approved", False),
                                 ("pending_approval", True), ("needs_reconciliation", True), ("sending", True), ("executing", True)):
            for nested in (False, True):
                payload = {"approval_required": True, "action_status": {"status": status} if nested else status,
                           "approval_status": {"status": "pending_approval"}}
                with self.subTest(status=status, nested=nested):
                    self.assertEqual(pi_odoo_runner._approval_required({"details": {"structuredContent": payload}}), expected)
        self.assertTrue(pi_odoo_runner._approval_required({"approval_required": True}))
        self.assertTrue(pi_odoo_runner._approval_required({"status": "success", "approval_required": True}))

    def test_verified_chatter_receipt_continues_to_final_summary_without_another_approval(self):
        async def check(root):
            requests = []
            # Actual failing trace envelope: the obsolete flag remains true after verification.
            receipt = {"approval_required": True, "success": True, "action_id": "chatter-verified",
                       "action_status": "verified", "result": [7433], "verification": {"status": "satisfied"}}
            async def execute(*_args, **_kwargs):
                return AgentToolResult(content=json.dumps(receipt), details=receipt)
            class ToolSet:
                def __init__(self, _url):
                    self.tools = [AgentTool(name="chatter_post", label="Chatter", description="Post a note",
                                           parameters={"type": "object", "properties": {}}, execute_fn=execute)]
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return None
            def handler(request):
                requests.append(json.loads(request.content))
                delta = ({"tool_calls": [{"index": 0, "id": "chatter-call", "type": "function",
                                          "function": {"name": "chatter_post", "arguments": "{}"}}]}
                         if len(requests) == 1 else {"content": "留言已核验，业务处理完成。"})
                body = {"choices": [{"delta": delta, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
                return httpx.Response(200, text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                                      headers={"content-type": "text/event-stream"})
            instruction = root / "instruction.txt"
            instruction.write_text("Post the approved note, then summarize.", encoding="utf-8")
            args = SimpleNamespace(instruction_file=instruction, session_file=root / "session.jsonl",
                                   usage_file=root / "usage.json", receipt_dir=root / "run", mcp_url="http://unused.invalid",
                                   max_turns=3, max_model_requests=3, max_output_tokens=None, pause_on_approval=True)
            stdout = io.StringIO()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with (patch.object(pi_odoo_runner, "McpToolSet", ToolSet),
                      patch.object(pi_odoo_runner, "OpenAICompatibleProvider", side_effect=lambda config: OpenAICompatibleProvider(config, client=client)),
                      patch.dict(os.environ, {"LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://unused.invalid/v1",
                                             "LLM_MODEL": "deepseek/test", "LLM_PROVIDER": "openai-compatible", "LLM_THINKING_TYPE": "high"}),
                      contextlib.redirect_stdout(stdout)):
                    await pi_odoo_runner.run(args)
            self.assertEqual(len(requests), 2)
            self.assertEqual(json.loads(args.usage_file.read_text())["modelCalls"], 2)
            rows = [json.loads(line) for line in args.session_file.read_text(encoding="utf-8").splitlines()]
            assistant = [row["message"] for row in rows if row.get("type") == "message" and row.get("message", {}).get("role") == "assistant"]
            self.assertEqual(assistant[-1]["stopReason"], "stop")
            self.assertIn("留言已核验", json.dumps(assistant[-1], ensure_ascii=False))
            self.assertEqual(sum(row.get("message", {}).get("role") == "toolResult" for row in rows), 1)
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_dynamic_history_recovery_is_typed_fail_closed_and_latest(self):
        def result(payload, *, is_error=False):
            return ToolResultMessage(
                tool_call_id="configure",
                tool_name="configure_odoo_tools",
                content=json.dumps(payload),
                details=None,
                is_error=is_error,
            )

        valid = result({"success": True, "active": ["actions"]})
        failed = result({"success": False, "error": "module unavailable"})
        self.assertEqual(
            pi_odoo_runner._history_dynamic_selection([valid]),
            ("actions",),
        )
        self.assertEqual(
            pi_odoo_runner._history_dynamic_selection([valid, failed]),
            ("actions",),
        )
        self.assertEqual(
            pi_odoo_runner._history_dynamic_selection(
                [valid, result({"success": True, "active": []})]
            ),
            (),
        )
        self.assertEqual(
            pi_odoo_runner._history_dynamic_selection(
                [valid, result({"success": True, "active": ["actions", "actions"]})]
            ),
            None,
        )
        self.assertEqual(
            pi_odoo_runner._history_dynamic_selection(
                [UserMessage(content='{"success":true,"active":["actions"]}'),
                 AssistantMessage(content='configure_odoo_tools')]
            ),
            None,
        )

    def test_new_receipt_history_selection_reaches_first_provider_request(self):
        names = {
            name
            for name in BASE_TOOLS
            if name not in {"get_current_time", "list_odoo_sops", "get_odoo_sop"}
        } | {
            name
            for group in CAPABILITY_GROUPS.values()
            for name in group["tools"]
        }
        request_payloads = []

        async def execute(*_args, **_kwargs):
            return AgentToolResult(content=json.dumps({"success": True, "result": []}))

        class ToolSet:
            def __init__(self, _url):
                self.tools = [
                    AgentTool(
                        name=f"mcp_odoo_{name}",
                        label=name,
                        description=name,
                        parameters={"type": "object"},
                        execute_fn=execute,
                    )
                    for name in sorted(names)
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        def handler(request):
            request_payloads.append(json.loads(request.content))
            if len(request_payloads) == 1:
                body = {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "configure-1",
                                "type": "function",
                                "function": {
                                    "name": "configure_odoo_tools",
                                    "arguments": '{"capabilities":["actions"]}',
                                },
                            }]
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                }
            else:
                body = {
                    "choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                }
            return httpx.Response(
                200,
                text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        def tool_names(payload):
            return {
                row["function"]["name"]
                for row in payload["tools"]
                if row.get("type") == "function"
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instruction = root / "instruction.txt"
            instruction.write_text("Use the available tools.", encoding="utf-8")
            session_file = root / "shared-session.jsonl"
            args = SimpleNamespace(
                instruction_file=instruction,
                session_file=session_file,
                usage_file=root / "run1" / "usage.json",
                receipt_dir=root / "run1",
                mcp_url="http://unused.invalid",
                max_turns=3,
                max_model_requests=2,
                max_output_tokens=None,
                runtime_mode="mcp",
                read_backend="mcp",
                action_backend="mcp",
                capability_backend="mcp",
                sop_mode="controlled",
                tool_mode="dynamic",
                world_mode="off",
                continue_run=False,
                pause_on_approval=False,
            )
            async def check():
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with (
                        patch.object(pi_odoo_runner, "McpToolSet", ToolSet),
                        patch.object(
                            pi_odoo_runner,
                            "OpenAICompatibleProvider",
                            side_effect=lambda config: OpenAICompatibleProvider(config, client=client),
                        ),
                        patch.dict(
                            os.environ,
                            {
                                "LLM_API_KEY": "test-only",
                                "LLM_BASE_URL": "https://unused.invalid/v1",
                                "LLM_MODEL": "deepseek/test",
                                "LLM_PROVIDER": "openai-compatible",
                                "LLM_THINKING_TYPE": "high",
                            },
                        ),
                    ):
                        await pi_odoo_runner.run(args)
                        args.usage_file = root / "run2" / "usage.json"
                        args.receipt_dir = root / "run2"
                        resumed_requests = args.receipt_dir / "requests"
                        resumed_requests.mkdir(parents=True)
                        (resumed_requests / "0001.request.json").write_text("{}", encoding="utf-8")
                        await pi_odoo_runner.run(args)
                        self.assertEqual(json.loads(args.usage_file.read_text())["modelCalls"], 1)
                        self.assertEqual((resumed_requests / "0001.request.json").read_text(), "{}")
                        self.assertTrue((resumed_requests / "0002.request.json").is_file())
                        args.usage_file = root / "run3" / "usage.json"
                        args.receipt_dir = root / "run3"
                        args.receipt_dir.mkdir(parents=True)
                        (args.receipt_dir / "dynamic-tools.jsonl").write_text(
                            json.dumps({
                                "event": "end",
                                "tool": "configure_odoo_tools",
                                "success": True,
                                "active": [],
                            }) + "\n",
                            encoding="utf-8",
                        )
                        await pi_odoo_runner.run(args)

            asyncio.run(check())

            self.assertEqual(json.loads((root / "run1" / "usage.json").read_text())["modelCalls"], 2)
            self.assertEqual(json.loads((root / "run3" / "usage.json").read_text())["modelCalls"], 1)
            first, second, third, fourth = map(tool_names, request_payloads)
            self.assertEqual(len(first), 14)
            self.assertNotIn("mcp_odoo_preview_write", first)
            self.assertEqual(len(second), 19)
            self.assertIn("mcp_odoo_preview_write", second)
            self.assertIn("mcp_odoo_preview_write", third)
            self.assertEqual(len(fourth), 14)
            self.assertNotIn("mcp_odoo_preview_write", fourth)

    def test_corrupt_present_receipt_blocks_history_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic-tools.jsonl"
            path.write_text("{not-json}\n", encoding="utf-8")
            self.assertEqual(pi_odoo_runner._receipt_dynamic_selection(path), (True, None))

    def test_receipt_selection_is_primary_over_history_and_failed_config_keeps_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic-tools.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({
                            "event": "end", "tool": "configure_odoo_tools",
                            "success": True, "active": ["actions"],
                        }),
                        json.dumps({
                            "event": "end", "tool": "configure_odoo_tools",
                            "success": False, "active": None,
                        }),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                pi_odoo_runner._receipt_dynamic_selection(path),
                (True, ("actions",)),
            )
            path.write_text(
                json.dumps({
                    "event": "end", "tool": "configure_odoo_tools",
                    "success": True, "active": [],
                }) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(pi_odoo_runner._receipt_dynamic_selection(path), (True, ()))

    def test_resumed_receipts_keep_request_and_tool_chronology(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0003.request.json").write_text("{}", encoding="utf-8")
            self.assertEqual(pi_odoo_runner.RequestReceipts(root).number, 3)
            (root / "tool-backends.jsonl").write_text('{"sequence":1,"end_sequence":4}\n', encoding="utf-8")
            (root / "dynamic-tools.jsonl").write_text('{"sequence":5,"end_sequence":6}\n', encoding="utf-8")
            next_sequence = pi_odoo_runner._next_receipt_sequence(root)
            self.assertEqual((next_sequence(), next_sequence()), (7, 8))

    def test_receipts_enforce_max_model_requests_before_writing(self):
        async def check(root: Path) -> None:
            receipts = pi_odoo_runner.RequestReceipts(root, max_model_requests=1)
            payload = {"model": "test", "messages": []}
            self.assertEqual(await receipts.before_provider_request(payload), payload)
            with self.assertRaisesRegex(RuntimeError, "max_model_requests"):
                await receipts.before_provider_request(payload)
            self.assertEqual(receipts.number, 1)
            self.assertEqual(len(list(root.glob("*.request.json"))), 1)

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_receipts_reject_non_positive_and_bool(self):
        for value in (0, -1, True, False, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pi_odoo_runner.RequestReceipts(Path("unused"), value)

    def test_harbor_passes_budget_flags_and_records_metadata(self):
        with patch.object(harbor_agent.BaseInstalledAgent, "__init__", return_value=None):
            agent = harbor_agent.PiAgentMcpBaseline(
                max_model_requests=2,
                max_output_tokens=64,
            )
        agent.model_name = "provider/model"
        agent.model_connection = SimpleNamespace(
            configured_base_url="https://example.invalid/v1",
            base_url="https://example.invalid/v1",
            api_key="test-only",
        )
        agent.context_id = agent.session_id = "test"
        agent.exec_as_agent = AsyncMock(
            side_effect=lambda _environment, *, command, **_kwargs: SimpleNamespace(
                stdout=(
                    '{"input": 1, "output": 2, "cacheRead": 0, "cacheWrite": 0, '
                    '"modelCalls": 1, "reasoning": 0, "compactionCalls": 0}'
                    if command == "cat /logs/agent/pi-agent-usage.json"
                    else ""
                )
            )
        )
        agent._upload_config_text = AsyncMock()
        context = SimpleNamespace()
        with patch.object(harbor_agent, "_start_task_mcp", new=AsyncMock()):
            asyncio.run(agent._run("do work", object(), context))
        command = next(call.kwargs["command"] for call in agent.exec_as_agent.await_args_list
                       if "--max-model-requests" in call.kwargs["command"])
        self.assertIn("--max-model-requests 2", command)
        self.assertIn("--max-output-tokens 64", command)
        self.assertEqual(context.metadata["max_model_requests"], 2)
        self.assertEqual(context.metadata["max_output_tokens"], 64)
        self.assertEqual(context.n_input_tokens, 1)
        self.assertEqual(context.n_cache_tokens, 0)
        self.assertEqual(context.n_output_tokens, 2)
        self.assertEqual(context.metadata["fresh_input_tokens"], 1)
        self.assertEqual(context.metadata["cache_read_tokens"], 0)
        self.assertEqual(context.metadata["compaction_total_tokens"], 0)

    def test_budget_blocks_second_real_provider_request_and_disables_session_recovery(self):
        async def check(root: Path) -> None:
            requests: list[dict] = []

            async def execute(*_args, **_kwargs):
                return AgentToolResult(content='{"success": true}')

            tool = AgentTool(
                name="fake_tool",
                label="Fake",
                description="Fake",
                parameters={"type": "object", "properties": {}},
                execute_fn=execute,
            )

            class ToolSet:
                def __init__(self, _url):
                    self.tools = [tool]

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return None

            def handler(request):
                requests.append(json.loads(request.content))
                body = {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call1",
                                "type": "function",
                                "function": {"name": "fake_tool", "arguments": "{}"},
                            }]
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                }
                return httpx.Response(
                    200,
                    text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                    headers={"content-type": "text/event-stream"},
                )

            instruction = root / "instruction.txt"
            instruction.write_text("Use fake_tool once.")
            args = SimpleNamespace(
                instruction_file=instruction,
                session_file=root / "session.jsonl",
                usage_file=root / "usage.json",
                mcp_url="http://unused.invalid",
                max_turns=3,
                max_model_requests=1,
                max_output_tokens=None,
            )
            captured_configs = []
            captured_session_configs = []
            original_load = HarnessSession.load

            async def capture_load(cls, config):
                captured_session_configs.append(config)
                return await original_load(config)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                def make_provider(config):
                    captured_configs.append(config)
                    return OpenAICompatibleProvider(config, client=client)

                stdout = io.StringIO()
                with (
                    patch.object(pi_odoo_runner, "McpToolSet", ToolSet),
                    patch.object(pi_odoo_runner, "OpenAICompatibleProvider", side_effect=make_provider),
                    patch.object(HarnessSession, "load", classmethod(capture_load)),
                    patch.dict(
                        os.environ,
                        {
                            "LLM_API_KEY": "test-only",
                            "LLM_BASE_URL": "https://unused.invalid/v1",
                            "LLM_MODEL": "deepseek/test",
                            "LLM_PROVIDER": "openai-compatible",
                            "LLM_THINKING_TYPE": "high",
                        },
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    try:
                        await pi_odoo_runner.run(args)
                    except RuntimeError as exc:
                        self.assertIn("max_model_requests", str(exc))

            self.assertEqual(len(requests), 1)
            self.assertEqual(len(list((root / "requests").glob("*.request.json"))), 1)
            self.assertEqual(captured_configs[0].max_retries, 0)
            self.assertEqual(captured_configs[0].max_tokens, None)
            self.assertFalse(captured_session_configs[0].retry_enabled)
            self.assertFalse(captured_session_configs[0].auto_compact_enabled)
            self.assertEqual(
                captured_session_configs[0].resource_paths.paths.home,
                root / ".pi-agent",
            )
            self.assertTrue((root / ".pi-agent" / "logs" / "agent-calls.jsonl").is_file())
            session_rows = [
                json.loads(line)
                for line in (root / "session.jsonl").read_text().splitlines()
            ]
            assistant_rows = [
                row for row in session_rows
                if row.get("type") == "message"
                and row.get("message", {}).get("role") == "assistant"
            ]
            self.assertEqual(assistant_rows[-1]["message"]["stopReason"], "error")
            self.assertEqual(json.loads((root / "usage.json").read_text())["modelCalls"], 1)

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))


if __name__ == "__main__":
    unittest.main()
