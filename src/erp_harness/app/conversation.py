"""Proposal-only Pi conversation worker used by the desktop erp_harness.app."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from erp_harness.runtime.messages import AssistantMessage
from erp_harness.runtime.storage.entries import CompactionEntry, MessageEntry
from erp_harness.runtime.storage import JsonlSessionStorage
from erp_harness.runtime.tools import AgentTool, AgentToolResult
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.providers.config import (
    OpenAICompatibleProviderConfig,
    ProviderModelMetadata,
    ProviderSettings,
)
from erp_harness.context.resources import ResourcePaths
from erp_harness.runtime.session import HarnessSession, SessionConfig

from erp_harness.app.stream_events import public_events

CONTEXT_WINDOW = 128_000
READ_MAX_ROWS = 5
READ_MAX_BYTES = 16_384
MODEL_COMPAT = {
    "supportsReasoningEffort": True,
    "requiresReasoningContentOnAssistantMessages": True,
}
CONVERSATION_POLICY = (
    "You are the ordinary conversation assistant for an ERP erp_harness.app. "
    "The erp_harness.app supports sales and invoicing (sale_invoice), purchasing "
    "(purchase), and linked sales-purchase-invoice workspaces "
    "(sale_purchase_invoice). It can read native Odoo data and, after approval "
    "for each write, carry out supported order, purchase, and invoice operations "
    "and read back their results. It can help prepare an order for an existing "
    "customer and product, or a purchase request with a supplier and lines, then "
    "confirm supported records or create and post an invoice when the user approves. "
    "Do not claim to have changed records before execution, and do not "
    "invent live ERP facts. For a vague sales or invoicing request, first ask for "
    "the customer and what they want done; pasted material or an existing order "
    "number is useful context. Do not ask for technical IDs or every field, and do "
    "not create an empty goal. An explicit request to browse pending orders "
    "read-only may be proposed without first asking for a customer. When the user "
    "already supplied customer, product, and quantity, ask only for the business "
    "target and commercial choices they must decide. Read Odoo price lists, customer "
    "profiles, addresses, and tax defaults during execution instead of asking for "
    "each one; accept an explicit request to use ERP defaults and never invent values. "
    "When the user explicitly asks to check a current customer, product, price, or payment "
    "term, use the read-only Odoo reference tool. Its result is an observed snapshot with "
    "a source and timestamp, not permission to write. If it fails or returns no match, say "
    "that the fact could not be verified. Do not call it merely because a sales word appears. "
    "Respect explicit material headers and user-provided meanings; do not ask the user to "
    "restate a value that is already labeled, and ask only when a genuinely necessary choice "
    "is missing or ambiguous. "
    "Keep the reply concise: a short summary and usually no more than two necessary "
    "questions. Do not promise "
    "payment, manufacturing, external attachment upload, or OCR, and do not claim "
    "a purchase is complete before the workspace has verified it. "
    "Ordinary discussion must not create a proposal. After a successful proposal "
    "tool call, tell the user briefly to click the card button '创建业务工作区', "
    "then click '开始执行'. Do not ask the user to reply with confirmation and do "
    "not imply that execution starts automatically. Never use shell, filesystem, "
    "network, MCP, or hidden reasoning as user-facing progress."
)


_REFERENCE_SPECS = {
    "customer": ("res.partner", ["id", "name", "display_name", "property_payment_term_id"]),
    "product": ("product.product", ["id", "name", "display_name", "default_code", "list_price"]),
    "payment_term": ("account.payment.term", ["id", "name"]),
    "sale_order": ("sale.order", ["id", "name", "state", "partner_id", "amount_total", "currency_id", "invoice_status"]),
    "purchase_order": ("purchase.order", ["id", "name", "state", "partner_id", "amount_total", "currency_id"]),
    "invoice": ("account.move", ["id", "name", "state", "move_type", "partner_id", "amount_total", "currency_id", "payment_state", "invoice_origin"]),
}
_ODOO_READS = None


def _odoo_reads():
    """Build the explicit, read-only Odoo facade for this worker."""
    global _ODOO_READS
    if _ODOO_READS is not None:
        return _ODOO_READS
    required = ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY")
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError("explicit Odoo connection settings are required")
    from erp_harness.erp.gateway import Json2ReadClient
    from erp_harness.erp.reads import NativeReads
    client = Json2ReadClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        username=os.environ["ODOO_USERNAME"], password=os.environ["ODOO_API_KEY"],
        api_key=os.environ["ODOO_API_KEY"], transport="json2", timeout=10,
    )
    _ODOO_READS = NativeReads(client)
    return _ODOO_READS


def _observed_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_reference_rows(rows, max_rows: int) -> tuple[list[dict], bool]:
    bounded: list[dict] = []
    truncated = False
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if len(bounded) >= max_rows:
            truncated = True
            break
        candidate = dict(row)
        # Leave room for the source, timestamp, model, and status envelope.
        if len(json.dumps([*bounded, candidate], ensure_ascii=False).encode("utf-8")) > READ_MAX_BYTES - 2_048:
            truncated = True
            break
        bounded.append(candidate)
    return bounded, truncated


async def _read_odoo_reference(_call_id, arguments, _signal=None, _on_update=None):
    values = dict(arguments or {})
    unknown = sorted(set(values) - {"resource", "query", "limit"})
    if unknown:
        error = "unsupported reference arguments"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    resource = values.get("resource")
    query = values.get("query", "")
    limit = values.get("limit", READ_MAX_ROWS)
    if not isinstance(resource, str) or resource not in _REFERENCE_SPECS:
        error = "resource must be customer, product, payment_term, sale_order, purchase_order, or invoice"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    if not isinstance(query, str) or len(query.strip()) > 200:
        error = "query must be at most 200 characters"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    if type(limit) is not int or not 1 <= limit <= READ_MAX_ROWS:
        error = f"limit must be an integer from 1 to {READ_MAX_ROWS}"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    model, fields = _REFERENCE_SPECS[resource]
    observed_at = _observed_at()
    try:
        result = _odoo_reads().call("search_records", {
            "model": model, "fields": fields, "query": query.strip() or None,
            "limit": min(limit + 1, READ_MAX_ROWS + 1), "offset": 0,
        })
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RuntimeError("native read was unavailable")
        raw_records = result.get("result")
        if not isinstance(raw_records, list) or any(not isinstance(row, dict) for row in raw_records):
            raise RuntimeError("native read returned malformed records")
        records, truncated = _bounded_reference_rows(raw_records[:limit], limit)
        truncated = truncated or len(raw_records) > limit
        payload = {"success": True, "status": "observed", "source": "native_odoo_read",
                   "observed_at": observed_at, "resource": resource, "model": model,
                   "count": len(records), "records": records, "truncated": truncated,
                   "may_have_more": truncated}
        if resource == "product":
            payload["price_semantics"] = "list_price only; customer pricelist, tax, and currency are not resolved"
    except Exception:
        payload = {"success": False, "status": "unavailable", "source": "native_odoo_read",
                   "observed_at": observed_at, "resource": resource, "model": model,
                   "verified": False, "error": "Odoo read could not be verified"}
    return AgentToolResult(content=json.dumps(payload, ensure_ascii=False), details=payload)


READ_ODOO_REFERENCE = AgentTool(
    name="read_odoo_reference",
    label="Read Odoo reference",
    description=(
        "Read a small, current Odoo reference snapshot only when the user explicitly asks "
        "to check a customer, product/price, payment term, order, or invoice. Fixed resources and fields; "
        "read-only, no writes, no arbitrary model or method. A failed read is unknown."
    ),
    parameters={
        "type": "object",
        "properties": {
            "resource": {"type": "string", "enum": ["customer", "product", "payment_term", "sale_order", "purchase_order", "invoice"]},
            "query": {"type": "string", "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": READ_MAX_ROWS},
        },
        "required": ["resource"],
        "additionalProperties": False,
    },
    execute_fn=_read_odoo_reference,
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


def _sum_usage_bucket(usages: list[object], name: str, *, empty: int | None = None) -> int | None:
    """Sum a bucket only when every participating receipt reported it.

    A provider-reported zero is meaningful.  A missing bucket remains unknown
    instead of being silently treated as zero and presented as a complete sum.
    """
    if not usages:
        return empty
    values = [getattr(usage, name, None) if usage is not None else None for usage in usages]
    if any(value is None for value in values):
        return None
    return sum(values)


def _message_usage(message: AssistantMessage) -> object | None:
    usage = getattr(message, "usage", None)
    if getattr(message, "stop_reason", None) in {"error", "aborted"}:
        fields = ("input", "cache_read", "cache_write", "output", "total_tokens", "reasoning")
        if usage is None or not any(getattr(usage, field, None) not in (None, 0) for field in fields):
            return None
    return usage


def _aggregate_usage(assistant: list[AssistantMessage], compactions: list[CompactionEntry]) -> dict[str, int | None]:
    assistant_usages = [_message_usage(message) for message in assistant]
    compaction_usages = [getattr(entry, "usage", None) for entry in compactions]
    usage = {
        # Keep model responses separate from compaction work.  The host reads
        # compaction entries independently and adds them to its public total.
        "input": _sum_usage_bucket(assistant_usages, "input"),
        "cache_read": _sum_usage_bucket(assistant_usages, "cache_read"),
        "cache_write": _sum_usage_bucket(assistant_usages, "cache_write"),
        "cache_write_1h": _sum_usage_bucket(assistant_usages, "cache_write_1h"),
        "output": _sum_usage_bucket(assistant_usages, "output"),
        "total": _sum_usage_bucket(assistant_usages, "total_tokens"),
        "reasoning": _sum_usage_bucket(assistant_usages, "reasoning"),
        "compaction_total": _sum_usage_bucket(compaction_usages, "total_tokens", empty=0),
        "compaction_input": _sum_usage_bucket(compaction_usages, "input", empty=0),
        "compaction_cache_read": _sum_usage_bucket(compaction_usages, "cache_read", empty=0),
        "compaction_cache_write": _sum_usage_bucket(compaction_usages, "cache_write", empty=0),
        "compaction_cache_write_1h": _sum_usage_bucket(compaction_usages, "cache_write_1h", empty=0),
        "compaction_output": _sum_usage_bucket(compaction_usages, "output", empty=0),
        "compaction_reasoning": _sum_usage_bucket(compaction_usages, "reasoning", empty=0),
        "compaction_calls": len(compactions),
    }
    return usage


async def _propose_business(_call_id, arguments, _signal=None, _on_update=None):
    values = dict(arguments or {})
    kind = values.get("type")
    if kind not in {"sale_invoice", "purchase", "sale_purchase_invoice"}:
        return AgentToolResult(
            content=json.dumps({"success": False, "error": "type must be sale_invoice, purchase, or sale_purchase_invoice"}),
            details={"success": False, "error": "unsupported business type"},
        )
    title = values.get("title")
    goal = values.get("goal")
    existing = values.get("existing_business_id")
    material_ids = values.get("material_ids")
    completion_target = values.get("completion_target")
    if completion_target is None:
        completion_target = "confirmed" if kind == "purchase" else "posted"
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        error = "title must be 1..200 characters"
    elif not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 20_000:
        error = "goal must be 1..20000 characters"
    elif existing is not None and (not isinstance(existing, str) or not existing.strip()):
        error = "existing_business_id must be a non-empty string"
    elif material_ids is not None and (not isinstance(material_ids, list) or len(material_ids) > 3 or
                                       any(not isinstance(item, str) or not item.strip() for item in material_ids)):
        error = "material_ids must contain at most 3 non-empty strings"
    elif completion_target not in {"read_only", "draft", "confirmed", "posted"}:
        error = "completion_target must be read_only, draft, confirmed, or posted"
    else:
        proposal = {"type": kind, "title": title.strip(), "goal": goal.strip(), "completion_target": completion_target}
        if existing is not None:
            proposal["existing_business_id"] = existing.strip()
        if material_ids is not None:
            proposal["material_ids"] = [item.strip() for item in material_ids]
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
        "Create a reviewable sale_invoice, purchase, or sale_purchase_invoice proposal "
        "after the user states a concrete business goal or explicitly asks to browse "
        "pending orders read-only. "
        "Ask for the smallest missing business context first; do not invent a goal "
        "or technical fields. This prepares the proposal and does not execute ERP "
        "writes; execution happens in the business workspace after approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["sale_invoice", "purchase", "sale_purchase_invoice"]},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "goal": {"type": "string", "minLength": 1, "maxLength": 20_000},
            "existing_business_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "material_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 3},
            "completion_target": {"type": "string", "enum": ["read_only", "draft", "confirmed", "posted"]},
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
    session = await HarnessSession.load(
        SessionConfig(
            provider=provider,
            owns_initial_provider=True,
            model=model,
            storage=JsonlSessionStorage(args.session_file),
            cwd=Path.cwd(),
            tools=[READ_ODOO_REFERENCE, PROPOSE_BUSINESS],
            max_turns=None,
            resource_paths=ResourcePaths(
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
            "runtime": "HarnessSession", "toolNames": [READ_ODOO_REFERENCE.name, PROPOSE_BUSINESS.name],
            "toolMode": "proposal_plus_readonly", "odooToolCount": 1,
        }, ensure_ascii=False), flush=True)
        # Use append-only journal entries rather than session.messages.  A
        # compaction replaces old context in the latter and would make a
        # count-based before/after slice lose part of this prompt's usage.
        entries_before = await session.session_entries()
        entry_ids_before = {entry.id for entry in entries_before}
        source = session.prompt(args.instruction_file.read_text(encoding="utf-8"))
        async for event in public_events(source):
            if hasattr(event, "model_dump_json"):
                line = event.model_dump_json(by_alias=True)
            elif isinstance(event, dict):
                line = json.dumps(event, ensure_ascii=False)
            else:
                continue
            print(line, flush=True)
        entries_after = await session.session_entries()
        new_entries = [entry for entry in entries_after if entry.id not in entry_ids_before]
        assistant = [entry.message for entry in new_entries
                     if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage)]
        compactions = [entry for entry in new_entries if isinstance(entry, CompactionEntry)]
        usage = {
            **_aggregate_usage(assistant, compactions),
            "modelCalls": receipts.number,
            "runtimeMode": "conversation",
            "toolMode": "proposal_plus_readonly",
            "total_scope": "assistant_responses_only",
            "input_semantics": "uncached",
        }
        args.usage_file.write_text(json.dumps(usage), encoding="utf-8")
    finally:
        await session.aclose()
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
