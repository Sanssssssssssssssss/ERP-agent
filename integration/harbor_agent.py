"""Harbor 0.22 entrants for the pinned Python Pi and Odoo runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
    "sale.order.action_cancel",
    "purchase.order.button_cancel",
    "purchase.order.button_approve",
    "mrp.production.action_confirm",
    "mrp.production.action_cancel",
)
MCP_ONLY_POLICY = (
    "Use mcp_odoo tools for every Odoo operation. Do not access Odoo through "
    "shell commands, direct HTTP, XML-RPC, JSON-2, PostgreSQL, or Python libraries."
)
RUNTIME_PACKAGES = (
    "anyio==4.14.2",
    "httpx[socks]==0.28.1",
    "jsonschema==4.26.0",
    "packaging==26.2",
    "pathspec==1.1.1",
    "pillow==12.2.0",
    "pydantic==2.13.4",
    "pygments==2.20.0",
    "pyyaml==6.0.3",
    "requests==2.33.1",
    "rich==15.0.0",
    "textual==8.2.8",
    "typer==0.26.7",
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


def _usage_bucket(usage: dict[str, Any], *names: str) -> int | None:
    """Read a nullable runner bucket without turning unknown into zero."""
    for name in names:
        if name in usage:
            value = usage[name]
            return value if type(value) is int and value >= 0 else None
    return None


def _nullable_add(*values: int | None) -> int | None:
    return sum(values) if all(value is not None for value in values) else None

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


async def _install_task_native(agent: Any, environment: BaseEnvironment) -> None:
    root = Path(__file__).resolve().parents[1]
    wheelhouse = root / ".runtime" / "wheelhouse-native-py312"
    if not wheelhouse.is_dir():
        raise RuntimeError(
            "Pinned wheelhouse is missing; restore the snapshot release asset to "
            ".runtime/wheelhouse-native-py312"
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
    packages = shlex.join(RUNTIME_PACKAGES)
    await agent.exec_as_root(
        environment,
        command=(
            "uv venv --python /usr/bin/python3 /tmp/pi-odoo-env && "
            "uv pip install --python /tmp/pi-odoo-env/bin/python --no-index "
            "--find-links /tmp/pi-odoo-wheelhouse "
            f"{packages}"
        ),
    )


async def _install_task_runtime(
    agent: Any, environment: BaseEnvironment, *, native_only: bool = False
) -> None:
    root = Path(__file__).resolve().parents[1]
    wheels = (
        Path(os.environ.get("WORKBENCH_BACKEND_WHEEL", root / "dist/erp_harness-0.5.4-py3-none-any.whl")),
        root / "dist/pi_agent_python-0.1.0-py3-none-any.whl",
    )
    for source in wheels:
        if not source.exists():
            raise RuntimeError(f"Pinned runtime input is missing: {source}")
    if native_only:
        await _install_task_native(agent, environment)
    else:
        await _install_task_mcp(agent, environment)
    await agent.exec_as_root(
        environment, command="mkdir -p /tmp/erp-harness-wheels"
    )
    for source in wheels:
        await environment.upload_file(
            source, f"/tmp/erp-harness-wheels/{source.name}"
        )
    await agent.exec_as_root(environment, command=(
        "uv pip install --python /tmp/pi-odoo-env/bin/python --no-deps "
        + shlex.join(f"/tmp/erp-harness-wheels/{source.name}" for source in wheels)
    ))


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
    """Pinned Python Pi CodingSession with the fixed Odoo tool contract."""

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
        capability_backend: str = "mcp",
        runtime_mode: str = "mcp",
        sop_mode: str = "off",
        tool_mode: str = "static",
        world_mode: str = "off",
        max_model_requests: int | None = None,
        max_output_tokens: int | None = None,
        snapshot_sha256: str | None = None,
        runtime_timeout_seconds: int | None = 1770,
        task_evidence: dict | None = None,
        **kwargs: Any,
    ) -> None:
        if version != PI_AGENT_COMMIT:
            raise ValueError(
                f"PiAgentMcpBaseline requires Pi Agent commit {PI_AGENT_COMMIT}"
            )
        if max_turns is not None and max_turns < 1:
            raise ValueError("max_turns must be positive")
        for name, value in (
            ("max_model_requests", max_model_requests),
            ("max_output_tokens", max_output_tokens),
        ):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer")
        if read_backend not in {"mcp", "native"}:
            raise ValueError("read_backend must be mcp or native")
        if action_backend not in {"mcp", "native"}:
            raise ValueError("action_backend must be mcp or native")
        if capability_backend not in {"mcp", "native"}:
            raise ValueError("capability_backend must be mcp or native")
        if runtime_mode not in {"mcp", "native"}:
            raise ValueError("runtime_mode must be mcp or native")
        if runtime_mode == "native" and {
            read_backend,
            action_backend,
            capability_backend,
        } != {"native"}:
            raise ValueError("native runtime_mode requires every Odoo backend to be native")
        if sop_mode not in {"off", "controlled"}:
            raise ValueError("sop_mode must be off or controlled")
        if tool_mode not in {"static", "dynamic"}:
            raise ValueError("tool_mode must be static or dynamic")
        if tool_mode == "dynamic" and sop_mode != "controlled":
            raise ValueError("dynamic tool_mode requires controlled sop_mode")
        if world_mode not in {"off", "record", "project"}:
            raise ValueError("world_mode must be off, record, or project")
        if runtime_timeout_seconds is not None and (type(runtime_timeout_seconds) is not int or runtime_timeout_seconds < 1):
            raise ValueError("runtime_timeout_seconds must be a positive integer")
        self._max_turns = max_turns
        self._thinking = thinking
        self._read_backend = read_backend
        self._action_backend = action_backend
        self._capability_backend = capability_backend
        self._runtime_mode = runtime_mode
        self._native_only = runtime_mode == "native"
        self._sop_mode = sop_mode
        self._tool_mode = tool_mode
        self._world_mode = world_mode
        self._max_model_requests = max_model_requests
        self._max_output_tokens = max_output_tokens
        self._snapshot_sha256 = snapshot_sha256
        self._runtime_timeout_seconds = runtime_timeout_seconds
        self._task_evidence = task_evidence
        super().__init__(*args, version=version, **kwargs)

    @staticmethod
    def name() -> str:
        return "pi-agent-odoo-mcp"

    async def install(self, environment: BaseEnvironment) -> None:
        await _install_task_runtime(
            self, environment, native_only=self._native_only
        )
        await self._capture_bench_state(environment, "initial")

    async def _capture_bench_state(self, environment: BaseEnvironment, phase: str) -> None:
        if phase not in {"initial", "final"}:
            raise ValueError("invalid state capture phase")
        await self.exec_as_root(
            environment,
            command=(
                "set -euo pipefail; mkdir -p /logs/agent/state; "
                "su postgres -s /bin/bash -c "
                "'pg_dump -d bench --no-owner --exclude-table-data=res_users_apikeys' "
                f"| gzip -n > /logs/agent/state/{phase}.sql.gz; "
                f"sha256sum /logs/agent/state/{phase}.sql.gz > /logs/agent/state/{phase}.sha256; "
                "date -u -r /tmp/saas_setup_complete +%Y-%m-%d > /logs/agent/state/scenario-date.txt"
            ),
            timeout_sec=60,
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        try:
            await self._run(instruction, environment, context)
            await self._capture_bench_state(environment, "final")
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
        deadline = (time.monotonic() + self._runtime_timeout_seconds
                    if self._runtime_timeout_seconds is not None else None)
        anchor = await self.exec_as_agent(
            environment, command="cat /logs/agent/state/scenario-date.txt"
        )
        instruction += (
            "\n\nScenario date anchor (UTC): " + (anchor.stdout or "").strip()
            + ". Interpret the task's relative day counts from this seeded scenario date."
        )
        if self._snapshot_sha256:
            result = await self.exec_as_agent(
                environment, command="cat /logs/agent/snapshot-receipt.json",
            )
            receipt = json.loads(result.stdout or "{}")
            if (receipt.get("status") != "verified"
                    or receipt.get("snapshot_sha256") != self._snapshot_sha256):
                raise RuntimeError("Verified matching snapshot is required before any model call")
        native_only = getattr(self, "_native_only", False)
        if native_only:
            await self.exec_as_agent(
                environment,
                command=(
                    "set -euo pipefail; command -v pgrep >/dev/null; "
                    "! pgrep -f '[o]doo[-_]mcp' >/dev/null"
                ),
            )
            await self.exec_as_agent(
                environment,
                command=(
                    "/tmp/pi-odoo-env/bin/python -c \"import importlib.metadata as metadata;"
                    "import importlib.util,json,socket;"
                    "from pathlib import Path;"
                    "found={name:importlib.util.find_spec(name) is not None "
                    "for name in ('mcp','mcp_types','odoo_mcp')};"
                    "installed={dist.metadata['Name'].lower() "
                    "for dist in metadata.distributions()};"
                    "banned=installed&{'mcp','mcp-types','odoo-mcp'};"
                    "sock=socket.socket();sock.settimeout(0.2);"
                    "port_open=sock.connect_ex(('127.0.0.1',8000))==0;sock.close();"
                    "pid_file=Path('/logs/agent/mcp-odoo.pid').exists();"
                    "clean=not any(found.values()) and not banned and not port_open and not pid_file;"
                    "receipt={'status':'verified' if clean else 'failed','installed_or_importable':found,"
                    "'installed_mcp_distributions':sorted(banned),"
                    "'mcp_process':False,'mcp_port_8000_open':port_open,"
                    "'mcp_pid_file':pid_file};"
                    "Path('/logs/agent/native-runtime-receipt.json').write_text("
                    "json.dumps(receipt,sort_keys=True));"
                    "assert clean\""
                ),
            )
        else:
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
            "PYTHONPATH": (
                "" if native_only else "/tmp/pi-odoo-mcp-source"
            ),
            "PI_AGENT_SESSION_ID": str(self.context_id or self.session_id or "trial"),
            "PI_ODOO_SOURCE_COMMIT": os.environ.get("PI_ODOO_SOURCE_COMMIT", ""),
            **bench_action_env(),
        }
        if self._task_evidence is not None:
            if not native_only:
                raise ValueError("task evidence requires the native runtime")
            specification = {**self._task_evidence, "instruction_sha256": hashlib.sha256(f"{instruction}\n\n{MCP_ONLY_POLICY}".encode()).hexdigest()}
            await self._upload_config_text(environment, content=json.dumps(specification),
                                           remote_path="/tmp/pi-odoo-task-evidence.json", filename="task-evidence-spec.json")
            env["ODOO_TASK_EVIDENCE_FILE"] = "/tmp/pi-odoo-task-evidence.json"
        command = (
            "set -o pipefail; export ODOO_URL=http://127.0.0.1:8069 ODOO_DB=bench ODOO_USERNAME=admin; "
            'export ODOO_API_KEY="$(cat /etc/odoo/api_key)"; '
            'export ODOO_PASSWORD="$ODOO_API_KEY" ODOO_TRANSPORT=json2; '
            '/tmp/pi-odoo-env/bin/python -m erp_harness.app.runner '
            "--instruction-file /tmp/pi-odoo-instruction.txt "
            "--usage-file /logs/agent/pi-agent-usage.json "
            f"--runtime-mode {'native' if native_only else 'mcp'} "
            f"--read-backend {self._read_backend} "
            f"--action-backend {self._action_backend} "
            f"--capability-backend {self._capability_backend} "
            f"--sop-mode {self._sop_mode} "
            f"--tool-mode {self._tool_mode} "
            f"--world-mode {self._world_mode} "
            + (f"--max-turns {self._max_turns} " if self._max_turns is not None else "")
            + (f"--max-model-requests {self._max_model_requests} "
               if self._max_model_requests is not None else "")
            + (f"--max-output-tokens {self._max_output_tokens} "
               if self._max_output_tokens is not None else "")
            + "2>&1 | stdbuf -oL tee /logs/agent/"
            + ("pi-agent-odoo.jsonl" if native_only else "pi-agent-odoo-mcp.jsonl")
        )
        remaining = int(deadline - time.monotonic()) if deadline is not None else None
        await self.exec_as_agent(
            environment,
            command=deadline_command(command, remaining),
            env=env,
            timeout_sec=max(1, remaining) + 10 if remaining is not None else None,
        )
        usage_result = await self.exec_as_agent(
            environment,
            command="cat /logs/agent/pi-agent-usage.json",
        )
        usage = json.loads(usage_result.stdout or "{}")
        fresh_input = _usage_bucket(usage, "input")
        cache_read = _usage_bucket(usage, "cacheRead", "cache_read")
        cache_write = _usage_bucket(usage, "cacheWrite", "cache_write")
        cache_write_1h = _usage_bucket(usage, "cacheWrite1H", "cache_write_1h")
        output = _usage_bucket(usage, "output")
        reasoning = _usage_bucket(usage, "reasoning")
        compaction_calls = _usage_bucket(usage, "compactionCalls", "compaction_calls")
        compaction_input = _usage_bucket(usage, "compactionInput", "compaction_input")
        compaction_cache_read = _usage_bucket(usage, "compactionCacheRead", "compaction_cache_read")
        compaction_cache_write = _usage_bucket(usage, "compactionCacheWrite", "compaction_cache_write")
        compaction_output = _usage_bucket(usage, "compactionOutput", "compaction_output")
        compaction_reasoning = _usage_bucket(usage, "compactionReasoning", "compaction_reasoning")
        compaction_total = _usage_bucket(usage, "compactionTotal", "compaction_total")
        if compaction_calls == 0:
            compaction_input = compaction_input if compaction_input is not None else 0
            compaction_cache_read = compaction_cache_read if compaction_cache_read is not None else 0
            compaction_cache_write = compaction_cache_write if compaction_cache_write is not None else 0
            compaction_output = compaction_output if compaction_output is not None else 0
            compaction_reasoning = compaction_reasoning if compaction_reasoning is not None else 0
            compaction_total = compaction_total if compaction_total is not None else 0
        context.n_input_tokens = _nullable_add(
            fresh_input, cache_read, cache_write,
            compaction_input, compaction_cache_read, compaction_cache_write,
        )
        context.n_output_tokens = _nullable_add(output, compaction_output)
        context.n_cache_tokens = _nullable_add(cache_read, compaction_cache_read)
        context.metadata = {
            "fresh_input_tokens": fresh_input,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "cache_write_1h_tokens": cache_write_1h,
            "output_tokens": output,
            "reasoning_tokens": reasoning,
            "compaction_total_tokens": compaction_total,
            "compaction_input_tokens": compaction_input,
            "compaction_cache_read_tokens": compaction_cache_read,
            "compaction_cache_write_tokens": compaction_cache_write,
            "compaction_cache_write_1h_tokens": _usage_bucket(usage, "compactionCacheWrite1H", "compaction_cache_write_1h"),
            "compaction_output_tokens": compaction_output,
            "compaction_reasoning_tokens": compaction_reasoning,
            "compaction_calls": compaction_calls,
            "model_calls": usage.get("modelCalls"),
            "pi_agent_commit": PI_AGENT_COMMIT,
            "mcp_odoo_commit": MCP_ODOO_COMMIT,
            "runtime_mode": self._runtime_mode,
            "read_backend": self._read_backend,
            "action_backend": self._action_backend,
            "capability_backend": self._capability_backend,
            "sop_mode": self._sop_mode,
            "tool_mode": self._tool_mode,
            "world_mode": self._world_mode,
            "snapshot_sha256": self._snapshot_sha256,
            "runtime_timeout_seconds": self._runtime_timeout_seconds,
            "max_model_requests": self._max_model_requests,
            "max_output_tokens": self._max_output_tokens,
            "commit_sha": os.environ.get("PI_ODOO_SOURCE_COMMIT") or None,
        }


def deadline_command(command: str, seconds: int | None) -> str:
    """GNU timeout owns the remote process group even if the host disappears."""
    if seconds is None:
        return f"bash -c {shlex.quote(command)}"
    if seconds < 1:
        raise TimeoutError("Agent runtime budget exhausted before model startup")
    return (f"timeout --signal=TERM --kill-after=5s {seconds}s "
            f"bash -c {shlex.quote(command)}")
