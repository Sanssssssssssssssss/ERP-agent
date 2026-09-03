# Stage 1 stopped before live A/B (2026-09-03)

Status: **incomplete, not an accepted native-read baseline**. No paid model
requests were made. Neither A nor B ran the business canary; C and Compiler
were not run. The existing 100-point historical baselines remain unchanged.

## What was implemented

- Opt-in native execution for four read tools, keeping the discovered Pi tool
  names, schemas, descriptions, text serialization, and structured results.
- The native implementation reuses pinned Odoo core helpers without importing
  the MCP SDK/server; a lazy package facade makes that import boundary testable.
  This is not a standalone MCP-free distribution: Pi and the remaining tools
  still have their existing MCP dependencies.
- A closed JSON-2 read-method allowlist, existing field ranking/redaction/cache,
  actual backend dispatch logs, Odoo JSON-2 attempt metadata, and report exports.
- Optional Python turn limit defaults to None; the experimental Python config
  removes its old 60-turn ceiling and retains the 1,800-second timeout.
- Offline checks and a no-LLM live-read gate/provisioning script. The latter is
  NOT validated: execution stopped during snapshot preparation before it ran.

## Checks and failures

1. Initial integration gate: 12 passed, 1 skipped (the old native-Pi Node hook
   unit check; Node is not on this WSL PATH). The read parity table covers 23
   fake-client inputs and full AgentTool output serialization for all four tools.
2. One combined pytest invocation failed during collection because both trees
   use `tests` as an import namespace. Running each suite from its proper root
   fixed the entry point: **931 MCP tests passed**, then **12 integration checks
   passed and 1 skipped**. See `integration/check_stage1.sh`.
3. The first detached fixture stopped during Odoo setup: ExitCode 255,
   OOMKilled false; Docker restarted. This is an environment interruption,
   not a scored agent result. Exact host shutdown causality is not proven.
4. A single recovery attempt used a fresh same-image, same-case container with
   foreground attachment and unchanged 3-CPU/4-GiB limits. Seeding completed.
   The subsequent preparation script nevertheless failed with:

   ```text
   tar: filestore: Cannot stat: No such file or directory
   tar: Exiting with failure status due to previous errors
   ```

   The script incorrectly assumes `/var/lib/odoo/filestore`. Read-only inspection
   confirmed `odoo.tools.config['data_dir'] == '/root/.local/share/Odoo'` and an
   existing `/root/.local/share/Odoo/filestore/bench`. This is our preparation
   script bug. Per the user's stop rule, no further repair or test was attempted.

## Known pending work, not silently repaired

- Derive the snapshot filestore path from the actual Odoo configuration; do not
  hardcode a user-home-dependent location. The current snapshot is incomplete.
- Align native connection defaults before live parity: `build_odoo_client`
  defaults to a 30-second timeout, whereas direct `OdooClient` construction
  defaults to 10. Also the live-gate script currently sets `ODOO_LANG`, while the
  existing core reads `ODOO_LOCALE`. These are source-review findings, not live
  differential results; the current prototype must not be promoted as equivalent.
- Run the real admin/restricted-principal differential checks. Only after they
  pass, complete identical-snapshot A/B deployment configs and run one paid pair.
  Snapshot restore and paid A/B configs have not been implemented yet.
- Keep the remaining MCP server's read-diagnostic counters distinct from native
  reads; they do not automatically observe calls that bypass that server.

## Local evidence and recovery

Base image: `sha256:c99d46e9d14c68b4951b316337b427e37173e49241aa9e9cfd2e865b8738916d`.
Both disposable fixtures are stopped and retained, not deleted. No source repo,
LLM credentials, scoring rules, or historical job logs were changed.

Ignored local evidence:

- `.runtime/stage1-offline-initial.log`
- `.runtime/stage1-regression.log` (failed combined collection)
- `.runtime/stage1/mcp-regression.log`, `.runtime/stage1/integration.log`
- `.runtime/stage1/fixture-initial-state.json`, `.runtime/stage1/fixture-initial.log`
- `.runtime/stage1/fixture-r2.log`, `.runtime/stage1/fixture-retest-failure.txt`
- `.runtime/stage1/fixture/` (partial dump and test API key; NOT a complete snapshot)

The implementation and this report are saved on an experiment branch. Main keeps
the published plan and the prior baseline; no force-push or history rewrite.
