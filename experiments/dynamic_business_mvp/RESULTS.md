# Dynamic business MVP: r1

Business state **PASS (7/7)**; agent termination **TURN_LIMIT**, not a natural
completion. This establishes a working sales-to-invoice MVP with dynamic tools,
not production readiness, the original ERP-Bench 2262 score, or token savings
against an equally successful static run. Static remains the default.

## Frozen inputs and scope

- Source: `774cc7145d6eda52794a69c8b124addc40860b1a`.
- Rollback baseline: tag `business-mvp-baseline-323eee3` at
  `323eee3d5efb29f4a4471890ab50ddd59dcc57a3`.
- Job: `dynamic-business-mvp-20260908-r1`; trial: `dynamic_business_mvp__aMqdwWE`.
- Public ERP-Bench synthetic fixture 2262; frozen manifest SHA-256:
  `169886cd1c4fa78290816fe57288f0d00b68f596b15be7eaa7236acc324a8eb1`.
- Existing provider: `api.commandcode.ai`, model `deepseek/deepseek-v4-flash`.
- Native reads/actions/capabilities, controlled SOP, dynamic tools, World record.
- One customer, one product, one confirmed sales order, one posted invoice;
  no procurement, manufacturing, payment or delivery-completion workflow.
- Bounds: 24 requests / 24 turns, 4096 output tokens per request, 900 seconds;
  automatic retries and compaction disabled.

## Observed result

The model created sales order ID 1 for Nimbus Bureau, one Open-Plan Noise Barrier
Wall at 695.22, 30 Days terms and an explicit commitment date. Invoice ID 1 was
linked through its sale line and posted for 695.22. Independent JSON-2 reads
confirmed seven fixed checks: frozen seed identity, exactly one new global SO,
valid SO fields, exactly one new linked posted customer invoice, terms/amount,
unique invoice-line linkage, and zero new PO/MO records. Existing IDs and seed
values were captured before any model request.

The last paid response posted the invoice successfully. The local turn guard
then stopped the session before another model response or final narrative.
The verifier still ran after the agent phase. Business state passed; the model
loop did not naturally finish. Both outcomes are retained in the report.

| Measurement | r1 |
| --- | ---: |
| Independent custom business checks | 7 / 7 |
| Model HTTP requests / HTTP 200 responses | 24 / 24 |
| Model-visible tool calls | 47 |
| Native dispatches, including internal capability discovery | 41 |
| MCP protocol calls | 0 |
| Persisted actions verified | 6 / 6 |
| Tool count sent to model | 14 initially, 28 from request 4 |
| Uncached input tokens | 32,737 |
| Cached input tokens | 646,784 |
| Output tokens, including reasoning | 17,849 |
| Total reported tokens | 697,370 |
| Agent wall time | 237.808 seconds |
| Entire Harbor trial | 276.415 seconds |

Native runtime and action-ledger integrity checks passed. Both requested SOPs
were successfully read before the first mutation. The dynamic publication hash
matched the actual tools sent in request 4. All request receipts have response
headers; the 25th assistant entry is a local turn-limit event, not an HTTP call.
Approvals used the explicit `bench_auto_disposable` policy; this does not validate
production human approval. Monetary cost was unavailable and is not inferred
from total tokens or cache hits.

Three recoverable tool errors occurred: `safe_write_review` was first requested
without required model/operation inputs, and two reads guessed absent Odoo 19
fields (`res.partner.journal_id`, `product.product.uom_po_id`). These calls did
not mutate business state. The model corrected its requests; no automatic API
retry was used. World recorded 27 observations, including those two failed reads.

## Checks and next bounded work

Before the paid run, 10 offline dynamic-tool/budget tests passed, including a
real CodingSession/provider with Mock HTTP proving that a second request cannot
cross a budget of one. A separate disposable Odoo preflight rejected an untouched
baseline (1/7) and accepted a manually constructed correct fixture (7/7).
That manual fixture was never present in the model's fresh trial database.

The dominant remaining payload is accumulated history. At request 20, serialized
messages occupied 156,202 bytes and tool schemas 21,498 bytes. These are UTF-8
bytes, not tokenizer measurements. Broad profile/field results persist in later
requests; dynamic schema selection alone does not solve this. Next work should
first test smaller metadata responses and schema-aware field selection offline,
then rerun this subset with enough room for a final response. The direct-configure
shortcut was not used in this trajectory: the model listed capabilities first.
No new full benchmark or static paid comparison was run.

## Local receipts and rollback

- `.runtime/jobs/dynamic-business-mvp-20260908-r1/dynamic_business_mvp__aMqdwWE/`
  contains `agent/requests/`, session, native/SOP/dynamic/World receipts, the
  action database and `verifier/verifier_details.json`.
- `.runtime/reports/dynamic_business_mvp__aMqdwWE/trial_summary.json` retains
  `agent_termination.kind=TURN_LIMIT` alongside `verifier_rules.passed=7`.
- `.runtime/dynamic-business-mvp/` retains preflight evidence and failed setup
  attempts. These are ignored local artifacts; credentials and raw logs are not
  committed.
- Reproduce with `bash experiments/dynamic_business_mvp/run.sh` in the prepared
  WSL environment. Choose a new job name in a local config for another run and
  pass it to `run.sh`; existing receipts should not be overwritten.
- To inspect the pre-MVP implementation in isolation:
  `git worktree add --detach ../pi-odoo-business-mvp-baseline business-mvp-baseline-323eee3`.

Automatic approval initially rejected the API launch because it classified the
data as potentially sensitive business data. A second review accepted the same
command after the public seeded fixture and disposable-only write boundary were
verified. The rejected attempt made no model request.
