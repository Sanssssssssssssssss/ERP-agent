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

## Control smoke receipts (2026-09-03)

Status: **the model/MCP/Odoo connections worked, but neither control completed the business task.** Do not treat this as a successful benchmark comparison or begin harness fusion from a claimed passing baseline.

Both controls used task `2262_easy_26_buy_only_net_30_no_adjacent_data`, Command Code model `deepseek/deepseek-v4-flash`, thinking `high`, Odoo MCP 1.3.2, and Harbor 0.22.0. The external control remains the unmodified `erp_agent_odoo.bench.agents:PiMcpBaseline` (Pi 0.84.1 and pi-mcp-extension 1.5.0); it needs the old repository's `src` directory on `PYTHONPATH`. No compiler was included or run, and no source repository was edited.

| Control | Assistant entries | MCP calls | Business-error payloads | Raw ERP score | Termination |
| --- | ---: | ---: | ---: | ---: | --- |
| Python Pi | 17 (16 successful responses, 1 provider error) | 53 | 8 | 0.00 | Upstream HTTP 400 `Content Exists Risk` |
| Native Pi, post-interruption recovery | 11 | 27 | 1 | 0.00 | `stopReason=length`; final response used all 16,384 output tokens for reasoning, with no tool call or final text |

Neither completed control made a successful business write. Python Pi used 46,587 uncached input + 414,080 cache-read + 52,600 output tokens; native Pi recovery used 12,216 + 172,928 + 27,145 respectively. These are usage receipts, not dollar-cost estimates. Native Pi's earlier interrupted attempt is separate: 14 assistant entries (3 connection errors), 29 MCP calls, one `bash` call for `date`, and no verifier result. It must not be counted as a completed trial.

Important limits and integration findings:

- Python Pi enforced `max_turns=50`. The native Harbor Pi adapter does **not** support that kwarg; the recovery config removed it and enforced a 900-second agent timeout instead. Prompts, available built-in tools, and termination controls also differ, so this is a connectivity smoke, not a matched-budget ablation.
- MCP can return `success:false` inside a tool result while protocol `isError` is false. The business-error counts above inspect the payload, not just the protocol flag.
- Both finished jobs hit Harbor `ValidationError` because ERP-Bench's detailed `reward.json` is not a scalar reward map. Raw `reward.txt` and `reward.json` independently agree on zero. The existing `integration.reward_adapter` generated separate scalar receipts without modifying the originals or changing either score; it is not yet wired into the live verifier.
- Provider errors and length-only responses can terminate these runners before a business solution. Preserve those termination reasons separately from verifier scores before the next experiment; no agent/MCP behavior was changed to hide them.

All full receipts remain local under ignored `.runtime/jobs/`: `control-python-pi-mcp-2262-20260903`, `control-real-pi-mcp-2262-20260903-retry` (interrupted), and `control-real-pi-mcp-2262-20260903-recovery`. A separate initial native setup attempt contains only an obsolete-import error and no model calls. Normalized reward copies are in `.runtime/artifacts/control-{python,real}-pi-2262/harbor-reward/`. `python -m unittest discover -s tests -v` passed both repository checks after these runs.

The API configuration is persisted in ignored `.runtime/control.env`, not just the launching process. Load it in WSL before running Harbor:

```bash
set -a
source .runtime/control.env
set +a
export OPENAI_API_KEY="$LLM_API_KEY" OPENAI_BASE_URL="$LLM_BASE_URL"
```

Secrets and raw job logs are intentionally not committed; only this sanitized receipt is saved to GitHub. No further paid retries or compiler/harness-fusion changes were made after the recovery result.

## Rollback and provenance

The repository uses two initial commits: one for the immutable pinned inputs, then one for integration code. The tag `snapshot-2026-09-03` identifies the first runnable research snapshot. Reset or branch from either commit without touching `erp-agent-odoo`, `pi-agent-python`, or the source MCP/benchmark checkouts.
