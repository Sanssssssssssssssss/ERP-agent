"""Public read diagnostics; never expose exception bodies or credential values."""
from __future__ import annotations

import errno
import socket
import ssl


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
    elif "validationerror" in names or any(token in text for token in ("invalid field", "unknown field", "invalid domain", "unsupported parameters")):
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
