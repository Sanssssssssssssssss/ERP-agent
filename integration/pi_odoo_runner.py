"""Run one Pi CodingSession with only tools from an Odoo MCP server."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
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

from integration.odoo_tools import route_tools
from integration.world_context import project_messages
from odoo_runtime.actions import NativeActions
from odoo_runtime.reads import NativeReads
from odoo_runtime.store import ActionStore
from odoo_runtime.world import WorldStore

CONTEXT_WINDOW = 128_000
MODEL_COMPAT = {
    "supportsReasoningEffort": True,
    "requiresReasoningContentOnAssistantMessages": True,
}
MCP_ONLY_POLICY = (
    "Use mcp_odoo tools for every Odoo operation. Do not access Odoo through "
    "shell commands, direct HTTP, XML-RPC, JSON-2, PostgreSQL, or Python libraries."
)


class RequestReceipts:
    """Keep actual model request bodies locally; never persist auth headers."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.number = 0

    async def before_provider_request(self, payload: object) -> object:
        self.number += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.number:04d}.request.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    async def before_provider_headers(self, headers: dict) -> dict:
        return headers

    async def after_provider_response(self, status: int, headers: dict) -> None:
        (self.directory / f"{self.number:04d}.response.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "request_ids": {
                        key: value
                        for key, value in headers.items()
                        if key.lower()
                        in {"x-request-id", "request-id", "x-generation-id"}
                    },
                }
            ),
            encoding="utf-8",
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
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--read-backend", choices=("mcp", "native"), default="mcp")
    parser.add_argument("--action-backend", choices=("mcp", "native"), default="mcp")
    parser.add_argument("--world-mode", choices=("off", "record", "project"), default="off")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL")
    if not api_key or not base_url or not model:
        raise RuntimeError("LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL are required")
    if args.max_turns is not None and args.max_turns < 1:
        raise ValueError("max-turns must be positive")
    world_mode = getattr(args, "world_mode", "off")
    if world_mode not in {"off", "record", "project"}:
        raise ValueError("world-mode must be off, record, or project")

    args.session_file.parent.mkdir(parents=True, exist_ok=True)
    args.usage_file.parent.mkdir(parents=True, exist_ok=True)
    provider_name = os.environ.get("LLM_PROVIDER", "openai-compatible")
    thinking = os.environ.get("LLM_THINKING_TYPE", "high")
    receipts = RequestReceipts(args.session_file.parent / "requests")
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=api_key,
            base_url=base_url,
            reasoning_effort=thinking,
            thinking_format="openai",
            compat=MODEL_COMPAT,
            provider_name=provider_name,
            timeout_seconds=180,
            max_retries=0,
            max_tokens=None,
            infer_api_from_model=False,
            provider_hooks=receipts,
        )
    )
    world = None
    actions = None
    try:
        async with McpToolSet(args.mcp_url) as toolset:
            native_runtime = None
            if (getattr(args, "read_backend", "mcp") == "native"
                    or getattr(args, "action_backend", "mcp") == "native"):
                os.environ["ODOO_REQUEST_LOG"] = str(args.session_file.parent / "odoo-native-requests.jsonl")
                os.environ["ODOO_REQUEST_BACKEND"] = "native"
                native_runtime = NativeReads.from_environment()
            native = native_runtime if getattr(args, "read_backend", "mcp") == "native" else None
            actions = (
                NativeActions(
                    native_runtime,
                    store=ActionStore(args.session_file.parent / "odoo-actions.sqlite3"),
                )
                if getattr(args, "action_backend", "mcp") == "native"
                else None
            )
            if actions is not None:
                actions.store.recover_interrupted()
            if world_mode != "off":
                try:
                    world = WorldStore(
                        args.session_file.parent / "world-observations.jsonl",
                        projection_path=args.session_file.parent / "world-projections.jsonl",
                    )
                except Exception as exc:  # noqa: BLE001 - optional world state must fail open
                    print(f"World initialization failed open: {type(exc).__name__}", file=sys.stderr)
            toolset.tools = route_tools(
                toolset.tools, args.session_file.parent / "tool-backends.jsonl", native, world, actions
            )
            cwd = Path.cwd()
            provider_config = OpenAICompatibleProviderConfig(
                name=provider_name,
                base_url=base_url,
                api_key_env="LLM_API_KEY",
                models=(model,),
                default_model=model,
                context_windows={model: CONTEXT_WINDOW},
                compat=MODEL_COMPAT,
                model_metadata={
                    model: ProviderModelMetadata(
                        reasoning=True,
                        context_window=CONTEXT_WINDOW,
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
                    owns_initial_provider=True,
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
            if world is not None and world_mode == "project":
                existing_transform = session._harness.config.transform_context

                async def project_context(messages, signal):
                    projected = project_messages(world, messages)
                    transformed = existing_transform(projected, signal) if existing_transform else projected
                    return await transformed if inspect.isawaitable(transformed) else transformed

                # The session owns refresh; only compose its existing hook for this run.
                session._harness.config.transform_context = project_context
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
                            "readBackend": getattr(args, "read_backend", "mcp"),
                            "actionBackend": getattr(args, "action_backend", "mcp"),
                            "worldMode": world_mode,
                            "toolNames": [tool.name for tool in session.tools],
                            "maxOutputTokens": None,
                            "requestReceipts": str(
                                args.session_file.parent / "requests"
                            ),
                            "maxTurns": args.max_turns,
                            "toolContractSha256": hashlib.sha256(json.dumps([
                                {"name": tool.name, "description": tool.description,
                                 "parameters": tool.parameters}
                                for tool in toolset.tools
                            ], sort_keys=True).encode()).hexdigest(),
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
                    "modelCalls": receipts.number,
                    "assistantEntries": len(assistant),
                    "worldMode": world_mode,
                    "actionBackend": getattr(args, "action_backend", "mcp"),
                }
                args.usage_file.write_text(json.dumps(usage), encoding="utf-8")
            finally:
                await session.aclose()
    finally:
        if actions is not None:
            try:
                summary = actions.store.summary()
                (args.session_file.parent / "action-ledger-summary.json").write_text(
                    json.dumps(summary, sort_keys=True), encoding="utf-8"
                )
            except Exception as exc:  # noqa: BLE001 - preserve the agent result on receipt failure
                print(f"Action ledger summary unavailable: {type(exc).__name__}", file=sys.stderr)
            finally:
                actions.store.close()
        if world is not None:
            try:
                world.write_summary(args.session_file.parent / "world-summary.json")
            except Exception as exc:  # noqa: BLE001 - derived receipts must not mask the run result
                print(f"Derived world summary unavailable: {type(exc).__name__}", file=sys.stderr)
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
