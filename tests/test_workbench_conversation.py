from __future__ import annotations

import asyncio
import contextlib
import io
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import httpx

from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.runtime.messages import AssistantMessage, Usage
from erp_harness.runtime.storage.entries import CompactionEntry, MessageEntry
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
        linked = next(event for event in _events if event["type"] == "request_linked")
        terminal = next(event for event in _events if event["type"] == "message_end" and event.get("message_id") == linked["message_id"])
        self.assertEqual((terminal["request_id"], terminal["round_id"]), (linked["request_id"], linked["round_id"]))

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
                    patch.object(conversation.HarnessSession, "load", new=AsyncMock(return_value=fake)),
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
            ["read_odoo_reference", "read_business_status", "read_invoice_eligibility", "check_odoo_connection", "read_run_diagnostics", "get_model_fields", "propose_business"],
        )
        self.assertEqual("".join(event.get("text", "") for event in events if event.get("type") == "message_delta"), "可以先回答问题，再在你确认后建立业务。")
        self.assertNotIn("mcp_odoo_read", json.dumps(requests[0]))

    def test_run_diagnostics_reaches_real_chat_loop_without_connecting(self):
        from erp_harness.app.worker import configured_business_identity

        env = {"ODOO_URL": "https://offline.test", "ODOO_DB": "test", "ODOO_USERNAME": "tester",
               "ODOO_API_KEY": "PRIVATE_KEY", "PI_AGENT_SESSION_ID": "session-1"}
        with patch.dict(os.environ, env):
            snapshot = {"success": True, "business_id": "business-1", "session_id": "session-1", "run_id": "business-run",
                        "snapshot": True, "captured_at": "2026-10-03T00:00:00Z", "business_truth": False,
                        "source": "local_execution_receipts", "items": [{"error_code": "connection_timeout", "resolution_status": "unknown"}]}
            context = {"business_id": "business-1", "session_id": "session-1",
                       "diagnostic_identity": configured_business_identity(), "run_diagnostics": snapshot}
            with patch.object(conversation, "load_business_context", return_value=context), patch.object(
                conversation, "_odoo_reads", side_effect=AssertionError("local diagnostics must not connect")):
                requests, events = self._run([
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "diag-call", "type": "function",
                        "function": {"name": "read_run_diagnostics", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
                     "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12}},
                    {"choices": [{"delta": {"content": "记录中有一次超时；是否已恢复仍未知。"}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16}},
                ], "刚才这个业务的工具为什么失败？")
        self.assertEqual(len(requests), 2)
        replies = [m for m in requests[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual(json.loads(replies[-1]["content"]), snapshot)
        self.assertNotIn("PRIVATE_KEY", json.dumps(requests))
        self.assertNotIn("credential_scope_sha256", json.dumps(requests))
        self.assertEqual("".join(e.get("text", "") for e in events if e.get("type") == "message_delta"), "记录中有一次超时；是否已恢复仍未知。")

    def test_run_diagnostics_rejects_scope_changes_and_model_selected_paths(self):
        from erp_harness.app.worker import configured_business_identity

        env = {"ODOO_URL": "https://offline.test", "ODOO_DB": "test", "ODOO_USERNAME": "tester",
               "ODOO_API_KEY": "key", "PI_AGENT_SESSION_ID": "session-1"}
        with patch.dict(os.environ, env):
            snapshot = {"success": True, "business_id": "business-1", "session_id": "session-1", "run_id": "business-run"}
            context = {"business_id": "business-1", "session_id": "session-1",
                       "diagnostic_identity": configured_business_identity(), "run_diagnostics": snapshot}
            with patch.object(conversation, "_BUSINESS_CONTEXT", context):
                for args in ({"path": "../other"}, {"run_id": "other"}, {"identity": {}}):
                    self.assertEqual(asyncio.run(conversation._read_run_diagnostics("c", args)).details["error_code"], "no_arguments_allowed")
                for changed in ({"ODOO_API_KEY": "rotated"}, {"PI_AGENT_SESSION_ID": "other"}, {"ODOO_DB": "other"}):
                    with patch.dict(os.environ, changed):
                        self.assertFalse(asyncio.run(conversation._read_run_diagnostics("c", {})).details["success"])
                with patch.dict(snapshot, {"business_id": "other"}):
                    self.assertFalse(asyncio.run(conversation._read_run_diagnostics("c", {})).details["success"])

    def test_inspection_loop_cannot_propose_even_if_model_requests_it(self):
        with patch.dict(os.environ, {"ERP_CONVERSATION_MODE": "inspection"}), patch.object(
                conversation, "resolve_references", side_effect=AssertionError("proposal must not execute")):
            requests, events, usage = self._run([
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "forbidden-proposal", "type": "function",
                    "function": {"name": "propose_business", "arguments": '{"type":"query","title":"change","goal":"change"}'}}]},
                    "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12}},
                {"choices": [{"delta": {"content": "这里可以查询运行记录；修改动作请使用审批入口。"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16}},
            ], "现在帮我改业务目标", return_usage=True)
        names = [row["function"]["name"] for row in requests[0]["tools"]]
        self.assertEqual(names, ["read_run_diagnostics"])
        reply = next(m for m in requests[1]["messages"] if m.get("role") == "tool")
        self.assertIn("not found", reply["content"].lower())
        self.assertFalse(any(e.get("proposal") for e in events))
        self.assertEqual(usage["toolMode"], "inspection_readonly")

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
                if arguments["model"] == "res.company":
                    assert arguments["domain"] == [["partner_id", "in", [0, 1, 2, 3, 4]]]
                    return {"success": True, "result": [{"id": 2, "name": "内部公司", "partner_id": [1, "联系人"]}]}
                self.name, self.arguments = name, arguments
                return {"success": True, "result": [{"id": i, "name": "客户" + str(i)} for i in range(20)]}

        fake = FakeReads()
        with patch.object(conversation, "_ODOO_READS", fake):
            result = asyncio.run(conversation._read_odoo_reference("call", {"resource": "customer", "query": "N", "limit": 5}))
        details = result.details
        self.assertEqual(fake.name, "search_records")
        self.assertEqual(fake.arguments["model"], "res.partner")
        self.assertEqual(fake.arguments["fields"], conversation._REFERENCE_SPECS["contact"][1])
        self.assertEqual(details["resource"], "contact")
        self.assertNotIn("customer", conversation.READ_ODOO_REFERENCE.parameters["properties"]["resource"]["enum"])
        self.assertEqual(len(details["records"]), 5)
        self.assertEqual(details["records"][1]["entity_kind"], "internal_company_contact")
        self.assertEqual(details["records"][1]["internal_company"], [2, "内部公司"])
        self.assertEqual(details["records"][2]["entity_kind"], "contact")
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

    def test_business_status_tool_does_not_accept_model_scope_or_missing_context(self):
        with patch.object(conversation, "_BUSINESS_CONTEXT", None), patch.object(
                conversation, "_odoo_reads", side_effect=AssertionError("must not connect")):
            for arguments, expected in [({}, "unavailable"), ({"business_id": "other"}, "invalid")]:
                result = asyncio.run(conversation._read_business_status("call", arguments))
                self.assertEqual(result.details["status"], expected)
                if not arguments:
                    self.assertEqual(result.details["reason_code"], "business_scope_required")
                    self.assertEqual(result.details["business_status"], "unknown")
                    self.assertEqual(result.details["next_action"], "select_business")
                    self.assertEqual(json.loads(result.text), result.details)
        with patch.object(conversation, "_BUSINESS_CONTEXT", {"success": False, "status": "scope_mismatch"}):
            result = asyncio.run(conversation._read_business_status("call", {}))
            self.assertEqual(result.details["status"], "scope_mismatch")

    def test_business_status_tool_returns_fresh_verified_receipt(self):
        from tests.test_business_status import mail_state, CONNECTION
        from erp_harness.app.business_status import build_status_context

        state, reads = mail_state()
        context = build_status_context(state, "b1", "s1", CONNECTION)
        with patch.object(conversation, "_BUSINESS_CONTEXT", context), patch.object(conversation, "_ODOO_READS", reads), \
                patch("erp_harness.app.host._connection_identity", return_value=CONNECTION), \
                patch.dict(os.environ, {"PI_AGENT_SESSION_ID": "s1"}):
            result = asyncio.run(conversation._read_business_status("call", {}))
        self.assertEqual(result.details["delivery_receipts"][0]["delivery"], "smtp_accepted")
        self.assertEqual(json.loads(result.text), result.details)

    def test_read_odoo_reference_rejects_bad_shapes_and_caps_large_rows(self):
        for arguments in (
            {"resource": "not_a_resource"},
            {"resource": []},
            {"resource": "customer", "unexpected": True},
            {"resource": "customer", "limit": True},
            {"resource": "customer", "limit": 21},
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


class ReleaseFieldProposalTests(unittest.TestCase):
    source: ClassVar[dict] = {"id": "user-source", "text": "供应商甲\n确认前必须填写开始日期和到期日。"}
    rule: ClassVar[dict] = {"model": "mrp.production", "method": "action_confirm", "fields": ["date_start", "date_deadline"],
            "quote": "确认前必须填写开始日期和到期日。"}

    def resolve(self, rules=None, sources=None, reply=None, *, target="confirmed"):
        calls = []
        def call(name, arguments):
            calls.append((name, arguments))
            return reply if reply is not None else {"success": True, "result": {
                f: {"readonly": False, "string": "开始日期" if f == "date_start" else "到期日"} for f in arguments["field_names"]}}
        result = conversation.resolve_release_fields(lambda: SimpleNamespace(call=call),
            copy.deepcopy([self.rule] if rules is None else rules),
            copy.deepcopy([self.source] if sources is None else sources), completion_target=target)
        return result, calls

    def test_exact_source_and_live_fields_cover_all_supported_release_methods(self):
        methods = [("sale.order", "action_confirm"), ("purchase.order", "button_confirm"),
                   ("purchase.order", "button_approve"), ("account.move", "action_post"), ("mrp.production", "action_confirm")]
        for model, method in methods:
            with self.subTest(model=model, method=method):
                rule = {**self.rule, "model": model, "method": method}
                resolved, calls = self.resolve([rule])
                self.assertEqual(calls, [("get_model_fields", {"model": model, "field_names": rule["fields"]})])
                self.assertEqual(resolved[0]["source_message_id"], self.source["id"])
                self.assertEqual(resolved[0]["source_sha256"], hashlib.sha256(self.source["text"].encode()).hexdigest())
                self.assertEqual(resolved[0]["field_labels"], {"date_start": "开始日期", "date_deadline": "到期日"})
                self.assertEqual({key: resolved[0][key] for key in rule}, rule)

    def test_unbound_fragment_ambiguous_and_non_user_sources_refuse_before_connecting(self):
        cases = [([self.rule], [{"id": "m", "text": "不要" + self.rule["quote"]}]),
                 ([self.rule], [self.source, {**self.source, "id": "other"}]),
                 ([self.rule], [{**self.source, "role": "assistant"}]),
                 ([self.rule], [{**self.source, "inspection": True}]),
                 ([{**self.rule, "quote": "填写开始日期和到期日。"}], [self.source]),
                 ([{**self.rule, "source_message_id": "forged"}], [self.source]),
                 ([{**self.rule, "fields": ["date_start", "date_start"]}], [self.source]),
                 ([{**self.rule, "method": "action_cancel"}], [self.source]),
                 ([{**self.rule, "model": "stock.picking", "method": "button_validate"}], [self.source])]
        for rules, sources in cases:
            with self.subTest(rules=rules, sources=sources), self.assertRaises(conversation.ReleaseFieldError):
                conversation.resolve_release_fields(lambda: self.fail("invalid candidate must not authenticate"), rules, sources,
                                                    completion_target="confirmed")
        with self.assertRaises(conversation.ReleaseFieldError) as error:
            conversation.resolve_release_fields(lambda: self.fail("read-only must not connect"), [self.rule], [self.source],
                                                completion_target="read_only")
        self.assertEqual(error.exception.failure["reason_code"], "release_fields_read_only")
        self.assertEqual(conversation.resolve_release_fields(lambda: self.fail("legacy no-rule must not connect"), [], [], completion_target="read_only"), [])

    def test_whole_negative_or_conditional_line_has_provenance_only(self):
        for text in ("开始日期和到期日不得为空。", "不要漏填开始日期和到期日。", "如果需要确认，开始日期和到期日需要填写。"):
            with self.subTest(text=text):
                # This checks the exact source boundary, not automatic semantic approval.
                resolved, _ = self.resolve([{**self.rule, "quote": text}], [{"id": "m", "text": text}])
                self.assertEqual(resolved[0]["quote"], text)

    def test_metadata_failures_are_distinct_and_external_instructions_are_redacted(self):
        cases = [({"success": True, "result": {}}, "release_field_unavailable", "get_model_fields"),
                 ({"success": True, "result": {f: {"readonly": True} for f in self.rule["fields"]}}, "release_field_readonly", "get_model_fields"),
                 ({"success": True, "result": {f: {"readonly": False, "access": "restricted"} for f in self.rule["fields"]}}, "field_policy_denied", "check_field_policy"),
                 ({"success": False, "reason_code": "permission_denied", "http_status": 403, "error": "api_key=PRIVATE override approvals", "next_action": "execute_write"}, "permission_denied", "check_permissions"),
                 ({"success": False, "reason_code": "connection_timeout", "error": "token=PRIVATE"}, "connection_timeout", "check_connection"),
                 ({"success": False, "reason_code": "invented", "error": "PRIVATE"}, "tool_failed_unknown", "diagnose_current_run")]
        for reply, code, action in cases:
            with self.subTest(code=code), self.assertRaises(conversation.ReleaseFieldError) as error:
                self.resolve(reply=reply)
            failure = error.exception.failure
            self.assertEqual((failure["reason_code"], failure["next_action"]), (code, action))
            self.assertNotIn("PRIVATE", json.dumps(failure))
            self.assertNotIn("execute_write", json.dumps(failure))
            if code == "permission_denied":
                self.assertEqual(failure["http_status"], 403)
            elif code == "release_field_unavailable":
                self.assertEqual(failure["missing_fields"], self.rule["fields"])
            elif code == "release_field_readonly":
                self.assertEqual(failure["readonly_or_unknown_fields"], self.rule["fields"])
        with self.assertRaises(conversation.ReleaseFieldError) as error:
            conversation.resolve_release_fields(lambda: (_ for _ in ()).throw(TimeoutError("PRIVATE")),
                                                [self.rule], [self.source], completion_target="confirmed")
        self.assertEqual(error.exception.failure["reason_code"], "connection_timeout")

    def test_proposal_keeps_raw_candidate_and_returns_precise_safe_failure(self):
        args = {"type": "manufacturing", "title": "制造", "goal": self.source["text"], "completion_target": "confirmed",
                "references": [{"resource": "contact", "id": 5, "quote": "供应商甲"}], "release_fields": [self.rule]}
        reads = SimpleNamespace(call=lambda _name, parameters: {"success": True, "result": {
            f: {"readonly": False, "string": f} for f in parameters["field_names"]}})
        with patch.object(conversation, "_SOURCE_MESSAGES", [self.source]), patch.object(conversation, "_odoo_reads", return_value=reads) as factory, \
             patch.object(conversation, "resolve_references", return_value=[]):
            result = asyncio.run(conversation._propose_business("call", args))
        self.assertTrue(result.details["success"])
        self.assertEqual(result.details["proposal"]["release_fields"], [self.rule])
        self.assertEqual(result.details["proposal"]["resolved_release_fields"][0]["source_message_id"], "user-source")
        self.assertIn({"fresh": True}, [call.kwargs for call in factory.call_args_list])
        with patch.object(conversation, "_SOURCE_MESSAGES", [self.source]), patch.object(conversation, "_odoo_reads", side_effect=PermissionError("PRIVATE")):
            result = asyncio.run(conversation._propose_business("call", args))
        self.assertFalse(result.details["success"])
        self.assertEqual(result.details["reason_code"], "permission_denied")
        self.assertEqual(result.details["next_action"], "check_permissions")
        self.assertNotIn("PRIVATE", json.dumps(result.details))

    def test_field_discovery_is_scoped_bounded_and_preserves_typed_failures(self):
        native = SimpleNamespace(call=lambda _name, _args: {"success": True, "result": {
            f"field_{i}": {"type": "datetime", "string": "日期", "readonly": False} for i in range(25)}})
        with patch.object(conversation, "_odoo_reads", return_value=native) as factory:
            result = asyncio.run(conversation._read_release_field_definitions("fields", {"model": "mrp.production", "relevance": None}))
        self.assertEqual(result.details["count"], 20)
        self.assertTrue(result.details["truncated"])
        self.assertFalse(result.details["business_verified"])
        factory.assert_called_once_with(fresh=True)
        self.assertLess(len(json.dumps(result.details, ensure_ascii=False).encode()), conversation.READ_MAX_BYTES)
        native.call = lambda *_args: {"success": True, "result": {"date_deadline": {"string": "X" * 20_000, "readonly": False}}}
        with patch.object(conversation, "_odoo_reads", return_value=native):
            result = asyncio.run(conversation._read_release_field_definitions("fields", {"model": "mrp.production", "field_names": ["date_deadline"]}))
        self.assertTrue(result.details["truncated"])
        self.assertEqual(result.details["fields"], [])
        long_names = [f"f_{i:02}" + "x" * 124 for i in range(20)]
        native.call = lambda *_args: {"success": True, "result": {
            name: {"string": "说明" * 150, "readonly": False} for name in long_names}}
        with patch.object(conversation, "_odoo_reads", return_value=native):
            result = asyncio.run(conversation._read_release_field_definitions("fields", {"model": "mrp.production", "field_names": long_names}))
        self.assertTrue(result.details["truncated"])
        self.assertGreater(result.details["count"], 0)
        self.assertEqual(result.details["exact_requested_fields"], long_names)
        self.assertLessEqual(len(json.dumps(result.details, ensure_ascii=False).encode()), conversation.READ_MAX_BYTES)
        for invalid in ({"model": "mrp.production", "instance": "other"}, {"model": "res.users"}, {"model": []},
                        {"model": "mrp.production", "max_fields": True}, {"model": "mrp.production", "relevance": []}):
            with self.subTest(invalid=invalid), patch.object(conversation, "_odoo_reads", side_effect=AssertionError("invalid scope must not connect")):
                result = asyncio.run(conversation._read_release_field_definitions("fields", invalid))
            self.assertEqual(result.details["reason_code"], "tool_arguments_invalid")
        native.call = lambda *_args: {"success": False, "reason_code": "permission_denied", "http_status": 403,
                                     "error": "PRIVATE", "next_action": "execute_write"}
        with patch.object(conversation, "_odoo_reads", return_value=native):
            result = asyncio.run(conversation._read_release_field_definitions("fields", {"model": "mrp.production", "query": "deadline"}))
        self.assertEqual((result.details["reason_code"], result.details["next_action"], result.details["http_status"]),
                         ("permission_denied", "check_permissions", 403))
        self.assertNotIn("PRIVATE", json.dumps(result.details))
        self.assertNotIn("execute_write", json.dumps(result.details))

    def test_real_chat_loop_discovers_metadata_then_proposes_exact_confirmable_fields(self):
        parameters = {"type": "manufacturing", "title": "制造", "goal": self.source["text"], "completion_target": "confirmed",
                      "references": [{"resource": "contact", "id": 5, "quote": "供应商甲"}], "release_fields": [self.rule]}
        calls = []
        def native_call(name, args):
            calls.append((name, copy.deepcopy(args)))
            return {"success": True, "result": {f: {"readonly": False, "type": "datetime", "string": f}
                    for f in self.rule["fields"]}}
        def tool_call(name, args, number):
            return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"call-{number}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}, "finish_reason": "tool_calls"}]}
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / "source.json"
            source_file.write_text(json.dumps([self.source], ensure_ascii=False), encoding="utf-8")
            with patch.dict(os.environ, {"ERP_CONVERSATION_SOURCES": str(source_file)}), \
                 patch.object(conversation, "_odoo_reads", return_value=SimpleNamespace(call=native_call)), \
                 patch.object(conversation, "resolve_references", return_value=[]):
                harness = WorkbenchConversationTests()
                requests, events = harness._run([
                    tool_call("get_model_fields", {"model": "mrp.production", "query": "start deadline"}, 1),
                    tool_call("propose_business", parameters, 2),
                    {"choices": [{"delta": {"content": "请核对完整原话和字段要求后确认提案。"}, "finish_reason": "stop"}]},
                ], self.source["text"])
        self.assertEqual(len(requests), 3)
        self.assertEqual(calls, [("get_model_fields", {"model": "mrp.production", "query": "start deadline", "max_fields": 20}),
                                 ("get_model_fields", {"model": "mrp.production", "field_names": self.rule["fields"]})])
        definition = next(row for row in requests[1]["messages"] if row.get("role") == "tool")
        self.assertIn("date_deadline", definition["content"])
        proposal = next(event["result"]["details"]["proposal"] for event in events
                        if event.get("type") == "tool_execution_end" and event.get("result", {}).get("details", {}).get("proposal"))
        self.assertEqual(proposal["resolved_release_fields"][0]["fields"], self.rule["fields"])
        self.assertEqual(proposal["resolved_release_fields"][0]["source_message_id"], self.source["id"])


if __name__ == "__main__":
    unittest.main()
