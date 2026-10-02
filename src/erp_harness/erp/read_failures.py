"""Public read diagnostics; never expose exception bodies or credential values."""
from __future__ import annotations

import ast
import errno
import re
import socket
import ssl

from pydantic import ValidationError

# Stable public guidance is shared by tool replies and execution diagnostics.
# Arbitrary error bodies never become recovery instructions.
FAILURE_GUIDANCE = {
    "connection_unconfigured": ("configuration", "configure_connection"),
    "instance_unknown": ("configuration", "select_configured_instance"),
    "connection_timeout": ("odoo_transport", "check_connection"),
    "connection_refused": ("odoo_transport", "check_service"),
    "connection_unavailable": ("odoo_transport", "check_connection"),
    "dns_failed": ("odoo_transport", "check_address"),
    "tls_error": ("odoo_transport", "check_tls"),
    "database_unavailable": ("configuration", "check_database"),
    "authentication_failed": ("authentication", "check_credentials"),
    "field_policy_denied": ("authorization", "check_field_policy"),
    "permission_denied": ("authorization", "check_permissions"),
    "rate_limited": ("odoo_transport", "wait_then_recheck"),
    "endpoint_not_found": ("configuration", "check_endpoint"),
    "record_unavailable": ("business_reference", "resolve_reference"),
    "query_invalid": ("tool_arguments", "correct_query"),
    "invalid_response": ("odoo_response", "check_service_logs"),
    "response_too_large": ("odoo_response", "reduce_read_size"),
    "server_error": ("odoo_server", "check_service_logs"),
    "read_failed_unknown": ("unknown", "check_odoo_connection"),
    "tool_failed_unknown": ("unknown", "diagnose_current_run"),
    "tool_arguments_invalid": ("tool_arguments", "correct_arguments"),
    "local_resource_missing": ("local_resource", "check_local_resource"),
    "knowledge_index_required": ("knowledge", "index_knowledge"),
    "knowledge_capacity_exceeded": ("knowledge", "reduce_index_scope"),
    "knowledge_identity_changed": ("authorization", "use_current_identity"),
    "knowledge_refresh_conflict": ("knowledge", "index_knowledge"),
    "knowledge_storage_failed": ("local_resource", "check_knowledge_store"),
    "tool_execution_failed": ("runtime", "check_host_runtime"),
    "task_not_found": ("local_resource", "list_async_tasks"),
    "task_limit_reached": ("runtime", "wait_then_recheck"),
    "task_not_cancellable": ("tool_contract", "inspect_task_status"),
    "action_identity_changed": ("authorization", "renew_proposal"),
    "action_policy_changed": ("action_policy", "validate_again"),
    "action_prestate_changed": ("business_precondition", "read_then_validate"),
    "approval_required": ("authorization", "review_existing_approval"),
    "approval_invalid": ("authorization", "validate_again"),
    "approval_expired": ("authorization", "validate_again"),
    "writes_disabled": ("action_policy", "request_host_enablement"),
    "action_resource_busy": ("action_ledger", "reconcile_without_replay"),
    "action_claim_unavailable": ("action_ledger", "review_existing_approval"),
    "action_preparation_failed": ("local_preparation", "correct_local_inputs"),
    "action_send_marker_failed": ("action_ledger", "reconcile_without_replay"),
    "action_known_rejected": ("odoo_business", "correct_then_validate"),
    "action_verification_failed": ("business_verification", "reconcile_without_replay"),
    "action_outcome_unknown": ("odoo_transport", "reconcile_without_replay"),
    "action_validation_failed": ("tool_arguments", "correct_arguments"),
    "scope_handoff_required": ("authorization", "renew_proposal"),
    "business_choice_required": ("business_precondition", "request_user_input"),
    "scope_reconfirmation_required": ("authorization", "renew_proposal"),
    "stale_approval": ("authorization", "validate_again"),
    "needs_reconciliation": ("action_ledger", "reconcile_without_replay"),
    "method_not_supported": ("action_policy", "request_supported_alternative"),
    "invoice_delivery_reconciliation_required": ("business_verification", "reconcile_delivery"),
    "sop_unknown": ("tool_contract", "list_odoo_sops"),
    "sop_inputs_invalid": ("tool_contract", "correct_sop_inputs"),
    "sop_operation_invalid": ("tool_contract", "separate_operations"),
    "sop_method_unreviewed": ("authorization", "select_reviewed_method"),
    "observation_reference_missing": ("tool_contract", "search_observations"),
    "observation_access_denied": ("authorization", "use_current_identity"),
    "observation_path_invalid": ("tool_contract", "read_observation_directory"),
    "observation_request_invalid": ("tool_contract", "correct_observation_request"),
    "observation_integrity_failed": ("evidence", "refresh_read"),
    "capability_selection_invalid": ("tool_contract", "correct_capability_selection"),
    "capability_unknown": ("tool_contract", "list_odoo_capabilities"),
    "capability_module_missing": ("environment", "check_installed_modules"),
    "capability_publication_failed": ("runtime", "check_host_runtime"),
    "scope_mismatch": ("run_scope", "check_host_run_scope"),
    "identity_mismatch": ("authorization", "check_host_run_scope"),
    "identity_or_scope_unavailable": ("run_scope", "check_host_run_scope"),
    "no_arguments_allowed": ("tool_arguments", "correct_arguments"),
    "diagnostic_size_limit": ("runtime", "check_host_runtime"),
}


def read_failure(error: Exception | dict) -> dict:
    if isinstance(error, dict) and error.get("reason_code"):
        result = {key: error[key] for key in ("status", "reason_code", "error", "next_action", "http_status") if key in error}
        result["error"] = error.get("detail", result.get("error", "读取失败。"))
        return result
    chain = []
    current = error if isinstance(error, Exception) else RuntimeError(str(error.get("error", "")))
    while current is not None and id(current) not in {id(item) for item in chain}:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " ".join(str(item).lower() for item in chain)
    names = " ".join(str((getattr(item, "odoo_error", None) or {}).get("name", "")).lower() for item in chain)
    http = next((getattr(item, "status_code", None) for item in chain if isinstance(getattr(item, "status_code", None), int)), None)
    if "explicit odoo connection settings" in text:
        code, message, action = "connection_unconfigured", "未配置完整的 Odoo 连接信息。", "configure_connection"
    elif "unknown odoo instance" in text:
        code, message, action = "instance_unknown", "指定的 Odoo 实例未配置，请选择当前已配置的实例。", "select_configured_instance"
    elif any(isinstance(item, ssl.SSLError) for item in chain):
        code, message, action = "tls_error", "Odoo TLS 连接或证书验证失败。", "check_tls"
    elif any(isinstance(item, socket.gaierror) for item in chain):
        code, message, action = "dns_failed", "无法解析 Odoo 服务地址。", "check_address"
    elif any(isinstance(item, TimeoutError) for item in chain) or any(token in text for token in ("timed out", "connection timeout")):
        code, message, action = "connection_timeout", "Odoo 连接或读取超时。", "check_connection"
    elif any(isinstance(item, ConnectionRefusedError) or getattr(item, "errno", None) in {errno.ECONNREFUSED, 10061} for item in chain) or any(token in text for token in ("connection refused", "actively refused", "winerror 10061")):
        code, message, action = "connection_refused", "Odoo 服务拒绝连接；请检查服务是否启动及端口。", "check_service"
    elif any(isinstance(item, ConnectionError) or getattr(item, "errno", None) in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNRESET, errno.ECONNABORTED, 10051, 10054, 10065} for item in chain):
        code, message, action = "connection_unavailable", "Odoo 网络连接不可用。", "check_connection"
    elif "database" in text and any(token in text for token in ("does not exist", "not found", "unknown database")):
        code, message, action = "database_unavailable", "指定的 Odoo 数据库不存在或不可用。", "check_database"
    elif http == 401 or "accessdenied" in names:
        code, message, action = "authentication_failed", "Odoo 认证失败，请检查账号及 API 密钥。", "check_credentials"
    elif "field policy denies" in text:
        code, message, action = "field_policy_denied", "本地字段访问策略拒绝这项读取。", "check_field_policy"
    elif http == 403 or "accesserror" in names or any(isinstance(item, PermissionError) for item in chain) or any(token in text for token in ("accesserror", "access denied", "permission denied", "forbidden")):
        code, message, action = "permission_denied", "当前账号或字段策略不允许这项读取。", "check_permissions"
    elif http == 429 or (isinstance(error, dict) and error.get("rate_limited")):
        code, message, action = "rate_limited", "只读请求受到限流。", "wait_then_recheck"
    elif http == 404:
        code, message, action = "endpoint_not_found", "Odoo JSON-2 接口地址不存在；请检查地址和版本。", "check_endpoint"
    elif "missingerror" in names:
        code, message, action = "record_unavailable", "目标记录不存在或当前账号不可见。", "resolve_reference"
    elif "validationerror" in names or any(token in text for token in ("invalid field", "unknown field", "invalid domain", "unsupported parameters", "requires a non-empty domain", "offset must be greater", "limit must", "validation failed for tool")):
        code, message, action = "query_invalid", "查询字段或条件不被当前接口接受。", "correct_query"
    elif any(token in text for token in ("invalid json", "malformed records")):
        code, message, action = "invalid_response", "Odoo 返回的数据格式无效。", "check_service_logs"
    elif any(token in text for token in ("response byte limit", "response exceeded")):
        code, message, action = "response_too_large", "读取结果超过响应大小上限。", "reduce_read_size"
    elif http is not None and http >= 500:
        code, message, action = "server_error", "Odoo 服务端处理失败。", "check_service_logs"
    else:
        code, message, action = "read_failed_unknown", "本次读取失败，具体原因尚未确定。", "check_odoo_connection"
    status = "permission_denied" if code in {"authentication_failed", "permission_denied", "field_policy_denied"} else "unconfigured" if code == "connection_unconfigured" else "unavailable" if code.startswith("connection_") or code in {"dns_failed", "tls_error", "database_unavailable", "server_error", "rate_limited", "invalid_response", "read_failed_unknown"} else "error"
    return {"status": status, "reason_code": code, "error": message, "next_action": action,
            **({"http_status": http} if http is not None else {})}


def tool_failure(error: Exception | dict) -> dict:
    """Classify shared tool exceptions without publishing external exception bodies."""
    if isinstance(error, dict):
        failure = error.get("failure")
        code = error.get("reason_code") or (failure.get("code") if isinstance(failure, dict) else None)
        if not isinstance(code, str) or code not in FAILURE_GUIDANCE:
            code = "tool_failed_unknown"
        result = {"status": "error", "reason_code": code,
                  "error": "工具调用失败；请按错误分类检查对应条件。"}
    else:
        result = read_failure(error)
        current, seen, validation = error, set(), None
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, ValidationError):
                validation = current
                break
            current = current.__cause__ or current.__context__
        if validation is not None:
            issues = [{"path": ".".join(str(part) for part in row["loc"]), "type": row["type"]}
                      for row in validation.errors(include_input=False, include_context=False, include_url=False)[:8]]
            result.update(reason_code="tool_arguments_invalid", error="工具参数不符合当前契约。",
                          parameter_issues=issues)
            result["error"] += " 参数：" + "、".join(row["path"] for row in issues)
        if result["reason_code"] == "field_policy_denied" and type(error) is ValueError:
            match = re.fullmatch(r"Field policy denies access to (\[[^\n]{1,2000}\]) on ([A-Za-z0-9_.]+)(?:; aggregation on restricted fields is blocked to prevent inference\.)?", str(error))
            if match:
                try:
                    fields = ast.literal_eval(match[1])
                except (SyntaxError, ValueError):
                    fields = None
                if isinstance(fields, list) and all(isinstance(field, str) and re.fullmatch(r"[A-Za-z0-9_]{1,160}", field) for field in fields):
                    result.update(restricted_fields=fields[:8], restricted_field_count=len(fields), model=match[2])
                    result["error"] += " 字段：" + "、".join(fields[:8])
        if result["reason_code"] == "read_failed_unknown":
            if isinstance(error, FileNotFoundError):
                code, message = "local_resource_missing", "工具所需的本地文件或目录不存在。"
            else:
                code, message = "tool_failed_unknown", "本次工具调用失败，原因尚未确定；请查看当前运行诊断。"
            result.update(reason_code=code, error=message)
        result["error"] = result["error"].replace("这项读取", "这项调用").replace("只读请求", "工具请求").replace("读取", "调用")
    result["failure_layer"], result["next_action"] = FAILURE_GUIDANCE[result["reason_code"]]
    return result
