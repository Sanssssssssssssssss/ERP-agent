#!/usr/bin/env bash
# Run Stage 4 writes only in a labelled container restored from the fixed snapshot.
set -euo pipefail
cd "$(dirname "$0")/.."

snapshot=${1:?Pass a Stage-3 snapshot directory}
snapshot=$(realpath "$snapshot")
test -f "$snapshot/manifest.json"
expected=$(python3 -c 'import json; print(json.load(open("configs/stage4-actions-ab.json"))["agents"][0]["kwargs"]["snapshot_sha256"])')
test "$(sha256sum "$snapshot/manifest.json" | cut -d' ' -f1)" = "$expected"
# Same empty task image pinned by integration/deadline_gate.py; the capture image already contains bench.
image=sha256:c99d46e9d14c68b4951b316337b427e37173e49241aa9e9cfd2e865b8738916d
docker image inspect "$image" >/dev/null

stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
fixture=pi-odoo-stage4-fixture-$stamp
run=.runtime/stage4/$stamp
remote=/tmp/pi-odoo-harness-stage4-$stamp
venv=/tmp/pi-odoo-env-stage4
logs=/logs/stage4-action-gate-$stamp
mkdir -p "$run/action-gate"

finish() {
    status=$?
    trap - EXIT
    if docker inspect "$fixture" >/dev/null 2>&1; then
        docker cp "$fixture:$logs/." "$run/action-gate/" >/dev/null 2>&1 || true
        docker logs "$fixture" > "$run/container.log" 2>&1 || true
        test "$(docker inspect "$fixture" --format '{{index .Config.Labels "pi-odoo-harness-lab"}}')" = stage4
        docker rm -f "$fixture" >/dev/null
    fi
    printf 'Action gate artifacts: %s/action-gate\n' "$run"
    exit "$status"
}
trap finish EXIT

docker run -d --name "$fixture" --label pi-odoo-harness-lab=stage4 \
    -e PI_ODOO_LAB_SNAPSHOT=1 \
    -v "$(pwd)/integration/snapshot_entrypoint.sh:/lab-snapshot-entrypoint.sh:ro" \
    -v "$(pwd)/integration/snapshot.py:/lab-snapshot.py:ro" \
    -v "$snapshot:/snapshot:ro" \
    --entrypoint /bin/bash "$image" /lab-snapshot-entrypoint.sh >/dev/null

for attempt in $(seq 1 150); do
    if docker exec "$fixture" test -f /tmp/saas_setup_complete \
        && docker exec "$fixture" pgrep -fx '/usr/bin/python3 /usr/bin/odoo --config /etc/odoo/odoo.conf --http-port=8069 --max-cron-threads=0' >/dev/null; then
        break
    fi
    test "$(docker inspect "$fixture" --format '{{.State.Running}}')" = true
    sleep 2
done
docker exec "$fixture" test -f /tmp/saas_setup_complete
docker exec "$fixture" mkdir -p /tmp/pi-odoo-mcp-source "$remote/agent/src" "$logs"
docker cp mcp/src/odoo_mcp "$fixture:/tmp/pi-odoo-mcp-source/"
docker cp integration "$fixture:$remote/"
docker cp odoo_runtime "$fixture:$remote/"
for package in pi_ai pi_agent pi_coding; do
    docker cp "agent/src/$package" "$fixture:$remote/agent/src/"
done
docker cp .runtime/wheelhouse-py312 "$fixture:/tmp/pi-odoo-wheelhouse"
docker exec "$fixture" uv venv --clear --python /usr/bin/python3 --system-site-packages "$venv"
docker exec "$fixture" uv pip install --python "$venv/bin/python" --no-index --find-links /tmp/pi-odoo-wheelhouse \
    odoo-mcp==1.3.2 mcp==2.0.0 mcp-types==2.0.0 requests==2.33.1 \
    jsonschema==4.26.0 packaging==26.2 pathspec==1.1.1 pillow==12.2.0 \
    pygments==2.20.0 pyyaml==6.0.3 rich==15.0.0 textual==8.2.8 typer==0.26.7

docker exec -e PI_ODOO_ACTION_GATE=1 -i "$fixture" \
    odoo shell --config /etc/odoo/odoo.conf -d bench --no-http \
    < integration/action_gate.py | tee "$run/provision.log"
set +e
docker exec -e PYTHONPATH="$remote/agent/src:$remote:/tmp/pi-odoo-mcp-source" \
    -e ACTION_GATE_ROOT="$logs" -e ODOO_URL=http://127.0.0.1:8069 -e ODOO_DB=bench \
    -e ODOO_USERNAME=admin -e ODOO_TRANSPORT=json2 -e ODOO_JSON2_DATABASE_HEADER=1 \
    "$fixture" /bin/bash -c \
    'export ODOO_API_KEY="$(cat /etc/odoo/api_key)" ODOO_PASSWORD="$(cat /etc/odoo/api_key)"; exec "$1" -m integration.action_gate' \
    _ "$venv/bin/python" | tee "$run/gate.log"
gate_status=${PIPESTATUS[0]}
set -e
if ((gate_status)); then
    docker exec "$fixture" su postgres -s /bin/bash -c \
        "psql -d bench -Atc \"SELECT id,name,active,write_date FROM res_partner WHERE name LIKE 'PI_ACTION_GATE_%' ORDER BY id\"" \
        | tee "$run/failure-db.txt"
    exit "$gate_status"
fi
