# Dynamic business MVP

This is a small independent Odoo acceptance experiment. It restores the
approved `2262_easy_26_buy_only_net_30_no_adjacent_data` snapshot, then asks
the dynamic native-tool agent to create one Nimbus Bureau sales order for one
Open-Plan Noise Barrier Wall and exactly one linked posted invoice.

The verifier captures existing IDs/counts before the model and checks the
delta afterwards. It re-reads Odoo through the independent JSON-2 client and
reports business checks separately from Harbor's score. It checks customer and
product identity, list price, quantity, explicit commitment date, confirmed
state, 30 Days terms, one posted linked invoice with a unique sale-line link,
amount, and zero new purchase/manufacturing orders. This is not the original
ERP-Bench task score and does not claim full 2262 coverage.

Latest complete run: [r2 results and round table](RESULTS_R2.md),
[full tool details](ROUND_DETAILS_R2.md), [CSV](ROUND_DETAILS_R2.csv).
It naturally finished in 29 model rounds and passed 7/7 business checks under
the historical verifier contract. That result is retained as recorded; it is
not relabeled as a pass under the newer contract below.

The current verifier contract is version 2, with 10 checks. It snapshots selected payment
fields (`state`, `amount`, `date`, `partner_id`, `memo`, `write_date`) and
selected picking/move fields before and after the run. It rejects any payment
addition, deletion, or observed-field change; preserves existing pickings and
moves; and allows only the one outgoing picking in the expected linked state
for the new order, customer, exact sale line, product, and quantity while its
picking and move remain pending. A reserved move is allowed when it is still in an
allowed nonterminal state and `picked` is false. The gate also requires a
posted invoice with `payment_state=not_paid` and a full `amount_residual`.
These state and snapshot checks establish the frozen business conditions, not
complete action provenance or an audit of every Odoo field. Older baselines
without the version-2 records fail closed.

Native write validation now rejects ISO `T`, timezone suffixes, and invalid
calendar dates before durable approval, including batch and nested relation
writes. Date fields accept `YYYY-MM-DD`; datetime fields also accept
`YYYY-MM-DD HH:MM:SS` in UTC. The existing date-only-to-midnight behavior and
false/null clearing remain supported. Approved values are sent unchanged.
Related live metadata is loaded only for live validation, once per related
model within that validation; unavailable metadata prevents approval.

Offline regression on 2026-09-08 after these fixes:

| Check | Result |
| --- | --- |
| Project `pytest tests` | 98 passed, 47 subtests passed; 1 Node check skipped in WSL |
| Exact skipped JavaScript test, using installed Windows Node | Passed (`NATIVE_EXTENSION_OK`) |
| Clean native environment: layout, dynamic tools, runner budget | 14 passed |
| Dynamic module self-check and verifier self-check | Both passed |
| Real model API requests | 0 |

The project suite includes 23 action tests and 8 verifier tests, with valid
pending deliveries, payment changes, invoice reconciliation, delivery state and
link violations, record deletion, and incomplete baselines. WSL receipts are
in ignored `.runtime/mvp-fix-regression-hKdFno/`. These are offline regressions;
the stronger 10-check contract has not been run against a new live business
trial. The historical r2 defect report remains unchanged. Dynamic tools remain
an experiment and are not promoted to the default configuration.

Run `bash experiments/dynamic_business_mvp/run.sh` in the existing WSL
environment from a clean commit. The wrapper verifies the frozen manifest hash
and source case, then reuses `integration/run_baseline.sh` with the dedicated
snapshot compose overlay. Credentials remain in ignored `.runtime/control.env`.
The current acceptance run has no configured model-turn, request-count, or
per-response output limit. It restores the normal session defaults and the Stage
7 infrastructure timeout (3570 seconds inside the agent, 3600 seconds outside).
Acceptance requires natural agent completion plus an independent business pass.
The previous 24-turn run is retained in RESULTS.md and its frozen source commit.

Work proceeds in this order: functional correctness, deployment readiness, then
engineering and token-efficiency tradeoffs. Reports include an experiment summary
and every model round, including tool results, usage, timing and final completion.
Static tools remain the default outside this experiment. Equal-quality token
savings require a separate matching static business run.
