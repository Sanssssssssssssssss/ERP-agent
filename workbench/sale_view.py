from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


READBACK_FIELDS: dict[str, tuple[str, ...]] = {
    "res.partner": ("id", "name", "display_name", "email"),
    "sale.order": ("id", "name", "state", "partner_id", "amount_total", "currency_id", "payment_term_id", "order_line", "invoice_ids", "picking_ids", "invoice_status", "commitment_date", "client_order_ref"),
    "sale.order.line": ("id", "name", "order_id", "product_id", "product_uom_qty", "product_uom_id", "price_unit", "price_subtotal", "price_total"),
    "account.move": ("id", "name", "state", "move_type", "partner_id", "amount_total", "currency_id", "invoice_payment_term_id", "invoice_origin", "invoice_line_ids", "payment_state", "amount_residual", "invoice_date"),
    "account.move.line": ("id", "name", "move_id", "product_id", "quantity", "product_uom_id", "price_unit", "price_subtotal", "price_total"),
    "stock.picking": ("id", "name", "state", "sale_id", "origin", "partner_id", "scheduled_date"),
}
RELATION_FIELDS: dict[str, tuple[str, ...]] = {
    "sale.order": ("partner_id", "order_line", "invoice_ids", "picking_ids"),
    "sale.order.line": ("order_id",),
    "account.move": ("partner_id", "invoice_line_ids"),
    "account.move.line": ("move_id",),
    "stock.picking": ("sale_id", "partner_id"),
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
    if tool not in {"read_record", "search_records"} or not isinstance(payload, dict):
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


def _read_one(reads: Callable[[str, dict[str, Any]], Any] | Any, model: str, record_id: int) -> tuple[dict[str, Any] | None, str | None]:
    arguments = {"model": model, "record_id": record_id, "fields": list(READBACK_FIELDS[model])}
    try:
        call = reads if callable(reads) else getattr(reads, "call")
        return _read_result(call("read_record", arguments), record_id)
    except Exception as exc:  # noqa: BLE001 - readback must preserve stale state
        return None, f"{type(exc).__name__}: {exc}"[:300]


def _run_sort_key(run: dict[str, Any]) -> tuple[str, str]:
    return (str(run.get("started_at") or ""), str(run.get("ended_at") or ""))


def _activity(runs: list[dict[str, Any]], approvals: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"phase": "idle", "label": "等待开始", "detail": "业务已建立，尚未启动执行。"}
    run = runs[0]
    status = run.get("status")
    tools = run.get("tools") if isinstance(run.get("tools"), list) else []
    latest_tool = next((tool for tool in reversed(tools) if isinstance(tool, dict)), None)
    pending = any(
        row.get("run_id") == run.get("id") and row.get("status") == "pending_approval"
        for row in approvals
        if isinstance(row, dict)
    ) or bool(run.get("pending_approval_action_ids"))
    if status == "needs_reconciliation":
        phase, label, detail = "reconciliation", "需要核对", "存在不确定写入，禁止自动重试。"
    elif status == "interrupted":
        phase, label, detail = "interrupted", "已中断", "运行因主机或进程中断而停止。"
    elif status == "cancelled":
        phase, label, detail = "cancelled", "已取消", "运行已取消，未据此判断业务结果。"
    elif status == "failed":
        phase, label, detail = "failed", "执行失败", "运行失败；请查看 trace 和独立核验。"
    elif status == "completed":
        phase, label, detail = "completed", "已结束", "运行已结束；业务结果请查看独立核验。"
    elif status == "awaiting_approval" or (status == "running" and pending):
        phase, label, detail = "approval", "等待审批", "存在待处理的 ERP 写入审批。"
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
    latest_public_text = next(
        (row.get("text", "").strip()[:1200] for row in reversed(rounds)
         if isinstance(row, dict) and isinstance(row.get("text"), str) and row.get("text", "").strip()),
        None,
    )
    if latest_public_text:
        activity["intent"] = latest_public_text
    if latest_tool:
        if isinstance(latest_tool.get("name"), str) and latest_tool.get("name"):
            activity["tool_name"] = latest_tool["name"]
        if phase == "tool" and isinstance(latest_tool.get("round"), int):
            activity["round"] = latest_tool["round"]
        activity["at"] = latest_tool.get("ended_at") or latest_tool.get("started_at")
    events = run.get("events") if isinstance(run.get("events"), list) else []
    if events and isinstance(events[-1], dict):
        last = events[-1]
        if isinstance(last.get("type"), str):
            activity["last_event"] = last["type"]
        activity["at"] = last.get("at") or activity.get("at")
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


def _evidence(document: dict[str, Any], label: str) -> dict[str, Any] | None:
    run_id, tool_id = document.get("source_run_id"), document.get("source_tool_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    kind = "readback" if document.get("source") == "refresh_native_read" else "tool"
    item: dict[str, Any] = {"run_id": run_id, "kind": kind, "label": "独立回读快照" if kind == "readback" else label}
    if document.get("source") == "native_read_receipt" and isinstance(tool_id, str) and tool_id:
        item["tool_id"] = tool_id
    if isinstance(document.get("observed_at"), str):
        item["observed_at"] = document["observed_at"]
    return item


def _action_stage(model: Any, operation: Any) -> str | None:
    model, operation = str(model or "").lower(), str(operation or "").lower()
    if model == "sale.order" and operation == "action_confirm":
        return "confirm"
    if model == "sale.order" and operation == "create":
        return "quote"
    if (model == "account.move" and operation in {"create", "action_post"}) or (model == "sale.advance.payment.inv" and operation in {"create", "create_invoices"}):
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
    return "read" if model in {"sale.order", "account.move"} and operation in {None, "read_record", "search_records"} else None


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
    run: dict[str, Any], readback_documents: list[dict[str, Any]] | None = None
) -> bool:
    documents = run.get("documents") if isinstance(run.get("documents"), list) else []
    orders = [doc for doc in documents if isinstance(doc, dict) and doc.get("model") == "sale.order" and doc.get("source_run_id") == run.get("id")]
    if not orders:
        return False
    observed_order_ids = {doc.get("id") for doc in orders if type(doc.get("id")) is int}
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
) -> dict[str, Any]:
    evidence_by_stage: dict[str, list[dict[str, Any]]] = {stage_id: [] for stage_id, _ in STAGES}
    observed_by_stage: dict[str, bool] = {stage_id: False for stage_id, _ in STAGES}
    awaiting_stage_ids: set[str] = set()
    for document in documents:
        label = f"读取 {document.get('model', '单据')} {document.get('name') or document.get('id')}"
        item = _evidence(document, label)
        model = document.get("model")
        fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
        relevant = model in {"sale.order", "account.move"}
        if relevant:
            observed_by_stage["read"] = True
        if item is None:
            if model == "sale.order":
                observed_by_stage["quote"] |= fields.get("amount_total") is not None or fields.get("order_line") is not None
                observed_by_stage["confirm"] = True
            elif model == "account.move":
                observed_by_stage["invoice"] = True
            continue
        evidence_by_stage["read"].append(item)
        if model == "sale.order":
            if fields.get("amount_total") is not None or fields.get("order_line") is not None:
                evidence_by_stage["quote"].append(item)
            evidence_by_stage["confirm"].append(item)
        elif model == "account.move":
            evidence_by_stage["invoice"].append(item)

    verified_actions: dict[str, list[dict[str, Any]]] = {stage_id: [] for stage_id, _ in STAGES}
    for run in runs:
        tools = run.get("tools") if isinstance(run.get("tools"), list) else []
        for tool in tools:
            result = tool.get("result") if isinstance(tool, dict) and isinstance(tool.get("result"), dict) else {}
            verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
            if result.get("action_status") != "verified" or verification.get("status") != "satisfied":
                continue
            arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
            model, operation = _tool_action_fields(tool)
            stage_id = _action_stage(model, operation)
            if not stage_id or not isinstance(run.get("id"), str):
                continue
            verified_actions[stage_id].append({"run_id": run["id"], "tool_id": tool.get("id"), "action_id": tool.get("action_id") or result.get("action_id"), "kind": "action", "model": model, "operation": operation, "label": "结构化 ERP 动作已核验"})

    for approval in approvals or []:
        if approval.get("status") != "pending_approval" or not isinstance(approval.get("run_id"), str):
            continue
        operation = str(approval.get("operation") or approval.get("method") or "")
        stage_id = _action_stage(approval.get("model"), operation)
        if not stage_id:
            continue
        item = {"run_id": approval["run_id"], "action_id": approval.get("action_id"), "kind": "action", "label": "等待主机审批的 ERP 动作"}
        evidence_by_stage[stage_id].append({key: value for key, value in item.items() if value})
        awaiting_stage_ids.add(stage_id)

    for approval in approvals or []:
        verification = approval.get("verification") if isinstance(approval.get("verification"), dict) else {}
        if approval.get("status") != "verified" or verification.get("status") != "satisfied":
            continue
        stage_id = _action_stage(approval.get("model"), approval.get("operation"))
        if stage_id and isinstance(approval.get("run_id"), str):
            verified_actions[stage_id].append({"run_id": approval["run_id"], "action_id": approval.get("action_id"), "kind": "action", "model": approval.get("model"), "operation": approval.get("operation") or approval.get("method"), "label": "结构化 ERP 动作已核验"})

    for stage_id, items in verified_actions.items():
        evidence_by_stage[stage_id].extend({key: value for key, value in item.items() if value} for item in items)

    evidence_by_stage["verify"] = list(evidence_by_stage["read"])
    evidence_by_stage["verify"].extend(item for stage_id in ("confirm", "invoice") for item in verified_actions[stage_id])
    observed_by_stage["verify"] = observed_by_stage["read"]

    check_by_name = {row.get("name"): row for row in checks if isinstance(row, dict)}
    current_run_id = runs[0].get("id") if runs else None
    stages: list[dict[str, Any]] = []
    for stage_id, label in STAGES:
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
                current_relevant = _run_has_relevant_evidence(current_run, readback_documents) if current_run else False
                check_complete = check_by_name.get("invoice_posted", {}).get("status") == "passed" and check_by_name.get("invoice_linked_to_order", {}).get("status") == "passed" and current_relevant
                if check_complete:
                    status, detail = "verified", "结构化发票过账动作及关联回读已核验。"
                else:
                    status, detail = "observed", "已核验发票动作，但仍缺少当前运行的过账及关联回读。"
            else:
                status = {"passed": "verified", "failed": "failed", "unknown": "unknown"}.get(check.get("status"), "unknown")
                detail = check.get("detail", detail)
        elif stage_id == "verify":
            current_read = [row for row in evidence_by_stage["read"] if not current_run_id or row.get("run_id") == current_run_id]
            current_run = runs[0] if runs else None
            current_relevant = _run_has_relevant_evidence(current_run, readback_documents) if current_run else False
            if outcome_status == "passed" and current_relevant:
                status, detail = "verified", "销售订单和关联发票的基础检查均通过。"
            elif outcome_status == "failed" and current_relevant:
                status, detail = "failed", "基础销售开票检查未通过。"
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
            _action_stage(row.get("model"), row.get("operation"))
            for row in approvals or []
            if row.get("action_id") in pending and row.get("status") == "pending_approval"
        ]
        current_stage_id = next((stage for stage in pending_stages if stage), None)
    elif current_run and current_run.get("status") == "running":
        tools = current_run.get("tools") if isinstance(current_run.get("tools"), list) else []
        current_stage_id = next((_tool_stage(tool) for tool in reversed(tools) if isinstance(tool, dict) and _tool_stage(tool)), None)
        if not current_stage_id:
            pending = set(current_run.get("pending_approval_action_ids") or [])
            current_stage_id = next((_action_stage(row.get("model"), row.get("operation") or row.get("method")) for row in approvals or [] if row.get("action_id") in pending), None)
    result: dict[str, Any] = {"stages": stages}
    if isinstance(current_run_id, str):
        result["run_id"] = current_run_id
    if current_stage_id:
        result["current_stage_id"] = current_stage_id
    return result


def _outcome(checks: list[dict[str, Any]]) -> dict[str, str]:
    required = {"observed_order", "observed_customer", "observed_payment_term", "observed_document_states", "order_confirmed", "invoice_linked_to_order", "invoice_posted"}
    by_name = {row.get("name"): row for row in checks if isinstance(row, dict)}
    statuses = [by_name.get(name, {}).get("status") for name in required]
    linked = required.issubset(by_name) and all(by_name[name].get("source") == "native_readback" for name in required)
    if not linked or any(status == "failed" for status in statuses):
        status = "failed" if "failed" in statuses else "unknown"
    elif all(status == "passed" for status in statuses):
        status = "passed"
    else:
        status = "unknown"
    detail = {
        "passed": "销售订单已确认，关联发票已过账。",
        "failed": "销售订单或关联发票的基础检查未通过。",
        "unknown": "缺少足够的结构化读取证据，暂不能判断销售开票结果。",
    }[status]
    return {"status": status, "label": "销售与开票基础检查", "detail": detail, "scope": "sale_invoice_basic_checks"}


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
    observations: dict[tuple[str, int], dict[str, Any]] = {}
    failures: dict[tuple[str, int], str] = {}
    fresh: set[tuple[str, int]] = set()
    for run in runs:
        for document in run.get("documents", []):
            model, record_id = document.get("model"), document.get("id")
            if model in READBACK_FIELDS and isinstance(record_id, int):
                observations[(model, record_id)] = document

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
        previous = observations.get((model, record_id), {})
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
        for field in RELATION_FIELDS.get(model, ()):
            for related_id in _relation_ids(row.get(field)):
                related_model = {
                    ("sale.order", "partner_id"): "res.partner",
                    ("sale.order", "order_line"): "sale.order.line",
                    ("sale.order", "invoice_ids"): "account.move",
                    ("sale.order", "picking_ids"): "stock.picking",
                    ("sale.order.line", "order_id"): "sale.order",
                    ("account.move", "partner_id"): "res.partner",
                    ("account.move", "invoice_line_ids"): "account.move.line",
                    ("account.move.line", "move_id"): "account.move",
                    ("stock.picking", "sale_id"): "sale.order",
                    ("stock.picking", "partner_id"): "res.partner",
                }.get((model, field))
                if related_model and (related_model, related_id) not in seen:
                    seen.add((related_model, related_id))
                    queue.append((related_model, related_id))

    fresh_by_model: dict[str, list[dict[str, Any]]] = {}
    for (model, record_id), document in observations.items():
        if (model, record_id) in fresh:
            fresh_by_model.setdefault(model, []).append(document)
    orders = fresh_by_model.get("sale.order", [])
    invoices = fresh_by_model.get("account.move", [])
    # Reading a search result does not select one of its orders as this task's target.
    order = orders[0] if len(orders) == 1 else None
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
    states_known = bool(order_state) and bool(invoice_states) and all(bool(value) for value in invoice_states)
    checks.append(_check("observed_document_states", "已读取单据状态", "passed" if states_known else "unknown", "订单和关联发票状态均已观测。" if states_known else "订单或关联发票状态未完整观测。"))
    order_confirmed_status = (
        "passed" if order and order_state == "sale"
        else "failed" if order and order_state is not None
        else "unknown"
    )
    checks.append(_check("order_confirmed", "销售订单已确认", order_confirmed_status,
                         "销售订单状态为 sale。" if order_confirmed_status == "passed" else
                         "销售订单存在但尚未处于 sale 状态。" if order_confirmed_status == "failed" else
                         "没有足够读取结果确认销售订单状态。"))
    linked_status = "passed" if linked_invoices else "unknown"
    checks.append(_check("invoice_linked_to_order", "发票关联销售订单", linked_status, "销售订单的 invoice_ids 包含已读取发票。" if linked_invoices else "未从读取结果确认销售订单与发票关联。"))
    invoice_state_values = [doc.get("state") for doc in linked_invoices]
    posted_status = (
        "passed" if linked_invoices and all(value == "posted" for value in invoice_state_values)
        else "failed" if linked_invoices and all(value is not None for value in invoice_state_values)
        else "unknown"
    )
    checks.append(_check("invoice_posted", "发票已过账", posted_status, "关联发票状态为 posted。" if posted_status == "passed" else "关联发票存在但状态不是 posted。" if posted_status == "failed" else "没有足够读取结果确认发票过账。"))
    for (model, record_id), failure in failures.items():
        checks.append(_check(f"read_{model}_{record_id}", f"读取 {model} {record_id}", "unknown", f"读取失败：{failure}"))

    verification_status = (
        "failed" if any(c["status"] == "failed" for c in checks)
        else "passed" if checks and all(c["status"] == "passed" for c in checks)
        else "unknown"
    )
    business["readback"] = {
        "documents": list(observations.values()),
        "checks": checks,
        "observed_at": _now(),
        "stale": bool(failures),
        "latest_run_id": runs[-1].get("id") if runs else None,
        "verification_status": verification_status,
        "outcome": _outcome(checks),
    }
    return business_detail(state, business_id)


def business_detail(state: dict[str, Any], business_id: str) -> dict[str, Any]:
    business = state["businesses"].get(business_id)
    if business is None:
        raise KeyError("unknown business")
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
        public = {key: value for key, value in run.items() if key not in {"_round_tool_start", "assistant_text", "rounds", "tools", "events"}}
        usage = public.get("usage")
        if isinstance(usage, dict):
            public["usage"] = {"input": usage.get("input"), "cache_read": usage.get("cache_read", usage.get("cacheRead")),
                                "output": usage.get("output"), "reasoning": usage.get("reasoning"), "total": usage.get("total")}
        linked_evidence = _run_has_relevant_evidence(run, matching_readback_documents)
        if isinstance(readback, dict) and run.get("id") == readback_run_id:
            public["verification_status"] = (
                _outcome(list(checks.values()))["status"]
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
    outcome = _outcome(factual_checks)
    if isinstance(readback, dict) and not readback_fresh:
        outcome = {**outcome, "status": "unknown", "detail": "存在较新的运行、单据观察或不完整回读，请重新读取状态。"}
    execution = _execution_projection(
        runs,
        list(docs.values()),
        [] if detail_stale else factual_checks,
        outcome.get("status", "unknown"),
        approvals,
        matching_readback_documents,
    )
    return {"business": business, "runs": public_runs, "approvals": approvals,
            "documents": list(docs.values()), "checks": factual_checks,
            "observed_at": observed_at,
            "stale": detail_stale,
            "summary": next((run.get("summary") for run in runs if run.get("summary")), None),
            "activity": _activity(runs, approvals), "execution": execution, "outcome": outcome}
