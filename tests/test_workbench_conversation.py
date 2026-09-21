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

from pi_ai.openai_compatible import OpenAICompatibleProvider
from pi_agent.messages import AssistantMessage, Usage
from pi_agent.session.entries import CompactionEntry, MessageEntry
from erp_harness.app import conversation


class WorkbenchConversationTests(unittest.TestCase):
    def test_usage_receipt_keeps_zero_and_marks_partial_buckets_unknown(self):
        zero = SimpleNamespace(input=0, cache_read=0, output=0, total_tokens=0, reasoning=0)
        result = conversation._aggregate_usage([SimpleNamespace(usage=zero)], [])
        self.assertEqual({result[key] for key in ("input", "cache_read", "output", "total", "reasoning")}, {0})
        self.assertEqual(result["compaction_total"], 0)
        self.assertEqual(result["compaction_calls"], 0)

        partial = SimpleNamespace(input=4, output=2, total_tokens=6, reasoning=1)
        result = conversation._aggregate_usage(
            [SimpleNamespace(usage=zero), SimpleNamespace(usage=partial)], []
        )
        self.assertIsNone(result["cache_read"])
        self.assertEqual(result["input"], 4)
        self.assertEqual(result["total"], 6)

        error = SimpleNamespace(
            usage=zero, stop_reason="error"
        )
        result = conversation._aggregate_usage([error], [])
        self.assertIsNone(result["input"])
        self.assertIsNone(result["total"])

        aborted_with_subtotal = SimpleNamespace(
            usage=SimpleNamespace(input=4, cache_read=0, cache_write=0, output=0, total_tokens=4, reasoning=0),
            stop_reason="aborted",
        )
        result = conversation._aggregate_usage([aborted_with_subtotal], [])
        self.assertEqual(result["input"], 4)
        self.assertEqual(result["total"], 4)

    def test_usage_receipt_keeps_compaction_separate_and_unknown(self):
        assistant = SimpleNamespace(input=10, cache_read=5, cache_write=2, cache_write_1h=0, output=2, total_tokens=17, reasoning=0)
        compaction = SimpleNamespace(
            usage=SimpleNamespace(input=20, cache_read=3, cache_write=4, cache_write_1h=1, output=1, total_tokens=24, reasoning=0)
        )
        result = conversation._aggregate_usage([SimpleNamespace(usage=assistant)], [compaction])
        self.assertEqual(result["total"], 17)
        self.assertEqual(result["compaction_total"], 24)
        self.assertEqual(result["compaction_input"], 20)
        self.assertEqual(result["compaction_cache_read"], 3)
        self.assertEqual(result["cache_write"], 2)
        self.assertEqual(result["cache_write_1h"], 0)
        self.assertEqual(result["compaction_cache_write"], 4)
        self.assertEqual(result["compaction_cache_write_1h"], 1)
        self.assertEqual(result["compaction_output"], 1)
        self.assertEqual(result["compaction_reasoning"], 0)
        self.assertEqual(result["compaction_calls"], 1)

        result = conversation._aggregate_usage([SimpleNamespace(usage=assistant)], [SimpleNamespace(usage=None)])
        self.assertIsNone(result["compaction_total"])
        self.assertEqual(result["compaction_calls"], 1)

    def _run(self, responses: list[dict], instruction: str, *, return_usage: bool = False):
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
            events = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
            if return_usage:
                return requests, events, json.loads(args.usage_file.read_text(encoding="utf-8"))
            return requests, events

    def test_run_writes_scoped_usage_receipt(self):
        _requests, _events, usage = self._run(
            [{"choices": [{"delta": {"content": "完成"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4}}],
            "请简短回答。",
            return_usage=True,
        )
        self.assertEqual(usage["input"], 4)
        self.assertEqual(usage["output"], 0)
        self.assertEqual(usage["total"], 4)
        self.assertEqual(usage["compaction_total"], 0)
        self.assertEqual(usage["total_scope"], "assistant_responses_only")
        self.assertEqual(usage["input_semantics"], "uncached")

    def test_run_uses_new_journal_entries_after_compaction(self):
        old = MessageEntry(message=AssistantMessage(
            content=[], usage=Usage(input=100, output=10, total_tokens=110)
        ))
        first = MessageEntry(message=AssistantMessage(
            content=[], usage=Usage(input=4, output=2, total_tokens=6)
        ))
        second = MessageEntry(message=AssistantMessage(
            content=[], usage=Usage(input=5, output=0, total_tokens=5)
        ))
        compact = CompactionEntry(
            summary="old context", usage=Usage(input=20, output=3, total_tokens=23)
        )

        class FakeSession:
            messages = [old.message]

            def __init__(self):
                self._entries = iter(((old,), (old, first, compact, second)))

            async def session_entries(self):
                return next(self._entries)

            def prompt(self, _instruction):
                async def empty_events():
                    if False:
                        yield None
                return empty_events()

            async def aclose(self):
                return None

        fake = FakeSession()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instruction_file = root / "instruction.txt"
            instruction_file.write_text("继续处理", encoding="utf-8")
            args = SimpleNamespace(
                instruction_file=instruction_file,
                usage_file=root / "usage.json",
                session_file=root / "conversation.jsonl",
                receipt_dir=root / "receipts",
            )
            output = io.StringIO()

            async def run():
                with (
                    patch.object(conversation.CodingSession, "load", new=AsyncMock(return_value=fake)),
                    patch.dict(
                        os.environ,
                        {"LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://unused.invalid/v1", "LLM_MODEL": "test/model"},
                        clear=False,
                    ),
                    contextlib.redirect_stdout(output),
                ):
                    await conversation.run(args)

            asyncio.run(run())
            usage = json.loads(args.usage_file.read_text(encoding="utf-8"))
        self.assertEqual(usage["input"], 9)
        self.assertEqual(usage["output"], 2)
        self.assertEqual(usage["total"], 11)
        self.assertEqual(usage["compaction_total"], 23)
        self.assertEqual(usage["compaction_input"], 20)
        self.assertEqual(usage["compaction_calls"], 1)

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
