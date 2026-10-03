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
from erp_harness.app.request_receipts import ReceiptPersistenceError, RequestReceipts
from erp_harness.app.worker import child_environment
from erp_harness.context.paths import RuntimePaths
from erp_harness.context.resources import ResourcePaths
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.providers.provider import ProviderRequestRejected
from erp_harness.providers.retry import is_retryable_assistant_error
from erp_harness.runtime.harness import SimpleCancellationToken
from erp_harness.runtime.loop import run_agent_loop
from erp_harness.runtime.messages import AssistantMessage, UserMessage
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
