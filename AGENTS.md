# Code Review Rules

- Preserve the native Odoo execution path; do not reintroduce an MCP runtime dependency.
- Keep field ACLs, per-action approval, and unknown-write handling explicit. Unknown writes must never be replayed automatically.
- Treat an Odoo readback as evidence for the current run, not as an ERP-Bench score.
- Report fresh input, cache reads, output/reasoning, and compaction separately; unknown usage stays unknown.
- Mock and renderer checks support mechanism coverage. They do not prove a real Odoo business transaction.
- Cleanup-only changes preserve existing features and compatibility entry points. Share equivalent implementation; unused product capabilities are not permission to remove them.

# Model regression maintenance

- After an observed agent failure, retain the earliest causal event and first corrective request in `experiments/agent_regression/`; freeze complete context, tool schemas, request/call IDs, source hashes and expected business constraints before changing the candidate.
- Add paid cases only from actual run evidence. Keep synthetic edge cases in offline tests; UI-only failures without a model request do not become invented model cases. Reuse existing normal-path controls.
- Do not require identical traces. Check business state, authorization/verification and then efficiency. Unknown outcomes stay unknown; no automatic paid retries.
