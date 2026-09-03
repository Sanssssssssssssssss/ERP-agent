# Pi + Odoo MCP + ERP-Bench Harness Lab

This is the clean, standalone research surface for one experiment: run the completed Python Pi-style agent loop against Odoo exclusively through MCP, then score the resulting Odoo state with ERP-Bench. It deliberately does not include the ERP compiler or modify any source repository.

## What is here

```text
agent/        pinned Pi Agent for Python source and tests
mcp/          pinned odoo-mcp source and tests
bench/        ERP-Bench generator plus 300 executable Harbor tasks
integration/  the MCP-only Pi runner and Harbor adapter
configs/      one-task example and optional Docker proxy overlay
patches/      the exact three-task Odoo 19 image-pin patch
.runtime/     ignored local wheelhouse, jobs, logs, and environments
```

The Python harness is layered as follows:

- `pi_ai`: provider/model streaming, reasoning, retry, and token accounting.
- `pi_agent`: the actual agent loop, messages, tools, events, persistence, and MCP adapter.
- `pi_coding`: the higher-level coding session, resource loading, compaction, CLI, and TUI.

All three stay together because `pi_coding.CodingSession` is the completed harness entry point and depends on the other two. The experiment runner disables skills, extensions, project resources, and ordinary coding tools; the model receives only the 41 tools advertised by Odoo MCP.

Pinned source identities and the exact selection/exclusion rules are in [`sources.lock.json`](sources.lock.json). The important exclusions are intentional:

- no compiler code;
- no nested `.git` directories;
- no copied `.venv` (the old editable environment points back to its source checkout);
- no ERP-Bench `tasks_ui` duplicate;
- no generated jobs, traces, caches, or secrets.

## Recreate a local Python environment

Use Python 3.12 and keep the environment under the ignored `.runtime` directory:

```powershell
uv venv .runtime/venv --python 3.12
uv pip install --python .runtime/venv/Scripts/python.exe -e ./agent -e ./mcp
```

For the pinned Linux wheels used inside Harbor, download the `wheelhouse-mcp-py312.zip` asset from the `snapshot-2026-09-03` GitHub release and expand it to `.runtime/wheelhouse-py312`. Verify its SHA-256 against `sources.lock.json`.

## Run the bounded Harbor experiment

Install Harbor 0.22.0 in WSL/Linux, restore the wheelhouse above, edit the model name in `configs/harbor-one-task.example.json`, and run from the repository root so `integration.harbor_agent` is importable:

```bash
export PYTHONPATH="$PWD"
harbor run -c configs/harbor-one-task.example.json
```

The adapter waits for the task-local Odoo database, reads its short-lived API key inside the container, starts Odoo MCP on loopback, uploads the pinned Python harness, and records Pi session, MCP, and token-usage receipts under the Harbor job directory. `configs/harbor-proxy-compose.yml` is optional and should be added only when the model endpoint requires the host proxy.

To use the runner outside Harbor, start `odoo-mcp` over streamable HTTP and invoke:

```powershell
$env:PYTHONPATH="$PWD\agent\src"
.runtime\venv\Scripts\python.exe integration\pi_odoo_runner.py `
  --instruction-file task.md `
  --usage-file .runtime\artifacts\usage.json
```

Writes are disabled by default in standalone Odoo MCP. Harbor enables writes only inside the disposable benchmark container. Never commit real `.env`, `odoo_config.json`, policy files containing private operational details, or job logs.

## Rollback and provenance

The repository uses two initial commits: one for the immutable pinned inputs, then one for integration code. The tag `snapshot-2026-09-03` identifies the first runnable research snapshot. Reset or branch from either commit without touching `erp-agent-odoo`, `pi-agent-python`, or the source MCP/benchmark checkouts.
