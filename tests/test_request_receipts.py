from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import httpx

from erp_harness.app.host import Workbench
from erp_harness.app.request_receipts import (
    ReceiptPersistenceError,
    RequestReceipts,
    _sum_usage_bucket,
)
from erp_harness.app.worker import child_environment
from erp_harness.context.paths import RuntimePaths
from erp_harness.context.resources import ResourcePaths
from erp_harness.providers.anthropic import AnthropicProvider
from erp_harness.providers.env import AnthropicConfig, OpenAICompatibleConfig
from erp_harness.providers.google import GoogleGenerativeAIProvider
from erp_harness.providers.mistral import MistralConversationsProvider
from erp_harness.providers.openai_codex import (
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
)
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.providers.provider import ProviderRequestRejected
from erp_harness.providers.retry import is_retryable_assistant_error
from erp_harness.runtime.harness import SimpleCancellationToken
from erp_harness.runtime.loop import run_agent_loop
from erp_harness.runtime.messages import AssistantMessage, Usage, UserMessage
from erp_harness.runtime.provider import provider_request_kind, scoped_provider_stream
from erp_harness.runtime.session import HarnessSession, SessionConfig
from erp_harness.runtime.storage import JsonlSessionStorage
from erp_harness.runtime.tools import AgentTool, AgentToolResult


def response(text="done", tool=False):
    delta = {"reasoning_content": "PRIVATE_THOUGHT", "content": text}
    if tool:
        delta["tool_calls"] = [{"index": 0, "id": "tool-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]
    body = {"choices": [{"delta": delta, "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
    return httpx.Response(200, text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                          headers={"content-type": "text/event-stream", "x-request-id": "provider-id", "authorization": "PRIVATE_HEADER"})


def provider(client, receipts=None, retries=0, api="openai-completions"):
    return OpenAICompatibleProvider(OpenAICompatibleConfig(api_key="test-only", base_url="https://unused.invalid/v1",
                                    api=api, max_retries=retries, max_retry_delay_seconds=0, provider_hooks=receipts), client=client)


def endpoint_response(api):
    if api == "openai-completions":
        return response()
    return httpx.Response(200, text=(
        'data: {"type":"response.output_text.delta","delta":"done"}\n\n'
        'data: {"type":"response.completed","response":{"status":"completed",'
        '"usage":{"input_tokens":4,"output_tokens":2,"total_tokens":6}}}\n\n'
    ), headers={"content-type": "text/event-stream"})


def rows(root):
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.meta.json"))]


COMPATIBILITY_APIS = ("anthropic", "google", "mistral", "codex")


def compatibility_provider(api, client, hooks, retries=1):
    common = {"base_url": "https://unused.invalid/v1", "provider_hooks": hooks,
              "max_retries": retries, "max_retry_delay_seconds": 0}
    if api == "anthropic":
        return AnthropicProvider(AnthropicConfig(api_key="test-only", **common), client=client)
    if api == "codex":
        async def credentials():
            return OpenAICodexCredentials(access_token="test-only", account_id="test")
        return OpenAICodexProvider(OpenAICodexConfig(credential_resolver=credentials, **common), client=client)
    cls = GoogleGenerativeAIProvider if api == "google" else MistralConversationsProvider
    return cls(OpenAICompatibleConfig(api_key="test-only", **common), client=client)


def compatibility_response(api):
    if api == "anthropic":
        body = (
            'data: {"type":"message_start","message":{"usage":{"input_tokens":4}}}\n\n'
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
            'data: {"type":"message_stop"}\n\n')
    elif api == "google":
        body = 'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}\n\n'
    elif api == "codex":
        body = ('data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"input_tokens":4,"output_tokens":2,"total_tokens":6}}}\n\n')
    else:
        body = 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


class RequestReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()

    async def asyncTearDown(self):
        self.capture.__exit__(None, None, None)
        self.tmp.cleanup()

    async def test_real_loop_links_messages_tools_rounds_without_changing_payload(self):
        sent = []
        def handler(request):
            sent.append(json.loads(request.content))
            return response(tool=len(sent) % 2 == 1)
        async def read(*_args):
            return AgentToolResult(content="read ok")
        tool = AgentTool(name="read", label="Read", description="Read", parameters={"type": "object", "properties": {}}, execute_fn=read)
        receipts = RequestReceipts(self.root)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def execute(hooks):
                source = run_agent_loop(provider=provider(client, hooks), model="test", system="unchanged", messages=[],
                                        prompts=[UserMessage(content="hello")], tools=[tool])
                if hooks:
                    return [event async for event in hooks.events(source)]
                return [event async for event in source]
            with patch.dict(os.environ, child_environment("session-real", "run-real"), clear=True):
                events = await execute(receipts)
            await execute(None)
        self.assertEqual(sent[:2], sent[2:])
        metadata = rows(self.root)
        self.assertEqual(len(metadata), 2)
        for index, row in enumerate(metadata):
            self.assertEqual((row["run_id"], row["session_id"]), ("run-real", "session-real"))
            self.assertEqual(row["request_kind"], "normal")
            self.assertEqual(row["status"], "completed")
            end = next(event for event in events if event.get("message_id") == row["message_id"] and event["type"] == "message_end")
            self.assertEqual((end["request_id"], end["round_id"]), (row["request_id"], row["round_id"]))
            self.assertEqual(json.loads((self.root / f"{index + 1:04d}.request.json").read_text()), sent[index])
            self.assertLessEqual(row["started_at"], row["headers_received_at"])
            self.assertLessEqual(row["headers_received_at"], row["finished_at"])
        tool_events = [event for event in events if event["type"] in {"tool_execution_start", "tool_execution_end"}]
        self.assertEqual(len(tool_events), 2)
        self.assertFalse(tool_events[-1]["isError"])
        self.assertTrue(all(event["request_id"] == metadata[0]["request_id"] for event in tool_events))
        public = "".join(path.read_text() for path in self.root.glob("*.output.json"))
        self.assertNotIn("PRIVATE_THOUGHT", public)
        self.assertNotIn("PRIVATE_HEADER", "".join(path.read_text() for path in self.root.glob("*.json")))
        self.assertEqual(provider_request_kind.get(), "unknown")

    async def test_http_retry_is_two_attempts_one_call_and_resume_does_not_overwrite(self):
        calls = 0
        def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(503, text="retry") if calls == 1 else response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            recorder = RequestReceipts(self.root)
            stream = provider(client, recorder, retries=1).stream_response(model="test", system="s", messages=[], tools=[])
            _ = [event async for event in scoped_provider_stream(stream, "normal")]
            first = rows(self.root)
            self.assertEqual([row["status"] for row in first], ["retry", "completed"])
            self.assertEqual([row["attempt"] for row in first], [1, 2])
            self.assertEqual(first[0]["call_id"], first[1]["call_id"])
            self.assertNotEqual(first[0]["request_id"], first[1]["request_id"])
            snapshot = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
            resumed = RequestReceipts(self.root)
            _ = [event async for event in provider(client, resumed).stream_response(model="test", system="s", messages=[], tools=[])]
        self.assertEqual(len(rows(self.root)), 3)
        self.assertEqual(rows(self.root)[2]["request_kind"], "unknown")
        self.assertTrue(all((self.root / name).read_bytes() == body for name, body in snapshot.items()))

    async def test_request_cap_blocks_retry_posts_in_both_endpoints(self):
        for api in ("openai-completions", "openai-responses"):
            for cap in (1, 2):
                with self.subTest(api=api, cap=cap):
                    root = self.root / api / str(cap)
                    receipts, sent = RequestReceipts(root, max_model_requests=cap), []
                    def handler(request, _sent=sent, _api=api):
                        _sent.append(request)
                        return httpx.Response(503, text="retry") if len(_sent) == 1 else endpoint_response(_api)
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        stream = scoped_provider_stream(provider(client, receipts, retries=1, api=api).stream_response(
                            model="test", system="s", messages=[], tools=[]), "normal")
                        if cap == 1:
                            with self.assertRaises(ProviderRequestRejected) as caught:
                                _ = [event async for event in stream]
                            self.assertFalse(is_retryable_assistant_error(AssistantMessage(
                                model="test", stop_reason="error", error_message=str(caught.exception))))
                        else:
                            events = [event async for event in stream]
                            self.assertEqual(events[-1].type, "done")
                            self.assertEqual(events[-1].message.usage.total_tokens, 6)
                    self.assertEqual(len(sent), cap)
                    self.assertEqual(receipts.number, cap)
                    self.assertEqual([row["status"] for row in rows(root)],
                                     ["retry"] if cap == 1 else ["retry", "completed"])

    async def test_compatibility_adapters_gate_every_http_and_network_retry_attempt(self):
        for api in COMPATIBILITY_APIS:
            for failure in ("http", "network"):
                for cap in (1, 2):
                    with self.subTest(api=api, failure=failure, cap=cap):
                        root = self.root / api / failure / str(cap)
                        receipts, sent = RequestReceipts(root, max_model_requests=cap), []
                        def handler(request, _sent=sent, _api=api, _failure=failure):
                            _sent.append(json.loads(request.content))
                            if len(_sent) == 1:
                                if _failure == "network":
                                    raise httpx.ConnectError("offline connection failure", request=request)
                                return httpx.Response(503, text="offline transient")
                            return compatibility_response(_api)
                        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                            stream = scoped_provider_stream(compatibility_provider(api, client, receipts).stream_response(
                                model="test", system="s", messages=[], tools=[]), "normal")
                            if cap == 1:
                                with self.assertRaises(ProviderRequestRejected):
                                    _ = [event async for event in stream]
                            else:
                                events = [event async for event in stream]
                                self.assertEqual(events[-1].type, "done")
                        metadata = rows(root)
                        self.assertEqual(len(sent), cap)
                        self.assertEqual(receipts.number, cap)
                        self.assertEqual([r["status"] for r in metadata], ["retry"] if cap == 1 else ["retry", "completed"])
                        if cap == 2:
                            self.assertEqual(sent[0], sent[1])
                            self.assertEqual(metadata[0]["call_id"], metadata[1]["call_id"])
                            self.assertEqual([r["attempt"] for r in metadata], [1, 2])
                            self.assertEqual(metadata[1]["usage"]["total_tokens"], 6 if api in {"anthropic", "codex"} else None)

    async def test_compatibility_stream_retries_share_the_physical_attempt_gate(self):
        retry_bodies = {
            "anthropic": 'data: {"type":"error","error":{"type":"overloaded_error","message":"offline overload"}}\n\n',
            "google": "",
            "codex": 'data: {"type":"response.failed","response":{"error":{"type":"service_unavailable_error","code":"server_is_overloaded","message":"offline overload"}}}\n\n',
        }
        for api, body in retry_bodies.items():
            for cap in (1, 2):
                with self.subTest(api=api, cap=cap):
                    root = self.root / api / "stream" / str(cap)
                    receipts, sent = RequestReceipts(root, max_model_requests=cap), []
                    def handler(request, _sent=sent, _api=api, _body=body):
                        _sent.append(request)
                        return httpx.Response(200, text=_body) if len(_sent) == 1 else compatibility_response(_api)
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        stream = compatibility_provider(api, client, receipts).stream_response(model="test", system="s", messages=[], tools=[])
                        if cap == 1:
                            with self.assertRaises(ProviderRequestRejected):
                                _ = [event async for event in stream]
                        else:
                            events = [event async for event in stream]
                            self.assertEqual(events[-1].type, "done")
                    self.assertEqual(len(sent), cap)
                    self.assertEqual([r["status"] for r in rows(root)], ["retry"] if cap == 1 else ["retry", "completed"])

    async def test_compatibility_retry_disk_faults_block_new_posts(self):
        write = Path.write_text
        for api in COMPATIBILITY_APIS:
            for fault in ("initial_request", "retry_request", "first_response"):
                with self.subTest(api=api, fault=fault):
                    root = self.root / api / fault
                    receipts, sent = RequestReceipts(root), []
                    target = {"initial_request": "0001.request.tmp", "retry_request": "0002.request.tmp",
                              "first_response": "0001.response.tmp"}[fault]
                    def write_fault(path, *args, _root=root, _target=target, **kwargs):
                        if path.parent == _root and path.name == _target:
                            raise OSError("PRIVATE_DISK_DETAIL 503")
                        return write(path, *args, **kwargs)
                    def handler(request, _sent=sent, _api=api):
                        _sent.append(request)
                        return httpx.Response(503, text="retry") if len(_sent) == 1 else compatibility_response(_api)
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        with patch.object(Path, "write_text", write_fault), self.assertRaises(ReceiptPersistenceError) as caught:
                            _ = [event async for event in compatibility_provider(api, client, receipts).stream_response(
                                model="test", system="s", messages=[], tools=[])]
                    self.assertNotIn("PRIVATE_DISK_DETAIL", str(caught.exception))
                    self.assertEqual(len(sent), 0 if fault == "initial_request" else 1)

    async def test_compatibility_output_fault_keeps_done_then_blocks_next_request(self):
        write = Path.write_text
        for api in COMPATIBILITY_APIS:
            with self.subTest(api=api):
                root = self.root / api
                receipts, sent = RequestReceipts(root), []
                def handler(request, _sent=sent, _api=api):
                    _sent.append(request)
                    return compatibility_response(_api)
                def write_fault(path, *args, _root=root, **kwargs):
                    if path.parent == _root and path.name == "0001.output.tmp":
                        raise OSError("PRIVATE_DISK_DETAIL")
                    return write(path, *args, **kwargs)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    adapter = compatibility_provider(api, client, receipts)
                    with patch.object(Path, "write_text", write_fault):
                        events = [event async for event in adapter.stream_response(model="test", system="s", messages=[], tools=[])]
                    self.assertEqual(events[-1].type, "done")
                    with self.assertRaises(ReceiptPersistenceError):
                        _ = [event async for event in adapter.stream_response(model="test", system="s", messages=[], tools=[])]
                self.assertEqual(len(sent), 1)

    async def test_compatibility_optional_observer_errors_stay_isolated(self):
        class Observers(RequestReceipts):
            async def before_provider_attempt(self, *args):
                await super().before_provider_attempt(*args)
                raise RuntimeError("ordinary observer failure")
            async def after_provider_attempt(self, *args):
                await super().after_provider_attempt(*args)
                raise RuntimeError("ordinary observer failure")
        for api in COMPATIBILITY_APIS:
            with self.subTest(api=api):
                receipts, sent = Observers(self.root / api), []
                def handler(request, _sent=sent, _api=api):
                    _sent.append(request)
                    return httpx.Response(503, text="retry") if len(_sent) == 1 else compatibility_response(_api)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    events = [event async for event in compatibility_provider(api, client, receipts).stream_response(
                        model="test", system="s", messages=[], tools=[])]
                self.assertEqual(events[-1].type, "done")
                self.assertEqual([r["status"] for r in rows(self.root / api)], ["retry", "completed"])

    async def test_compatibility_partial_transport_failure_is_not_replayed_and_usage_stays_unknown(self):
        for api in COMPATIBILITY_APIS:
            with self.subTest(api=api):
                root = self.root / api
                receipts, sent = RequestReceipts(root), []
                body = compatibility_response(api).content
                if api == "anthropic":
                    body = body.split(b'data: {"type":"message_delta"')[0]
                elif api == "codex":
                    body = body.split(b'data: {"type":"response.completed"')[0]
                elif api == "mistral":
                    body = body.split(b"data: [DONE]")[0]
                class Interrupted(httpx.AsyncByteStream):
                    def __init__(self, content):
                        self.content = content
                    async def __aiter__(self):
                        yield self.content
                        raise httpx.ReadError("offline partial response")
                def handler(request, _sent=sent, _body=body):
                    _sent.append(request)
                    return httpx.Response(200, stream=Interrupted(_body))
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    events = [event async for event in compatibility_provider(api, client, receipts).stream_response(
                        model="test", system="s", messages=[], tools=[])]
                self.assertEqual(events[-1].type, "error")
                self.assertEqual(len(sent), 1)
                self.assertEqual(rows(root)[0]["status"], "error")
                self.assertTrue(all(value is None for value in rows(root)[0]["usage"].values()))

    async def test_compatibility_authoritative_header_and_retry_observer_rejections_propagate(self):
        for api in COMPATIBILITY_APIS:
            for phase in ("headers", "retry"):
                with self.subTest(api=api, phase=phase):
                    class Rejected(RequestReceipts):
                        async def before_provider_headers(self, headers, _phase=phase):
                            if _phase == "headers":
                                raise ProviderRequestRejected("offline authoritative gate")
                            return await super().before_provider_headers(headers)
                        async def after_provider_attempt(self, _status):
                            raise ProviderRequestRejected("offline authoritative gate")
                    receipts, sent = Rejected(self.root / api / phase), []
                    def handler(request, _sent=sent):
                        _sent.append(request)
                        return httpx.Response(503, text="offline transient")
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        with self.assertRaises(ProviderRequestRejected):
                            _ = [event async for event in compatibility_provider(api, client, receipts).stream_response(
                                model="test", system="s", messages=[], tools=[])]
                    self.assertEqual(len(sent), 0 if phase == "headers" else 1)

    async def test_google_and_mistral_wire_usage_preserves_known_and_missing_buckets(self):
        for api in ("google", "mistral"):
            for variant in ("full", "partial", "invalid", "missing"):
                with self.subTest(api=api, variant=variant):
                    root = self.root / api / variant
                    receipts = RequestReceipts(root)
                    if api == "google":
                        normal = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
                        values = {"full": {"promptTokenCount": 100, "cachedContentTokenCount": 20,
                                             "candidatesTokenCount": 5, "thoughtsTokenCount": 3, "totalTokenCount": 108},
                                  "partial": {"totalTokenCount": 12},
                                  "invalid": {"promptTokenCount": True, "cachedContentTokenCount": False,
                                              "candidatesTokenCount": -1, "thoughtsTokenCount": 3.5, "totalTokenCount": "8"}}
                        key = "usageMetadata"
                    else:
                        normal = {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
                        values = {"full": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 20},
                                             "completion_tokens": 8, "total_tokens": 108},
                                  "partial": {"total_tokens": 12},
                                  "invalid": {"prompt_tokens": True, "prompt_tokens_details": {"cached_tokens": False},
                                              "completion_tokens": -1, "total_tokens": "8"}}
                        key = "usage"
                    body = "data: " + json.dumps(normal) + "\n\n"
                    if variant != "missing":
                        # Repeated cumulative usage and an empty trailing usage must not add or erase counts.
                        usage_line = "data: " + json.dumps({key: values[variant]}) + "\n\n"
                        body += usage_line * 2 + "data: " + json.dumps({key: {}}) + "\n\n"
                    if api == "mistral":
                        body += "data: [DONE]\n\n"
                    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _, _body=body: httpx.Response(200, text=_body))) as client:
                        events = [event async for event in compatibility_provider(api, client, receipts).stream_response(
                            model="test", system="s", messages=[], tools=[])]
                    self.assertEqual(events[-1].type, "done")
                    usage = rows(root)[0]["usage"]
                    if variant == "full":
                        self.assertEqual((usage["input"], usage["cache_read"], usage["output"], usage["total_tokens"]), (80, 20, 8, 108))
                        self.assertEqual(usage["reasoning"], 3 if api == "google" else None)
                    elif variant == "partial":
                        self.assertEqual(usage["total_tokens"], 12)
                        self.assertTrue(all(value is None for field, value in usage.items() if field != "total_tokens"))
                    else:
                        self.assertTrue(all(value is None for value in usage.values()))
                    self.assertIsNone(usage["cache_write"])
                    self.assertEqual(_sum_usage_bucket([events[-1].message.usage], "total_tokens"), usage["total_tokens"])

    async def test_mistral_stream_usage_configuration_respects_optout_and_payload_hook(self):
        for mode in ("default", "compat_optout", "hook_optout"):
            with self.subTest(mode=mode):
                class Capture(RequestReceipts):
                    async def before_provider_request(self, payload, _mode=mode):
                        if _mode == "hook_optout":
                            payload["stream_options"] = {"include_usage": False}
                        return await super().before_provider_request(payload)
                sent = []
                def handler(request, _sent=sent):
                    _sent.append(json.loads(request.content))
                    return compatibility_response("mistral")
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    adapter = MistralConversationsProvider(OpenAICompatibleConfig(api_key="test-only",
                        max_retries=0, provider_hooks=Capture(self.root / mode),
                        compat={"supportsUsageInStreaming": False} if mode == "compat_optout" else {}), client=client)
                    _ = [event async for event in adapter.stream_response(model="test", system="s", messages=[], tools=[])]
                if mode == "compat_optout":
                    self.assertNotIn("stream_options", sent[0])
                else:
                    self.assertEqual(sent[0]["stream_options"], {"include_usage": mode == "default"})

    async def test_compatibility_terminal_guards_prevent_incomplete_tool_dispatch(self):
        cases = [("google", reason, True, "{}", 1 if reason == "STOP" else 0)
                 for reason in ("STOP", "MAX_TOKENS", "SAFETY", "MALFORMED_FUNCTION_CALL", "UNKNOWN", None)]
        cases += [("mistral", reason, done, arguments, expected) for reason, done, arguments, expected in (
            ("stop", True, "{}", 1), ("tool_calls", True, "{}", 1),
            ("length", True, "{}", 0), ("model_length", True, "{}", 0),
            ("unknown", True, "{}", 0), (None, False, "{}", 0), ("stop", False, "{}", 0),
            (None, True, "{}", 0), ("stop", True, "{", 0), ("length", True, "{", 0))]
        for index, (api, reason, done, arguments, expected) in enumerate(cases):
            with self.subTest(api=api, reason=reason, done=done, arguments=arguments):
                dispatched, sent = [], []
                async def execute(*_args, _dispatched=dispatched):
                    _dispatched.append(True)
                    return AgentToolResult(content="offline only")
                tool = AgentTool(name="offline_tool", label="Offline", description="Offline terminal control",
                    parameters={"type": "object", "properties": {}}, execute_fn=execute)
                if api == "google":
                    body = {"candidates": [{"content": {"parts": [{"functionCall": {
                        "id": "call", "name": "offline_tool", "args": {}}}]}, "finishReason": reason}],
                        "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 20,
                            "candidatesTokenCount": 5, "thoughtsTokenCount": 3, "totalTokenCount": 108}}
                else:
                    body = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "type": "function",
                        "function": {"name": "offline_tool", "arguments": arguments}}]}, "finish_reason": reason}],
                        "usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 20},
                            "completion_tokens": 8, "total_tokens": 108}}
                wire = "data: " + json.dumps(body) + "\n\n" + ("data: [DONE]\n\n" if api == "mistral" and done else "")
                def handler(request, _sent=sent, _wire=wire, _api=api):
                    _sent.append(request)
                    return httpx.Response(200, text=_wire) if len(_sent) == 1 else compatibility_response(_api)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    events = [event async for event in run_agent_loop(provider=compatibility_provider(api, client, RequestReceipts(self.root / str(index))),
                        model="test", system="s", messages=[], prompts=[UserMessage(content="offline guard")], tools=[tool])]
                self.assertEqual(len(dispatched), expected)
                # Length rejects dispatch, then the existing loop may request a corrected response.
                continuation = reason in {"MAX_TOKENS", "length", "model_length"} and arguments == "{}"
                self.assertEqual(len(sent), 2 if continuation else 1 + expected)
                if continuation:
                    self.assertEqual(rows(self.root / str(index))[0]["stop_reason"], "length")
                usage = rows(self.root / str(index))[0]["usage"]
                self.assertEqual((usage["input"], usage["cache_read"], usage["output"], usage["total_tokens"]), (80, 20, 8, 108))
                if not expected and not continuation:
                    error = next(event.message for event in events if event.type == "message_end" and getattr(event.message, "stop_reason", None) == "error")
                    if reason in {"SAFETY", "MALFORMED_FUNCTION_CALL", "UNKNOWN", "unknown"}:
                        self.assertIn(reason, error.error_message)
                    elif api == "mistral" and not done:
                        self.assertIn("[DONE]", error.error_message)
                    elif reason is None:
                        self.assertIn("finish", error.error_message)
                    else:
                        self.assertIn("arguments", error.error_message)

    async def test_explicit_bad_argument_shapes_are_not_replaced_with_empty_arguments(self):
        absent = object()
        cases = [(api, value) for api in ("google", "mistral") for value in (None, [], True, 1)]
        cases += [("mistral", value) for value in (absent, "", "[]", "null")]
        cases += [("google", absent), ("google", {}), ("mistral", "{}")]
        for index, (api, value) in enumerate(cases):
            with self.subTest(api=api, value=value):
                dispatched, sent = [], []
                async def execute(_call, arguments, *_args, _dispatched=dispatched):
                    _dispatched.append(arguments)
                    return AgentToolResult(content="offline only")
                tool = AgentTool(name="offline_tool", label="Offline", description="Offline shape control",
                    parameters={"type": "object", "properties": {}}, execute_fn=execute)
                function = {"name": "offline_tool"}
                if value is not absent:
                    function["args" if api == "google" else "arguments"] = value
                if api == "google":
                    body = {"candidates": [{"content": {"parts": [{"functionCall": function}]}, "finishReason": "STOP"}]}
                else:
                    body = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": function}]}, "finish_reason": "tool_calls"}]}
                wire = "data: " + json.dumps(body) + "\n\n" + ("data: [DONE]\n\n" if api == "mistral" else "")
                def handler(request, _sent=sent, _wire=wire, _api=api):
                    _sent.append(request)
                    return httpx.Response(200, text=_wire) if len(_sent) == 1 else compatibility_response(_api)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    _ = [event async for event in run_agent_loop(provider=compatibility_provider(api, client, RequestReceipts(self.root / f"shape-{index}")),
                        model="test", system="s", messages=[], prompts=[UserMessage(content="offline guard")], tools=[tool])]
                expected = api == "google" and (value is absent or value == {}) or api == "mistral" and value == "{}"
                self.assertEqual(dispatched, [{}] if expected else [])
                self.assertEqual(len(sent), 2 if expected else 1)

    def test_terminal_error_usage_distinguishes_reported_zero_from_missing(self):
        from erp_harness.app.request_receipts import _message_usage

        reported = AssistantMessage(stop_reason="error", usage=Usage(output=0, total_tokens=0))
        missing = AssistantMessage(stop_reason="error")
        self.assertEqual(RequestReceipts._usage(reported)["output"], 0)
        self.assertEqual(RequestReceipts._usage(reported)["total_tokens"], 0)
        self.assertIsNone(RequestReceipts._usage(reported)["input"])
        self.assertEqual(_message_usage(reported).output, 0)
        self.assertIsNone(_message_usage(missing))
        self.assertTrue(all(value is None for value in RequestReceipts._usage(missing).values()))

    async def test_retry_capture_failures_block_new_posts_in_both_endpoints(self):
        write, replace = Path.write_text, Path.replace
        for api in ("openai-completions", "openai-responses"):
            for fault in ("request_write", "metadata_replace", "first_response_write"):
                with self.subTest(api=api, fault=fault):
                    root = self.root / api / fault
                    receipts, sent = RequestReceipts(root), []
                    def handler(request, _sent=sent, _api=api):
                        _sent.append(request)
                        return httpx.Response(503, text="retry") if len(_sent) == 1 else endpoint_response(_api)
                    def write_fault(path, *args, _root=root, _fault=fault, **kwargs):
                        selected = path.parent == _root and (
                            _fault == "request_write" and path.name == "0002.request.tmp"
                            or _fault == "first_response_write" and path.name == "0001.response.tmp")
                        if selected:
                            raise OSError("PRIVATE_STORAGE_DETAIL 503 timeout")
                        return write(path, *args, **kwargs)
                    def replace_fault(path, target, *, _root=root, _fault=fault):
                        if _fault == "metadata_replace" and path.parent == _root and path.name == "0002.meta.tmp":
                            raise PermissionError("PRIVATE_STORAGE_DETAIL 503 timeout")
                        return replace(path, target)
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        with (patch.object(Path, "write_text", write_fault),
                              patch.object(Path, "replace", replace_fault),
                              self.assertRaises(ReceiptPersistenceError)):
                            _ = [event async for event in provider(client, receipts, retries=1, api=api).stream_response(
                                model="test", system="s", messages=[], tools=[])]
                        with self.assertRaises(ReceiptPersistenceError):
                            _ = [event async for event in provider(client, receipts, retries=1, api=api).stream_response(
                                model="test", system="s", messages=[], tools=[])]
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(receipts.number, 1 if fault == "first_response_write" else 2)
                    self.assertFalse(any(row.get("http_status") == 200 for row in rows(root)))
        self.assertNotIn("PRIVATE_STORAGE_DETAIL", self.output.getvalue())

    async def test_generic_attempt_observation_failures_keep_retry_compatibility(self):
        class FailedObserver(RequestReceipts):
            async def before_provider_attempt(self, payload, attempt):
                await super().before_provider_attempt(payload, attempt)
                raise PermissionError("optional observation failed")
            async def after_provider_attempt(self, status):
                await super().after_provider_attempt(status)
                raise PermissionError("optional observation failed")
        for api in ("openai-completions", "openai-responses"):
            with self.subTest(api=api):
                root = self.root / api
                receipts, sent = FailedObserver(root), []
                def handler(request, _sent=sent, _api=api):
                    _sent.append(request)
                    return httpx.Response(503, text="retry") if len(_sent) == 1 else endpoint_response(_api)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    events = [event async for event in provider(client, receipts, retries=1, api=api).stream_response(
                        model="test", system="s", messages=[], tools=[])]
                self.assertEqual(len(sent), 2)
                self.assertEqual(events[-1].type, "done")
                self.assertEqual([row["status"] for row in rows(root)], ["retry", "completed"])

    async def test_authoritative_gate_failures_do_not_trigger_session_recovery(self):
        write = Path.write_text
        for fault in ("cap", "cap_503", "retry_request_write", "initial_sticky"):
            with self.subTest(fault=fault):
                root = self.root / fault
                receipts = RequestReceipts(root / "requests", max_model_requests={"cap": 1, "cap_503": 503}.get(fault))
                if fault == "cap_503":
                    # Resumed chronology near the cap must not look like HTTP503.
                    receipts.number = 502
                sent = []
                def handler(request, _sent=sent):
                    _sent.append(request)
                    return httpx.Response(503, text="retry")
                def write_fault(path, *args, _root=root, _fault=fault, **kwargs):
                    selected = path.parent == _root / "requests" and (
                        _fault == "retry_request_write" and path.name == "0002.request.tmp"
                        or _fault == "initial_sticky" and path.name == "previous.meta.tmp")
                    if selected:
                        raise OSError("PRIVATE_STORAGE_DETAIL 503 timeout")
                    return write(path, *args, **kwargs)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    session = await HarnessSession.load(SessionConfig(
                        provider=provider(client, receipts, retries=1), model="test", system="s", tools=[],
                        storage=JsonlSessionStorage(root / "session.jsonl"), cwd=root,
                        resource_paths=ResourcePaths(root=root / ".pi-agent", paths=RuntimePaths(
                            home=root / ".pi-agent", agents_home=root / ".agents"),
                            project_resources_enabled=False),
                        skills_enabled=False, extensions_enabled=False, auto_compact_enabled=False,
                        retry_enabled=True, retry_max_retries=3, retry_base_delay_ms=0))
                    try:
                        with patch.object(Path, "write_text", write_fault):
                            if fault == "initial_sticky":
                                receipts._write("previous", "meta", {})
                            events = [event async for event in session.prompt("offline test")]
                    finally:
                        await session.aclose()
                self.assertEqual(len(sent), 0 if fault == "initial_sticky" else 1)
                self.assertFalse(any(event.type == "auto_retry_start" for event in events))
                failures = [event.message for event in events if event.type == "message_end"
                            and isinstance(event.message, AssistantMessage) and event.message.stop_reason == "error"]
                self.assertEqual(len(failures), 1)
                self.assertNotIn("PRIVATE_STORAGE_DETAIL", failures[0].error_message)

    async def test_compaction_history_and_turn_prefix_are_two_explicit_requests(self):
        receipts = RequestReceipts(self.root)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response("summary"))) as client:
            session = SimpleNamespace(model="test", session_id="session", _retry_cancel_event=asyncio.Event(),
                                      _config=SimpleNamespace(retry_enabled=False),
                                      _harness=SimpleNamespace(config=SimpleNamespace(provider=provider(client, receipts))))
            for method in ("_complete_summary_prompt", "_generate_compaction_summary"):
                setattr(session, method, MethodType(getattr(HarnessSession, method), session))
            plan = SimpleNamespace(turn_prefix_messages=(UserMessage(content="prefix"),),
                                   messages_to_summarize=(UserMessage(content="history"),),
                                   previous_summary=None, read_files=(), modified_files=())
            await HarnessSession._generate_compaction_plan_summary(session, plan)
        metadata = rows(self.root)
        self.assertEqual([row["request_kind"] for row in metadata], ["compaction", "compaction"])
        self.assertTrue(all("message_id" not in row and "round_id" not in row for row in metadata))
        self.assertEqual(provider_request_kind.get(), "unknown")

    async def test_pre_send_request_and_metadata_failures_block_http(self):
        write, replace = Path.write_text, Path.replace
        for failure in ("request_write", "request_replace", "metadata_write", "metadata_replace"):
            with self.subTest(failure=failure):
                root = self.root / failure
                receipts, sent = RequestReceipts(root), []
                def write_fault(path, *args, _root=root, _failure=failure, **kwargs):
                    suffix = ".request.tmp" if _failure == "request_write" else ".meta.tmp"
                    if _failure.endswith("write") and path.parent == _root and path.name.endswith(suffix):
                        raise OSError("PRIVATE_STORAGE_DETAIL 503 timeout")
                    return write(path, *args, **kwargs)
                def replace_fault(path, target, *, _root=root, _failure=failure):
                    suffix = ".request.tmp" if _failure == "request_replace" else ".meta.tmp"
                    if _failure.endswith("replace") and path.parent == _root and path.name.endswith(suffix):
                        raise PermissionError("PRIVATE_STORAGE_DETAIL 503 timeout")
                    return replace(path, target)
                def handler(request, _sent=sent):
                    _sent.append(request)
                    return response()
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with (patch.object(Path, "write_text", write_fault),
                          patch.object(Path, "replace", replace_fault),
                          self.assertRaises(ReceiptPersistenceError) as caught):
                        _ = [event async for event in provider(client, receipts).stream_response(
                            model="test", system="s", messages=[], tools=[])]
                    # Repairing the disk alone does not silently resume this worker.
                    with self.assertRaises(ReceiptPersistenceError):
                        _ = [event async for event in provider(client, receipts).stream_response(
                            model="test", system="s", messages=[], tools=[])]
                self.assertEqual(sent, [])
                self.assertEqual(receipts.number, 1)
                self.assertIsInstance(caught.exception.__cause__, OSError)
                self.assertNotIn("PRIVATE_STORAGE_DETAIL", str(caught.exception))
                self.assertFalse(is_retryable_assistant_error(AssistantMessage(
                    model="test", stop_reason="error", error_message=str(caught.exception))))
        self.assertIn('"type": "receipt_warning"', self.output.getvalue())
        self.assertNotIn("PRIVATE_STORAGE_DETAIL", self.output.getvalue())

    async def test_post_send_receipt_failures_keep_terminal_and_block_next_request(self):
        write = Path.write_text
        for suffix in ("response", "meta", "output"):
            with self.subTest(suffix=suffix):
                root = self.root / suffix
                receipts, sent = RequestReceipts(root), []
                def handler(request, _root=root, _sent=sent):
                    # The prepared request and metadata must exist at the POST boundary.
                    self.assertTrue((_root / "0001.request.json").is_file())
                    self.assertEqual(rows(_root)[0]["status"], "running")
                    _sent.append(request)
                    return response()
                def write_fault(path, *args, _root=root, _sent=sent, _suffix=suffix, **kwargs):
                    if _sent and path.parent == _root and path.name.endswith(f".{_suffix}.tmp"):
                        raise OSError("post-send disk unavailable")
                    return write(path, *args, **kwargs)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with patch.object(Path, "write_text", write_fault):
                        events = [event async for event in scoped_provider_stream(
                            provider(client, receipts).stream_response(model="test", system="s", messages=[], tools=[]), "normal")]
                    self.assertEqual(events[-1].type, "done")
                    self.assertEqual(events[-1].message.text, "done")
                    self.assertEqual(events[-1].message.usage.total_tokens, 6)
                    with self.assertRaises(ReceiptPersistenceError):
                        _ = [event async for event in provider(client, receipts).stream_response(
                            model="test", system="s", messages=[], tools=[])]
                self.assertEqual(len(sent), 1)
                self.assertEqual(receipts.number, 1)

    async def test_compaction_capture_failure_blocks_history_and_prefix_posts(self):
        write = Path.write_text
        for stage in ("before_history", "after_history"):
            with self.subTest(stage=stage):
                root = self.root / stage
                receipts, sent = RequestReceipts(root), []
                def handler(request, _sent=sent):
                    _sent.append(request)
                    return response("summary")
                def write_fault(path, *args, _root=root, _sent=sent, _stage=stage, **kwargs):
                    selected = path.parent == _root and (
                        _stage == "before_history" and path.name.endswith(".request.tmp")
                        or _stage == "after_history" and _sent and path.name.endswith(".output.tmp"))
                    if selected:
                        raise OSError("compaction receipt disk unavailable")
                    return write(path, *args, **kwargs)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    session = SimpleNamespace(model="test", session_id="session", _retry_cancel_event=asyncio.Event(),
                                              _config=SimpleNamespace(retry_enabled=False),
                                              _harness=SimpleNamespace(config=SimpleNamespace(provider=provider(client, receipts))))
                    for method in ("_complete_summary_prompt", "_generate_compaction_summary"):
                        setattr(session, method, MethodType(getattr(HarnessSession, method), session))
                    plan = SimpleNamespace(turn_prefix_messages=(UserMessage(content="prefix"),),
                                           messages_to_summarize=(UserMessage(content="history"),),
                                           previous_summary=None, read_files=(), modified_files=())
                    with patch.object(Path, "write_text", write_fault), self.assertRaises(ReceiptPersistenceError):
                        await HarnessSession._generate_compaction_plan_summary(session, plan)
                self.assertEqual(len(sent), 0 if stage == "before_history" else 1)
                self.assertEqual(receipts.number, 1)
                self.assertEqual(next(iter(receipts._rows.values()))["request_kind"], "compaction")
                self.assertEqual(provider_request_kind.get(), "unknown")

    async def test_network_error_preserves_unknown_usage(self):
        def fail(request):
            raise httpx.ConnectError("offline", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            source = provider(client, RequestReceipts(self.root)).stream_response(model="test", system="s", messages=[], tools=[])
            events = [event async for event in scoped_provider_stream(source, "normal")]
        self.assertEqual(events[-1].type, "error")
        metadata = rows(self.root)[0]
        self.assertEqual(metadata["status"], "error")
        self.assertNotIn("http_status", metadata)
        self.assertTrue(all(value is None for value in metadata["usage"].values()))

    async def test_headers_are_not_end_and_consumer_close_closes_http(self):
        class SlowStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"a"},"finish_reason":null}]}\n\n'
                await asyncio.sleep(3600)
            async def aclose(self):
                self.closed = True
        body = SlowStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))) as client:
            source = scoped_provider_stream(provider(client, RequestReceipts(self.root)).stream_response(
                model="test", system="s", messages=[], tools=[]), "normal")
            await anext(source)
            metadata = rows(self.root)[0]
            self.assertIn("headers_received_at", metadata)
            self.assertNotIn("finished_at", metadata)
            await source.aclose()
        self.assertTrue(body.closed)
        self.assertEqual(rows(self.root)[0]["status"], "aborted")
        self.assertEqual(provider_request_kind.get(), "unknown")

    async def test_cancel_during_http_wait_closes_stream_and_resets_scope(self):
        waiting = asyncio.Event()
        class WaitingStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                waiting.set()
                await asyncio.sleep(3600)
                yield b""
            async def aclose(self):
                self.closed = True
        body = WaitingStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))) as client:
            source = scoped_provider_stream(provider(client, RequestReceipts(self.root)).stream_response(
                model="test", system="s", messages=[], tools=[]), "normal")
            await anext(source)  # Provider start follows response headers.
            pending = asyncio.create_task(anext(source))
            await waiting.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertTrue(body.closed)
        self.assertEqual(rows(self.root)[0]["status"], "aborted")
        self.assertEqual(provider_request_kind.get(), "unknown")

    async def test_loop_abort_clone_stays_unlinked_and_leaves_no_http_task(self):
        waiting = asyncio.Event()
        class WaitingStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                waiting.set()
                await asyncio.sleep(3600)
                yield b""
            async def aclose(self):
                self.closed = True
        body, signal = WaitingStream(), SimpleCancellationToken()
        receipts = RequestReceipts(self.root)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))) as client:
            async def run():
                return [event async for event in receipts.events(run_agent_loop(provider=provider(client, receipts),
                    model="test", system="s", messages=[], tools=[], prompts=[UserMessage(content="hello")], signal=signal))]
            task = asyncio.create_task(run())
            await waiting.wait()
            signal.cancel()
            events = await asyncio.wait_for(task, 1)
        terminal = next(event for event in events if event["type"] == "message_end" and event.get("message", {}).get("role") == "assistant")
        self.assertEqual(terminal["message"]["stopReason"], "aborted")
        self.assertNotIn("request_id", terminal)
        self.assertNotIn("message_id", rows(self.root)[0])
        self.assertEqual(rows(self.root)[0]["status"], "aborted")
        self.assertTrue(body.closed)
        self.assertEqual(provider_request_kind.get(), "unknown")

    async def test_actual_receipts_survive_host_ingestion_and_scoped_inspector_rpc(self):
        host = Workbench(self.root / "profile", repo=Path.cwd())
        host._launch = lambda *_args, **_kwargs: None
        try:
            session_id = host.create_session("trace integration")["id"]
            host.store.data["messages"][session_id].append({"id": "message", "role": "user", "text": "read only",
                "proposal": {"id": "proposal", "status": "pending", "type": "sale_invoice", "title": "Trace", "goal": "read only"}})
            business = host.confirm_business(session_id, "proposal", True)
            with patch.dict(os.environ, {"ODOO_URL": "https://odoo.test", "ODOO_DB": "test", "ODOO_USERNAME": "test"}):
                run = host.start_run(session_id, business["id"])
            recorder = RequestReceipts(host.store.root / "runs" / run["id"] / "requests")
            wire = io.StringIO()
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response("confirmed read"))) as client:
                with patch.dict(os.environ, child_environment(session_id, run["id"]), clear=True), contextlib.redirect_stdout(wire):
                    async for event in recorder.events(run_agent_loop(provider=provider(client, recorder), model="test",
                        system="s", messages=[], prompts=[UserMessage(content="read only")], tools=[])):
                        print(json.dumps(event))
            process = SimpleNamespace(stdout=io.StringIO(wire.getvalue()), stderr=io.StringIO(), wait=lambda: 0, poll=lambda: 0)
            host._consume_worker(run["id"], process, self.root / "missing-usage.json")
            scope = {"session_id": session_id, "business_id": business["id"], "run_id": run["id"]}
            summary = host.call("get_trace", {**scope, "summary_only": True})
            request = summary["requests"][0]
            self.assertEqual((request["association"], request["round"], request["kind"]), ("linked", 1, "normal"))
            self.assertEqual(request["usage"]["total"], 6)
            self.assertGreaterEqual(request["duration_ms"], 0)
            self.assertLessEqual(request["started_at"], request["ended_at"])
            detail = host.call("get_trace_detail", {**scope, "kind": "request", "id": request["id"]})
            self.assertEqual(detail["data"]["response"]["output"]["text"], "confirmed read")
            self.assertEqual(detail["data"]["response"]["output"]["usage"]["total_tokens"], 6)
            self.assertNotIn("PRIVATE_THOUGHT", json.dumps(detail))
        finally:
            host.close()
