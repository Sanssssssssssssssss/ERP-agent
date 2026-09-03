from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from odoo_mcp import server, tools_read
from pi_agent.tools import AgentTool, AgentToolResult
from pi_ai.openai_compatible import OpenAICompatibleProvider

from integration import pi_odoo_runner

ROOT = Path(__file__).resolve().parents[1]


class BaselineFixTest(unittest.TestCase):
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
        self.assertIn("max_fields", advertised.description)

    def test_python_runner_preserves_instrumented_provider_and_sends_no_output_cap(
        self,
    ):
        async def check(root):
            requests = []

            async def execute(*args, **kwargs):
                return AgentToolResult(content='{"success":true}')

            tool = AgentTool(
                name="mcp_odoo_health_check",
                label="Health",
                description="Health",
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
                                "function": {"name": tool.name, "arguments": "{}"},
                            }
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
            )
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
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
                        },
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    await pi_odoo_runner.run(args)
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
            prior = next(m for m in requests[1]["messages"] if m["role"] == "assistant")
            self.assertEqual(prior["reasoning_content"], "")
            self.assertNotIn(
                "must-not-be-recorded",
                (root / "requests/0001.response.json").read_text(),
            )

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

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
