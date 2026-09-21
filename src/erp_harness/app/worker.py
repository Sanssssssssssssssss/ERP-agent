from __future__ import annotations

import os
import sys
from pathlib import Path


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
               "ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["PI_AGENT_SESSION_ID"] = session_id
    env["HARBOR_TRIAL_ID"] = run_id
    env["ODOO_ACTION_APPROVAL_MODE"] = "host"
    env["ODOO_TRANSPORT"] = "json2"
    env["ODOO_MCP_ENABLE_WRITES"] = "1"
    env["ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS"] = ",".join((
        "sale.order.action_confirm",
        "purchase.order.button_confirm",
        "purchase.order.button_approve",
        "sale.advance.payment.inv.create_invoices",
        "account.move.action_post",
        "account.move.send.wizard.action_send_and_print",
    ))
    # The shared parser uses legacy completeness detection before honoring API_KEY.
    if env.get("ODOO_API_KEY") and not env.get("ODOO_PASSWORD"):
        env["ODOO_PASSWORD"] = env["ODOO_API_KEY"]
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
    return env
