"""Proposal-only Pi conversation worker used by the desktop erp_harness.app."""
from __future__ import annotations

# 普通对话链路：用户文本 → HarnessSession → 只读查询 / 业务提案。
# 固定两个工具：read_odoo_reference、propose_business。没有写工具。
# 查询只能选择预设资源和字段。结果带来源、时间及截断标记。
# 提案交给 host 确认。提案成功既不表示 Odoo 已写入，也不授予后续写权限。
# 会话、请求回执、用量分别落盘；未报告的用量桶保留未知。

import argparse
import asyncio
import json
import os
import re
import unicodedata
from collections import Counter
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
from erp_harness.app.business import BUSINESS_TARGETS, COMPLETION_TARGETS, default_target, valid_target

CONTEXT_WINDOW = 128_000
READ_MAX_ROWS = 5
READ_PAGE_MAX = 20
READ_MAX_BYTES = 16_384
MODEL_COMPAT = {
    "supportsReasoningEffort": True,
    "requiresReasoningContentOnAssistantMessages": True,
}
CONVERSATION_POLICY = (
    "You are the ordinary conversation assistant for an ERP erp_harness.app. "
    "The erp_harness.app supports sales and invoicing (sale_invoice), purchasing "
    "(purchase), and linked sales-purchase-invoice workspaces "
    "(sale_purchase_invoice), inventory, manufacturing, payment, refund and reconciliation. "
    "It can read native Odoo data and, after approval "
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
    "external attachment upload or OCR, and do not claim "
    "a business is complete before the workspace has verified it. Payment and refund "
    "goals require the original document, amount, currency and company; bank reconciliation "
    "requires matching journal entries, not merely an invoice marked paid. "
    "Ordinary discussion must not create a proposal. After a successful proposal "
    "tool call, tell the user briefly to click the card button '创建业务工作区', "
    "then click '开始执行'. Do not ask the user to reply with confirmation and do "
    "not imply that execution starts automatically. Never use shell, filesystem, "
    "network, MCP, or hidden reasoning as user-facing progress."
)


_REFERENCE_SPECS = {
    "contact": ("res.partner", ["id", "name", "display_name", "email", "city", "company_id", "property_payment_term_id"]),
    "product": ("product.product", ["id", "name", "display_name", "default_code", "list_price"]),
    "payment_term": ("account.payment.term", ["id", "name"]),
    "sale_order": ("sale.order", ["id", "name", "state", "partner_id", "company_id", "date_order", "client_order_ref", "amount_total", "currency_id", "invoice_status"]),
    "purchase_order": ("purchase.order", ["id", "name", "state", "partner_id", "company_id", "date_order", "partner_ref", "amount_total", "currency_id"]),
    "company": ("res.company", ["id", "name", "currency_id"]),
    "tax": ("account.tax", ["id", "name", "amount", "amount_type", "type_tax_use", "company_id"]),
    "sale_line": ("sale.order.line", ["id", "order_id", "product_id", "product_uom_qty", "price_unit", "tax_ids"]),
    "purchase_line": ("purchase.order.line", ["id", "order_id", "product_id", "product_qty", "price_unit", "tax_ids"]),
    "invoice": ("account.move", ["id", "name", "state", "move_type", "partner_id", "amount_total", "currency_id", "payment_state", "invoice_origin"]),
    "transfer": ("stock.picking", ["id", "name", "state", "partner_id", "origin", "scheduled_date"]),
    "production": ("mrp.production", ["id", "name", "state", "product_id", "product_qty"]),
    "payment": ("account.payment", ["id", "name", "state", "partner_id", "amount", "currency_id", "is_matched"]),
}
_ODOO_READS = None
_KNOWLEDGE = None
_SOURCE_MESSAGES = []
_TASK_ENTITIES = None


def _task_entities(reads, source_text, knowledge):
    """Recall literal user names outside the prompt; IDs retain their model namespace."""
    def normalized(text):
        return unicodedata.normalize("NFKC", text).casefold()
    source = normalized(source_text)
    result = reads.call("search_records", {"model": "res.company", "fields": ["id", "name", "partner_id"], "limit": 100})
    companies = result.get("result", []) if result.get("success") else []
    internal = {r["partner_id"][0] for r in companies if r.get("partner_id")}
    hints = [{"model": "res.company", "id": r["id"], "name": r["name"], "filter_field": "company_id", "contact_id": r["partner_id"][0]}
             for r in companies if normalized(r["name"]) in source]
    found = knowledge.search_knowledge(source_text, "res.partner", limit=20)
    if found.get("status") == "index_missing" or not set(_REFERENCE_SPECS["contact"][1]).issubset(found.get("coverage", {}).get("fields", [])):
        indexed = knowledge.index_knowledge("res.partner", fields=_REFERENCE_SPECS["contact"][1], full_refresh=True)
        if indexed.get("success"):
            found = knowledge.search_knowledge(source_text, "res.partner", limit=20)
    ids = [r["record_id"] for r in found.get("results", [])] if found.get("success") else []
    result = reads.call("search_records", {"model": "res.partner", "domain": [["id", "in", ids]], "fields": ["id", "name"], "limit": 20}) if ids else {}
    for row in result.get("result", []) if result.get("success") else []:
        if normalized(row["name"]) in source:
            hints.append({"model": "res.partner", "id": row["id"], "name": row["name"], "filter_field": "partner_id",
                          "entity_kind": "internal_company_contact" if row["id"] in internal else "contact"})
    # 候选提示只认原话中的完整名称；不把 BM25 排名当作身份授权。
    return hints[:20]


def resolve_references(reads, references, source_text):
    """Bind a quoted user name/reference to one live record, never digits inferred from it."""
    if not isinstance(references, list) or len(references) > 20:
        raise ValueError("references must contain at most 20 records")
    resolved = []
    for reference in references:
        if (not isinstance(reference, dict) or set(reference) - {"resource", "id", "quote", "purpose"}
                or not {"resource", "id", "quote"}.issubset(reference)
                or reference.get("purpose", "target") not in {"target", "source"}):
            raise ValueError("reference requires resource, id and an exact quote from the user")
        resource, record_id, quote = (reference[k] for k in ("resource", "id", "quote"))
        resource = "contact" if resource == "customer" else resource  # 旧提案只在入口兼容。
        if resource not in _REFERENCE_SPECS or type(record_id) is not int or record_id < 1 or not isinstance(quote, str) or not quote or quote not in source_text:
            raise ValueError("reference must cite the user's exact name or document reference")
        model, fields = _REFERENCE_SPECS[resource]
        names = [f for f in ("name", "default_code", "client_order_ref", "partner_ref", "email") if f in fields]
        domain = ["|"] * (len(names) - 1) + [[f, "=", quote] for f in names]
        result = reads.call("search_records", {"model": model, "domain": domain, "fields": fields, "limit": 2})
        rows = result.get("result", [])
        if not result.get("success") or len(rows) != 1 or rows[0].get("id") != record_id:
            raise ValueError("quoted target does not resolve uniquely to that ID in the current role; read or clarify it first")
        resolved.append({**reference, "model": model, "fields": rows[0]})
    return resolved


def _reference_query(resource, values):
    """Translate only the public read contract; NativeReads still enforces field ACLs."""
    model, allowed = _REFERENCE_SPECS[resource]
    fields = values.get("fields", allowed)
    domain = values.get("domain", [])
    order = values.get("order", "id asc")
    if (not isinstance(fields, list) or not fields or any(f not in allowed for f in fields)
            or not isinstance(domain, list) or len(domain) > 40 or not isinstance(order, str)):
        raise ValueError("fields/domain/order have invalid shapes or unsupported fields")
    for term in domain:
        if isinstance(term, str) and term in {"&", "|", "!"}:
            continue
        if (not isinstance(term, (list, tuple)) or len(term) != 3 or term[0] not in allowed
                or term[1] not in {"=", "!=", ">", ">=", "<", "<=", "in", "not in", "ilike", "not ilike"}):
            raise ValueError("domain must use advertised fields and Odoo filter operators")
    if any(not re.fullmatch(r"(?:" + "|".join(map(re.escape, allowed)) + r")(?:\s+(?:asc|desc))?", part.strip(), re.I)
           for part in order.split(",")):
        raise ValueError("order must use advertised fields followed by asc or desc")
    # 精确条件放 domain。分词只做文本交集，不能把金额/日期拼成关键词。
    query = values.get("query", "").strip()
    search_fields = [f for f in ("name", "default_code", "client_order_ref", "partner_ref", "email", "city", "origin") if f in allowed]
    if resource in {"sale_order", "purchase_order", "invoice"}:
        search_fields.append("partner_id")
    if query and not search_fields:
        raise ValueError("this resource requires domain filters, not free-text query")
    for token in query.split():
        domain = domain + ["|"] * (len(search_fields) - 1) + [[f, "ilike", token] for f in search_fields]
    return model, list(dict.fromkeys(["id", *fields])), domain, order


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
    # 入参不能指定任意 model、method 或 fields。资源名称映射由后端维护。
    # 查询失败返回 unavailable；空结果与无法查询必须区分。
    values = dict(arguments or {})
    unknown = sorted(set(values) - {"resource", "query", "limit", "offset", "domain", "fields", "order", "include_count", "match"})
    if unknown:
        error = "unsupported reference arguments"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    resource = values.get("resource")
    resource = "contact" if resource == "customer" else resource  # 旧 trace/会话仍可回读。
    query = values.get("query", "")
    limit = values.get("limit", READ_MAX_ROWS)
    if not isinstance(resource, str) or resource not in _REFERENCE_SPECS:
        error = "resource must be one of: " + ", ".join(_REFERENCE_SPECS)
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    if not isinstance(query, str) or len(query.strip()) > 200:
        error = "query must be at most 200 characters"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    if type(limit) is not int or not 1 <= limit <= READ_PAGE_MAX:
        error = f"limit must be an integer from 1 to {READ_PAGE_MAX}"
        payload = {"success": False, "status": "invalid", "error": error}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    try:
        model, fields, domain, order = _reference_query(resource, values)
        offset = values.get("offset", 0)
        include_count = values.get("include_count", False)
        match = values.get("match", "keyword")
        if type(offset) is not int or offset < 0 or type(include_count) is not bool or match not in {"keyword", "bm25"}:
            raise ValueError("invalid offset, include_count or match")
        if match == "bm25" and (not query.strip() or values.get("domain") or offset or include_count):
            raise ValueError("BM25 supplies candidates only; use structured reads for conditions, counts and complete sets")
    except (ValueError, TypeError) as exc:
        payload = {"success": False, "status": "invalid", "error": str(exc)}
        return AgentToolResult(content=json.dumps(payload), details=payload)
    observed_at = _observed_at()
    try:
        reads = _odoo_reads()
        coverage = None
        global _KNOWLEDGE, _TASK_ENTITIES
        from erp_harness.erp.knowledge import NativeKnowledge
        if match == "bm25" or _SOURCE_MESSAGES:
            if _KNOWLEDGE is None or _KNOWLEDGE.reads is not reads:
                _KNOWLEDGE = NativeKnowledge(reads)
                _TASK_ENTITIES = None
            if _SOURCE_MESSAGES and _TASK_ENTITIES is None:
                _TASK_ENTITIES = _task_entities(reads, "\n".join(m["text"] for m in _SOURCE_MESSAGES), _KNOWLEDGE)
        if match == "bm25":
            found = _KNOWLEDGE.search_knowledge(query, model, limit=limit)
            if found.get("status") == "index_missing":
                indexed = _KNOWLEDGE.index_knowledge(model, fields=_REFERENCE_SPECS[resource][1], full_refresh=True)
                if not indexed.get("success"):
                    raise RuntimeError("index refresh failed")
                found = _KNOWLEDGE.search_knowledge(query, model, limit=limit)
            if not found.get("success"):
                raise RuntimeError("knowledge read failed")
            domain = [["id", "in", [r["record_id"] for r in found.get("results", [])]]]
            coverage = {"coverage": found.get("coverage"), "freshness": found.get("freshness")}
        result = reads.call("search_records", {
            "model": model, "fields": fields, "domain": domain,
            "limit": limit + 1, "offset": offset, "order": order,
        })
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RuntimeError(str(result.get("error", "native read was unavailable")))
        raw_records = result.get("result")
        if not isinstance(raw_records, list) or any(not isinstance(row, dict) for row in raw_records):
            raise RuntimeError("native read returned malformed records")
        records, truncated = _bounded_reference_rows(raw_records[:limit], limit)
        truncated = truncated or len(raw_records) > limit
        payload = {"success": True, "status": "observed", "source": "native_odoo_read",
                   "observed_at": observed_at, "resource": resource, "model": model,
                   "count": len(records), "records": records, "truncated": truncated,
                   "may_have_more": truncated, "complete": not truncated and offset == 0 and match != "bm25",
                   "offset": offset, "next_offset": offset + len(records) if truncated and records else None,
                   "scope": {"domain": domain, "order": order, "fields": fields},
                   "read_more": "repeat with next_offset; narrow fields if a row exceeds the byte limit"}
        if _SOURCE_MESSAGES and _TASK_ENTITIES:
            payload["user_named_entities"] = _TASK_ENTITIES
            payload["entity_semantics"] = "Names recalled from the original user text. Internal ERP company scope uses company_id; its res.partner contact uses partner_id only when the user means that contact as counterparty. Digits in names are not IDs. These hints are not exhaustive."
        if resource == "contact":
            # res.partner 同时存客户、供应商、员工和内部公司联系人，不能凭资源别名判定角色。
            company_rows = reads.call("search_records", {"model": "res.company", "domain": [["partner_id", "in", [r["id"] for r in records]]],
                                                        "fields": ["id", "name", "partner_id"], "limit": READ_PAGE_MAX}) if records else {}
            companies = {r["partner_id"][0]: [r["id"], r["name"]] for r in company_rows.get("result", [])
                         if company_rows.get("success") and r.get("partner_id")}
            for record in records:
                record["entity_kind"] = "internal_company_contact" if record["id"] in companies else "contact"
                if record["id"] in companies:
                    record["internal_company"] = companies[record["id"]]
            payload["resource_semantics"] = "A contact can be a customer, supplier, employee or internal company contact; this lookup alone does not establish a customer relationship. Internal company ownership uses res.company IDs in company_id."
        if records and all("state" in row for row in records):
            payload["page_counts_by_state"] = dict(Counter(row["state"] for row in records))
        if coverage is not None:
            payload.update({**coverage, "match": "bm25", "complete": False, "notice": "ranked candidates; not an exhaustive answer"})
        if include_count:
            counted = reads.call("aggregate_records", {"model": model, "domain": domain, "group_by": [], "measures": ["__count"]})
            rows = counted.get("rows", [])
            count = rows[0].get("__count") if counted.get("success") and len(rows) == 1 else None
            payload["total_count"] = count
            payload["count_status"] = "observed" if type(count) is int else "unavailable"
        if resource == "product":
            payload["price_semantics"] = "list_price only; customer pricelist, tax, and currency are not resolved"
        # 公司及其联系人可同名、同 ID。只给提示仍会让空的往来查询冒充公司统计。
        # 平铺 AND 查询可安全保留其他条件，对照两种关系；不替用户选含义。
        terms = values.get("domain", [])
        if (match == "keyword" and "company_id" in _REFERENCE_SPECS[resource][1]
                and terms and all(isinstance(t, (list, tuple)) for t in terms)
                and not any(t[0] == "company_id" for t in terms)):
            parties = [t for t in terms if t[0] == "partner_id"]
            company = next((e for e in (_TASK_ENTITIES or []) if e["model"] == "res.company"
                            and len(parties) == 1 and parties[0][1] == "=" and parties[0][2] == e.get("contact_id")), None)
            if company:
                scoped = {**values, "domain": [["company_id", "=", company["id"]] if t[0] == "partner_id" else t for t in terms],
                          "limit": min(limit, 3)}
                alternative = (await _read_odoo_reference(_call_id, scoped)).details
                # 两份证据均保留来源及范围。每份最多三行，继续读取仍用原分页接口。
                payload["records"] = records[:3]
                payload["count"] = len(payload["records"])
                if "page_counts_by_state" in payload:
                    payload["page_counts_by_state"] = dict(Counter(r["state"] for r in payload["records"]))
                if len(records) > 3:
                    payload.update(truncated=True, may_have_more=True, complete=False, next_offset=offset + 3)
                payload = {"success": True, "status": "ambiguous_entity_scope", "complete": False,
                           "notice": "The named entity is both an internal ERP company and a contact. These queries answer different questions. Use order_company for orders belonging to that company, counterparty for orders trading with its contact. Resolve the user's meaning before giving a count; neither interpretation alone verifies intent.",
                           "interpretations": {"counterparty": payload, "order_company": alternative}}
                while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > READ_MAX_BYTES:
                    pages = [p for p in payload["interpretations"].values() if p.get("records")]
                    if not pages:
                        raise RuntimeError("query scope metadata exceeds the response byte limit")
                    page = max(pages, key=lambda p: len(json.dumps(p["records"])))
                    page["records"].pop()
                    page.update(count=len(page["records"]), truncated=True, may_have_more=True, complete=False,
                                next_offset=page["offset"] + len(page["records"]) if page["records"] else None)
                    if "page_counts_by_state" in page:
                        page["page_counts_by_state"] = dict(Counter(r["state"] for r in page["records"]))
    except Exception as exc:
        denied = any(word in str(exc).lower() for word in ("accesserror", "access denied", "permission", "policy denies", "restricted"))
        payload = {"success": False, "status": "permission_denied" if denied else "unavailable", "source": "native_odoo_read",
                   "observed_at": observed_at, "resource": resource, "model": model,
                   "verified": False, "error": "Odoo read could not be verified"}
    return AgentToolResult(content=json.dumps(payload, ensure_ascii=False), details=payload)


READ_ODOO_REFERENCE = AgentTool(
    name="read_odoo_reference",
    label="Read Odoo reference",
    description=(
        "Read current ERP facts. S... sales use sale_order; P... purchase orders use purchase_order. "
        "company means the internal document owner (company_id). contact searches all contacts (res.partner), "
        "including suppliers and internal company contacts; it does not establish a customer role. "
        "query is a name/reference or space-separated text fragments. Use domain for company, partner ID, "
        "state, amount and date conditions, order for sorting, include_count for the full matching count. "
        "A name's digits are not an ID: resolve the full customer/supplier name first. "
        "Use offset until next_offset is null for ALL records; prefer fields [id,name] for lists. "
        "Tax IDs are not percentages: read sale_line/purchase_line by order_id, then tax by id. "
        "match=bm25 is optional fuzzy candidate recall, never a complete set. No writes. "
        "Available fields by resource: " + json.dumps({k: v[1] for k, v in _REFERENCE_SPECS.items()})
    ),
    parameters={
        "type": "object",
        "properties": {
            "resource": {"type": "string", "enum": list(_REFERENCE_SPECS)},
            "query": {"type": "string", "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": READ_PAGE_MAX},
            "offset": {"type": "integer", "minimum": 0},
            "domain": {"type": "array", "description": "Odoo domain, e.g. [[\"partner_id\",\"=\",516],[\"amount_total\",\"<=\",2000]]. Fields must be advertised for this resource."},
            "fields": {"type": "array", "items": {"type": "string"}},
            "order": {"type": "string", "description": "e.g. date_order desc,id desc. Equal maximum amounts remain a business tie unless user specifies a tie-break."},
            "include_count": {"type": "boolean"},
            "match": {"type": "string", "enum": ["keyword", "bm25"]},
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
    if not isinstance(kind, str) or kind not in BUSINESS_TARGETS:
        return AgentToolResult(
            content=json.dumps({"success": False, "error": "unsupported business type"}),
            details={"success": False, "error": "unsupported business type"},
        )
    title = values.get("title")
    goal = values.get("goal")
    existing = values.get("existing_business_id")
    material_ids = values.get("material_ids")
    completion_target = values.get("completion_target")
    if completion_target is None:
        completion_target = default_target(kind)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        error = "title must be 1..200 characters"
    elif not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 20_000:
        error = "goal must be 1..20000 characters"
    elif existing is not None and (not isinstance(existing, str) or not existing.strip()):
        error = "existing_business_id must be a non-empty string"
    elif material_ids is not None and (not isinstance(material_ids, list) or len(material_ids) > 3 or
                                       any(not isinstance(item, str) or not item.strip() for item in material_ids)):
        error = "material_ids must contain at most 3 non-empty strings"
    elif not valid_target(kind, completion_target):
        error = "completion_target is not supported for this business type"
    else:
        proposal = {"type": kind, "title": title.strip(), "goal": goal.strip(), "completion_target": completion_target}
        references = values.get("references", [])
        if _SOURCE_MESSAGES and completion_target != "read_only" and not references:
            payload = {"success": False, "error": "A write proposal needs a live user-quoted document or business party reference. Resolve it first; if the target is unspecified, clarify or propose a read-only lookup."}
            return AgentToolResult(content=json.dumps(payload), details=payload)
        if references:
            try:
                resolve_references(_odoo_reads(), references, "\n".join(m["text"] for m in _SOURCE_MESSAGES))
            except (ValueError, TypeError, RuntimeError) as exc:
                payload = {"success": False, "error": str(exc)}
                return AgentToolResult(content=json.dumps(payload, ensure_ascii=False), details=payload)
            proposal["references"] = references
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
        "Create a reviewable ERP business proposal "
        "after the user states a concrete business goal or explicitly asks to browse "
        "pending orders read-only. "
        "Ask for the smallest missing business context first; do not invent a goal "
        "or technical fields. This prepares the proposal and does not execute ERP "
        "writes; execution happens in the business workspace after approval."
        " Write proposals require references for the user-named existing document or customer/supplier; include the named company too. "
        "Use only IDs observed in Odoo and quote the exact user-supplied name/reference; digits inside names are not IDs. "
        "purpose=target binds the intended write subject; purpose=source is a reference document used for copying/derivation, not the write target. "
        "Preserve constraints exactly: no receiving means do not complete a receipt; confirmation may create pending transfers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(BUSINESS_TARGETS)},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "goal": {"type": "string", "minLength": 1, "maxLength": 20_000},
            "existing_business_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "material_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 3},
            "completion_target": {"type": "string", "enum": list(COMPLETION_TARGETS)},
            "references": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
                "resource": {"type": "string", "enum": list(_REFERENCE_SPECS)}, "id": {"type": "integer", "minimum": 1},
                "quote": {"type": "string"}, "purpose": {"type": "string", "enum": ["target", "source"]}}, "required": ["resource", "id", "quote"], "additionalProperties": False}},
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
    global _SOURCE_MESSAGES, _TASK_ENTITIES
    _TASK_ENTITIES = None
    source_file = os.environ.get("ERP_CONVERSATION_SOURCES")
    _SOURCE_MESSAGES = json.loads(Path(source_file).read_text(encoding="utf-8")) if source_file else []
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
            timeout_seconds=None,
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
        timeout_seconds=None,
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
