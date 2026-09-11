# Code Review Rules

- Preserve the native Odoo execution path; do not reintroduce an MCP runtime dependency.
- Keep field ACLs, per-action approval, and unknown-write handling explicit. Unknown writes must never be replayed automatically.
- Treat an Odoo readback as evidence for the current run, not as an ERP-Bench score.
- Report fresh input, cache reads, output/reasoning, and compaction separately; unknown usage stays unknown.
- Mock and renderer checks support mechanism coverage. They do not prove a real Odoo business transaction.
