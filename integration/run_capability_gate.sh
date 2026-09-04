#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

snapshot=${1:?Pass the fixed snapshot directory}
snapshot=$(realpath "$snapshot")
test -f "$snapshot/manifest.json"
expected=$(python3 -c 'import json; print(json.load(open("configs/stage4-actions-ab.json"))["agents"][0]["kwargs"]["snapshot_sha256"])')
test "$(sha256sum "$snapshot/manifest.json" | cut -d' ' -f1)" = "$expected"
image=sha256:c99d46e9d14c68b4951b316337b427e37173e49241aa9e9cfd2e865b8738916d
docker image inspect "$image" >/dev/null

stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
fixture=pi-odoo-stage5-capability-$stamp
run=.runtime/stage5/$stamp
remote=/tmp/pi-odoo-harness-stage5-$stamp
venv=/tmp/pi-odoo-env-stage5
logs=/logs/stage5-capability-gate-$stamp
mkdir -p "$run/capability-gate"

finish() {
    status=$?
    trap - EXIT
    if docker inspect "$fixture" >/dev/null 2>&1; then
        docker cp "$fixture:$logs/." "$run/capability-gate/" >/dev/null 2>&1 || true
        docker logs "$fixture" > "$run/container.log" 2>&1 || true
        test "$(docker inspect "$fixture" --format '{{index .Config.Labels "pi-odoo-harness-lab"}}')" = stage5
        docker rm -f "$fixture" >/dev/null
    fi
    printf 'Capability gate artifacts: %s/capability-gate\n' "$run"
    exit "$status"
}
trap finish EXIT

docker run -d --name "$fixture" --label pi-odoo-harness-lab=stage5 \
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

docker exec -e PYTHONPATH="$remote/agent/src:$remote:/tmp/pi-odoo-mcp-source" \
    -e CAPABILITY_GATE_ROOT="$logs" -e ODOO_URL=http://127.0.0.1:8069 \
    -e ODOO_DB=bench -e ODOO_USERNAME=admin -e ODOO_TRANSPORT=json2 \
    -e ODOO_JSON2_DATABASE_HEADER=1 "$fixture" /bin/bash -c \
    'export ODOO_API_KEY="$(cat /etc/odoo/api_key)" ODOO_PASSWORD="$(cat /etc/odoo/api_key)"; exec "$1" -m integration.capability_gate' \
    _ "$venv/bin/python" | tee "$run/gate.log"
