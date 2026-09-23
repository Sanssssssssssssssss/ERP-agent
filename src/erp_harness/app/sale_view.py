from __future__ import annotations

# 桌面业务投影：从工具回执发现单据，再用 NativeReads 刷新关联记录。
# READBACK_FIELDS 限定读取字段；RELATION_FIELDS 限定沿哪些业务关系展开。
# 目标单据优先由已验证动作定位。历史搜索里出现的单据不能直接当作本次成果。
# completion_target 决定所需检查：只读、草稿、确认、过账各不相同。
# 执行状态回答“运行到哪”；outcome 回答“规定的基础检查是否满足”。
# passed 需要完整的 native_readback 证据。缺失或不可读保留 unknown。
# 这里的检查覆盖明确字段与关系，不等于完整业务语义正确，也不等于 bench 分数。

from datetime import datetime, timezone
import re
from typing import Any, Callable
from .business import BUSINESS_LABELS, ENTERPRISE_TYPES, default_target
from . import enterprise_view


READBACK_FIELDS: dict[str, tuple[str, ...]] = {
    "res.partner": ("id", "name", "display_name", "email", "company_id"),
    "sale.order": ("id", "name", "state", "partner_id", "company_id", "amount_total", "currency_id", "payment_term_id", "order_line", "invoice_ids", "picking_ids", "invoice_status", "commitment_date", "client_order_ref"),
    "sale.order.line": ("id", "name", "order_id", "product_id", "product_uom_qty", "product_uom_id", "price_unit", "price_subtotal", "price_total"),
    "purchase.order": ("id", "name", "state", "partner_id", "company_id", "amount_total", "currency_id", "order_line", "origin", "date_order", "date_planned"),
    "purchase.order.line": ("id", "name", "order_id", "sale_order_id", "sale_line_id", "product_id", "product_qty", "product_uom_id", "price_unit", "price_subtotal", "price_total", "date_planned"),
    "account.move": ("id", "name", "state", "move_type", "partner_id", "amount_total", "currency_id", "invoice_payment_term_id", "invoice_origin", "invoice_line_ids", "payment_state", "amount_residual", "invoice_date", "invoice_pdf_report_id"),
    "account.move.line": ("id", "name", "move_id", "product_id", "quantity", "product_uom_id", "price_unit", "price_subtotal", "price_total"),
    "stock.picking": ("id", "name", "state", "sale_id", "origin", "partner_id", "scheduled_date"),
}
RELATION_FIELDS: dict[str, dict[str, str]] = {
    "sale.order": {"partner_id": "res.partner", "order_line": "sale.order.line", "invoice_ids": "account.move", "picking_ids": "stock.picking"},
    "sale.order.line": {"order_id": "sale.order"},
    "purchase.order": {"partner_id": "res.partner", "order_line": "purchase.order.line"},
    "purchase.order.line": {"order_id": "purchase.order", "sale_order_id": "sale.order", "sale_line_id": "sale.order.line"},
    "account.move": {"partner_id": "res.partner", "invoice_line_ids": "account.move.line"},
    "account.move.line": {"move_id": "account.move"},
    "stock.picking": {"sale_id": "sale.order", "partner_id": "res.partner"},
}


def _text(value: Any) -> str:
    if isinstance(value, list) and value and isinstance(value[0], int):
        return str(value[1]) if len(value) > 1 else str(value[0])
    return str(value) if value is not None else ""


def collect_documents(
    tool: str,
    arguments: dict[str, Any],
    payload: dict[str, Any] | None = None,
    *,
    source_run_id: str | None = None,
    source_tool_id: str | None = None,
) -> list[dict[str, Any]]:
    """Project the native read response using the actual tool arguments.

    Native read responses deliberately contain no model name, so deriving the
    model from the response (or from model prose) would produce a false UI
    document.  ``arguments`` is the recorded tool invocation and ``payload``
    is its structured native response.
    """
    if payload is None:
        return []
    if tool.startswith("mcp_odoo_"):
        tool = tool[len("mcp_odoo_"):]
    if tool not in {"read_record", "search_records", "find_records"} or not isinstance(payload, dict):
        return []
    if payload.get("success") is False or payload.get("error"):
        return []
    model = arguments.get("model")
    requested_id = arguments.get("record_id")
    result = payload.get("result")
    rows = [result] if tool == "read_record" and isinstance(result, dict) else result
    if not isinstance(model, str) or not isinstance(rows, list):
        return []
    documents = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        if isinstance(requested_id, int) and row["id"] != requested_id:
            continue
        document = {
            "id": row["id"], "model": model, "name": _text(row.get("name") or row.get("display_name") or row["id"]),
            "state": row.get("state"), "fields": {str(k): v for k, v in row.items() if k not in {"id", "name", "display_name", "state"}},
            "source": "native_read_receipt",
        }
        if isinstance(source_run_id, str) and source_run_id:
            document["source_run_id"] = source_run_id
        if isinstance(source_tool_id, str) and source_tool_id:
            document["source_tool_id"] = source_tool_id
        documents.append(document)
    return documents


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _relation_ids(value: Any) -> list[int]:
    """Read ids from Odoo many2one and one2many wire values."""
    if isinstance(value, int) and not isinstance(value, bool):
        return [value]
    if isinstance(value, dict) and isinstance(value.get("id"), int):
        return [value["id"]]
    if not isinstance(value, list):
        return []
    if len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):
        return [value[0]]
    result: list[int] = []
    for item in value:
        result.extend(_relation_ids(item))
    return list(dict.fromkeys(result))


def _origin_contains_exact(origin: Any, names: set[str]) -> bool:
    """Match an Odoo origin token without treating S00001x as S00001."""
    if not names:
        return False
    tokens = {token for token in re.split(r"[,;|\s]+", str(origin or "")) if token}
    return bool(tokens & {name for name in names if isinstance(name, str) and name})


def _read_result(payload: Any, expected_id: int) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "native read returned a non-object response"
    current: Any = payload
    for _ in range(3):
        if isinstance(current, dict) and isinstance(current.get("details"), dict):
            current = current["details"]
        elif isinstance(current, dict) and isinstance(current.get("structuredContent"), dict):
            current = current["structuredContent"]
        else:
            break
    if not isinstance(current, dict):
        return None, "native read returned an invalid structured response"
    if current.get("success") is False or current.get("error"):
        return None, str(current.get("error") or "native read failed")[:300]
    result = current.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("id"), int):
        return None, "native read did not observe the requested record"
    if result["id"] != expected_id:
        return None, f"native read returned id {result['id']} for requested {expected_id}"
    return result, None


def _read_one(reads: Callable[[str, dict[str, Any]], Any] | Any, model: str, record_id: int, fields=None) -> tuple[dict[str, Any] | None, str | None]:
    arguments = {"model": model, "record_id": record_id, "fields": list(fields if fields is not None else READBACK_FIELDS[model])}
    try:
        call = reads if callable(reads) else getattr(reads, "call")
        return _read_result(call("read_record", arguments), record_id)
    except Exception as exc:  # noqa: BLE001 - readback must preserve stale state
        return None, f"{type(exc).__name__}: {exc}"[:300]


def _run_sort_key(run: dict[str, Any]) -> tuple[str, str]:
    return (str(run.get("started_at") or ""), str(run.get("ended_at") or ""))


def _activity(
    runs: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    readback_status: str | None = None,
) -> dict[str, Any]:
    if not runs:
        return {"phase": "idle", "label": "等待开始", "detail": "业务已建立，尚未启动执行。"}
    run = runs[0]
    status = run.get("status")
    tools = run.get("tools") if isinstance(run.get("tools"), list) else []
    latest_tool = next((tool for tool in reversed(tools) if isinstance(tool, dict) and tool.get("status") == "running"), None)
    latest_tool = latest_tool or next((tool for tool in reversed(tools) if isinstance(tool, dict)), None)
    pending = any(
        row.get("run_id") == run.get("id") and row.get("status") == "pending_approval"
        for row in approvals
        if isinstance(row, dict)
    ) or bool(run.get("pending_approval_action_ids"))
    retry = run.get("retry") if isinstance(run.get("retry"), dict) else {}
    retrying = retry.get("status") == "retrying"
    if status == "needs_reconciliation":
        phase, label, detail = "reconciliation", "需要核对", "存在不确定写入，禁止自动重试。"
    elif status == "awaiting_input":
        phase, label, detail = "awaiting_input", "等待补充条件", (run.get("handoff") or {}).get("message", "请在聊天中补充条件，并确认更新后的提案。")
    elif status == "interrupted":
        phase, label, detail = "interrupted", "已中断", "运行因主机或进程中断而停止。"
    elif status == "cancelled":
        phase, label, detail = "cancelled", "已取消", "运行已取消，未据此判断业务结果。"
    elif status == "failed":
        phase, label, detail = "failed", "执行失败", "运行失败；请查看 trace 和独立核验。"
    elif status == "completed" and readback_status == "verifying":
        phase, label, detail = "readback", "独立回读中", "运行已完成，正在独立核验最新业务状态。"
    elif status == "completed" and readback_status in {"unavailable", "discarded"}:
        reason = "不可用" if readback_status == "unavailable" else "已丢弃"
        phase, label, detail = "readback_unknown", "独立回读未知", f"运行已完成，但独立回读{reason}，当前结果不能据此确认。"
    elif status == "completed":
        phase, label, detail = "completed", "已结束", "运行已结束；业务结果请查看独立核验。"
    elif status == "awaiting_approval" or (status == "running" and pending):
        phase, label, detail = "approval", "等待审批", "存在待处理的 ERP 写入审批。"
    elif status == "running" and retrying:
        phase, label, detail = "retrying", "正在恢复", "模型或工具暂时失败，主机正在恢复当前运行。"
    elif status == "running" and (
        run.get("phase") == "waiting_for_model"
        or (
            isinstance(run.get("resumed_at"), str)
            and bool(run.get("resumed_at").strip())
            and not (isinstance(run.get("first_new_output"), str) and run.get("first_new_output").strip())
        )
    ):
        phase, label, detail = "model_wait", "等待模型响应", "当前运行已恢复，正在等待下一次模型结果。"
    elif status == "running" and latest_tool and latest_tool.get("status") == "running":
        phase, label, detail = "tool", "执行工具", "正在等待工具回执。"
    elif status == "running":
        phase, label, detail = "model", "模型处理中", "正在等待下一轮模型结果。"
    elif status == "cancel_requested":
        phase, label, detail = "cancelling", "正在取消", "已请求停止当前运行。"
    else:
        phase, label, detail = "unknown", "状态未知", "当前运行状态无法安全归类。"
    activity: dict[str, Any] = {
        "phase": phase,
        "label": label,
        "detail": detail,
        "tool_count": run.get("tool_count", len(tools)),
        "model_rounds": run.get("model_rounds", len(run.get("rounds", [])) if isinstance(run.get("rounds"), list) else 0),
    }
    rounds = run.get("rounds") if isinstance(run.get("rounds"), list) else []
    live_messages = run.get("live_messages") if isinstance(run.get("live_messages"), list) else []
    latest_live = next(
        (row for row in reversed(live_messages)
         if isinstance(row, dict) and isinstance(row.get("text"), str) and row.get("text", "").strip()),
        None,
    )
    latest_public_text = (latest_live.get("text", "").strip()[:1200] if latest_live else None)
    if latest_public_text is None:
        latest_public_text = next(
            (row.get("text", "").strip()[:1200] for row in reversed(rounds)
             if isinstance(row, dict) and isinstance(row.get("text"), str) and row.get("text", "").strip()),
            None,
        )
    if latest_public_text:
        activity["intent"] = latest_public_text
    if latest_live and run.get("last_event_at"):
        activity["at"] = run["last_event_at"]
    if latest_tool:
        activity["tool_id"] = latest_tool.get("id")
        activity["tool_status"] = latest_tool.get("status")
        if isinstance(latest_tool.get("name"), str) and latest_tool.get("name"):
            activity["tool_name"] = latest_tool["name"]
        if status in {"running", "awaiting_approval"} and isinstance(latest_tool.get("round"), int):
            activity["round"] = latest_tool["round"]
        activity["at"] = latest_tool.get("ended_at") or latest_tool.get("started_at")
    events = run.get("events") if isinstance(run.get("events"), list) else []
    if events and isinstance(events[-1], dict):
        last = events[-1]
        if isinstance(last.get("type"), str):
            activity["last_event"] = last["type"]
        activity["at"] = last.get("at") or activity.get("at")
    if latest_live and run.get("last_event_at"):
        activity["at"] = run["last_event_at"]
    if phase in {"completed", "failed"} and run.get("ended_at"):
        activity["at"] = run["ended_at"]
    else:
        activity.setdefault("at", run.get("ended_at") or run.get("started_at"))
    return activity


def _check(name: str, label: str, status: str, detail: str) -> dict[str, Any]:
    return {"name": name, "label": label, "status": status, "detail": detail, "source": "native_readback"}


STAGES = (
    ("read", "读取当前状态"),
    ("quote", "销售报价"),
    ("confirm", "确认销售订单"),
    ("invoice", "关联并过账发票"),
    ("verify", "独立核验"),
)

PURCHASE_STAGES = (
    ("read", "读取当前状态"),
    ("purchase", "确认采购订单"),
    ("verify", "独立核验"),
)

CHAIN_STAGES = (
    ("read", "读取当前状态"),
    ("quote", "销售报价"),
    ("confirm", "确认销售订单"),
    ("purchase", "确认采购订单"),
    ("invoice", "关联并过账发票"),
    ("verify", "独立核验"),
)


def _stages_for(business_type: str, completion_target: str = "posted") -> tuple[tuple[str, str], ...]:
    """Return only the stages required by the requested business target.

    A draft target must not look like a failed confirmation or invoice task.
    The stage list is therefore truncated at the target, while retaining the
    final independent verification stage.
    """
    if business_type == "invoice_delivery":
        return (("read", "核对发票与收件人"), ("invoice", "生成正式 PDF 并发送"), ("verify", "核对投递回执"))
    if business_type in ENTERPRISE_TYPES:
        return (("read", "读取当前状态"), ("verify", "独立核验")) if completion_target == "read_only" else (("read", "读取当前状态"), (business_type, BUSINESS_LABELS[business_type]), ("verify", "独立核验"))
    if business_type == "purchase":
        stages = PURCHASE_STAGES
    elif business_type == "sale_purchase_invoice":
        stages = CHAIN_STAGES
    else:
        stages = STAGES
    if completion_target == "read_only":
        return tuple(stage for stage in stages if stage[0] in {"read", "verify"})
    last_stage = {
        "draft": "quote" if business_type != "purchase" else "purchase",
        "confirmed": "confirm" if business_type != "purchase" else "purchase",
        "posted": "invoice",
    }.get(completion_target)
    if not last_stage:
        return stages
    result: list[tuple[str, str]] = []
    for stage in stages:
        result.append(stage)
        if stage[0] == last_stage:
            break
    if result and result[-1][0] != "verify":
        result.append(("verify", "独立核验"))
    return tuple(result)


def _verified_action_record_ids(
    runs: list[dict[str, Any]], model: str, operations: set[str] | None = None,
) -> set[int]:
    """Get verified target ids from native action receipts.

    ``execute_method`` identifies the action in its recorded arguments. Native
    verification puts the resulting records under ``verification.evidence.records``.
    Host reconciliation events supersede the original receipt for the same action;
    read/search results and result prose never select a target.
    """
    ordered = sorted((row for row in runs if isinstance(row, dict)), key=_run_sort_key, reverse=True)
    for run in ordered:
        events = run.get("events") if isinstance(run.get("events"), list) else []
        reconciled = {
            event["action_id"]: event
            for event in events if isinstance(event, dict)
            and event.get("type") == "reconciliation" and isinstance(event.get("action_id"), str)
        }
        receipts = []
        for tool in run.get("tools", []) if isinstance(run.get("tools"), list) else []:
            if not isinstance(tool, dict):
                continue
            result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
            action_id = result.get("action_id")
            if isinstance(action_id, str) and action_id in reconciled:
                continue
            arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
            tool_name = str(tool.get("name") or "").removeprefix("mcp_odoo_")
            if tool_name == "execute_method":
                receipts.append((arguments.get("model"), arguments.get("method"), "records", result))
            elif tool_name == "execute_approved_write":
                receipts.append((result.get("model"), result.get("operation"), "record_ids", result))
        for receipt in reconciled.values():
            if receipt.get("kind") in {"write", "method"}:
                receipts.append((receipt.get("model"), receipt.get("operation"),
                                 "record_ids" if receipt["kind"] == "write" else "records", receipt))
        found_action = False
        record_ids: set[int] = set()
        for action_model, operation, evidence_ids, result in receipts:
            if action_model != model or (operations and operation not in operations):
                continue
            verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
            # A host reconciliation supersedes the original receipt, including
            # an unconfirmed result; never recover a stale target from an older run.
            if result.get("type") == "reconciliation":
                found_action = True
            if result.get("action_status") != "verified" or verification.get("status") != "satisfied":
                continue
            found_action = True
            evidence = verification.get("evidence") if isinstance(verification.get("evidence"), dict) else {}
            ids = evidence.get(evidence_ids)
            if isinstance(ids, list):
                for record in ids:
                    record_id = record.get("id") if evidence_ids == "records" and isinstance(record, dict) else record
                    if type(record_id) is int and record_id > 0:
                        record_ids.add(record_id)
        if found_action:
            return record_ids
    return set()


def _target_order_ids(run: dict[str, Any] | None, business_type: str) -> set[int]:
    if not isinstance(run, dict):
        return set()
    model = "purchase.order" if business_type == "purchase" else "sale.order"
    operations = {"create", "write", "action_confirm", "button_confirm", "button_approve"}
    return _verified_action_record_ids([run], model, operations)


def _target_order_ids_for_runs(runs: list[dict[str, Any]], business_type: str) -> set[int]:
    return _verified_action_record_ids(runs, "purchase.order" if business_type == "purchase" else "sale.order", {"create", "write", "action_confirm", "button_confirm", "button_approve"})


def _readback_targets(business: dict[str, Any], runs: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Shared desktop/chat scope: explicit bindings and existing verified receipts."""
    targets = {(r["model"], r["id"]) for r in business.get("references", [])
               if r.get("purpose") != "source" and isinstance(r.get("model"), str)
               and type(r.get("id")) is int and r["id"] > 0}
    kind = business.get("type", "sale_invoice")
    if kind in ENTERPRISE_TYPES:
        action_targets, _ = enterprise_view.action_targets(sorted(runs, key=_run_sort_key))
        targets.update(action_targets)
    else:
        for model, target_kind in (("sale.order", "sale_invoice"), ("purchase.order", "purchase")):
            if target_kind == kind or kind == "sale_purchase_invoice":
                targets.update((model, i) for i in _target_order_ids_for_runs(runs, target_kind))
    return targets


def _related_records(model: str, fields: dict[str, Any]) -> set[tuple[str, int]]:
    return {(related_model, record_id)
            for field, related_model in RELATION_FIELDS.get(model, {}).items()
            for record_id in _relation_ids(fields.get(field))}


def _select_target_document(documents: list[dict[str, Any]], target_ids: set[int]) -> dict[str, Any] | None:
    """Select only a uniquely verified and freshly observed target."""
    if target_ids:
        candidates = [document for document in documents if document.get("id") in target_ids]
        return candidates[0] if len(target_ids) == 1 and len(candidates) == 1 else None
    return documents[0] if len(documents) == 1 else None


def _annotate_document_scope(
    documents: list[dict[str, Any]], runs: list[dict[str, Any]], business_type: str,
    targets: set[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Mark historical order documents and their explicitly linked records."""
    if business_type in ENTERPRISE_TYPES:
        targets, _ = enterprise_view.action_targets(list(reversed(runs)))
        if not targets:
            return documents
        return [{**doc, "document_scope": "current" if (doc.get("model"), doc.get("id")) in targets else "reference", "is_reference": (doc.get("model"), doc.get("id")) not in targets} for doc in documents]
    if targets and business_type != "invoice_delivery":
        # Only trusted roots and their explicit relationships are current.
        # Exploration remains in history, without becoming completion evidence.
        by_key = {(doc.get("model"), doc.get("id")): doc for doc in documents}
        current = set(targets)
        order_targets = {model: {i for m, i in targets if m == model}
                         for model in {m for m, _ in targets} & {"sale.order", "purchase.order"}}
        queue = list(targets)
        while queue:
            model, record_id = queue.pop()
            fields = by_key.get((model, record_id), {}).get("fields") or {}
            related = {key for key in _related_records(model, fields) - current
                       if key[0] not in order_targets or key[1] in order_targets[key[0]]}
            current.update(related)
            queue.extend(related)
        return [{**doc, "document_scope": "current" if (doc.get("model"), doc.get("id")) in current else "reference",
                 "is_reference": (doc.get("model"), doc.get("id")) not in current} for doc in documents]
    # Legacy unbound businesses retain their existing observation display.
    return [{**doc, "document_scope": doc.get("document_scope") if doc.get("document_scope") in {"current", "reference"} else "current",
             "is_reference": doc.get("document_scope") == "reference"} for doc in documents]


def _evidence(document: dict[str, Any], label: str) -> dict[str, Any] | None:
    run_id, tool_id = document.get("source_run_id"), document.get("source_tool_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    kind = "readback" if document.get("source") == "refresh_native_read" else "action" if document.get("source") == "native_action_readback" else "tool"
    item: dict[str, Any] = {"run_id": run_id, "kind": kind, "label": "独立回读快照" if kind == "readback" else "已核验动作状态回读" if kind == "action" else label}
    if document.get("source") == "native_read_receipt" and isinstance(tool_id, str) and tool_id:
        item["tool_id"] = tool_id
    if kind == "action" and isinstance(document.get("source_action_id"), str):
        item["action_id"] = document["source_action_id"]
    if isinstance(document.get("observed_at"), str):
        item["observed_at"] = document["observed_at"]
    return item


def _action_stage(model: Any, operation: Any) -> str | None:
    model, operation = str(model or "").lower(), str(operation or "").lower()
    if model in {"stock.picking", "stock.move", "stock.backorder.confirmation", "stock.return.picking"}:
        return "inventory"
    if model.startswith("mrp."):
        return "manufacturing"
    if model in {"account.payment", "account.payment.register"}:
        return "payment"
    if model == "account.move.reversal":
        return "refund"
    if model in {"account.move.line", "account.bank.statement.line"}:
        return "reconciliation"
    if model == "sale.order" and operation == "action_confirm":
        return "confirm"
    if model == "sale.order" and operation == "create":
        return "quote"
    if model == "purchase.order" and operation in {"create", "write", "button_confirm", "button_approve"}:
        return "purchase"
    if model == "purchase.order.line" and operation in {"create", "write"}:
        return "purchase"
    if (model == "account.move" and operation in {"create", "action_post"}) or (model == "sale.advance.payment.inv" and operation in {"create", "create_invoices"}) or (model == "account.move.send.wizard" and operation == "action_send_and_print"):
        return "invoice"
    return None


def _tool_stage(tool: dict[str, Any]) -> str | None:
    arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
    result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
    candidates = [result]
    result_approval = result.get("approval") if isinstance(result.get("approval"), dict) else None
    argument_approval = arguments.get("approval") if isinstance(arguments.get("approval"), dict) else None
    if result_approval:
        candidates.append(result_approval)
    if argument_approval:
        candidates.append(argument_approval)
    candidates.append(arguments)
    for candidate in candidates:
        stage = _action_stage(candidate.get("model"), candidate.get("operation") or candidate.get("method"))
        if stage:
            return stage
    model = next((candidate.get("model") for candidate in candidates if candidate.get("model")), None)
    operation = next((candidate.get("operation") or candidate.get("method") for candidate in candidates if candidate.get("operation") or candidate.get("method")), None)
    tool_name = str(tool.get("name") or "").lower().removeprefix("mcp_odoo_")
    if tool_name in {"read_record", "search_records", "find_records"} and isinstance(model, str) and model:
        return "read"
    return "read" if model in {"sale.order", "purchase.order", "account.move"} and operation in {None, "read_record", "search_records", "find_records"} else None


def _tool_action_fields(tool: dict[str, Any]) -> tuple[Any, Any]:
    arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
    result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
    candidates = [result]
    for container in (result, arguments):
        nested = container.get("approval") if isinstance(container.get("approval"), dict) else None
        if nested:
            candidates.append(nested)
    candidates.append(arguments)
    for candidate in candidates:
        model = candidate.get("model")
        operation = candidate.get("operation") or candidate.get("method")
        if _action_stage(model, operation):
            return model, operation
    return None, None


def _run_has_relevant_evidence(
    run: dict[str, Any], readback_documents: list[dict[str, Any]] | None = None,
    business_type: str = "sale_invoice", completion_target: str = "posted",
) -> bool:
    documents = run.get("documents") if isinstance(run.get("documents"), list) else []
    if business_type == "invoice_delivery":
        return any(d.get("model") == "account.move" and d.get("source") == "refresh_native_read" for d in readback_documents or [])
    if business_type in ENTERPRISE_TYPES:
        targets, _ = enterprise_view.action_targets([run])
        return any(doc.get("source") == "refresh_native_read" and ((doc.get("model"), doc.get("id")) in targets or completion_target == "read_only") for doc in readback_documents or [])
    if business_type == "purchase":
        orders = [doc for doc in documents if isinstance(doc, dict) and doc.get("model") == "purchase.order" and doc.get("source_run_id") == run.get("id")]
        if not orders:
            return False
        ids = _target_order_ids(run, business_type) or {doc.get("id") for doc in orders}
        return any(doc.get("model") == "purchase.order" and doc.get("id") in ids and doc.get("source") == "refresh_native_read"
                   for doc in (readback_documents or [])) or any(doc.get("state") in {"purchase", "done"} for doc in orders)
    if business_type == "sale_purchase_invoice":
        if not _run_has_relevant_evidence(run, readback_documents, "sale_invoice", "posted"):
            return False
        purchase_documents = [doc for doc in documents if isinstance(doc, dict) and doc.get("model") == "purchase.order" and doc.get("source_run_id") == run.get("id")]
        if not purchase_documents:
            return False
        sale_ids = {doc.get("id") for doc in documents if doc.get("model") == "sale.order" and type(doc.get("id")) is int}
        sale_names = {doc.get("name") for doc in documents if doc.get("model") == "sale.order"}
        linked = False
        for purchase in purchase_documents:
            fields = purchase.get("fields") if isinstance(purchase.get("fields"), dict) else {}
            linked_id = _relation_ids(fields.get("sale_order_id"))
            origin = str(fields.get("origin") or "")
            linked |= bool(set(linked_id) & sale_ids) or _origin_contains_exact(origin, sale_names)
        return linked
    orders = [doc for doc in documents if isinstance(doc, dict) and doc.get("model") == "sale.order" and doc.get("source_run_id") == run.get("id")]
    if not orders:
        return False
    observed_order_ids = _target_order_ids(run, business_type) or {doc.get("id") for doc in orders if type(doc.get("id")) is int}
    if completion_target in {"draft", "confirmed"}:
        return any(
            isinstance(doc, dict) and doc.get("source") == "refresh_native_read"
            and doc.get("model") == "sale.order" and doc.get("id") in observed_order_ids
            for doc in (readback_documents or [])
        )
    observed_invoice_ids = {
        doc.get("id") for doc in documents
        if isinstance(doc, dict) and doc.get("model") == "account.move"
        and doc.get("source_run_id") == run.get("id") and type(doc.get("id")) is int
    }
    invoice_ids: set[int] = set()
    for order in orders:
        fields = order.get("fields") if isinstance(order.get("fields"), dict) else {}
        invoice_ids.update(_relation_ids(fields.get("invoice_ids")))
    if invoice_ids & observed_invoice_ids:
        return True
    if not readback_documents:
        return False
    for document in readback_documents:
        if not isinstance(document, dict) or document.get("source") != "refresh_native_read":
            continue
        if document.get("model") != "sale.order" or document.get("id") not in observed_order_ids:
            continue
        fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
        linked_ids = set(_relation_ids(fields.get("invoice_ids")))
        if linked_ids & observed_invoice_ids:
            return True
    return False


def _execution_projection(
    runs: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    outcome_status: str,
    approvals: list[dict[str, Any]] | None = None,
    readback_documents: list[dict[str, Any]] | None = None,
    business_type: str = "sale_invoice",
    completion_target: str = "posted",
) -> dict[str, Any]:
    def action_stage(model, operation):
        stage = _action_stage(model, operation)
        return business_type if stage and business_type in ENTERPRISE_TYPES else stage

    stage_defs = _stages_for(business_type, completion_target)
    evidence_by_stage: dict[str, list[dict[str, Any]]] = {stage_id: [] for stage_id, _ in stage_defs}
    observed_by_stage: dict[str, bool] = {stage_id: False for stage_id, _ in stage_defs}
    awaiting_stage_ids: set[str] = set()
    for document in documents:
        if document.get("document_scope") == "reference":
            continue
        label = f"读取 {document.get('model', '单据')} {document.get('name') or document.get('id')}"
        item = _evidence(document, label)
        model = document.get("model")
        fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
        relevant = model in {"sale.order", "purchase.order", "account.move"} or (business_type in ENTERPRISE_TYPES and model in enterprise_view.FIELDS)
        if relevant:
            observed_by_stage["read"] = True
        if item is None:
            if model == "sale.order":
                if "quote" in observed_by_stage:
                    observed_by_stage["quote"] |= fields.get("amount_total") is not None or fields.get("order_line") is not None
                if "confirm" in observed_by_stage:
                    observed_by_stage["confirm"] = True
            elif model == "purchase.order" and "purchase" in observed_by_stage:
                observed_by_stage["purchase"] = True
            elif model == "account.move":
                if "invoice" in observed_by_stage:
                    observed_by_stage["invoice"] = True
            continue
        evidence_by_stage["read"].append(item)
        if model == "sale.order":
            if "quote" in evidence_by_stage and (fields.get("amount_total") is not None or fields.get("order_line") is not None):
                evidence_by_stage["quote"].append(item)
            if "confirm" in evidence_by_stage:
                evidence_by_stage["confirm"].append(item)
        elif model == "account.move":
            if "invoice" in evidence_by_stage:
                evidence_by_stage["invoice"].append(item)
        elif model == "purchase.order" and "purchase" in evidence_by_stage:
            evidence_by_stage["purchase"].append(item)
        if business_type in evidence_by_stage and business_type in ENTERPRISE_TYPES and relevant:
            evidence_by_stage[business_type].append(item)

    verified_actions: dict[str, list[dict[str, Any]]] = {stage_id: [] for stage_id, _ in stage_defs}
    for run in runs:
        tools = run.get("tools") if isinstance(run.get("tools"), list) else []
        for tool in tools:
            result = tool.get("result") if isinstance(tool, dict) and isinstance(tool.get("result"), dict) else {}
            verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
            if result.get("action_status") != "verified" or verification.get("status") != "satisfied":
                continue
            arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
            model, operation = _tool_action_fields(tool)
            stage_id = action_stage(model, operation)
            if not stage_id or stage_id not in verified_actions or not isinstance(run.get("id"), str):
                continue
            verified_actions[stage_id].append({"run_id": run["id"], "tool_id": tool.get("id"), "action_id": tool.get("action_id") or result.get("action_id"), "kind": "action", "model": model, "operation": operation, "label": "结构化 ERP 动作已核验"})

    for approval in approvals or []:
        if approval.get("status") != "pending_approval" or not isinstance(approval.get("run_id"), str):
            continue
        operation = str(approval.get("operation") or approval.get("method") or "")
        stage_id = action_stage(approval.get("model"), operation)
        if not stage_id or stage_id not in evidence_by_stage:
            continue
        item = {"run_id": approval["run_id"], "action_id": approval.get("action_id"), "kind": "action", "label": "等待主机审批的 ERP 动作"}
        evidence_by_stage[stage_id].append({key: value for key, value in item.items() if value})
        awaiting_stage_ids.add(stage_id)

    for approval in approvals or []:
        verification = approval.get("verification") if isinstance(approval.get("verification"), dict) else {}
        if approval.get("status") != "verified" or verification.get("status") != "satisfied":
            continue
        stage_id = action_stage(approval.get("model"), approval.get("operation"))
        if stage_id in verified_actions and isinstance(approval.get("run_id"), str):
            verified_actions[stage_id].append({"run_id": approval["run_id"], "action_id": approval.get("action_id"), "kind": "action", "model": approval.get("model"), "operation": approval.get("operation") or approval.get("method"), "label": "结构化 ERP 动作已核验"})

    for stage_id, items in verified_actions.items():
        if stage_id in evidence_by_stage:
            evidence_by_stage[stage_id].extend({key: value for key, value in item.items() if value} for item in items)

    evidence_by_stage["verify"] = list(evidence_by_stage["read"])
    evidence_by_stage["verify"].extend(item for stage_id in verified_actions if stage_id not in {"read", "verify"} for item in verified_actions[stage_id])
    observed_by_stage["verify"] = observed_by_stage["read"]

    check_by_name = {row.get("name"): row for row in checks if isinstance(row, dict)}
    current_run_id = runs[0].get("id") if runs else None
    stages: list[dict[str, Any]] = []
    for stage_id, label in stage_defs:
        evidence = evidence_by_stage[stage_id]
        current_evidence = [row for row in evidence if not current_run_id or row.get("run_id") == current_run_id]
        status = "pending"
        detail = "尚未观测到结构化证据。"
        if evidence:
            status = "observed"
            detail = f"已由 {len(evidence)} 条结构化读取证据观测。"
        elif observed_by_stage[stage_id]:
            status = "unknown"
            detail = "存在历史单据，但缺少可关联运行证据。"
        if stage_id in awaiting_stage_ids:
            status, detail = "awaiting_approval", "存在待处理的结构化 ERP 动作审批。"
        elif stage_id == "confirm" and evidence:
            check = check_by_name.get("order_confirmed", {})
            if current_run_id and not current_evidence:
                status, detail = "observed", "仅有历史运行证据，当前运行尚未确认销售订单。"
            elif any(item.get("kind") == "action" for item in current_evidence):
                status, detail = "verified", "结构化销售订单确认动作已核验。"
            else:
                status = {"passed": "verified", "failed": "failed", "unknown": "unknown"}.get(check.get("status"), "unknown")
                detail = check.get("detail", detail)
        elif stage_id == "invoice" and evidence:
            check = check_by_name.get("invoice_posted", {})
            if current_run_id and not current_evidence:
                status, detail = "observed", "仅有历史运行证据，当前运行尚未确认发票状态。"
            elif any(item.get("kind") == "action" for item in current_evidence):
                current_run = runs[0] if runs else None
                current_relevant = _run_has_relevant_evidence(current_run, readback_documents, business_type, completion_target) if current_run else False
                check_complete = check_by_name.get("invoice_posted", {}).get("status") == "passed" and check_by_name.get("invoice_linked_to_order", {}).get("status") == "passed" and current_relevant
                if check_complete:
                    status, detail = "verified", "结构化发票过账动作及关联回读已核验。"
                else:
                    status, detail = "observed", "已核验发票动作，但仍缺少当前运行的过账及关联回读。"
            else:
                    status = {"passed": "verified", "failed": "failed", "unknown": "unknown"}.get(check.get("status"), "unknown")
                    detail = check.get("detail", detail)
        elif stage_id == "purchase" and evidence:
            check = check_by_name.get("purchase_draft" if completion_target == "draft" else "purchase_confirmed", {})
            if current_run_id and not current_evidence:
                status, detail = "observed", "仅有历史运行证据，当前运行尚未确认采购订单。"
            elif any(item.get("kind") == "action" for item in current_evidence):
                status = "verified" if check.get("status") == "passed" else "observed"
                detail = "结构化采购确认动作已核验。" if status == "verified" else "已核验采购动作，但终态回读尚未通过。"
            else:
                status = {"passed": "verified", "failed": "failed", "unknown": "unknown"}.get(check.get("status"), "unknown")
                detail = check.get("detail", detail)
        elif stage_id in ENTERPRISE_TYPES and evidence:
            status = "verified" if outcome_status == "passed" and current_evidence else "observed"
            detail = "当前业务关系已独立回读。" if status == "verified" else "已观测动作，等待终态核验。"
        elif stage_id == "verify":
            current_read = [row for row in evidence_by_stage["read"] if not current_run_id or row.get("run_id") == current_run_id]
            current_run = runs[0] if runs else None
            current_relevant = _run_has_relevant_evidence(current_run, readback_documents, business_type, completion_target) if current_run else False
            if outcome_status == "passed" and current_relevant:
                status, detail = "verified", "业务目标所需的基础检查均通过。"
            elif outcome_status == "failed" and current_relevant:
                status, detail = "failed", "业务目标所需的基础检查未通过。"
            elif outcome_status in {"passed", "failed"} and evidence_by_stage["read"]:
                status, detail = "unknown", "已有历史读取证据，但当前运行尚未完成核验。"
            elif evidence_by_stage["read"]:
                status, detail = "unknown", "已有读取证据，但基础检查尚未完整通过。"
        stages.append({"id": stage_id, "label": label, "status": status, "detail": detail, "evidence": evidence})

    current_run = runs[0] if runs else None
    current_stage_id = None
    if current_run and current_run.get("status") == "awaiting_approval":
        pending = set(current_run.get("pending_approval_action_ids") or [])
        pending_stages = [
            action_stage(row.get("model"), row.get("operation"))
            for row in approvals or []
            if row.get("action_id") in pending and row.get("status") == "pending_approval"
        ]
        current_stage_id = next((stage for stage in pending_stages if stage), None)
    elif current_run and current_run.get("status") == "running":
        tools = current_run.get("tools") if isinstance(current_run.get("tools"), list) else []
        current_stage_id = next((_tool_stage(tool) for tool in reversed(tools) if isinstance(tool, dict) and _tool_stage(tool)), None)
        if not current_stage_id:
            pending = set(current_run.get("pending_approval_action_ids") or [])
            current_stage_id = next((action_stage(row.get("model"), row.get("operation") or row.get("method")) for row in approvals or [] if row.get("action_id") in pending), None)
    if business_type in ENTERPRISE_TYPES and current_stage_id not in {None, "read", "verify"}:
        current_stage_id = business_type
    result: dict[str, Any] = {"stages": stages}
    if isinstance(current_run_id, str):
        result["run_id"] = current_run_id
    if current_stage_id:
        result["current_stage_id"] = current_stage_id
    return result


def _required_check_names(business_type: str, completion_target: str) -> set[str]:
    if business_type == "invoice_delivery":
        return {"invoice_recipient_verified", "invoice_mail_sent"}
    if business_type in ENTERPRISE_TYPES:
        return {"enterprise_observed"} if completion_target == "read_only" else {"enterprise_observed", "enterprise_settled", "enterprise_final"}
    if business_type == "purchase":
        return {"observed_purchase", "observed_supplier"} if completion_target == "read_only" else ({
            "observed_purchase", "observed_supplier", "observed_document_states", "purchase_lines_valid", "purchase_draft",
        } if completion_target == "draft" else {
            "observed_purchase", "observed_supplier", "observed_document_states", "purchase_lines_valid", "purchase_confirmed",
        })
    elif business_type == "sale_purchase_invoice":
        return {"observed_order", "observed_purchase"} if completion_target == "read_only" else {
            "observed_order", "observed_customer", "observed_purchase", "observed_supplier",
            "observed_payment_term", "observed_document_states", "order_confirmed",
            "purchase_lines_valid", "purchase_confirmed", "purchase_linked_to_order",
            "invoice_linked_to_order", "invoice_posted",
        }
    elif completion_target == "read_only":
        return {"observed_order", "observed_customer"}
    elif completion_target == "draft":
        return {"observed_order", "observed_customer", "observed_payment_term", "observed_document_states", "order_draft"}
    elif completion_target == "confirmed":
        return {"observed_order", "observed_customer", "observed_document_states", "order_confirmed"}
    return {"observed_order", "observed_customer", "observed_payment_term", "observed_document_states", "order_confirmed", "invoice_linked_to_order", "invoice_posted"}


def _outcome(checks: list[dict[str, Any]], business_type: str = "sale_invoice",
             completion_target: str = "posted") -> dict[str, str]:
    required = _required_check_names(business_type, completion_target)
    required.update(row["name"] for row in checks if str(row.get("name", "")).startswith("requested_reference_"))
    scope = f"{business_type}_{completion_target}_checks"
    by_name = {row.get("name"): row for row in checks if isinstance(row, dict)}
    statuses = [by_name.get(name, {}).get("status") for name in required]
    linked = required.issubset(by_name) and all(by_name[name].get("source") == "native_readback" for name in required)
    if not linked or any(status == "failed" for status in statuses):
        status = "failed" if "failed" in statuses else "unknown"
    elif all(status == "passed" for status in statuses):
        status = "passed"
    else:
        status = "unknown"
    labels = {
        "purchase": "采购基础检查",
        "sale_purchase_invoice": "销售、采购与开票基础检查",
        "sale_invoice": "销售与开票基础检查",
    }
    passed_details = {
        ("invoice_delivery", "sent"): "正式发票 PDF 已交付邮件服务器；不代表收件人已打开或阅读。",
        ("purchase", "read_only"): "采购订单及供应商读取完成。",
        ("purchase", "draft"): "采购草稿及供应商、采购行检查通过。",
        ("purchase", "confirmed"): "采购订单已确认，供应商和采购行检查通过。",
        ("sale_purchase_invoice", "read_only"): "销售与采购相关记录已读取。",
        ("sale_purchase_invoice", "posted"): "销售订单已确认，采购订单已确认，关联发票已过账。",
        ("sale_invoice", "read_only"): "销售订单与客户读取完成。",
        ("sale_invoice", "draft"): "销售订单草稿及客户、付款条款检查通过。",
        ("sale_invoice", "confirmed"): "销售订单已确认，客户读取与单据状态检查通过。",
        ("sale_invoice", "posted"): "销售订单已确认，关联发票已过账。",
    }
    detail = passed_details.get((business_type, completion_target), "业务目标检查通过。") if status == "passed" else (
        "业务目标所需的基础检查未通过。" if status == "failed" else
        "缺少足够的结构化读取证据，暂不能判断业务目标结果。"
    )
    if business_type in ENTERPRISE_TYPES and status == "passed":
        detail = "当前单据终态与基础关系检查通过。"
    label = labels.get(business_type, BUSINESS_LABELS.get(business_type, labels["sale_invoice"]) + "基础检查")
    return {"status": status, "label": label, "detail": detail, "scope": scope}


def _finish_readback(state: dict[str, Any], business: dict[str, Any], runs: list[dict[str, Any]],
                     observations: dict[tuple[str, int], dict[str, Any]], failures: dict[tuple[str, int], str],
                     checks: list[dict[str, Any]], business_type: str, target: str) -> dict[str, Any]:
    for index, reference in enumerate(business.get("references", [])):
        if reference.get("purpose") == "source":
            continue
        model, record_id = reference["model"], reference["id"]
        observed = observations.get((model, record_id))
        status = "unknown"
        if observed and (model, record_id) not in failures:
            expected = {k: v for k, v in reference["fields"].items() if k in {"name", "company_id", "partner_id", "currency_id"} or (business_type == "invoice_delivery" and k in {"email", "parent_id", "commercial_partner_id", "type", "function", "active"})}
            actual = {**observed.get("fields", {}), "name": observed.get("name")}
            if all(k in actual for k in expected):
                status = "passed" if all(actual[k] == v for k, v in expected.items()) else "failed"
        # 收尾依据是原始主体与实际目标的关系，不能只看“读过一张单”。
        relation = "partner_id" if model == "res.partner" and reference.get("purpose") != "recipient" else "company_id" if model == "res.company" else None
        related_ids = set()
        for order_model, kind in (("sale.order", "sale_invoice"), ("purchase.order", "purchase")):
            ids = _target_order_ids_for_runs(runs, kind)
            targets = [row for (m, i), row in observations.items() if m == order_model and (m, i) not in failures and (not ids or i in ids)]
            if relation and len(targets) == 1:
                value = targets[0].get("fields", {}).get(relation)
                related_ids.update(_relation_ids(value))
            if model == order_model and ids and record_id not in ids:
                status = "failed"
        if relation and related_ids and status != "failed":
            status = "passed" if record_id in related_ids else "failed"
        checks.append(_check(f"requested_reference_{index}", f"原始主体：{reference['quote']}", status,
                             "核对用户原始引用与本次回读关系；不代表自由文本的全部业务条件已验证。"))
    for (model, record_id), failure in failures.items():
        checks.append(_check(f"read_{model}_{record_id}", f"读取 {model} {record_id}", "unknown", f"读取失败：{failure}"))
    business["readback"] = {
        "documents": list(observations.values()), "checks": checks, "observed_at": _now(),
        "stale": bool(failures), "latest_run_id": runs[-1].get("id") if runs else None,
        "verification_status": _outcome(checks, business_type, target)["status"],
        "outcome": _outcome(checks, business_type, target),
    }
    return business_detail(state, business["id"])


def _finish_purchase_readback(state: dict[str, Any], business: dict[str, Any], runs: list[dict[str, Any]],
                              observations: dict[tuple[str, int], dict[str, Any]], failures: dict[tuple[str, int], str],
                              fresh_by_model: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    orders = fresh_by_model.get("purchase.order", [])
    target_ids = _target_order_ids_for_runs(runs, "purchase")
    order = _select_target_document(orders, target_ids)
    fields = order.get("fields", {}) if order else {}
    partner_ids = _relation_ids(fields.get("partner_id"))
    partners = fresh_by_model.get("res.partner", [])
    lines = fresh_by_model.get("purchase.order.line", [])
    line_ids = set(_relation_ids(fields.get("order_line")))
    valid_lines = bool(line_ids) and line_ids.issubset({row.get("id") for row in lines}) and all(
        row.get("fields", {}).get("product_id") and row.get("fields", {}).get("product_qty") is not None
        for row in lines if row.get("id") in line_ids
    )
    state_value = order.get("state") if order else None
    target = business.get("completion_target", "confirmed")
    checks = [
        _check("observed_purchase", "当前采购订单", "passed" if order else "unknown", "采购订单由 native read 观测。" if order else "尚未观测到唯一采购订单。"),
        _check("observed_supplier", "已读取供应商", "passed" if partner_ids and any(row.get("id") in partner_ids for row in partners) else "unknown", "供应商关系与记录均已观测。" if partner_ids and partners else "供应商关系或记录未观测完整。"),
        _check("observed_document_states", "已读取采购单状态", "passed" if state_value else "unknown", "采购订单状态已观测。" if state_value else "没有足够读取结果确认采购单状态。"),
        _check("purchase_lines_valid", "采购行有效", "passed" if valid_lines else "failed" if line_ids else "unknown", "采购行包含商品和数量。" if valid_lines else "采购行缺少商品或数量。" if line_ids else "尚未观测采购行。"),
        _check("purchase_draft", "采购订单为草稿", "passed" if state_value == "draft" else "failed" if state_value is not None else "unknown", "采购订单状态为 draft。" if state_value == "draft" else "采购订单存在但不是 draft。" if state_value else "尚未确认采购订单状态。"),
        _check("purchase_confirmed", "采购订单已确认", "passed" if state_value in {"purchase", "done"} else "unknown" if target != "confirmed" else "failed" if state_value is not None else "unknown", "采购订单状态为 purchase 或 done。" if state_value in {"purchase", "done"} else "采购订单尚未处于已确认状态。" if target != "confirmed" else "采购订单存在但未处于已确认状态。" if state_value else "尚未确认采购订单状态。"),
    ]
    return _finish_readback(state, business, runs, observations, failures, checks, "purchase", business.get("completion_target", "confirmed"))


def _finish_chain_readback(state: dict[str, Any], business: dict[str, Any], runs: list[dict[str, Any]],
                           observations: dict[tuple[str, int], dict[str, Any]], failures: dict[tuple[str, int], str],
                           fresh_by_model: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    orders = fresh_by_model.get("sale.order", [])
    purchases = fresh_by_model.get("purchase.order", [])
    sale_target_ids = _target_order_ids_for_runs(runs, "sale_invoice")
    purchase_target_ids = _target_order_ids_for_runs(runs, "purchase")
    order = _select_target_document(orders, sale_target_ids)
    purchase = _select_target_document(purchases, purchase_target_ids)
    order_fields = order.get("fields", {}) if order else {}
    purchase_fields = purchase.get("fields", {}) if purchase else {}
    order_name = order.get("name") if order else None
    origin = str(purchase_fields.get("origin") or "")
    order_id_set = {order.get("id")} if order and type(order.get("id")) is int else set()
    relation = bool(set(_relation_ids(purchase_fields.get("sale_order_id"))) & order_id_set) or _origin_contains_exact(origin, {order_name} if order_name else set())
    invoice_ids = set(_relation_ids(order_fields.get("invoice_ids"))) if order else set()
    invoices = [row for row in fresh_by_model.get("account.move", []) if row.get("id") in invoice_ids]
    partner_ids = _relation_ids(order_fields.get("partner_id"))
    purchase_partner_ids = _relation_ids(purchase_fields.get("partner_id"))
    lines = fresh_by_model.get("purchase.order.line", [])
    line_ids = set(_relation_ids(purchase_fields.get("order_line")))
    order_line_ids = set(_relation_ids(order_fields.get("order_line")))
    for line in lines:
        if line.get("id") not in line_ids:
            continue
        line_fields = line.get("fields", {}) if isinstance(line.get("fields"), dict) else {}
        relation = relation or bool(set(_relation_ids(line_fields.get("sale_order_id"))) & order_id_set)
        relation = relation or bool(set(_relation_ids(line_fields.get("sale_line_id"))) & order_line_ids)
    valid_lines = bool(line_ids) and line_ids.issubset({row.get("id") for row in lines}) and all(
        row.get("fields", {}).get("product_id") and row.get("fields", {}).get("product_qty") is not None
        for row in lines if row.get("id") in line_ids
    )
    order_state = order.get("state") if order else None
    purchase_state = purchase.get("state") if purchase else None
    invoice_states = [row.get("state") for row in invoices]
    partners = fresh_by_model.get("res.partner", [])
    checks = [
        _check("observed_order", "当前销售订单", "passed" if order else "unknown", "销售订单由 native read 观测。" if order else "尚未观测到唯一销售订单。"),
        _check("observed_customer", "已读取客户", "passed" if partner_ids and any(row.get("id") in partner_ids for row in partners) else "unknown", "客户关系与记录均已观测。" if partner_ids else "客户关系未观测完整。"),
        _check("observed_purchase", "当前采购订单", "passed" if purchase else "unknown", "采购订单由 native read 观测。" if purchase else "尚未观测到唯一采购订单。"),
        _check("observed_supplier", "已读取供应商", "passed" if purchase_partner_ids and any(row.get("id") in purchase_partner_ids for row in partners) else "unknown", "供应商关系与记录均已观测。" if purchase_partner_ids else "供应商关系未观测完整。"),
        _check("observed_payment_term", "已读取付款条款", "passed" if _relation_ids(order_fields.get("payment_term_id")) else "unknown", "销售订单付款条款已观测。" if _relation_ids(order_fields.get("payment_term_id")) else "付款条款未观测完整。"),
        _check("observed_document_states", "已读取单据状态", "passed" if order_state and purchase_state and invoice_states and all(invoice_states) else "unknown", "销售、采购和发票状态均已观测。" if order_state and purchase_state and invoice_states else "单据状态未完整观测。"),
        _check("order_confirmed", "销售订单已确认", "passed" if order_state in {"sale", "done"} else "failed" if order_state else "unknown", "销售订单状态为 sale。" if order_state in {"sale", "done"} else "销售订单尚未确认。" if order_state else "销售订单状态未知。"),
        _check("purchase_lines_valid", "采购行有效", "passed" if valid_lines else "failed" if line_ids else "unknown", "采购行包含商品和数量。" if valid_lines else "采购行缺少商品或数量。" if line_ids else "采购行未知。"),
        _check("purchase_confirmed", "采购订单已确认", "passed" if purchase_state in {"purchase", "done"} else "failed" if purchase_state else "unknown", "采购订单已确认。" if purchase_state in {"purchase", "done"} else "采购订单未确认。" if purchase_state else "采购订单状态未知。"),
        _check("purchase_linked_to_order", "采购单关联销售单", "passed" if relation else "failed" if purchase and order else "unknown", "采购单 origin 或结构化关系指向销售单。" if relation else "采购单未确认关联销售单。"),
        _check("invoice_linked_to_order", "发票关联销售订单", "passed" if invoices else "unknown", "销售订单的 invoice_ids 包含已读取发票。" if invoices else "未确认关联发票。"),
        _check("invoice_posted", "发票已过账", "passed" if invoices and all(value == "posted" for value in invoice_states) else "failed" if invoices and all(value is not None for value in invoice_states) else "unknown", "关联发票已过账。" if invoices and all(value == "posted" for value in invoice_states) else "关联发票未全部过账。" if invoices else "发票状态未知。"),
    ]
    return _finish_readback(state, business, runs, observations, failures, checks, "sale_purchase_invoice", business.get("completion_target", "posted"))


def _refresh_invoice_delivery(state, business, runs, reads):
    from erp_harness.erp import invoice_mail
    from erp_harness.erp.business_operations import _Evidence
    observations, failures, checks = {}, {}, []
    business["delivery_receipts"] = []
    business["delivery_receipts_run_id"] = runs[-1]["id"] if runs else None
    try:
        invoice_id, recipient_id = invoice_mail.requested(business.get("references", []))
        payload = {"model": "account.move", "method": "message_post", "instance": reads.instance,
                   "kwargs": {"ids": [invoice_id], "partner_ids": [recipient_id]}}
        runtime = reads.instances[reads.instance]
        invoice_mail.parties(_Evidence(runtime, payload), invoice_id, recipient_id)
        for ref in business["references"]:
            row, error = _read_one(reads, ref["model"], ref["id"], list(ref["fields"]))
            if error:
                failures[(ref["model"], ref["id"])] = error
                continue
            docs = collect_documents("read_record", {"model": ref["model"], "record_id": ref["id"]}, {"result": row})
            observations[(ref["model"], ref["id"])] = {**docs[0], "source": "refresh_native_read", "observed_at": _now()}
        checks.append(_check("invoice_recipient_verified", "发票公司、客户与财务收件人", "passed", "直接读取公司、商业主体、联系人用途和登记邮箱。"))
        receipts = []
        for approval in state.get("approvals", {}).values():
            evidence = (approval.get("prestate") or {}).get("invoice_mail")
            if (approval.get("business_id") != business["id"] or not evidence
                    or evidence["invoice"]["id"] != invoice_id or evidence["recipient"]["id"] != recipient_id):
                continue
            receipts.append(invoice_mail.verify(runtime, payload, evidence))
        if not receipts:
            # 已发送的发票可以只读完成；必须重新核对地址、正文、PDF 和实际通知状态。
            evidence = invoice_mail.prestate(runtime, payload)
            receipts.append(invoice_mail.verify(runtime, payload, evidence, historical=True))
        sent = any(r["status"] == "satisfied" for r in receipts)
        checks.append(_check("invoice_mail_sent", "正式 PDF 与邮件投递回执", "passed" if sent else "unknown",
                             "收件地址、PDF 校验和及 SMTP 接受状态已核对；不代表已读。" if sent else "没有确认投递成功；禁止根据 PDF 或留言状态推断发送完成。"))
        business["delivery_receipts"] = receipts
    except Exception as exc:
        checks.append(_check("invoice_recipient_verified", "发票发送核验", "unknown", str(exc)[:300]))
    return _finish_readback(state, business, runs, observations, failures, checks, "invoice_delivery", "sent")


def refresh_business(
    state: dict[str, Any],
    business_id: str,
    native_reads: Callable[[str, dict[str, Any]], Any] | Any,
) -> dict[str, Any]:
    """Re-read observed sale documents and project factual business checks.

    ``native_reads`` is a read-only callable with the same shape as
    ``NativeReads.call``.  This function never sends a write operation.  The
    newest observation for each ``(model, id)`` replaces older projections;
    historical runs remain available in the returned detail.
    """
    business = state["businesses"].get(business_id)
    if business is None:
        raise KeyError("unknown business")
    runs = sorted(
        [row for row in state.get("runs", {}).values() if row.get("business_id") == business_id],
        key=_run_sort_key,
    )
    kind = business.get("type", "sale_invoice")
    if kind == "invoice_delivery":
        return _refresh_invoice_delivery(state, business, runs, native_reads)
    if kind in ENTERPRISE_TYPES:
        target = business.get("completion_target") or default_target(kind)
        rows, failures, checks, targets = enterprise_view.readback(kind, target, runs, lambda model, record_id, fields: _read_one(native_reads, model, record_id, fields))
        observations = {}
        for (model, record_id), row in rows.items():
            projected = collect_documents("read_record", {"model": model, "record_id": record_id}, {"success": True, "result": row}, source_run_id=targets.get((model, record_id)) or (runs[-1]["id"] if runs else None))
            if projected:
                observations[(model, record_id)] = {**projected[0], "source": "refresh_native_read", "observed_at": _now()}
        return _finish_readback(state, business, runs, observations, failures, checks, kind, target)
    observations: dict[tuple[str, int], dict[str, Any]] = {}
    failures: dict[tuple[str, int], str] = {}
    fresh: set[tuple[str, int]] = set()
    for run in runs:
        for document in run.get("documents", []):
            model, record_id = document.get("model"), document.get("id")
            if model in READBACK_FIELDS and isinstance(record_id, int):
                observations[(model, record_id)] = document
    observed_documents = observations
    targets = _readback_targets(business, runs)
    if targets:
        # Keep the old discovery fallback only for businesses without bindings.
        observations = {key: observations.get(key, {"model": key[0], "id": key[1]})
                        for key in sorted(targets) if key[0] in READBACK_FIELDS}

    queue = list(observations)
    seen = set(queue)
    while queue:
        model, record_id = queue.pop(0)
        row, failure = _read_one(native_reads, model, record_id)
        if failure:
            failures[(model, record_id)] = failure
            continue
        projected = collect_documents("read_record", {"model": model, "record_id": record_id}, {"success": True, "result": row})
        if not projected:
            failures[(model, record_id)] = "native read result could not be projected"
            continue
        document = projected[0]
        previous = observed_documents.get((model, record_id), {})
        for key in ("source_run_id", "source_tool_id"):
            if key in previous:
                document[key] = previous[key]
        if "source_observed_at" in previous:
            document["source_observed_at"] = previous["source_observed_at"]
        elif isinstance(previous.get("observed_at"), str):
            document["source_observed_at"] = previous["observed_at"]
        document["observed_at"], document["source"] = _now(), "refresh_native_read"
        observations[(model, record_id)] = document
        fresh.add((model, record_id))
        for related in sorted(_related_records(model, row) - seen):
            seen.add(related)
            queue.append(related)

    fresh_by_model: dict[str, list[dict[str, Any]]] = {}
    # 旧单据仍可展示；本次检查只使用成功刷新的记录，不能让旧值冒充当前证据。
    for (model, record_id), document in observations.items():
        if (model, record_id) in fresh:
            fresh_by_model.setdefault(model, []).append(document)
    business_type = business.get("type", "sale_invoice")
    if business_type == "purchase":
        return _finish_purchase_readback(state, business, runs, observations, failures, fresh_by_model)
    if business_type == "sale_purchase_invoice":
        return _finish_chain_readback(state, business, runs, observations, failures, fresh_by_model)
    orders = fresh_by_model.get("sale.order", [])
    invoices = fresh_by_model.get("account.move", [])
    # A verified create/confirm receipt selects the current order when a run
    # also contains historical search results.  A search result alone never
    # selects a business target.
    target_ids = _target_order_ids_for_runs(runs, business_type)
    order = _select_target_document(orders, target_ids)
    order_fields = order.get("fields", {}) if order else {}
    invoice_ids = set(_relation_ids(order_fields.get("invoice_ids"))) if order else set()
    linked_invoices = [doc for doc in invoices if doc["id"] in invoice_ids]
    checks: list[dict[str, Any]] = []
    checks.append(_check("observed_order", "当前销售订单", "passed" if order else "unknown", "销售订单由 native read 观测。" if order else "读取到多个销售订单，尚未确定当前业务的目标订单。" if orders else "尚未观测到销售订单。"))
    partner_id = _relation_ids(order_fields.get("partner_id"))
    partner_seen = any(doc["id"] in partner_id for doc in fresh_by_model.get("res.partner", []))
    checks.append(_check("observed_customer", "已读取客户", "passed" if partner_seen else "unknown", "客户关系与客户记录均已观测。" if partner_seen else "客户关系或客户记录未观测完整。"))
    term = order_fields.get("payment_term_id") if order else None
    if term is None and linked_invoices:
        term = linked_invoices[0].get("fields", {}).get("invoice_payment_term_id")
    checks.append(_check("observed_payment_term", "已读取付款条款", "passed" if _relation_ids(term) else "unknown", "付款条款关系已由读取结果提供。" if _relation_ids(term) else "读取结果没有付款条款关系。"))
    order_state = order.get("state") if order else None
    invoice_states = [doc.get("state") for doc in linked_invoices]
    target = business.get("completion_target", "posted")
    states_known = bool(order_state) if target in {"draft", "confirmed"} else bool(order_state) and bool(invoice_states) and all(bool(value) for value in invoice_states)
    state_detail = "报价状态已观测。" if target == "draft" else "销售订单状态已观测。" if target == "confirmed" else "订单和关联发票状态均已观测。"
    missing_state_detail = "报价状态未完整观测。" if target == "draft" else "销售订单状态未完整观测。" if target == "confirmed" else "订单或关联发票状态未完整观测。"
    checks.append(_check("observed_document_states", "已读取单据状态", "passed" if states_known else "unknown", state_detail if states_known else missing_state_detail))
    order_confirmed_status = (
        "passed" if order and order_state == "sale"
        else "failed" if order and order_state is not None
        else "unknown"
    )
    checks.append(_check("order_confirmed", "销售订单已确认", order_confirmed_status,
                         "销售订单状态为 sale。" if order_confirmed_status == "passed" else
                         "销售订单存在但尚未处于 sale 状态。" if order_confirmed_status == "failed" else
                         "没有足够读取结果确认销售订单状态。"))
    if business.get("completion_target", "posted") == "draft":
        checks.append(_check("order_draft", "销售订单为草稿", "passed" if order and order_state == "draft" else "failed" if order and order_state is not None else "unknown",
                             "销售订单状态为 draft。" if order and order_state == "draft" else "销售订单存在但不是 draft。" if order else "没有足够读取结果确认销售订单状态。"))
    linked_status = "passed" if linked_invoices else "unknown"
    checks.append(_check("invoice_linked_to_order", "发票关联销售订单", linked_status, "销售订单的 invoice_ids 包含已读取发票。" if linked_invoices else "未从读取结果确认销售订单与发票关联。"))
    invoice_state_values = [doc.get("state") for doc in linked_invoices]
    posted_status = (
        "passed" if linked_invoices and all(value == "posted" for value in invoice_state_values)
        else "failed" if linked_invoices and all(value is not None for value in invoice_state_values)
        else "unknown"
    )
    checks.append(_check("invoice_posted", "发票已过账", posted_status, "关联发票状态为 posted。" if posted_status == "passed" else "关联发票存在但状态不是 posted。" if posted_status == "failed" else "没有足够读取结果确认发票过账。"))
    return _finish_readback(state, business, runs, observations, failures, checks, business_type, target)



def _action_execution_receipt(row, run, titles):
    if row.get("receipt_error") or not isinstance(row.get("payload"), dict) or any(row.get(key) is not None and not isinstance(row[key], dict) for key in ("prestate", "verification")):
        raise ValueError("receipt structure invalid")
    payload, verification = row.get("payload") or {}, row.get("verification") or {}
    model = payload.get("model", "")
    operation = payload.get("operation") or payload.get("method", "")
    ids = payload.get("record_ids") or (payload.get("kwargs") or {}).get("ids") or []
    evidence = verification.get("evidence") or {}
    status = row.get("status")
    verified = status == "verified" and verification.get("status") == "satisfied"
    public_status = "verified" if verified else "pending" if status in {"pending_approval", "approved"} else "failed" if status == "known_failed" else "unknown"
    if status in {"known_failed", "expired"} and not row.get("sent_at"):
        public_status = "not_executed"
    detail = {"verified": "动作已执行并核验；不代表整项业务目标全部完成。", "pending": "等待审批或执行。",
              "failed": "动作未通过执行核验。", "not_executed": "本动作未发送至 Odoo。", "unknown": "写入结果或核验依据不完整，需要核对，不能自动重试。"}[public_status]
    if status == "expired" and public_status == "not_executed":
        detail = "审批已过期，本动作未发送至 Odoo。"
    item = {"id": row["action_id"], "action_id": row["action_id"], "run_id": run["id"],
            "kind": "action", "model": model, "record_ids": ids, "status": public_status,
            "title": titles.get((model, operation), {"create": "创建业务记录", "write": "更新业务记录", "unlink": "删除业务记录"}.get(operation, "执行业务动作")), "detail": detail}
    stamp = row.get("finished_at") or row.get("created_at")
    if type(stamp) in {int, float}:
        try:
            item["observed_at"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError, OSError):
            pass  # Invalid timestamps do not invent a date or hide otherwise valid effects.
    if evidence.get("invoice_records"):
        item["model"], item["record_ids"] = "account.move", [r["id"] for r in evidence["invoice_records"] if isinstance(r, dict) and isinstance(r.get("id"), int)]
    elif operation == "create":
        item["record_ids"] = evidence.get("record_ids", [])
    if (model, operation) == ("account.move.send.wizard", "action_send_and_print"):
        item["detail"] += " 此步骤只生成 PDF，不证明邮件已发送。"
    if row.get("kind") == "chatter":
        item["title"] = "发布单据留言"
        item["detail"] += " 留言不是邮件投递回执。"
    mail = (row.get("prestate") or {}).get("invoice_mail")
    if mail:
        delivered = verified and evidence.get("delivery") == "smtp_accepted"
        item.update({"kind": "email", "title": "邮件已交付服务器" if delivered else "邮件投递未确认",
                     "status": "verified" if delivered else public_status if public_status != "verified" else "unknown",
                     "detail": ("SMTP 已接受；不能证明收件人已打开或阅读。" if delivered else "本动作没有已核验的邮件投递回执，需核对后再决定下一步。") +
                               f" 收件地址：{mail.get('email_to') or '未读取'}；附件：{(mail.get('attachment') or {}).get('name') or '未读取'}。"})
    return item


def _run_execution_receipts(state: dict[str, Any], runs: list[dict[str, Any]], approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Latest-run effects from the ledger, independently of worker success or model prose."""
    if not runs:
        return []
    run = runs[0]
    archive = state.get("receipt_ledger", {}).get(run["id"], {})
    rows = archive.get("rows", [])
    titles = {
        ("sale.advance.payment.inv", "create"): "准备销售开票",
        ("sale.advance.payment.inv", "create_invoices"): "生成客户发票",
        ("account.move", "action_post"): "发票或凭证过账",
        ("account.move.send.wizard", "create"): "保存发票文件处理选项",
        ("account.move.send.wizard", "action_send_and_print"): "生成正式发票 PDF",
        ("sale.order", "action_confirm"): "确认销售订单",
        ("purchase.order", "button_confirm"): "确认采购订单",
        ("account.payment.register", "action_create_payments"): "登记收付款",
        ("account.move.reversal", "reverse_moves"): "生成贷项通知",
        ("stock.picking", "button_validate"): "处理库存收发",
        ("mrp.production", "button_mark_done"): "登记制造完工",
        ("account.move.line", "reconcile"): "核销会计分录",
    }
    output = []
    row_errors = False
    for index, row in enumerate(rows):
        try:
            item = _action_execution_receipt(row, run, titles)
        except (AttributeError, KeyError, TypeError, ValueError):
            row_errors = True
            action_id = row.get("action_id") if isinstance(row, dict) else None
            item = {"id": action_id or f"invalid:{run['id']}:{index}", "kind": "action", "status": "unknown",
                    "title": "动作回执无法核验", "detail": "该条记录结构不完整；其他正常回执仍可查看，不得据此重试写入。",
                    "run_id": run["id"], **({"action_id": action_id} if action_id else {})}
        output.append(item)
    # A denied email method may have no registered action. Preserve its tool evidence.
    denied_mail = []
    for tool in run.get("tools", []):
        args, result = tool.get("arguments") or {}, tool.get("result") or {}
        if (not isinstance(args, dict) or not isinstance(result, dict) or result.get("success") is not False or not result.get("error") or
                tool.get("name", "").removeprefix("mcp_odoo_") != "execute_method"):
            continue
        blocked_wizard = args.get("model") == "account.move.send.wizard" and args.get("method") == "action_send_and_print" and "email sending is blocked" in str(result["error"])
        rejected_mail = args.get("model") == "account.move" and args.get("method") == "message_post" and not result.get("action_id")
        if blocked_wizard or rejected_mail:
            missing_target = "invoice delivery requires exactly one invoice, company and recipient reference" in str(result["error"])
            denied_mail.append({"id": f"mail:{tool['id']}", "kind": "email", "status": "failed", "title": "邮件发送未完成",
                           "detail": "未确认唯一的发票、公司与收件人，邮件发送被拒绝。" if missing_target else "本轮通过 PDF 向导发送邮件的请求被运行时拒绝，没有发送回执。" if blocked_wizard else "邮件发送请求未通过校验：" + str(result["error"])[:180],
                           "run_id": run["id"], "tool_id": tool["id"], "observed_at": tool.get("ended_at")})
    if denied_mail:
        output.append(next((row for row in denied_mail if row["detail"].startswith("未确认唯一")), denied_mail[0]))
    business = state["businesses"][run["business_id"]]
    if business.get("delivery_receipts_run_id") == run["id"] and not any(item["kind"] == "email" and item["status"] == "verified" for item in output):
        for receipt in business.get("delivery_receipts", []):
            evidence = receipt.get("evidence") or {}
            if receipt.get("status") == "satisfied" and evidence.get("delivery") == "smtp_accepted" and type(evidence.get("invoice_id")) is int:
                output.append({"id": f"delivery:{run['id']}", "kind": "email", "status": "verified", "title": "邮件投递回执已核验",
                               "detail": f"本轮核对到给 {evidence.get('email_to') or '登记地址'} 的 SMTP 接受回执；不能证明收件人已读，也不代表本轮重新发送。",
                               "run_id": run["id"], "model": "account.move", "record_ids": [evidence["invoice_id"]]})
                break
    if not archive.get("available"):
        for approval in approvals:
            if approval.get("run_id") == run["id"]:
                output.append({"id": approval["action_id"], "kind": "action", "status": "unknown", "title": "动作账本不可用",
                               "detail": "审批记录仍在，但无法据此确认动作执行结果。", "run_id": run["id"], "action_id": approval["action_id"]})
    if not any(item["kind"] == "email" for item in output):
        output.append({"id": f"mail:{run['id']}", "kind": "email", "status": "not_observed", "title": "邮件：无发送记录",
                       "detail": "本轮没有邮件发送回执。PDF、单据留言和模型总结都不能证明邮件发送。", "run_id": run["id"]})
    trace_present = bool(run.get("tools") or run.get("events"))
    output.append({"id": f"archive:{run['id']}", "kind": "archive", "status": "verified" if archive.get("available") and trace_present and not row_errors else "unknown",
                   "title": "执行记录已留档" if archive.get("available") and trace_present and not row_errors else "执行留档不完整",
                   "detail": f"本轮保留 {len(rows)} 条动作账本记录与 {len(run.get('tools', []))} 次工具记录，可查看运行详情或导出业务回执。" if archive.get("available") and trace_present and not row_errors else "动作账本或工具记录缺失、不可读或结构损坏；已有日志不能代替缺失的执行证据。",
                   "run_id": run["id"]})
    return output


def _execution_receipts(state: dict[str, Any], runs: list[dict[str, Any]], approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    historical = []
    for run in reversed(runs[1:]):
        for item in _run_execution_receipts(state, [run], approvals):
            if item["kind"] == "action" or item["kind"] == "email" and item["status"] != "not_observed":
                historical.append({**item, "title": "此前运行 · " + item["title"]})
    return historical + _run_execution_receipts(state, runs, approvals)


def business_detail(state: dict[str, Any], business_id: str) -> dict[str, Any]:
    business = state["businesses"].get(business_id)
    if business is None:
        raise KeyError("unknown business")
    business_type = business.get("type", "sale_invoice")
    completion_target = business.get("completion_target") or default_target(business_type)
    runs = sorted(
        [row for row in state["runs"].values() if row.get("business_id") == business_id],
        key=_run_sort_key,
        reverse=True,
    )
    approvals = [row for row in state.get("approvals", {}).values() if row.get("business_id") == business_id]
    docs: dict[tuple[str, int], dict[str, Any]] = {}
    checks: dict[str, dict[str, Any]] = {}
    for run in reversed(runs):
        for doc in run.get("documents", []):
            docs[(doc["model"], doc["id"])] = doc
    readback = business.get("readback")
    if isinstance(readback, dict) and isinstance(readback.get("checks"), list):
        for check in readback["checks"]:
            if isinstance(check, dict):
                checks[check.get("name", "unknown")] = check
    elif runs:
        for check in runs[0].get("checks", []):
            checks[check.get("name", "unknown")] = check
    current_run_id = runs[0].get("id") if runs else None
    readback_run_id = readback.get("latest_run_id") if isinstance(readback, dict) else None
    readback_current = isinstance(readback, dict) and readback_run_id == current_run_id
    readback_documents = readback.get("documents", []) if isinstance(readback, dict) and isinstance(readback.get("documents"), list) else []
    readback_fresh = readback_current and not readback.get("stale") and not any(
        document.get("observed_at", "") > readback.get("observed_at", "") for document in docs.values()
    )
    matching_readback_documents = readback_documents if readback_fresh else None
    public_runs = []
    for run in runs:
        public = {key: value for key, value in run.items() if key not in {"_round_tool_start", "_compaction_known_total", "assistant_text", "rounds", "tools", "events", "live_messages", "_message_sequences", "finalized_message_ids", "instruction"}}
        usage = public.get("usage")
        if isinstance(usage, dict):
            public["usage"] = {key: usage.get(key) for key in
                                ("input", "cache_read", "output", "reasoning", "total",
                                 "reported_total", "missing_usage_rounds", "compaction_total", "compaction_calls")}
            public["usage"]["cache_read"] = usage.get("cache_read", usage.get("cacheRead"))
        linked_evidence = _run_has_relevant_evidence(run, matching_readback_documents, business_type, completion_target)
        if isinstance(readback, dict) and run.get("id") == readback_run_id:
            public["verification_status"] = (
                _outcome(list(checks.values()), business_type, completion_target)["status"]
                if readback_fresh and linked_evidence else "unknown"
            )
        elif isinstance(readback, dict) and run.get("id") == current_run_id:
            public["verification_status"] = "unknown"
        public_runs.append(public)
    if isinstance(readback, dict):
        for document in readback_documents:
            if isinstance(document, dict) and isinstance(document.get("model"), str) and isinstance(document.get("id"), int):
                key = (document["model"], document["id"])
                if document.get("observed_at", "") >= docs.get(key, {}).get("observed_at", ""):
                    docs[key] = document
    detail_stale = bool(readback.get("stale")) if isinstance(readback, dict) else bool(runs and runs[0].get("stale", False))
    if isinstance(readback, dict) and not readback_fresh:
        detail_stale = True
    observed_at = (
        readback.get("observed_at") if isinstance(readback, dict)
        else max((doc.get("observed_at", "") for doc in docs.values()), default=None)
    )
    factual_checks = list(checks.values())
    required_checks = _required_check_names(business_type, completion_target)
    visible_checks = [row for row in factual_checks if row.get("name") in required_checks or str(row.get("name", "")).startswith(("read_", "requested_reference_"))]
    public_documents = _annotate_document_scope(list(docs.values()), runs, business_type, _readback_targets(business, runs))
    outcome = _outcome(factual_checks, business_type, completion_target)
    if isinstance(readback, dict) and not readback_fresh:
        outcome = {**outcome, "status": "unknown", "detail": "存在较新的运行、单据观察或不完整回读，请重新读取状态。"}
    execution = _execution_projection(
        runs,
        public_documents,
        [] if detail_stale else visible_checks,
        outcome.get("status", "unknown"),
        approvals,
        matching_readback_documents,
        business_type,
        completion_target,
    )
    live_messages = []
    for run in runs:
        for message in run.get("live_messages", []) if isinstance(run.get("live_messages"), list) else []:
            if isinstance(message, dict):
                live_messages.append(dict(message))
    artifacts = [dict(item) for item in business.get("artifacts", []) if isinstance(item, dict)]
    material_rows = []
    for material_id in business.get("material_ids", []) if isinstance(business.get("material_ids"), list) else []:
        material = state.get("materials", {}).get(material_id)
        if isinstance(material, dict):
            material_rows.append({key: material.get(key) for key in ("id", "session_id", "name", "size", "sha256", "created_at", "row_count", "preview", "media_type")})
    business_public = dict(business)
    business_public["materials"] = material_rows
    return {"business": business_public, "runs": public_runs, "approvals": approvals, "receipts": _execution_receipts(state, runs, approvals),
            "artifacts": artifacts, "materials": material_rows, "live_messages": live_messages, "documents": public_documents, "checks": visible_checks,
            "observed_at": observed_at,
            "stale": detail_stale,
            "summary": next((run.get("summary") for run in runs if run.get("summary")), None),
            "activity": _activity(runs, approvals, business.get("readback_status")), "execution": execution, "outcome": outcome}
