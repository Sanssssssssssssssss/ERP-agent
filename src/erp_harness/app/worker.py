from __future__ import annotations

# worker 启动契约：当前 Python → 已安装的 erp_harness 模块 → JSON 事件流。
# 业务 worker 有原生 Odoo 工具与逐项审批；对话 worker 只有查询和提案工具。
# 子进程环境采用白名单。凭据来自宿主，不从模型参数读取。
# PI_AGENT_SESSION_ID 等旧键继续保留，用于关联已有日志与账本。

import os
import sys
from pathlib import Path

from erp_harness.erp.business_operations import ENTERPRISE_METHODS


def _field_policy_environment() -> dict[str, str]:
    from erp_harness.erp._odoo_core.field_policy import field_policy_file_path

    path = field_policy_file_path()
    # 只继承字段约束。共享配置中的写方法授权不扩大到 worker。
    return {"ODOO_MCP_FIELD_POLICY_FILE": str(Path(path).resolve())} if path else {}


def worker_command(repo: Path, instruction: Path, usage: Path, session: Path, *, continue_run: bool) -> list[str]:
    command = [sys.executable, "-m", "erp_harness.app.runner",
               "--instruction-file", str(instruction), "--usage-file", str(usage),
               "--session-file", str(session), "--receipt-dir", str(usage.parent), "--runtime-mode", "native",
               "--read-backend", "native", "--action-backend", "native",
               "--capability-backend", "native", "--sop-mode", "controlled",
               "--tool-mode", "dynamic", "--world-mode", "record", "--pause-on-approval"]
    if continue_run:
        command.append("--continue-run")
    return command


def conversation_command(repo: Path, instruction: Path, usage: Path, session: Path) -> list[str]:
    """Launch the isolated, proposal-only Pi conversation worker."""
    return [
        sys.executable,
        "-m",
        "erp_harness.app.conversation",
        "--instruction-file",
        str(instruction),
        "--usage-file",
        str(usage),
        "--session-file",
        str(session),
        "--receipt-dir",
        str(usage.parent),
    ]


def child_environment(session_id: str, run_id: str) -> dict[str, str]:
    allowed = ("PATH", "SystemRoot", "TEMP", "TMP", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
               "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_THINKING_TYPE",
               "ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY", "ERP_MEMORY_MODE", "ERP_KNOWLEDGE_DIR", "ERP_KNOWLEDGE_MAX_DOCS")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.setdefault("ERP_MEMORY_MODE", "off")
    env["PI_AGENT_SESSION_ID"] = session_id
    env["HARBOR_TRIAL_ID"] = run_id
    env["ODOO_ACTION_APPROVAL_MODE"] = "host"
    # 启用写能力不等于批准写入。每个动作仍须通过宿主审批和账本检查。
    env["ODOO_TRANSPORT"] = "json2"
    env["ODOO_MCP_ENABLE_WRITES"] = "1"
    env["ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS"] = ",".join((
        "sale.order.action_confirm",
        "purchase.order.button_confirm",
        "purchase.order.button_approve",
        "sale.advance.payment.inv.create_invoices",
        "account.move.action_post",
        "account.move.message_post",
        "account.move.send.wizard.action_send_and_print",
        *ENTERPRISE_METHODS,
    ))
    # The shared parser uses legacy completeness detection before honoring API_KEY.
    if env.get("ODOO_API_KEY") and not env.get("ODOO_PASSWORD"):
        env["ODOO_PASSWORD"] = env["ODOO_API_KEY"]
    env.update(_field_policy_environment())
    return env


def conversation_environment(session_id: str, run_id: str) -> dict[str, str]:
    """Environment for ordinary conversation and its fixed read-only Odoo tool."""
    allowed = (
        "PATH", "SystemRoot", "TEMP", "TMP", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_THINKING_TYPE", "LLM_PROVIDER",
        "ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["PI_AGENT_SESSION_ID"] = session_id
    env["HARBOR_TRIAL_ID"] = run_id
    env.update(_field_policy_environment())
    return env
