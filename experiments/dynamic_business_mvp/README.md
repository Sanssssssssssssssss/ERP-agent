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
