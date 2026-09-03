# Pi + Odoo MCP + ERP-Bench Harness Lab

This is the clean, standalone research surface for one experiment: run the completed Python Pi-style agent loop against Odoo exclusively through MCP, then score the resulting Odoo state with ERP-Bench. It deliberately does not include the ERP compiler or modify any source repository.

## What is here

```text
agent/        pinned Pi Agent for Python source and tests
mcp/          pinned odoo-mcp source and tests
bench/        ERP-Bench generator plus 300 executable Harbor tasks
integration/  Python/native Pi adapters, request receipts, offline run reports
configs/      two single-case baselines and optional Docker proxy overlay
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

Install Harbor 0.22.0 in WSL/Linux and put `harbor` on `PATH`. Restore the wheelhouse above. For native Pi, restore the Node/nvm archives from release `baseline-fixes-2026-09-03` to `.runtime/runtime-bundles/` and check the hashes in `sources.lock.json`. Keep API settings (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_THINKING_TYPE`) in ignored `.runtime/control.env`; align the model name in the chosen config. Run one case per control:

```bash
bash integration/run_baseline.sh configs/baseline-python.json
bash integration/run_baseline.sh configs/baseline-native.json
```

Choose a fresh `job_name` for each run; never overwrite old jobs. Both checked-in baseline configs have a 1,800-second agent timeout; Python also has a 60-turn ceiling. Neither sends a client output-token cap. The native adapter is now local and does not import the old ERP repository. These are usable-control checks, not a matched-budget ablation: native Pi still has its ordinary coding tools, unlike the MCP-only Python runner.

The adapter waits for the task-local Odoo database, reads its short-lived API key inside the container, starts Odoo MCP on loopback, and records Pi session, MCP, actual request bodies/statuses, and token-usage receipts. Both controls upload the same **locally patched** MCP source on top of the pinned wheel dependencies. No compiler is involved.

The baseline configs include `configs/harbor-proxy-compose.yml`. Set `ERP_HARBOR_PROXY` to a proxy reachable from Docker, or remove the overlay on a direct-network host. The launch script loads and checks credentials inside Bash: expanding `$LLM_API_KEY` in a PowerShell-to-WSL command string previously produced empty `OPENAI_*` variables and two setup-only failures. Those failed jobs are retained, not scored as model runs.

To use the runner outside Harbor, start `odoo-mcp` over streamable HTTP and invoke:

```powershell
$env:PYTHONPATH="$PWD\agent\src"
.runtime\venv\Scripts\python.exe integration\pi_odoo_runner.py `
  --instruction-file task.md `
  --usage-file .runtime\artifacts\usage.json
```

Writes are disabled by default in standalone Odoo MCP. Harbor enables writes only inside the disposable benchmark container. Never commit real `.env`, `odoo_config.json`, policy files containing private operational details, or job logs.

## Post-fix business baselines (2026-09-03)

**Both controls scored 100, with all 62 applicable ERP rules passing and no Harbor exception.** Each ran the same public synthetic task 2262 in a separate disposable Odoo container, using the same model, `high` reasoning, and patched MCP. The measured integration checkpoint is `73c50b13746ffea45253060c38f28c953f69d193`; subsequent reporting corrections do not change those raw runs.

| Control | ERP score | Model / HTTP calls | MCP calls | Fresh input | Cached input | Output | Reasoning (within output) | End condition |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Python Pi | 100 | 60 | 119 | 49,175 | 2,016,640 | 131,134 | 115,199 | Local 60-turn guard after business completion; no natural final reply |
| Native Pi | 100 | 49 | 78 | 47,115 | 1,610,496 | 102,793 | 90,190 | Natural `stop` |

All **109 captured requests returned HTTP 200**, and none contained `max_tokens` or `max_completion_tokens`. Largest successful single outputs were 18,387 tokens (Python) and 16,942 (native), both followed by tool use. Native also made one `bash` call (`date`); Python used only MCP. The runs include 11 and 8 recoverable business-error payloads respectively, not erased from the transcripts. Total job times, including installation/verifier, were 22m33s and 17m46s. This is one passing case per control, not a multi-case quality estimate or matched-budget harness comparison.

Python's loop appends a **synthetic assistant error** for its local turn guard: there are 61 assistant entries but only 60 actual model/HTTP calls. The reporter retains that event as a visible local-control label and reports `TURN_LIMIT`, not a provider failure or an unreported charged call. The original run's usage JSON/Harbor metadata used the old entry counter; the corrected report and actual request bodies are the authoritative call count. Future runner usage uses the request counter directly. The source sessions and scores were not rewritten.

Local receipts:

- Python: `.runtime/jobs/baseline-python-schema-bounds-no-cap-20260903-r2/2262_easy_26_buy_only_net_30_no__RYrBu4c`; session SHA-256 `3e66e192cc763cbc77b0376c845108293df94b347b33c647d7165bdc5d62a022`.
- Native: `.runtime/jobs/baseline-native-schema-bounds-no-cap-20260903-r2/2262_easy_26_buy_only_net_30_no__KL96Gzy`; session SHA-256 `4d7a11a062ccaf4eff34197edfc4d1eb2ecdad5d4cdf243ad945795ca04dba22`.
- Both task checksums: `e379100ad4708dd0be1f510cc443af3bf5c9d4b5334479415944494016791366`. Verifier `reward.txt=100.00` agrees with `verifier_details.json` and Harbor `result.json`.
- Browse `.runtime/reports/index.html` for the two new runs plus retained historical failures. No further paid run was started after these results.

## Historical control smoke receipts (before fixes, 2026-09-03)

Historical status: **the model/MCP/Odoo connections worked, but neither original control completed the business task.** These rows remain evidence of the original failures, not the post-fix results.

Both original controls used task `2262_easy_26_buy_only_net_30_no_adjacent_data`, Command Code model `deepseek/deepseek-v4-flash`, thinking `high`, Odoo MCP 1.3.2, and Harbor 0.22.0. The historical external control was the unmodified `erp_agent_odoo.bench.agents:PiMcpBaseline` (Pi 0.84.1 and pi-mcp-extension 1.5.0). The new local control preserves those versions without requiring the old repository on `PYTHONPATH`. No compiler was included or run, and no source repository was edited.

| Control | Assistant entries | MCP calls | Business-error payloads | Raw ERP score | Termination |
| --- | ---: | ---: | ---: | ---: | --- |
| Python Pi | 17 (16 successful responses, 1 provider error) | 53 | 8 | 0.00 | Upstream HTTP 400 `Content Exists Risk` |
| Native Pi, post-interruption recovery | 11 | 27 | 1 | 0.00 | `stopReason=length`; final response used all 16,384 output tokens for reasoning, with no tool call or final text |

Neither completed control made a successful business write. Python Pi used 46,587 uncached input + 414,080 cache-read + 52,600 output tokens; native Pi recovery used 12,216 + 172,928 + 27,145 respectively. These are usage receipts, not dollar-cost estimates. Native Pi's earlier interrupted attempt is separate: 14 assistant entries (3 connection errors), 29 MCP calls, one `bash` call for `date`, and no verifier result. It must not be counted as a completed trial.

Important limits and integration findings:

- Python Pi enforced `max_turns=50`. The native Harbor Pi adapter does **not** support that kwarg; the recovery config removed it and enforced a 900-second agent timeout instead. Prompts, available built-in tools, and termination controls also differ, so this is a connectivity smoke, not a matched-budget ablation.
- MCP can return `success:false` inside a tool result while protocol `isError` is false. The business-error counts above inspect the payload, not just the protocol flag.
- Both old finished jobs hit Harbor `ValidationError` because ERP-Bench's detailed `reward.json` is not a scalar reward map. Raw `reward.txt` and `reward.json` independently agree on zero. Historical logs remain untouched. New task-2262 runs write the detailed object to `verifier_details.json`, so Harbor reads the existing scalar `reward.txt`; scoring rules are unchanged. The other 299 task verifiers have not been normalized.
- Provider errors and length-only responses can terminate these runners before a business solution. Preserve those termination reasons separately from verifier scores before the next experiment; no agent/MCP behavior was changed to hide them.

All full receipts remain local under ignored `.runtime/jobs/`: `control-python-pi-mcp-2262-20260903`, `control-real-pi-mcp-2262-20260903-retry` (interrupted), and `control-real-pi-mcp-2262-20260903-recovery`. A separate initial native setup attempt contains only an obsolete-import error and no model calls. Normalized reward copies are in `.runtime/artifacts/control-{python,real}-pi-2262/harbor-reward/`. `python -m unittest discover -s tests -v` passed both repository checks after these runs.

The API configuration is persisted in ignored `.runtime/control.env`, not just the launching process. Load it in WSL before running Harbor:

```bash
set -a
source .runtime/control.env
set +a
export OPENAI_API_KEY="$LLM_API_KEY" OPENAI_BASE_URL="$LLM_BASE_URL"
```

Secrets and raw job logs are intentionally not committed; only sanitized receipts are saved to GitHub. The subsequent targeted regression probes and post-fix runs are separate from these historical receipts.

## Inspect every run and model call

The old `trial_summary.py` infrastructure is preserved under `integration/`. The report command reuses the pinned Python Pi HTML exporter and its **Usage** tab for both Python and native Pi sessions; it does not introduce another frontend or service.

Create a **separate** Linux host environment from the pinned agent lock for reporting (Harbor itself must also be available on `PATH` for `--run-config`):

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.runtime/venv-linux" uv sync --project agent --frozen --no-dev
export PYTHONPATH="$PWD/agent/src:$PWD"
# Offline: inspect an existing trial, job, or jobs directory. No model calls.
.runtime/venv-linux/bin/python -m integration.report .runtime/jobs
# Live run: the script loads the persistent env. This DOES run the model.
bash integration/run_baseline.sh configs/baseline-python.json
```

The wrapper exports reports even if Harbor exits unsuccessfully. After a killed host/WSL process, run the offline command to recover reports from persisted logs. It does not repair, overwrite, resume, or rerun those source logs. An incomplete event stream can fall back to a valid session with an explicit warning; an unsupported or invalid session is listed in `report_errors.json`, not silently dropped. Native Pi adaptation currently supports the four entry kinds observed in these controls; other entry kinds fail visibly.

Open `.runtime/reports/index.html`, then choose a run:

- `session.html`: searchable transcript, tool arguments/results, errors, and the **Usage** dashboard.
- `requests.json`: each recorded model response's tokens, stop reason, tool names, available latency/TTFT, and diagnostics (including gateway attempts when reported).
- `trial_summary.json`: totals and separate agent termination, Harbor failure, raw verifier score, and actual HTTP request-directory/count. `harbor/` contains a separate normalized reward receipt.
- Original trial `agent/requests/`: numbered actual outbound request bodies and HTTP status/request IDs. Authentication headers are never recorded. These files are distinct from model-response entries because retries can make the counts differ.

Tokens are **observed usage**, not a bill: reasoning is included in output, failed requests without reported usage remain unknown, and custom-provider prices may be unknown. One assistant entry is not necessarily one physical HTTP attempt. Native Pi logs do not provide all the Python timing/diagnostic fields. Reports contain task data and stay ignored/local.

The original 51-wheel release is a task-image supplement, not a complete clean-host environment: it lacks legacy `httpx`/`httpcore`, which the task image had supplied. The isolated reporting environment was supplemented with the original lock's `httpx==0.28.1` and `httpcore==1.0.9`; use the full agent lock for recreation rather than relying on image-inherited packages.

## Reproduced causes and minimal fixes

**The 400 trigger was isolated at the input boundary.** Reconstructing the failing Python conversation from its session and pinned tool definitions reproduced `400 Content Exists Risk`. A fixed-context bisection isolated `res.company.chart_template.selection`, a 151-entry country/region accounting-template enum. No claim is made about which individual label triggers the upstream policy.

| Same reconstructed conversation; only stated change | HTTP |
| --- | ---: |
| Original full context: 71 messages, 41 tools | 400 |
| Add native-style empty `reasoning_content` to assistant messages | 400 |
| Normalize non-empty reasoning into `reasoning_content` | 400 |
| Previous successful turn's context | 200 |
| Keep full 177-field schema, remove only that selection enum | 200 |
| Return the existing ranker's top 10 fields, as requested | 200 |

Why did Python encounter it and native Pi not? Python called `get_model_fields(model="res.company", max_fields=10)`. MCP silently ignored `max_fields` unless `relevance="top"`, returning 177 fields. Its advertised description did not explain that condition. The native recovery trajectory never requested `res.company` metadata and never included `chart_template`. This is a tool-interface/trajectory difference, not evidence that Python's chat protocol is inherently rejected.

The shared MCP fix defaults field discovery to the existing `top` ranker and documents the bound in its advertised description. Explicit `field_names` retain exact technical fields; `relevance=null` still exposes the complete schema. There is no country-specific blacklist or task-answer filter. Explicitly requesting the full enum can still encounter the upstream policy. Both controls receive the same fix. Private probe bodies/responses remain under `.runtime/artifacts/request-probe/`; diagnostic probes used a 128-token response ceiling, unlike the uncapped baseline runs.

**Native Pi's output cap was real SDK behavior.** Pi 0.84.1 defaults a missing model `maxTokens` to 16,384. Merely deleting the config property restores that default. The local extension uses Pi's official `before_provider_request` hook to remove both `max_tokens` and `max_completion_tokens` from the actual outgoing body. It also records that body. Provider-side limits still apply.

**Python instrumentation is preserved across session loading.** `CodingSession.load` previously replaced the runner's supplied provider, dropping its hooks/options. The integration now owns and retains the supplied provider, with no output cap and request receipts. Agent core source is unchanged. Native-compatible empty reasoning fields were aligned but are not claimed as the 400 fix.

Deterministic checks: 8 integration tests pass, including real Python `CodingSession` + mocked HTTP, the native JavaScript hook, and correct local-turn-limit accounting. The **complete MCP suite passes 931 tests** in the isolated Linux environment (pytest 9.1.0, no inherited proxy variables, temporary files on the Linux filesystem). The initial Windows run had 929 passes plus a symlink-privilege failure and a task-order timing tie; a Linux run on the Windows mount inherited proxy/permission assumptions. No unrelated production code was changed to make those environment-sensitive tests pass. The real baseline outcome is separate from these mechanism tests.

## Rollback and provenance

The repository uses two initial commits: one for the immutable pinned inputs, then one for integration code. The tag `snapshot-2026-09-03` identifies the first runnable research snapshot. Reset or branch from either commit without touching `erp-agent-odoo`, `pi-agent-python`, or the source MCP/benchmark checkouts.
