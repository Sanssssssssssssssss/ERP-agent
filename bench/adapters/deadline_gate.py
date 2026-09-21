"""No-model Harbor/Docker check: deadlines stop the service before verification.

Run with Harbor 0.22 on PATH and the lab's cached task image. All generated
fixtures and receipts stay in .runtime/deadline-gates; no real Odoo DB is started.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bench.adapters.harbor_agent import PiAgentMcpBaseline, deadline_command

IMAGE = "sha256:c99d46e9d14c68b4951b316337b427e37173e49241aa9e9cfd2e865b8738916d"


class DeadlineProbe(PiAgentMcpBaseline):
    """Exercise the real run/cancel wrapper with a harmless, TERM-resistant child."""

    MODEL_CONNECTION = None

    def __init__(self, *args, finish_normally=False, **kwargs):
        self._finish_normally = finish_normally
        super().__init__(*args, **kwargs)

    async def install(self, environment):
        pass

    async def _run(self, instruction, environment, context):
        code = (
            "import signal,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "with open('/logs/agent/probe-ticks.txt','w',buffering=1) as f:\n"
            + (" for _ in range(2):\n" if self._finish_normally else " while True:\n")
            + "  f.write(str(time.time_ns())+'\\n'); time.sleep(0.1)\n"
        )
        await self.exec_as_agent(
            environment,
            command=deadline_command("python3 -u -c " + shlex.quote(code), self._runtime_timeout_seconds),
            timeout_sec=40,
        )


def main():
    root = Path(__file__).resolve().parents[2]
    artifacts = root / ".runtime/deadline-gates"
    artifacts.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=artifacts))
    task = run / "task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Process-lifetime check only. Never call a model.\n")
    (task / "task.toml").write_text(
        'schema_version = "1.1"\n'
        '[task]\nname = "pi-odoo/deadline-gate"\n'
        '[agent]\ntimeout_sec = 20\n'
        '[verifier]\ntimeout_sec = 3\n'
        '[environment]\n'
        f'docker_image = "{IMAGE}"\n'
        'cpus = 1\nmemory_mb = 512\nstorage_mb = 1024\n'
    )
    (task / "tests/test.sh").write_text(
        '#!/usr/bin/env bash\nset -eu\n'
        'touch /logs/verifier/ran\necho 1 > /logs/verifier/reward.txt\n'
    )
    compose = run / "compose.yml"
    # Docker's native mode also works when Harbor's egress sidecar is unavailable.
    compose.write_text('services:\n  main:\n    network_mode: none\n'
                       '    entrypoint: ["sleep"]\n    command: ["300"]\n')
    config = {
        "job_name": "deadline-gate", "jobs_dir": str(run), "quiet": True,
        "n_concurrent_trials": 1,
        "environment": {"type": "docker", "extra_docker_compose": [str(compose)]},
        "agents": [
            {"name": "bench.adapters.deadline_gate:DeadlineProbe", "override_timeout_sec": outer,
             "kwargs": {"runtime_timeout_seconds": inner, "finish_normally": normal}}
            for inner, outer, normal in ((2, 20, False), (20, 2, False), (20, 20, True))
        ],
        "tasks": [{"path": str(task)}],
    }
    config_path = run / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("LLM_", "OPENAI_", "ANTHROPIC_"))}
    environment["PYTHONPATH"] = str(root)
    with (run / "harbor.log").open("w") as log:
        process = subprocess.run(["harbor", "run", "-c", str(config_path)], cwd=root,
                                 env=environment, stdout=log, stderr=subprocess.STDOUT,
                                 timeout=120, check=False)
    trials = list((run / "deadline-gate").glob("*/result.json"))
    assert len(trials) == 3, f"Expected three process-lifetime checks; inspect {run}"
    rows = []
    for path in trials:
        result = json.loads(path.read_text())
        trial = path.parent
        kwargs = result["config"]["agent"]["kwargs"]
        inner, normal = kwargs["runtime_timeout_seconds"], kwargs["finish_normally"]
        ticks = trial / "agent/probe-ticks.txt"
        assert ticks.is_file() and ticks.stat().st_size > 0, "Child never started"
        timing = result["agent_execution"]
        started, finished = (datetime.fromisoformat(timing[key]).replace(tzinfo=timezone.utc)
                             for key in ("started_at", "finished_at"))
        last_tick_ns = int(ticks.read_text().splitlines()[-1])
        assert last_tick_ns <= int(finished.timestamp() * 1e9), "Child outlived agent phase"
        last_tick_seconds = last_tick_ns / 1e9 - started.timestamp()
        error = (result.get("exception_info") or {}).get("exception_type")
        verifier_ran = (trial / "verifier/ran").exists()
        if normal:
            assert error is None and verifier_ran, "Normal completion must reach the verifier"
            assert result["verifier_result"]["rewards"] == {"reward": 1.0}
        else:
            # Distinguish the inner hard kill from the later outer cancellation.
            assert last_tick_seconds < (inner + 6 if inner == 2 else inner), "Stop was late"
            assert not verifier_ran, "Verifier overlapped a failed agent"
            assert not result.get("verifier_result"), "Stopped trial must not receive a score"
            expected = "NonZeroAgentExitCodeError" if inner == 2 else "AgentTimeoutError"
            assert error == expected, f"Expected {expected}, got {error}; inspect {run}"
        assert not list((trial / "agent").glob("requests/*.request.json"))
        rows.append({"inner_seconds": inner, "normal_completion": normal, "exception": error,
                     "child_started": True, "last_tick_ns": last_tick_ns,
                     "last_tick_seconds_after_start": last_tick_seconds,
                     "agent_finished_at": timing["finished_at"],
                     "agent_wall_seconds": (finished - started).total_seconds(),
                     "verifier_ran": verifier_ran, "model_calls": 0})
    receipt = {"status": "passed", "harbor_exit": process.returncode, "trials": rows}
    (run / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"receipt": str(run / "receipt.json"), **receipt}), flush=True)


if __name__ == "__main__":
    main()
