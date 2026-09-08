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

from integration import harbor_agent, pi_odoo_runner
from pi_agent.tools import AgentTool, AgentToolResult
from pi_coding.session import CodingSession
from pi_ai.openai_compatible import OpenAICompatibleProvider


class RunnerBudgetTest(unittest.TestCase):
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
                    '{"input": 1, "output": 2, "cacheRead": 0, '
                    '"modelCalls": 1, "reasoning": 0}'
                    if command == "cat /logs/agent/pi-agent-usage.json"
                    else ""
                )
            )
        )
        agent._upload_config_text = AsyncMock()
        context = SimpleNamespace()
        with patch.object(harbor_agent, "_start_task_mcp", new=AsyncMock()):
            asyncio.run(agent._run("do work", object(), context))
        command = agent.exec_as_agent.await_args_list[0].kwargs["command"]
        self.assertIn("--max-model-requests 2", command)
        self.assertIn("--max-output-tokens 64", command)
        self.assertEqual(context.metadata["max_model_requests"], 2)
        self.assertEqual(context.metadata["max_output_tokens"], 64)

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
            original_load = CodingSession.load

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
                    patch.object(CodingSession, "load", classmethod(capture_load)),
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
