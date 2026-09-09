"""Proposal-only Pi conversation worker used by the desktop workbench."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from pi_agent.messages import AssistantMessage
from pi_agent.session import JsonlSessionStorage
from pi_agent.tools import AgentTool, AgentToolResult
from pi_ai.env import OpenAICompatibleConfig
from pi_ai.openai_compatible import OpenAICompatibleProvider
from pi_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderModelMetadata,
    ProviderSettings,
)
from pi_coding.resources import PiResourcePaths
from pi_coding.session import CodingSession, CodingSessionConfig

from integration.stream_events import public_events

CONTEXT_WINDOW = 128_000
MODEL_COMPAT = {
    "supportsReasoningEffort": True,
    "requiresReasoningContentOnAssistantMessages": True,
}
CONVERSATION_POLICY = (
    "You are the ordinary conversation assistant for an ERP workbench. "
    "The current business type is sales and invoicing (sale_invoice). The workbench "
    "can read native Odoo data and, after approval for each write, carry out the "
    "supported order and invoice operations and read back their results. It can "
    "help create an order for an existing customer and product, then confirm the "
    "order or create and post an invoice when the user approves those actions. "
    "Do not claim to have read Odoo or changed records before execution, and do not "
    "invent live ERP facts. For a vague sales or invoicing request, first ask for "
    "the customer and what they want done; pasted material or an existing order "
    "number is useful context. Do not ask for technical IDs or every field, and do "
    "not create an empty goal. An explicit request to browse pending orders "
    "read-only may be proposed without first asking for a customer. Do not promise "
    "payment, procurement, manufacturing, external attachment upload, or OCR. "
    "Ordinary discussion must not create a proposal. After a successful proposal "
    "tool call, tell the user briefly to click the card button '创建业务工作区', "
    "then click '开始执行'. Do not ask the user to reply with confirmation and do "
    "not imply that execution starts automatically. Never use shell, filesystem, "
    "network, MCP, or hidden reasoning as user-facing progress."
)


class _RequestReceipts:
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
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.number:04d}.response.json").write_text(
            json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8"
        )


async def _propose_business(_call_id, arguments, _signal=None, _on_update=None):
    values = dict(arguments or {})
    if values.get("type") != "sale_invoice":
        return AgentToolResult(
            content=json.dumps({"success": False, "error": "type must be sale_invoice"}),
            details={"success": False, "error": "type must be sale_invoice"},
        )
    title = values.get("title")
    goal = values.get("goal")
    existing = values.get("existing_business_id")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        error = "title must be 1..200 characters"
    elif not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 20_000:
        error = "goal must be 1..20000 characters"
    elif existing is not None and (not isinstance(existing, str) or not existing.strip()):
        error = "existing_business_id must be a non-empty string"
    else:
        proposal = {"type": "sale_invoice", "title": title.strip(), "goal": goal.strip()}
        if existing is not None:
            proposal["existing_business_id"] = existing.strip()
        return AgentToolResult(
            content=json.dumps({"success": True, "proposal": proposal}, ensure_ascii=False),
            details={"success": True, "proposal": proposal},
        )
    return AgentToolResult(
        content=json.dumps({"success": False, "error": error}, ensure_ascii=False),
        details={"success": False, "error": error},
    )


PROPOSE_BUSINESS = AgentTool(
    name="propose_business",
    label="Propose business",
    description=(
        "Create a reviewable sale_invoice proposal after the user states a concrete "
        "sales/invoicing goal or explicitly asks to browse pending orders read-only. "
        "Ask for the smallest missing business context first; do not invent a goal "
        "or technical fields. This prepares the proposal and does not execute ERP "
        "writes; execution happens in the business workspace after approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["sale_invoice"]},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "goal": {"type": "string", "minLength": 1, "maxLength": 20_000},
            "existing_business_id": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "required": ["type", "title", "goal"],
        "additionalProperties": False,
    },
    execute_fn=_propose_business,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruction-file", type=Path, required=True)
    parser.add_argument("--usage-file", type=Path, required=True)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL")
    if not api_key or not base_url or not model:
        raise RuntimeError("LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL are required")
    args.session_file.parent.mkdir(parents=True, exist_ok=True)
    args.usage_file.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    provider_name = os.environ.get("LLM_PROVIDER", "openai-compatible")
    thinking = os.environ.get("LLM_THINKING_TYPE", "high")
    receipts = _RequestReceipts(args.receipt_dir / "requests")
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
            infer_api_from_model=False,
            provider_hooks=receipts,
        )
    )
    provider_config = OpenAICompatibleProviderConfig(
        name=provider_name,
        base_url=base_url,
        api_key_env="LLM_API_KEY",
        models=(model,),
        default_model=model,
        context_windows={model: CONTEXT_WINDOW},
        compat=MODEL_COMPAT,
        model_metadata={model: ProviderModelMetadata(reasoning=True, context_window=CONTEXT_WINDOW)},
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
            cwd=Path.cwd(),
            tools=[PROPOSE_BUSINESS],
            max_turns=None,
            resource_paths=PiResourcePaths(
                root=args.receipt_dir / ".pi-agent",
                agents_root=args.receipt_dir / ".agents",
                project_resources_enabled=False,
            ),
            session_id=os.environ.get("PI_AGENT_SESSION_ID"),
            provider_name=provider_name,
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
            system=CONVERSATION_POLICY,
            skills_enabled=False,
            extensions_enabled=False,
            project_extensions_enabled=False,
        )
    )
    try:
        print(json.dumps({
            "type": "run_metadata", "kind": "conversation", "model": model,
            "runtime": "CodingSession", "toolNames": [PROPOSE_BUSINESS.name],
            "toolMode": "proposal_only", "odooToolCount": 0,
        }, ensure_ascii=False), flush=True)
        assistant_before = sum(isinstance(message, AssistantMessage) for message in session.messages)
        source = session.prompt(args.instruction_file.read_text(encoding="utf-8"))
        async for event in public_events(source):
            if hasattr(event, "model_dump_json"):
                line = event.model_dump_json(by_alias=True)
            elif isinstance(event, dict):
                line = json.dumps(event, ensure_ascii=False)
            else:
                continue
            print(line, flush=True)
        assistant = [message for message in session.messages if isinstance(message, AssistantMessage)][assistant_before:]
        usage = {
            "input": sum((getattr(message.usage, "input", 0) or 0) for message in assistant) or None,
            "output": sum((getattr(message.usage, "output", 0) or 0) for message in assistant) or None,
            "total": sum((getattr(message.usage, "total_tokens", 0) or 0) for message in assistant) or None,
            "reasoning": sum((getattr(message.usage, "reasoning", 0) or 0) for message in assistant) or None,
            "modelCalls": receipts.number,
            "runtimeMode": "conversation",
            "toolMode": "proposal_only",
        }
        args.usage_file.write_text(json.dumps(usage), encoding="utf-8")
    finally:
        await session.aclose()
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
