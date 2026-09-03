# Stage 1: Python Pi / MCP versus native Odoo reads

Approved scope (2026-09-03): publish this checkpoint before implementation; run
only A and B. No native-Pi C run, compiler, source-repository edits, or later-stage
experiments. Preserve baseline commit `1d4680b55f834f37ceb4bad5e9b9f03341007d49`.

## Comparison

- A: the current Python Pi harness with all Odoo tools routed through MCP.
- B: the same harness, with only `get_odoo_profile`, `get_model_fields`,
  `search_records`, and `read_record` routed directly to Odoo JSON-2. All other
  tools, including writes, remain on MCP.
- Preserve model-facing tool names, descriptions, schemas, results, prompts,
  model, reasoning setting, and permissions. Remove the Python-only 60-turn
  ceiling; use the same 1,800-second agent timeout and no client output-token cap.
- Preserve the reference MCP and both historical passing runs. Add only the
  required native read implementation, tests, and small runner/report changes.

## Gates and budget

1. Deterministic contract tests: field bounds (including the previous company
   schema regression), exact fields, domain validation, missing/empty records,
   redaction, errors, language/company context, and read-only retry behavior.
2. Live read differential checks against one isolated, quiescent Odoo fixture,
   including a restricted principal and policy. Do not treat two equally wrong
   implementations as proof: assert independent expected properties too.
3. One paid A/B pair on case 2262, in separate databases restored from the same
   prepared snapshot. Record snapshot/source/config/tool-contract identities.
   Both must pass all 62 applicable rules and finish normally before promotion.
4. Record physical backend routing, Odoo requests and retries separately from
   model-visible tool names. Reuse the existing per-request token reports.

Do not launch the paid pair if earlier gates fail. Initial paid budget: two
trials, one per arm. If testing identifies a problem, diagnose it and attempt
only one repair round, then retest the affected gate/arm. If that first repair
does not resolve the problem, stop and report the evidence to the user; do not
attempt a second repair, change the case, or weaken the verifier. Passing one
case establishes integration, not general quality or a statistically reliable
token improvement. Infrastructure interruptions remain explicit, not solver
failures or silent successes.

## Isolation and evidence

Use only this lab's source, isolated runtime, and disposable benchmark resources.
Never change `erp-agent-odoo`, `pi-agent-python`, or `erp-harness-tau`. Setup may
provision the test fixture, but the candidate read surface must reject writes.
Native reads must not import the MCP server or silently fall back to MCP. The
hybrid B process still uses MCP for the remaining tools; it is not MCP-free.

Use unique jobs and retain failed runs. Keep credentials, databases, provider
payloads, and raw traces in ignored `.runtime/`; commit only source/configuration
and sanitized summaries. Preserve upstream attribution. Save implementation and
result checkpoints to GitHub without force-pushing or rewriting old results.

## Deferred stages (not authorized in this run)

After the stage-1 gates pass: separately test context projection/deduplication,
then native writes, then dynamic tool selection, and finally MCP-free packaging.
Write migration must cover both CRUD approvals and allowlisted business methods.
An ambiguous remote commit requires reconciliation, not blind retry; a local
action ID alone cannot guarantee exactly-once execution. Compiler remains out
of scope. Do not scaffold these future subsystems now.
