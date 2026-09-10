from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from odoo_mcp import server, tools_read
from pi_agent.tools import AgentTool, AgentToolResult
from pi_ai.openai_compatible import OpenAICompatibleProvider

from integration import harbor_agent, pi_odoo_runner
from integration.world_context import expand_lossless_tables
from odoo_runtime.world import WorldStore
from odoo_runtime.world_tools import build_world_tools

ROOT = Path(__file__).resolve().parents[1]


class BaselineFixTest(unittest.TestCase):
    def test_native_install_excludes_mcp_and_system_site_packages(self):
        async def check():
            agent = SimpleNamespace(
                exec_as_agent=AsyncMock(),
                exec_as_root=AsyncMock(),
            )
            environment = SimpleNamespace(
                upload_dir=AsyncMock(),
                upload_file=AsyncMock(),
            )
            await harbor_agent._install_task_runtime(
                agent, environment, native_only=True
            )
            commands = " ".join(
                call.kwargs["command"] for call in agent.exec_as_root.await_args_list
            )
            uploads = " ".join(
                str(call.args) for call in environment.upload_dir.await_args_list
            )
            self.assertNotIn("--system-site-packages", commands)
            self.assertNotIn("odoo-mcp==", commands)
            self.assertNotIn("mcp==", commands)
            self.assertNotIn("pi-odoo-mcp-source", uploads)
            self.assertIn("wheelhouse-native-py312", uploads)
            self.assertIn("httpx[socks]==0.28.1", commands)

        asyncio.run(check())

    def test_current_time_is_a_tool_not_system_prompt_data(self):
        result = asyncio.run(pi_odoo_runner.CURRENT_TIME_TOOL.execute("clock", {}))
        payload = json.loads(result.text)
        self.assertEqual(
            payload["local_date"],
            datetime.fromisoformat(payload["local_datetime"]).date().isoformat(),
        )
        self.assertIsNotNone(datetime.fromisoformat(payload["utc_datetime"]).tzinfo)

    def test_disposable_bench_native_action_policy_is_explicit_and_closed(self):
        env = harbor_agent.bench_action_env()
        self.assertEqual(env["ODOO_MCP_ENABLE_WRITES"], "1")
        self.assertEqual(
            env["ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS"].split(","),
            list(harbor_agent.BENCH_SIDE_EFFECT_METHODS),
        )
        self.assertEqual(env["ODOO_MCP_AUDIT_LOG"], "/logs/agent/native-write-audit.jsonl")
        self.assertEqual(env["ODOO_MCP_ELICIT_WRITES"], "0")
        self.assertEqual(env["MCP_CHATTER_DIRECT"], "0")
        self.assertEqual(env["ODOO_MCP_ALLOW_UNKNOWN_METHODS"], "0")
        self.assertEqual(env["ODOO_ACTION_APPROVAL_MODE"], "bench-auto")

    def test_cancellation_waits_for_service_stop_even_after_a_second_cancel(self):
        async def check():
            started, stopping, release, stopped = (asyncio.Event() for _ in range(4))
            async def run_body(*_args):
                started.set()
                await asyncio.Event().wait()
            async def stop_service(service):
                self.assertEqual(service, "main")
                stopping.set()
                await release.wait()
                stopped.set()
            agent = SimpleNamespace(_run=run_body, _capture_bench_state=AsyncMock())
            environment = SimpleNamespace(stop_service=stop_service)
            run = inspect.unwrap(harbor_agent.PiAgentMcpBaseline.run)
            task = asyncio.create_task(run(agent, "test", environment, None))
            await started.wait()
            task.cancel()
            await stopping.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped.is_set())
            agent._run = AsyncMock()
            environment.stop_service = AsyncMock()
            await run(agent, "test", environment, None)
            environment.stop_service.assert_not_awaited()
            agent._run.side_effect = RuntimeError("nonzero child")
            with self.assertRaisesRegex(RuntimeError, "nonzero child"):
                await run(agent, "test", environment, None)
            environment.stop_service.assert_awaited_once_with("main")
        asyncio.run(check())

    def test_deadline_wraps_the_entire_pipeline_and_rejects_exhausted_budget(self):
        command = "printf '%s' 'quoted value' | tee /tmp/unused"
        import shlex
        argv = shlex.split(harbor_agent.deadline_command(command, 2))
        self.assertEqual(argv, ["timeout", "--signal=TERM", "--kill-after=5s", "2s", "bash", "-c", command])
        with self.assertRaises(TimeoutError):
            harbor_agent.deadline_command(command, 0)

    def test_mcp_default_honors_max_fields_and_exact_queries_keep_technical_fields(
        self,
    ):
        fields = {
            "id": {"type": "integer"},
            "name": {"type": "char", "required": True},
            "email": {"type": "char"},
            "chart_template": {
                "type": "selection",
                "selection": [[str(n), f"Template {n}"] for n in range(151)],
            },
        }
        client = SimpleNamespace(get_model_fields=lambda _: fields)
        with patch.object(
            tools_read, "_resolve_odoo", return_value=("default", client)
        ):
            bounded = server.get_model_fields(None, "res.company", max_fields=2)
            self.assertEqual(list(bounded["result"]), ["name", "email"])
            self.assertEqual(bounded["count"], 2)
            rejected = server.get_model_fields(None, "res.company", max_fields=100)
            self.assertFalse(rejected["success"])
            self.assertIn("between 1 and 30", rejected["error"])
            rejected = server.get_model_fields(None, "res.company", max_fields=0)
            self.assertFalse(rejected["success"])
            self.assertIn("between 1 and 30", rejected["error"])
            exact = server.get_model_fields(
                None, "res.company", field_names=["id", "chart_template"], max_fields=1
            )
            self.assertEqual(list(exact["result"]), ["id", "chart_template"])
            self.assertEqual(
                server.get_model_fields(None, "res.company", relevance=None)["count"], 4
            )
        advertised = next(
            tool
            for tool in asyncio.run(server.mcp.list_tools())
            if tool.name == "get_model_fields"
        )
        self.assertEqual(
            advertised.input_schema["properties"]["relevance"]["default"], "top"
        )
        self.assertEqual(
            advertised.input_schema["properties"]["max_fields"]["maximum"], 30
        )
        self.assertEqual(
            advertised.input_schema["properties"]["max_fields"]["minimum"], 1
        )
        self.assertIn("max_fields", advertised.description)

    def test_python_runner_preserves_instrumented_provider_and_sends_no_output_cap(
        self,
    ):
        async def check(root):
            requests = []

            async def execute(*args, **kwargs):
                return AgentToolResult(content=json.dumps({
                    "success": True, "result": {"id": 1, "name": "x" * 400},
                }))

            tool = AgentTool(
                name="mcp_odoo_read_record",
                label="Read",
                description="Read",
                parameters={"type": "object", "properties": {}},
                execute_fn=execute,
            )

            class ToolSet:
                def __init__(self, _url):
                    self.tools = [tool]

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            def handler(request):
                requests.append(json.loads(request.content))
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call1",
                                "type": "function",
                                "function": {"name": tool.name, "arguments": '{"model":"res.partner","record_id":1,"fields":["name"]}'},
                            },
                            {
                                "index": 1,
                                "id": "call2",
                                "type": "function",
                                "function": {"name": tool.name, "arguments": '{"model":"res.partner","record_id":1,"fields":["name"]}'},
                            },
                        ]
                    }
                    if len(requests) == 1
                    else {"content": "done"}
                )
                body = {
                    "choices": [
                        {
                            "delta": delta,
                            "finish_reason": "tool_calls"
                            if len(requests) == 1
                            else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
                return httpx.Response(
                    200,
                    text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                    headers={
                        "content-type": "text/event-stream",
                        "x-request-id": "test-id",
                        "authorization": "must-not-be-recorded",
                    },
                )

            instruction = root / "instruction.txt"
            instruction.write_text("Check health once.")
            args = SimpleNamespace(
                instruction_file=instruction,
                session_file=root / "session.jsonl",
                usage_file=root / "usage.json",
                mcp_url="http://unused.invalid",
                max_turns=3,
                world_mode="project",
            )
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                stdout = io.StringIO()
                with (
                    patch.object(pi_odoo_runner, "McpToolSet", ToolSet),
                    patch.object(
                        pi_odoo_runner,
                        "OpenAICompatibleProvider",
                        side_effect=lambda config: OpenAICompatibleProvider(
                            config, client=client
                        ),
                    ),
                    patch(
                        "pi_coding.session._create_runtime_provider",
                        side_effect=AssertionError("Configured provider was replaced"),
                    ),
                    patch.dict(
                        os.environ,
                        {
                            "LLM_API_KEY": "test-only",
                            "LLM_BASE_URL": "https://unused.invalid/v1",
                            "LLM_MODEL": "deepseek/test",
                            "LLM_PROVIDER": "openai-compatible",
                            "LLM_THINKING_TYPE": "high",
                            "PI_ODOO_SOURCE_COMMIT": "fixture-commit",
                            "ODOO_URL": "http://odoo.invalid",
                            "ODOO_DB": "bench",
                            "ODOO_USERNAME": "reader",
                            "ODOO_PASSWORD": "not-recorded",
                        },
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    await pi_odoo_runner.run(args)
            metadata = next(
                json.loads(line)
                for line in stdout.getvalue().splitlines()
                if line.startswith('{') and '"type": "run_metadata"' in line
            )
            self.assertEqual(metadata["commit_sha"], "fixture-commit")
            self.assertEqual(len(requests), 2)
            self.assertEqual(json.loads(args.usage_file.read_text())["modelCalls"], 2)
            for number, payload in enumerate(requests, 1):
                self.assertNotIn("max_tokens", payload)
                self.assertNotIn("max_completion_tokens", payload)
                self.assertEqual(
                    json.loads(
                        (root / "requests" / f"{number:04d}.request.json").read_text()
                    ),
                    payload,
                )
            self.assertIn(
                "get_current_time",
                {tool["function"]["name"] for tool in requests[0]["tools"]},
            )
            system = next(
                m for m in requests[0]["messages"] if m["role"] == "system"
            )
            self.assertNotIn("Current runtime date:", system["content"])
            prior = next(m for m in requests[1]["messages"] if m["role"] == "assistant")
            self.assertEqual(prior["reasoning_content"], "")
            tool_results = [m for m in requests[1]["messages"] if m["role"] == "tool"]
            self.assertEqual(len(tool_results), 2)
            self.assertIn("world_projection", tool_results[0]["content"])
            self.assertIn('"name": "' + "x" * 20, tool_results[1]["content"])
            self.assertEqual(json.loads((root / "world-summary.json").read_text())["projected_messages"], 1)
            self.assertNotIn(
                "must-not-be-recorded",
                (root / "requests/0001.response.json").read_text(),
            )

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_native_runner_projects_only_consumed_read_rows_in_record_and_project_modes(self):
        async def check(root, world_mode):
            requests = []
            rows = [
                {"id": index, "display_name": (
                    "VENDOR-UNIQUE-99 " * 20 if index == 99 else f"Partner {index} " + "x" * 90),
                 "default_code": f"P{index:03d}"}
                for index in range(1, 101)
            ]
            raw = {
                "success": True, "tool": "find_records", "count": len(rows),
                "result": rows, "fields_used": ["id", "display_name", "default_code"],
                "unavailable_fields": [], "has_more": False, "next_offset": None,
            }

            class NativeStub:
                instance = "default"

                def __init__(self):
                    self.calls = []

                def identity_context(self, instance=None):
                    identity = {
                        "instance": instance or self.instance, "url": "http://odoo.invalid",
                        "database": "bench", "username": "reader", "lang": "en_US",
                        "context": {}, "transport": "json2", "credential_scope_sha256": "fixture",
                    }
                    identity["identity_id"] = hashlib.sha256(json.dumps([
                        identity["instance"], identity["credential_scope_sha256"],
                    ], sort_keys=True).encode()).hexdigest()[:20]
                    return identity

                def call(self, name, arguments):
                    self.calls.append((name, arguments))
                    self_test.assertEqual(name, "find_records")
                    return raw

                def world_metadata(self, _name, _arguments):
                    return {}

                def world_rpc_evidence(self, _call_id):
                    return {"status": "fixture", "refs": []}

                def telemetry(self):
                    return {}

            class Store:
                def recover_interrupted(self):
                    pass

                def summary(self):
                    return {}

                def close(self):
                    pass

            native = NativeStub()
            self_test = self

            def native_actions(*_args, **_kwargs):
                return SimpleNamespace(store=Store())

            def native_capabilities(*_args, **_kwargs):
                return SimpleNamespace(close=lambda: None)

            def handler(request):
                requests.append(json.loads(request.content))
                if len(requests) == 1:
                    delta = {"tool_calls": [{
                        "index": 0, "id": "find-call", "type": "function",
                        "function": {
                            "name": "mcp_odoo_find_records",
                            "arguments": '{"model":"res.partner","domain":[["id",">",0]],"limit":20}',
                        },
                    }]}
                    finish_reason = "tool_calls"
                elif len(requests) < 5:
                    delta = {"tool_calls": [{
                        "index": 0, "id": f"time-call-{len(requests)}", "type": "function",
                        "function": {"name": "get_current_time", "arguments": "{}"},
                    }]}
                    finish_reason = "tool_calls"
                elif len(requests) == 5:
                    ref = "obs-000001-" + hashlib.sha256(b"find-call").hexdigest()[:10]
                    delta = {"tool_calls": [{"index": 0, "id": "search-call", "type": "function", "function": {"name": "search_observations", "arguments": '{"query":"VENDOR-UNIQUE-99"}'}}]}
                    finish_reason = "tool_calls"
                elif len(requests) == 6:
                    ref = "obs-000001-" + hashlib.sha256(b"find-call").hexdigest()[:10]
                    delta = {"tool_calls": [{"index": 0, "id": "read-call", "type": "function", "function": {"name": "read_observation", "arguments": json.dumps({"observation_ref": ref, "path": "$.result", "query": "VENDOR-UNIQUE-99", "fields": ["id", "display_name"], "limit": 1})}}]}
                    finish_reason = "tool_calls"
                else:
                    delta, finish_reason = {"content": "done"}, "stop"
                body = {
                    "choices": [{"delta": delta, "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                }
                return httpx.Response(
                    200, text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                    headers={"content-type": "text/event-stream", "x-request-id": "fixture"},
                )

            instruction = root / "instruction.txt"
            instruction.write_text("Find partners.")
            args = SimpleNamespace(
                instruction_file=instruction, session_file=root / "session.jsonl",
                usage_file=root / "usage.json", mcp_url="http://unused.invalid", max_turns=7,
                world_mode=world_mode, runtime_mode="native", read_backend="native",
                action_backend="native", capability_backend="native",
                tool_mode="dynamic", sop_mode="controlled",
            )
            original_hook = pi_odoo_runner.project_read_history
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                stdout = io.StringIO()
                with (
                    patch.object(pi_odoo_runner.NativeReads, "from_environment", return_value=native),
                    patch.object(pi_odoo_runner, "ActionStore", side_effect=lambda *_args, **_kwargs: Store()),
                    patch.object(pi_odoo_runner, "NativeActions", side_effect=native_actions),
                    patch.object(pi_odoo_runner, "NativeCapabilities", side_effect=native_capabilities),
                    patch.object(pi_odoo_runner, "project_read_history", wraps=original_hook) as history_hook,
                    patch.object(pi_odoo_runner, "OpenAICompatibleProvider", side_effect=lambda config: OpenAICompatibleProvider(config, client=client)),
                    patch("pi_coding.session._create_runtime_provider", side_effect=AssertionError("Configured provider was replaced")),
                    patch.dict(os.environ, {
                        "LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://unused.invalid/v1",
                        "LLM_MODEL": "deepseek/test", "LLM_PROVIDER": "openai-compatible",
                        "LLM_THINKING_TYPE": "high", "ODOO_URL": "http://odoo.invalid",
                        "ODOO_DB": "bench", "ODOO_USERNAME": "reader", "ODOO_PASSWORD": "not-recorded",
                    }),
                    contextlib.redirect_stdout(stdout),
                ):
                    await pi_odoo_runner.run(args)

            self.assertEqual([name for name, _arguments in native.calls], ["find_records"])
            self.assertGreaterEqual(history_hook.call_count, 7)
            self.assertEqual(len(requests), 7)
            self.assertEqual(json.loads((root / "requests" / "0001.request.json").read_text(encoding="utf-8")), requests[0])
            self.assertEqual(json.loads((root / "requests" / "0002.request.json").read_text(encoding="utf-8")), requests[1])
            self.assertEqual(json.loads((root / "requests" / "0003.request.json").read_text(encoding="utf-8")), requests[2])
            second = next(message for message in requests[1]["messages"] if message.get("role") == "tool")
            self.assertEqual(json.loads(second["content"]), raw)
            third = next(message for message in requests[4]["messages"] if message.get("tool_call_id") == "find-call")
            second_call = next(
                message for message in requests[1]["messages"]
                if message.get("role") == "assistant" and any(
                    call.get("id") == "find-call" for call in message.get("tool_calls", [])
                )
            )
            third_call = next(
                message for message in requests[2]["messages"]
                if message.get("role") == "assistant" and any(
                    call.get("id") == "find-call" for call in message.get("tool_calls", [])
                )
            )
            self.assertEqual(third_call, second_call)
            projected = json.loads(third["content"])
            self.assertEqual(third["tool_call_id"], "find-call")
            self.assertEqual(projected["world_observation"]["kind"], "externalized_read")
            self.assertNotIn("VENDOR-UNIQUE-99", third["content"])
            session_rows = [
                json.loads(line)["message"]
                for line in args.session_file.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("type") == "message"
            ]
            session_result = next(
                message for message in session_rows
                if message.get("role") == "toolResult" and message.get("toolCallId") == "find-call"
            )
            self.assertEqual(session_result["content"][0]["text"], json.dumps(raw, separators=(",", ":")))
            self.assertLess(len(third["content"].encode()), len(second["content"].encode()))
            search_result = next(message for message in requests[5]["messages"] if message.get("tool_call_id") == "search-call")
            self.assertEqual(json.loads(search_result["content"])["items"][0]["matches"][0]["path"], "$.result.98.display_name")
            read_result = next(message for message in requests[6]["messages"] if message.get("tool_call_id") == "read-call")
            self.assertEqual(json.loads(read_result["content"])["result"]["items"][0]["value"]["id"], 99)

        for world_mode in ("record", "project"):
            with self.subTest(world_mode=world_mode), tempfile.TemporaryDirectory() as directory:
                asyncio.run(check(Path(directory), world_mode))

    @unittest.skipUnless(
        shutil.which("node"), "Node required for native extension check"
    )
    def test_native_extension_removes_both_sdk_caps_without_recording_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            script = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import setup from './integration/native_pi_extension.mjs';
const handlers = new Map(); setup({on: (name, handler) => handlers.set(name, handler)});
const result = handlers.get('before_provider_request')({payload: {model: 'test', messages: [], max_tokens: 16384, max_completion_tokens: 16384}});
assert.equal('max_tokens' in result, false); assert.equal('max_completion_tokens' in result, false);
handlers.get('after_provider_response')({status: 200, headers: {'x-request-id': 'test', authorization: 'secret'}});
const raw = readFileSync(join(process.env.PI_BASELINE_REQUEST_DIR, '0001.response.json'), 'utf8');
assert(!raw.includes('secret')); console.log('NATIVE_EXTENSION_OK');
"""
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                cwd=ROOT,
                env={**os.environ, "PI_BASELINE_REQUEST_DIR": directory},
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("NATIVE_EXTENSION_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
