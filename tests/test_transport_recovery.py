"""A blank HTTP stream timeout must retry, or fail without a valid score."""
import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from erp_harness.app import runner as runner
from erp_harness.providers import OpenAICompatibleProvider
from erp_harness.runtime.session import SessionConfig


@pytest.mark.parametrize("recover", [True, False])
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_partial_stream_timeout_recovery(tmp_path: Path, recover: bool, error_type):
    async def check():
        calls = []

        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                raise error_type("")

        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                return httpx.Response(200, text=(
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"clock1",'
                    '"type":"function","function":{"name":"get_current_time","arguments":"{}"}}]},'
                    '"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n\n'
                    'data: [DONE]\n\n'
                ))
            if len(calls) == 2 or not recover:
                return httpx.Response(200, stream=BrokenStream())
            return httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}\n\n'
                'data: [DONE]\n\n'
            ))

        @asynccontextmanager
        async def no_odoo(_args):
            yield []

        original_load = runner.HarnessSession.load

        async def load_with_pruned_view(config):
            session = await original_load(config)

            class PrunedView:
                # Reproduce the accounting boundary after context compaction.
                @property
                def messages(self):
                    return session.messages[-1:]

                def __getattr__(self, name):
                    return getattr(session, name)

            return PrunedView()

        instruction = tmp_path / "instruction.txt"
        instruction.write_text("Reply done.", encoding="utf-8")
        args = SimpleNamespace(instruction_file=instruction,
                               session_file=tmp_path / "session.jsonl",
                               usage_file=tmp_path / "usage.json", max_turns=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch.object(runner, "_source_tools", no_odoo),
                patch.object(runner.HarnessSession, "load", side_effect=load_with_pruned_view),
                patch.object(runner, "OpenAICompatibleProvider", side_effect=lambda config:
                             OpenAICompatibleProvider(config, client=client)),
                patch.object(runner, "SessionConfig", side_effect=lambda **kwargs:
                             SessionConfig(**kwargs, retry_max_retries=1, retry_base_delay_ms=0)),
                patch.dict(os.environ, {"LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://unused.invalid/v1",
                                       "LLM_MODEL": "deepseek/test", "LLM_PROVIDER": "openai-compatible"}),
                redirect_stdout(io.StringIO()),
            ):
                if recover:
                    await runner.run(args)
                else:
                    with pytest.raises(RuntimeError, match="Provider run did not complete: Provider network error"):
                        await runner.run(args)
        assert len(calls) == 3
        rows = [json.loads(line) for line in args.session_file.read_text().splitlines()]
        errors = [row["message"] for row in rows if row.get("message", {}).get("stopReason") == "error"]
        assert errors and all(error_type.__name__ in row["errorMessage"] for row in errors)
        usage = json.loads(args.usage_file.read_text())
        assert usage["modelCalls"] == 3
        # The failed stream has unreported usage, even if a later request recovers.
        assert usage["input"] is None
        assert usage["output"] is None
        assert usage["unreportedUsageRequests"] == (1 if recover else 2)
        assert usage["lastStopReason"] == ("stop" if recover else "error")

    asyncio.run(check())
