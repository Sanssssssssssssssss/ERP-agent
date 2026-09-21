"""Budgeted static/dynamic tool-contract experiment.

No API call is made unless ``--run`` is supplied.  The native functions used by
this slice are pure report builders; the only model probe is a fixed fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from itertools import count
from pathlib import Path
from typing import Any

from erp_harness.tools.router import native_tool_catalog
from erp_harness.app.runner import (
    CONTEXT_WINDOW,
    CURRENT_TIME_TOOL,
    DYNAMIC_TOOL_POLICY,
    MCP_ONLY_POLICY,
    MODEL_COMPAT,
    RequestReceipts,
)
from erp_harness.erp._odoo_core.agent_tools import lookup_model_history_report
from erp_harness.erp._odoo_core.diagnostics import generate_json2_payload_report
from erp_harness.tools.dynamic_tools import DynamicToolController
from erp_harness.tools.sops import build_sop_tools
from pi_agent.messages import AssistantMessage, ToolCall, ToolResultMessage, Usage
from pi_agent.session import JsonlSessionStorage
from pi_agent.tools import AgentToolResult
from pi_ai.env import OpenAICompatibleConfig
from pi_ai.openai_compatible import OpenAICompatibleProvider
from pi_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderModelMetadata,
    ProviderSettings,
)
from pi_coding.resources import PiResourcePaths
from pi_coding.session import CodingSession, CodingSessionConfig

MAX_OUTPUT_TOKENS = 2048
DEFAULT_MAX_REQUESTS = 5
INVENTORY_FIXTURE = frozenset(
    {"account.move", "hr.employee", "hr.leave.report.calendar"}
)
MODULE_SLICE_PROMPT = """Run this exact module_slice and then give a short natural-language result.
Prepare the JSON-2 search_read request for sale.order with domain [["state", "=", "draft"]],
fields ["id", "name", "state"], and limit 2 by calling generate_json2_payload.
Also call lookup_model_history for account.invoice. These are previews/reports only: do not
start Odoo, MCP, HTTP, JSON-RPC, XML-RPC, PostgreSQL, or business writes. The final answer
must mention the two tool results and whether each succeeded. Do not invent records or API
responses. In dynamic mode choose the capability group needed for these calls; a newly
published tool is available on the next model turn.
"""


class BudgetExhausted(RuntimeError):
    pass


class BudgetReceipts(RequestReceipts):
    """Request receipts with a hard pre-HTTP model-call ceiling."""

    def __init__(self, directory: Path, maximum: int) -> None:
        super().__init__(directory)
        self.maximum = maximum
        self.http_200 = 0
        self.schema_bytes: list[int] = []
        self.budget_exhausted = False

    async def before_provider_request(self, payload: object) -> object:
        if self.number >= self.maximum:
            self.budget_exhausted = True
            raise BudgetExhausted(f"budget_exhausted: max_requests={self.maximum}")
        if isinstance(payload, dict):
            tools = payload.get("tools")
            self.schema_bytes.append(len(json.dumps(tools, ensure_ascii=False).encode()))
        else:
            self.schema_bytes.append(0)
        return await super().before_provider_request(payload)

    async def after_provider_response(self, status: int, headers: dict) -> None:
        if status == 200:
            self.http_200 += 1
        await super().after_provider_response(status, headers)


def _result(payload: dict[str, Any]) -> AgentToolResult:
    return AgentToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2), details=payload)


async def _fixture_search(_call_id: str, arguments: dict[str, Any], *_args) -> AgentToolResult:
    """Fixed module inventory used only by DynamicToolController's ir.model probe."""
    if arguments.get("model") != "ir.model":
        return _result({"success": False, "error": "offline_experiment_tool_disabled"})
    return _result({
        "success": True,
        "tool": "search_records",
        "model": "ir.model",
        "result": [{"model": name} for name in sorted(INVENTORY_FIXTURE)],
    })


def _pure_executor(name: str):
    async def execute(_call_id, arguments, *_args):
        from erp_harness.erp.capabilities import normalize_capability_arguments

        arguments = normalize_capability_arguments(name, dict(arguments))
        if name == "generate_json2_payload":
            return _result(generate_json2_payload_report(**arguments))
        if name == "lookup_model_history":
            return _result(lookup_model_history_report(arguments["name"]))
        return _result({"success": False, "error": "offline_experiment_tool_disabled"})

    return execute


def offline_tools(log_root: Path | None = None) -> list[AgentTool]:
    tools = []
    for tool in native_tool_catalog():
        name = tool.name.removeprefix("mcp_odoo_")
        if name == "search_records":
            tools.append(replace(tool, execute_fn=_fixture_search))
        elif name in {"generate_json2_payload", "lookup_model_history"}:
            tools.append(replace(tool, execute_fn=_pure_executor(name)))
        else:
            tools.append(tool)
    log_root = log_root or Path(tempfile.gettempdir()) / "pi-module-slice-check"
    tools.extend([
        CURRENT_TIME_TOOL,
        *build_sop_tools(log_root / "sop-events.jsonl", count(1).__next__),
    ])
    return tools


def _usage(messages: list[AssistantMessage]) -> dict[str, int | None]:
    def total(field: str) -> int | None:
        values = [getattr(message.usage, field) for message in messages
                  if field in message.usage.model_fields_set and getattr(message.usage, field) is not None]
        return sum(values) if len(values) == len(messages) else None

    fresh, cached = total("input"), total("cache_read")
    return {
        "fresh": fresh,
        "cached": cached,
        "output": total("output"),
        "reasoning": total("reasoning"),
        "total": total("total_tokens"),
    }


def _mcp_import_check() -> dict[str, Any]:
    roots = {"mcp", "mcp_types", "odoo_mcp"}
    imported = sorted(name for name in sys.modules if name.split(".", 1)[0] in roots)
    package_map = importlib.metadata.packages_distributions()
    distributions = sorted({dist for package, names in package_map.items()
                            if package.replace("_", "-") in {"mcp", "mcp-types", "odoo-mcp"}
                            for dist in names})
    return {"mcp_modules_imported": imported, "mcp_distributions_visible": distributions,
            "pass": not imported and not distributions}


def _validate_output(path: Path) -> None:
    if path.exists():
        raise ValueError(f"--output must name a new directory: {path}")
    path.mkdir(parents=True)


def _provider_config(model: str, provider_name: str, thinking: str, base_url: str, key: str, receipts: BudgetReceipts):
    provider = OpenAICompatibleProvider(OpenAICompatibleConfig(
        api_key=key, base_url=base_url, reasoning_effort=thinking,
        thinking_format="openai", compat=MODEL_COMPAT, provider_name=provider_name,
        timeout_seconds=300, max_retries=0, max_tokens=MAX_OUTPUT_TOKENS,
        infer_api_from_model=False, provider_hooks=receipts,
    ))
    config = OpenAICompatibleProviderConfig(
        name=provider_name, base_url=base_url, api_key_env="LLM_API_KEY",
        models=(model,), default_model=model, context_windows={model: CONTEXT_WINDOW},
        compat=MODEL_COMPAT,
        model_metadata={model: ProviderModelMetadata(reasoning=True, context_window=CONTEXT_WINDOW)},
        timeout_seconds=300, max_retries=0, thinking_levels=(thinking,),
        thinking_models=(model,), thinking_default=thinking,
        thinking_parameter="reasoning_effort", thinking_defaults={model: thinking},
    )
    return provider, config


async def run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_output(args.output)
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        if not os.environ.get(name):
            raise RuntimeError(f"{name} is required with --run")
    model, base_url = os.environ["LLM_MODEL"], os.environ["LLM_BASE_URL"].rstrip("/")
    thinking = os.environ.get("LLM_THINKING_TYPE", "high")
    provider_name = os.environ.get("LLM_PROVIDER", "openai-compatible")
    receipts = BudgetReceipts(args.output / "requests", args.max_requests)
    provider, provider_config = _provider_config(model, provider_name, thinking, base_url, os.environ["LLM_API_KEY"], receipts)
    tools = offline_tools(args.output)
    dynamic = None
    if args.mode == "dynamic":
        dynamic = DynamicToolController(tools, args.output / "dynamic-tools.jsonl", count(1).__next__)
        session_tools = list(dynamic.tools)
    else:
        session_tools = tools
    storage = JsonlSessionStorage(args.output / "session.jsonl")
    session = await CodingSession.load(CodingSessionConfig(
        provider=provider, owns_initial_provider=True, model=model, storage=storage,
        cwd=Path.cwd(), tools=session_tools, max_turns=None,
        resource_paths=PiResourcePaths(root=args.output / ".pi-agent", agents_root=args.output / ".agents", project_resources_enabled=False),
        provider_name=provider_name, provider_settings=ProviderSettings(providers=(provider_config,)),
        runtime_provider_config=provider_config, skills_enabled=False, extensions_enabled=False,
        retry_enabled=False, auto_compact_enabled=False,
        append_system_prompt=MCP_ONLY_POLICY + (DYNAMIC_TOOL_POLICY if dynamic else ""),
        thinking_level=thinking,
    ))
    if dynamic:
        dynamic.bind(session.stage_tools_for_next_turn)
    started = time.monotonic()
    error = None
    try:
        async with asyncio.timeout(300):
            async for _event in session.prompt(MODULE_SLICE_PROMPT):
                pass
    except Exception as exc:  # model/provider errors become partial receipts
        error = f"{type(exc).__name__}: {exc}"
    finally:
        await session.aclose()
    assistants = [message for message in session.messages if isinstance(message, AssistantMessage)]
    calls = [block for message in assistants for block in message.content if isinstance(block, ToolCall)]
    results = [message for message in session.messages if isinstance(message, ToolResultMessage)]
    successful = {message.tool_name for message in results if not message.is_error and isinstance(message.details, dict) and message.details.get("success") is True}
    validated = set()
    for message in results:
        details = message.details if isinstance(message.details, dict) else {}
        if message.tool_name == "mcp_odoo_generate_json2_payload" and details.get("success") is True:
            body = details.get("body", {})
            if (details.get("model") == "sale.order" and details.get("method") == "search_read"
                    and body.get("domain") == [["state", "=", "draft"]]
                    and body.get("fields") == ["id", "name", "state"] and body.get("limit") == 2):
                validated.add(message.tool_name)
        if (message.tool_name == "mcp_odoo_lookup_model_history"
                and details.get("success") is True and details.get("query") == "account.invoice"):
            validated.add(message.tool_name)
    final = assistants[-1] if assistants else None
    required = {"mcp_odoo_generate_json2_payload", "mcp_odoo_lookup_model_history"}
    mcp_check = _mcp_import_check()
    slice_pass = bool(final and final.stop_reason == "stop" and final.text.strip() and required <= successful and required <= validated and not error and not receipts.budget_exhausted and mcp_check["pass"])
    report = {
        "mode": args.mode, "module_slice": True, "slice_status": "passed" if slice_pass else "partial",
        "advertised_tool_count": len(tools), "initial_tool_count": len(session_tools),
        "baseline_commit": "98479c0", "source_commit": os.environ.get("PI_ODOO_SOURCE_COMMIT"),
        "prompt_sha256": hashlib.sha256(MODULE_SLICE_PROMPT.encode()).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3), "model": model, "reasoning": thinking,
        "model_calls": receipts.number, "http_200": receipts.http_200,
        "max_requests": args.max_requests, "budget_exhausted": receipts.budget_exhausted,
        "schema_bytes_per_round": receipts.schema_bytes, "usage": _usage(assistants),
        "unknown_usage_boundary": "null means provider did not report that field on every assistant response",
        "tool_calls": [{"name": call.name, "arguments": call.arguments} for call in calls],
        "tool_results": [{"name": message.tool_name, "is_error": message.is_error, "details": message.details} for message in results],
        "validated_tools": sorted(validated),
        "final": {"text": final.text if final else "", "stop_reason": final.stop_reason if final else None},
        "mcp_import_check": mcp_check, "error": error,
        "inventory_fixture": sorted(INVENTORY_FIXTURE), "native_functions": ["generate_json2_payload_report", "lookup_model_history_report"],
    }
    (args.output / "trace.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def _self_check_async() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        receipts = BudgetReceipts(root / "requests", 1)
        await receipts.before_provider_request({"tools": []})
        try:
            await receipts.before_provider_request({"tools": []})
        except BudgetExhausted:
            assert receipts.budget_exhausted
        else:
            raise AssertionError("budget gate did not fail closed")
        payload = generate_json2_payload_report(model="sale.order", method="search_read", kwargs={"domain": [["state", "=", "draft"]], "fields": ["id", "name", "state"], "limit": 2}, args=[])
        assert payload["model"] == "sale.order" and payload["body"]["domain"] == [["state", "=", "draft"]]
        mixed = generate_json2_payload_report(model="sale.order", method="search_read", args=[["state", "=", "draft"]], kwargs={"domain": [["state", "=", "draft"]], "fields": ["id", "name", "state"], "limit": 2})
        assert mixed["body"]["domain"] == [["state", "=", "draft"]] and mixed["body"]["limit"] == 2
        pure_result = await _pure_executor("generate_json2_payload")("check", {"model": "sale.order", "method": "search_read", "kwargs": {"domain": [["state", "=", "draft"]], "fields": ["id", "name", "state"], "limit": 2}})
        assert pure_result.details["body"]["fields"] == ["id", "name", "state"]
        history = lookup_model_history_report("account.invoice")
        assert history["success"] is True and history["query"] == "account.invoice"
        history_result = await _pure_executor("lookup_model_history")("check", {"name": "account.invoice"})
        assert history_result.details["query"] == "account.invoice"
        usage = _usage([AssistantMessage(content="ok", usage=Usage(input=100, cache_read=50, output=20, total_tokens=170))])
        assert usage == {"fresh": 100, "cached": 50, "output": 20, "reasoning": None, "total": 170}
        tools = offline_tools(root)
        dynamic = DynamicToolController(tools, root / "dynamic.jsonl", count(1).__next__)
        assert len(tools) == 44 and len(dynamic.tools) == 14 and _mcp_import_check()["pass"]
        dynamic.bind(lambda selected: None)
        configured = await next(tool for tool in dynamic.tools if tool.name == "configure_odoo_tools").execute("check", {"capabilities": ["migration"]})
        assert configured.details["success"] is True and "mcp_odoo_generate_json2_payload" in configured.details["published_tools"]
        existing = root / "existing"
        existing.mkdir()
        try:
            _validate_output(existing)
        except ValueError:
            pass
        else:
            raise AssertionError("existing output directory accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("static", "dynamic"), default="dynamic")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--run", action="store_true", help="explicitly permit real model API requests")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        asyncio.run(_self_check_async())
        print(json.dumps({"self_check": "passed"}))
        return 0
    if not args.run:
        parser.error("real API execution requires --run; use --self-check for offline checks")
    if args.output is None or args.max_requests < 1:
        parser.error("--run requires a new --output directory and positive --max-requests")
    report = asyncio.run(run(args))
    print(json.dumps({"mode": report["mode"], "slice_status": report["slice_status"], "model_calls": report["model_calls"], "http_200": report["http_200"], "output": str(args.output)}))
    return 0 if report["slice_status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
