# Backend integration

This source tree integrates desktop baseline `6821615186d18b42855bfd0692b471d0568adf5b` with benchmark backend `302102d46d7a0fb511d15a0ff3796a5aba9f07b5`. These inputs are recorded in [sources.lock.json](../sources.lock.json). The published v0.5.4 desktop archive predates this integration.

## Runtime and configuration

The [desktop worker](../src/erp_harness/app/worker.py) invokes [pi_odoo_runner](../src/erp_harness/app/runner.py) with:

| Setting | Value |
| --- | --- |
| Runtime, read, action, capability backends | `native` |
| Tool selection | `dynamic` |
| SOPs | `controlled` |
| World state | `record` |
| Desktop writes | Pause for per-action human approval; continue the same session |

Connection settings are entered in the workbench; dependencies and startup commands are in the [README](../README.md#quick-start-windows-development). Historical `bench/configs/stage*.json` retain their original experiment settings, including static-tool controls. Benchmark adapters may use `bench-auto` approval only in disposable task environments.

`TaskEvidence` is optional host-supplied benchmark evidence. The desktop does not supply it. The separate [company-record RAG experiment](../experiments/company_records_rag/README.md) remains outside the workbench runtime.

After an administrator tightens field ACLs, start a new business session. Re-running the same business or resuming after approval preserves its existing session and historical read results; a policy reload does not remove data already present in that context.

## Validation

2026-09-24: [PR #5](https://github.com/Sanssssssssssssssss/ERP-agent/pull/5) merged at `3a4ce45`; rollback tag: `backend-main-baseline-20260924`. All required CI checks passed, including historical MCP comparisons and the packaged desktop.

The following cleanup shares usage aggregation and run finalization, and computes each readback outcome once. All feature entry points remain; production Python decreases by 17 lines. Model instructions, tool schemas, approval and ledger paths retain their behavior. Local checks: 1,148 passed, 4 skipped, 179 subtests; current-source desktop IPC passed with one mock write. Independent equivalence review found no blocking differences. Paid model calls: 0; no new real ERP business score is claimed. Evidence: `.runtime/duplicate-cleanup-20260924/` and `.runtime/ipc-check/run-57688/`.

Frozen source checkpoint `backend-baseline-20260922-memory-off`: optional Mem0 and the persistent demo are included; both desktop and direct host default to memory off. Existing learning receipts do not establish a business-score or cost improvement. Local validation: 900 passed, 4 skipped, 119 subtests; the subsequent host-default check passed all 8 memory tests. Desktop typecheck, build, self-check and renderer checks passed. Older SQLite test cleanup emits Windows file-lock warnings after the successful test run. Full receipts remain in `.runtime/enterprise-validation-20260922/baseline/` and `.runtime/memory-integration-20260921/`.

[CI](../.github/workflows/checks.yml) runs existing desktop, approval, native-read, write-guard, evidence, and provider-recovery checks offline. Historical MCP comparisons run in their own environment. Sidecar preparation imports the packaged runtime and reads its native tool catalog and model-rename resource before replacing the prior bundle.

Offline checks establish mechanism coverage. Real business regression requires isolated Odoo runs with the source/configuration, logs, `reward.txt`, and `verifier_details.json` retained; two cases establish only those cases. Current run receipts belong with the release validation report, not the historical scores.

Historical evidence remains indexed in [the lab entry point](history/README-lab.md) and [the stage reports](../experiments/README.md). Stage 7 acceptance applies to its recorded static native run, not every later dynamic-tool change.
