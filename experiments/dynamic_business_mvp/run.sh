#!/usr/bin/env bash
# A new task rubric on the frozen 2262 data, using the existing native runner.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PI_ODOO_LAB_ROOT="$PWD"
export PI_ODOO_SNAPSHOT="$PWD/.runtime/stage3/20260903T195523Z-1156/snapshot"
export PI_ODOO_SNAPSHOT_CASE=2262_easy_26_buy_only_net_30_no_adjacent_data
config=${1:-experiments/dynamic_business_mvp/config.json}
python3 - "$config" "$PI_ODOO_SNAPSHOT/manifest.json" "$PI_ODOO_SNAPSHOT_CASE" <<'PY'
import hashlib, json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
manifest = pathlib.Path(sys.argv[2]).read_bytes()
assert json.loads(manifest)["case"] == sys.argv[3]
assert {a["kwargs"]["snapshot_sha256"] for a in config["agents"]} == {hashlib.sha256(manifest).hexdigest()}
PY
exec bash bench/adapters/run_baseline.sh "$config"
