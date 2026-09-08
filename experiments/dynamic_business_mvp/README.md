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
The run is limited to 24 model requests, 4096 output tokens per request, and
900 seconds, with automatic retries and compaction disabled. Static tools remain
the default outside this experiment. No claim of equal-quality token savings is
made without a matching static business run.
