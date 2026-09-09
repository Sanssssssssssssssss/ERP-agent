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
from unittest.mock import patch

import httpx

from pi_ai.openai_compatible import OpenAICompatibleProvider
from workbench import conversation


class WorkbenchConversationTests(unittest.TestCase):
    def _run(self, responses: list[dict], instruction: str):
        requests: list[dict] = []

        def handler(request):
            requests.append(json.loads(request.content))
            body = responses[min(len(requests) - 1, len(responses) - 1)]
            return httpx.Response(
                200,
                text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instruction_file = root / "instruction.txt"
            instruction_file.write_text(instruction, encoding="utf-8")
            args = SimpleNamespace(
                instruction_file=instruction_file,
                usage_file=root / "usage.json",
                session_file=root / "conversation.jsonl",
                receipt_dir=root / "receipts",
            )
            output = io.StringIO()

            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with (
                        patch.object(
                            conversation,
                            "OpenAICompatibleProvider",
                            side_effect=lambda config: OpenAICompatibleProvider(config, client=client),
                        ),
                        patch.dict(
                            os.environ,
                            {"LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://unused.invalid/v1", "LLM_MODEL": "test/model"},
                            clear=False,
                        ),
                        contextlib.redirect_stdout(output),
                    ):
                        await conversation.run(args)

            asyncio.run(run())
            return requests, [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]

    def test_real_coding_session_answers_without_read_request_and_advertises_fixed_tools(self):
        requests, events = self._run(
            [{"choices": [{"delta": {"content": "可以先回答问题，再在你确认后建立业务。"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12}}],
            "你好，你能做什么？",
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            [row["function"]["name"] for row in requests[0]["tools"]],
            ["read_odoo_reference", "propose_business"],
        )
        self.assertEqual("".join(event.get("text", "") for event in events if event.get("type") == "message_delta"), "可以先回答问题，再在你确认后建立业务。")
        self.assertNotIn("mcp_odoo_read", json.dumps(requests[0]))

    def test_real_coding_session_emits_server_checked_proposal_tool_result(self):
        requests, events = self._run(
            [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "propose_business", "arguments": '{"type":"sale_invoice","title":"销售订单与发票","goal":"为客户建立订单并开票"}'}}]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 4, "completion_tokens": 10, "total_tokens": 14}},
                {"choices": [{"delta": {"content": "我已准备好提案，请确认后再建立业务。"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16}},
            ],
            "请为客户建立订单并开票。",
        )
        self.assertGreaterEqual(len(requests), 2)
        tool_results = [event for event in events if event.get("type") == "tool_execution_end"]
        self.assertTrue(tool_results)
        self.assertEqual(tool_results[-1]["result"]["details"]["proposal"]["type"], "sale_invoice")
        system_text = json.dumps(requests[0]["messages"], ensure_ascii=False)
        self.assertIn("创建业务工作区", system_text)
        self.assertIn("开始执行", system_text)
        self.assertIn("Do not ask the user to reply with confirmation", system_text)

    def test_read_odoo_reference_is_bounded_and_source_stamped(self):
        class FakeReads:
            def call(self, name, arguments):
                self.name, self.arguments = name, arguments
                return {"success": True, "result": [{"id": i, "name": "客户" + str(i)} for i in range(20)]}

        fake = FakeReads()
        with patch.object(conversation, "_ODOO_READS", fake):
            result = asyncio.run(conversation._read_odoo_reference("call", {"resource": "customer", "query": "N", "limit": 5}))
        details = result.details
        self.assertEqual(fake.name, "search_records")
        self.assertEqual(fake.arguments["model"], "res.partner")
        self.assertEqual(fake.arguments["fields"], conversation._REFERENCE_SPECS["customer"][1])
        self.assertEqual(len(details["records"]), 5)
        self.assertTrue(details["truncated"])
        self.assertEqual(details["source"], "native_odoo_read")
        self.assertTrue(details["observed_at"].endswith("Z"))

    def test_read_odoo_reference_failure_is_unknown(self):
        class FailedReads:
            def call(self, _name, _arguments):
                return {"success": False, "error": "connection refused"}

        with patch.object(conversation, "_ODOO_READS", FailedReads()):
            result = asyncio.run(conversation._read_odoo_reference("call", {"resource": "product"}))
        self.assertFalse(result.details["success"])
        self.assertEqual(result.details["status"], "unavailable")
        self.assertFalse(result.details["verified"])

    def test_read_odoo_reference_rejects_bad_shapes_and_caps_large_rows(self):
        for arguments in (
            {"resource": "not_a_resource"},
            {"resource": []},
            {"resource": "customer", "unexpected": True},
            {"resource": "customer", "limit": True},
            {"resource": "customer", "limit": 6},
        ):
            with self.subTest(arguments=arguments):
                result = asyncio.run(conversation._read_odoo_reference("call", arguments))
                self.assertEqual(result.details["status"], "invalid")

        class MalformedReads:
            def call(self, _name, _arguments):
                return {"success": True, "result": [{"id": 1}, "bad"]}

        with patch.object(conversation, "_ODOO_READS", MalformedReads()):
            result = asyncio.run(conversation._read_odoo_reference("call", {"resource": "customer"}))
        self.assertEqual(result.details["status"], "unavailable")

        class HugeReads:
            def call(self, _name, _arguments):
                return {"success": True, "result": [{"id": 1, "name": "x" * 20_000}]}

        with patch.object(conversation, "_ODOO_READS", HugeReads()):
            result = asyncio.run(conversation._read_odoo_reference("call", {"resource": "customer"}))
        self.assertTrue(result.details["truncated"])
        self.assertLess(len(json.dumps(result.details, ensure_ascii=False).encode("utf-8")), conversation.READ_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
