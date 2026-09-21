#!/usr/bin/env bash
# Accept only a labelled, disposable fixture; keep every attempt's artifacts.
set -euo pipefail
cd "$(dirname "$0")/../.."
fixture=${1:?Pass the labelled disposable fixture container name}
stage=${2:-stage1}
case_name=${3:?Pass the benchmark task directory name}
[[ "$stage" =~ ^stage[1-7]$ ]]
[[ "$fixture" == pi-odoo-$stage-fixture-* ]]
[[ "$case_name" =~ ^[a-z0-9][a-z0-9._-]+$ ]]
test "$(docker inspect "$fixture" --format '{{index .Config.Labels "pi-odoo-harness-lab"}}')" = "$stage"
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
run=.runtime/$stage/$stamp
remote=/tmp/pi-odoo-harness-$stage-$stamp
venv=/tmp/pi-odoo-env-$stage
logs=/logs/$stage-agent-$stamp
mkdir -p "$run/snapshot" "$run/read-gate"
for attempt in $(seq 1 150); do
    if docker exec "$fixture" test -f /tmp/saas_setup_complete \
        && docker exec "$fixture" curl -sf http://127.0.0.1:8069/web/database/selector >/dev/null; then
        break
    fi
    test "$(docker inspect "$fixture" --format '{{.State.Running}}')" = true
    sleep 2
done
docker exec "$fixture" test -f /tmp/saas_setup_complete
docker exec "$fixture" curl -sf http://127.0.0.1:8069/web/database/selector >/dev/null
docker exec "$fixture" mkdir -p /tmp/pi-odoo-mcp-source "$remote/bench" "$logs"
docker cp bench/reference/mcp/src/odoo_mcp "$fixture:/tmp/pi-odoo-mcp-source/"
docker cp bench/adapters "$fixture:$remote/bench/"
docker cp bench/__init__.py "$fixture:$remote/bench/"
docker cp dist/erp_harness-0.5.4-py3-none-any.whl "$fixture:$remote/"
docker cp .runtime/wheelhouse-py312 "$fixture:/tmp/pi-odoo-wheelhouse"
docker exec "$fixture" uv venv --clear --python /usr/bin/python3 --system-site-packages "$venv"
docker exec "$fixture" uv pip install --python "$venv/bin/python" --no-index --find-links /tmp/pi-odoo-wheelhouse \
    odoo-mcp==1.3.2 mcp==2.0.0 mcp-types==2.0.0 requests==2.33.1 \
    jsonschema==4.26.0 packaging==26.2 pathspec==1.1.1 pillow==12.2.0 \
    pygments==2.20.0 pyyaml==6.0.3 rich==15.0.0 textual==8.2.8 typer==0.26.7

docker exec "$fixture" uv pip install --python "$venv/bin/python" --no-deps "$remote/erp_harness-0.5.4-py3-none-any.whl"

# Take the unmodified benchmark snapshot BEFORE making the separate security DB.
remote_snapshot=/tmp/pi-odoo-snapshot-$stage-$stamp
docker exec -e PI_ODOO_LAB_SNAPSHOT=1 -e PI_ODOO_SNAPSHOT_CASE="$case_name" \
    "$fixture" python3 \
    "$remote/bench/adapters/snapshot.py" capture "$remote_snapshot"
docker cp "$fixture:$remote_snapshot/." "$run/snapshot/"
docker inspect "$fixture" --format '{{.Image}}' > "$run/snapshot/image-id.txt"
git rev-parse HEAD > "$run/source-commit.txt"
printf 'Prepared snapshot: %s/snapshot\n' "$run"

docker exec "$fixture" su postgres -s /bin/bash -c 'dropdb --force --if-exists bench_read_gate'
docker exec "$fixture" su postgres -s /bin/bash -c 'createdb -O odoo bench_read_gate'
docker exec "$fixture" bash -c "runuser -u postgres -- pg_restore --exit-on-error -d bench_read_gate < $remote_snapshot/database.dump"
docker exec "$fixture" bash -c "odoo shell --config /etc/odoo/odoo.conf -d bench_read_gate --no-http < $remote/bench/adapters/read_gate.py"
set +e
docker exec -e PYTHONPATH="$remote:/tmp/pi-odoo-mcp-source" -e READ_GATE_ROOT="$logs" \
    "$fixture" "$venv/bin/python" -m bench.adapters.read_gate
result=$?
set -e
docker cp "$fixture:$logs/." "$run/read-gate/"
exit "$result"
