"""Run one Pi CodingSession with only tools from an Odoo MCP server."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from pi_agent.mcp import McpToolSet
from pi_agent.messages import AssistantMessage
from pi_agent.session import JsonlSessionStorage
from pi_ai.env import OpenAICompatibleConfig
from pi_ai.openai_compatible import OpenAICompatibleProvider
from pi_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderModelMetadata,
    ProviderSettings,
)
from pi_coding.resources import PiResourcePaths
from pi_coding.session import CodingSession, CodingSessionConfig

CONTEXT_WINDOW = 128_000
MAX_OUTPUT_TOKENS = 16_384
MCP_ONLY_POLICY = (
    "Use mcp_odoo tools for every Odoo operation. Do not access Odoo through "
    "shell commands, direct HTTP, XML-RPC, JSON-2, PostgreSQL, or Python libraries."
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruction-file", type=Path, required=True)
    parser.add_argument("--usage-file", type=Path, required=True)
    parser.add_argument(
        "--session-file",
        type=Path,
        default=Path("/logs/agent/pi-agent-session.jsonl"),
    )
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--max-turns", type=int, default=100)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL")
    if not api_key or not base_url or not model:
        raise RuntimeError("LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL are required")
    if args.max_turns < 1:
        raise ValueError("max-turns must be positive")

    args.session_file.parent.mkdir(parents=True, exist_ok=True)
    args.usage_file.parent.mkdir(parents=True, exist_ok=True)
    provider_name = os.environ.get("LLM_PROVIDER", "openai-compatible")
    thinking = os.environ.get("LLM_THINKING_TYPE", "high")
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=api_key,
            base_url=base_url,
            reasoning_effort=thinking,
            thinking_format="openai",
            compat={"supportsReasoningEffort": True},
            provider_name=provider_name,
            timeout_seconds=180,
            max_retries=0,
            max_tokens=MAX_OUTPUT_TOKENS,
            infer_api_from_model=False,
        )
    )
    try:
        async with McpToolSet(args.mcp_url) as toolset:
            cwd = Path.cwd()
            provider_config = OpenAICompatibleProviderConfig(
                name=provider_name,
                base_url=base_url,
                api_key_env="LLM_API_KEY",
                models=(model,),
                default_model=model,
                context_windows={model: CONTEXT_WINDOW},
                compat={"supportsReasoningEffort": True},
                model_metadata={
                    model: ProviderModelMetadata(
                        reasoning=True,
                        context_window=CONTEXT_WINDOW,
                        max_tokens=MAX_OUTPUT_TOKENS,
                    )
                },
                timeout_seconds=180,
                max_retries=0,
                thinking_levels=(thinking,),
                thinking_models=(model,),
                thinking_default=thinking,
                thinking_parameter="reasoning_effort",
                thinking_defaults={model: thinking},
            )
            session = await CodingSession.load(
                CodingSessionConfig(
                    provider=provider,
                    model=model,
                    storage=JsonlSessionStorage(args.session_file),
                    cwd=cwd,
                    tools=list(toolset.tools),
                    max_turns=args.max_turns,
                    resource_paths=PiResourcePaths(
                        root=args.session_file.parent / ".pi-agent",
                        agents_root=args.session_file.parent / ".agents",
                        project_resources_enabled=False,
                    ),
                    session_id=os.environ.get("PI_AGENT_SESSION_ID"),
                    provider_name=provider_name,
                    provider_settings=ProviderSettings(providers=(provider_config,)),
                    runtime_provider_config=provider_config,
                    skills_enabled=False,
                    extensions_enabled=False,
                    append_system_prompt=MCP_ONLY_POLICY,
                    thinking_level=thinking,
                )
            )
            try:
                system_prompt_path = args.session_file.with_name(
                    "pi-agent-system-prompt.txt"
                )
                system_prompt_path.write_text(session.system_prompt, encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "type": "run_metadata",
                            "entrant": "pi-agent-odoo-mcp",
                            "model": model,
                            "reasoning": thinking,
                            "mcpToolCount": len(toolset.tools),
                            "toolNames": [tool.name for tool in session.tools],
                            "maxOutputTokens": MAX_OUTPUT_TOKENS,
                            "maxTurns": args.max_turns,
                            "runtime": "CodingSession",
                            "sessionFile": str(args.session_file),
                            "systemPromptFile": str(system_prompt_path),
                            "systemPromptSha256": hashlib.sha256(
                                session.system_prompt.encode("utf-8")
                            ).hexdigest(),
                        }
                    ),
                    flush=True,
                )
                instruction = args.instruction_file.read_text(encoding="utf-8")
                async for event in session.prompt(instruction):
                    if event.type != "message_update":
                        print(event.model_dump_json(by_alias=True), flush=True)

                assistant = [
                    message
                    for message in session.messages
                    if isinstance(message, AssistantMessage)
                ]
                usage = {
                    "input": sum(message.usage.input for message in assistant),
                    "output": sum(message.usage.output for message in assistant),
                    "cacheRead": sum(message.usage.cache_read for message in assistant),
                    "cacheWrite": sum(
                        message.usage.cache_write for message in assistant
                    ),
                    "reasoning": sum(
                        message.usage.reasoning or 0 for message in assistant
                    ),
                    "modelCalls": len(assistant),
                }
                args.usage_file.write_text(json.dumps(usage), encoding="utf-8")
            finally:
                await session.aclose()
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
