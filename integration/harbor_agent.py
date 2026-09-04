"""Harbor 0.22 entrant for the pinned Python Pi harness and task-local Odoo MCP."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from pathlib import Path
from typing import Any

PI_AGENT_COMMIT = "2ee840c3e31a62c06237b4ac781c7f5863044d07"
MCP_ODOO_COMMIT = "76ec136e0c89c414811a12629eb2903d2dc27357"
MCP_ODOO_VERSION = "1.3.2"
MCP_ODOO_URL = "http://127.0.0.1:8000/mcp"
BENCH_SIDE_EFFECT_METHODS = (
    "sale.order.action_confirm",
    "purchase.order.button_confirm",
    "sale.advance.payment.inv.create_invoices",
    "account.move.action_post",
)
MCP_ONLY_POLICY = (
    "Use mcp_odoo tools for every Odoo operation. Do not access Odoo through "
    "shell commands, direct HTTP, XML-RPC, JSON-2, PostgreSQL, or Python libraries."
)


def bench_action_env() -> dict[str, str]:
    """Explicit action policy for the disposable ERP-Bench database."""
    return {
        "ODOO_MCP_ENABLE_WRITES": "1",
        "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": ",".join(
            BENCH_SIDE_EFFECT_METHODS
        ),
        "ODOO_MCP_AUDIT_LOG": "/logs/agent/native-write-audit.jsonl",
        "ODOO_MCP_ELICIT_WRITES": "0",
        "MCP_CHATTER_DIRECT": "0",
        "ODOO_MCP_ALLOW_UNKNOWN_METHODS": "0",
        "ODOO_ACTION_APPROVAL_MODE": "bench-auto",
    }

try:
    from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
    from harbor.agents.model_connection import ModelConnectionSpec
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
except ModuleNotFoundError as exc:
    if exc.name != "harbor":
        raise

    class _HarborRequired:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Harbor 0.22.0 is required to run this entry point")

    BaseInstalledAgent = _HarborRequired  # type: ignore[assignment,misc]
    BaseEnvironment = AgentContext = Any  # type: ignore[misc,assignment]
    ModelConnectionSpec = None  # type: ignore[assignment,misc]

    def with_prompt_template(function: Any) -> Any:
        return function


async def _install_task_mcp(agent: Any, environment: BaseEnvironment) -> None:
    root = Path(__file__).resolve().parents[1]
    wheelhouse = root / ".runtime" / "wheelhouse-py312"
    if not wheelhouse.is_dir():
        raise RuntimeError(
            "Pinned wheelhouse is missing; restore the snapshot release asset to "
            ".runtime/wheelhouse-py312"
        )

    await agent.exec_as_agent(
        environment,
        command=(
            "set -euo pipefail; for attempt in $(seq 1 300); do "
            "test -e /tmp/saas_setup_complete && "
            "curl -sf http://127.0.0.1:8069/web/version >/dev/null && exit 0; sleep 1; done; "
            "echo 'seeded Odoo setup was not ready after 300s' >&2; exit 1"
        ),
    )
    await environment.upload_dir(wheelhouse, "/tmp/pi-odoo-wheelhouse")
    await agent.exec_as_root(environment, command="mkdir -p /tmp/pi-odoo-mcp-source")
    await environment.upload_dir(
        root / "mcp/src/odoo_mcp", "/tmp/pi-odoo-mcp-source/odoo_mcp"
    )
    packages = shlex.join(
        (
            "odoo-mcp==1.3.2",
            "mcp==2.0.0",
            "mcp-types==2.0.0",
            "requests==2.33.1",
            "jsonschema==4.26.0",
            "packaging==26.2",
            "pathspec==1.1.1",
            "pillow==12.2.0",
            "pygments==2.20.0",
            "pyyaml==6.0.3",
            "rich==15.0.0",
            "textual==8.2.8",
            "typer==0.26.7",
        )
    )
    await agent.exec_as_root(
        environment,
        command=(
            "uv venv --python /usr/bin/python3 --system-site-packages /tmp/pi-odoo-env && "
            "uv pip install --python /tmp/pi-odoo-env/bin/python --no-index --find-links /tmp/pi-odoo-wheelhouse "
            f"{packages}"
        ),
    )


async def _install_task_runtime(agent: Any, environment: BaseEnvironment) -> None:
    root = Path(__file__).resolve().parents[1]
    runner = root / "integration" / "pi_odoo_runner.py"
    sources = tuple(
        root / "agent" / "src" / name for name in ("pi_ai", "pi_agent", "pi_coding")
    )
    for source in (*sources, runner):
        if not source.exists():
            raise RuntimeError(f"Pinned runtime input is missing: {source}")
    await _install_task_mcp(agent, environment)
    await agent.exec_as_root(
        environment, command="mkdir -p /tmp/pi-odoo-harness/agent/src"
    )
    for source in sources:
        await environment.upload_dir(
            source, f"/tmp/pi-odoo-harness/agent/src/{source.name}"
        )
    await environment.upload_file(runner, "/tmp/pi-odoo-runner.py")
    await environment.upload_dir(root / "integration", "/tmp/pi-odoo-harness/integration")
    await environment.upload_dir(root / "odoo_runtime", "/tmp/pi-odoo-harness/odoo_runtime")


async def _start_task_mcp(agent: Any, environment: BaseEnvironment) -> None:
    await agent.exec_as_agent(
        environment,
        command=(
            "set -euo pipefail; for attempt in $(seq 1 300); do "
            "test -s /etc/odoo/api_key && break; sleep 1; done; "
            "test -s /etc/odoo/api_key || { echo 'Odoo API key was not ready' >&2; exit 1; }; "
            'odoo_key="$(cat /etc/odoo/api_key)"; '
            "export PYTHONPATH=/tmp/pi-odoo-mcp-source ODOO_URL=http://127.0.0.1:8069 ODOO_DB=bench ODOO_USERNAME=admin "
            'ODOO_PASSWORD="$odoo_key" ODOO_API_KEY="$odoo_key" ODOO_TRANSPORT=json2 '
            "ODOO_JSON2_DATABASE_HEADER=1 ODOO_MCP_ENABLE_WRITES=1 "
            f"ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS={','.join(BENCH_SIDE_EFFECT_METHODS)} "
            "ODOO_MCP_LOG_JSON=1 "
            "ODOO_MCP_LOG_FILE=/logs/agent/mcp-odoo.jsonl "
            "ODOO_MCP_AUDIT_LOG=/logs/agent/mcp-write-audit.jsonl; "
            "export ODOO_REQUEST_LOG=/logs/agent/odoo-mcp-requests.jsonl ODOO_REQUEST_BACKEND=mcp; "
            "nohup /tmp/pi-odoo-env/bin/odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 "
            "--path /mcp >/logs/agent/mcp-odoo-server.log 2>&1 & "
            'echo "$!" >/logs/agent/mcp-odoo.pid; '
            'for attempt in $(seq 1 30); do python3 -c "import socket; '
            "socket.create_connection(('127.0.0.1',8000),1).close()\" && exit 0; "
            "sleep 1; done; tail -n 80 /logs/agent/mcp-odoo-server.log >&2; exit 1"
        ),
    )


class PiAgentMcpBaseline(BaseInstalledAgent):  # type: ignore[misc,valid-type]
    """Pinned Python Pi CodingSession with only task-local Odoo MCP tools."""

    MODEL_CONNECTION = (
        ModelConnectionSpec(passthrough=True)
        if ModelConnectionSpec is not None
        else None
    )

    def __init__(
        self,
        *args: Any,
        version: str = PI_AGENT_COMMIT,
        max_turns: int | None = None,
        thinking: str = "high",
        read_backend: str = "mcp",
        action_backend: str = "mcp",
        world_mode: str = "off",
        snapshot_sha256: str | None = None,
        runtime_timeout_seconds: int = 1770,
        **kwargs: Any,
    ) -> None:
        if version != PI_AGENT_COMMIT:
            raise ValueError(
                f"PiAgentMcpBaseline requires Pi Agent commit {PI_AGENT_COMMIT}"
            )
        if max_turns is not None and max_turns < 1:
            raise ValueError("max_turns must be positive")
        if read_backend not in {"mcp", "native"}:
            raise ValueError("read_backend must be mcp or native")
        if action_backend not in {"mcp", "native"}:
            raise ValueError("action_backend must be mcp or native")
        if world_mode not in {"off", "record", "project"}:
            raise ValueError("world_mode must be off, record, or project")
        if type(runtime_timeout_seconds) is not int or runtime_timeout_seconds < 1:
            raise ValueError("runtime_timeout_seconds must be a positive integer")
        self._max_turns = max_turns
        self._thinking = thinking
        self._read_backend = read_backend
        self._action_backend = action_backend
        self._world_mode = world_mode
        self._snapshot_sha256 = snapshot_sha256
        self._runtime_timeout_seconds = runtime_timeout_seconds
        super().__init__(*args, version=version, **kwargs)

    @staticmethod
    def name() -> str:
        return "pi-agent-odoo-mcp"

    async def install(self, environment: BaseEnvironment) -> None:
        await _install_task_runtime(self, environment)

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        try:
            await self._run(instruction, environment, context)
        except BaseException:
            # Docker exec cancellation does not stop its remote processes.
            # Stop this disposable service before Harbor can enter verification.
            stopping = asyncio.create_task(environment.stop_service("main"))
            while not stopping.done():
                try:
                    await asyncio.shield(stopping)
                except asyncio.CancelledError:
                    continue
            stopping.result()
            raise

    async def _run(self, instruction: str, environment: BaseEnvironment,
                   context: AgentContext) -> None:
        deadline = time.monotonic() + self._runtime_timeout_seconds
        if self._snapshot_sha256:
            result = await self.exec_as_agent(
                environment, command="cat /logs/agent/snapshot-receipt.json",
            )
            receipt = json.loads(result.stdout or "{}")
            if (receipt.get("status") != "verified"
                    or receipt.get("snapshot_sha256") != self._snapshot_sha256):
                raise RuntimeError("Verified matching snapshot is required before any model call")
        await _start_task_mcp(self, environment)
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")
        model = self.model_name.split("/", 1)[1]
        access = self.model_connection
        base_url = access.configured_base_url or access.base_url
        if not access.api_key or not base_url:
            raise RuntimeError(
                "Pi Agent requires a resolved model API key and base URL"
            )

        await self._upload_config_text(
            environment,
            content=f"{instruction}\n\n{MCP_ONLY_POLICY}",
            remote_path="/tmp/pi-odoo-instruction.txt",
            filename="instruction.txt",
        )
        env = {
            "LLM_API_KEY": access.api_key,
            "LLM_BASE_URL": base_url,
            "LLM_MODEL": model,
            "LLM_THINKING_TYPE": self._thinking,
            "PYTHONPATH": "/tmp/pi-odoo-harness/agent/src:/tmp/pi-odoo-harness:/tmp/pi-odoo-mcp-source",
            "PI_AGENT_SESSION_ID": str(self.context_id or self.session_id or "trial"),
            **bench_action_env(),
        }
        command = (
            "set -o pipefail; export ODOO_URL=http://127.0.0.1:8069 ODOO_DB=bench ODOO_USERNAME=admin; "
            'export ODOO_API_KEY="$(cat /etc/odoo/api_key)"; '
            'export ODOO_PASSWORD="$ODOO_API_KEY" ODOO_TRANSPORT=json2; '
            '/tmp/pi-odoo-env/bin/python /tmp/pi-odoo-runner.py '
            "--instruction-file /tmp/pi-odoo-instruction.txt "
            "--usage-file /logs/agent/pi-agent-usage.json "
            f"--read-backend {self._read_backend} "
            f"--action-backend {self._action_backend} "
            f"--world-mode {self._world_mode} "
            + (f"--max-turns {self._max_turns} " if self._max_turns is not None else "")
            + "2>&1 | stdbuf -oL tee /logs/agent/pi-agent-odoo-mcp.jsonl"
        )
        remaining = int(deadline - time.monotonic())
        await self.exec_as_agent(
            environment,
            command=deadline_command(command, remaining),
            env=env,
            timeout_sec=max(1, remaining) + 10,
        )
        usage_result = await self.exec_as_agent(
            environment,
            command="cat /logs/agent/pi-agent-usage.json",
        )
        usage = json.loads(usage_result.stdout or "{}")
        context.n_input_tokens = usage.get("input", 0) + usage.get("cacheRead", 0)
        context.n_output_tokens = usage.get("output", 0)
        context.n_cache_tokens = usage.get("cacheRead", 0)
        context.metadata = {
            "reasoning_tokens": usage.get("reasoning"),
            "model_calls": usage.get("modelCalls"),
            "pi_agent_commit": PI_AGENT_COMMIT,
            "mcp_odoo_commit": MCP_ODOO_COMMIT,
            "read_backend": self._read_backend,
            "action_backend": self._action_backend,
            "world_mode": self._world_mode,
            "snapshot_sha256": self._snapshot_sha256,
            "runtime_timeout_seconds": self._runtime_timeout_seconds,
        }


def deadline_command(command: str, seconds: int) -> str:
    """GNU timeout owns the remote process group even if the host disappears."""
    if seconds < 1:
        raise TimeoutError("Agent runtime budget exhausted before model startup")
    return (f"timeout --signal=TERM --kill-after=5s {seconds}s "
            f"bash -c {shlex.quote(command)}")
