#!/usr/bin/env bash
# Accept only a labelled, disposable fixture; keep every attempt's artifacts.
set -euo pipefail
cd "$(dirname "$0")/.."
fixture=${1:?Pass the labelled disposable fixture container name}
stage=${2:-stage1}
[[ "$stage" =~ ^stage[1-7]$ ]]
[[ "$fixture" == pi-odoo-$stage-fixture-* ]]
test "$(docker inspect "$fixture" --format '{{index .Config.Labels "pi-odoo-harness-lab"}}')" = "$stage"
run=.runtime/$stage/$(date -u +%Y%m%dT%H%M%SZ)
remote=/tmp/pi-odoo-harness-$stage
venv=/tmp/pi-odoo-env-$stage
logs=/logs/$stage-agent
mkdir -p "$run/snapshot" "$run/read-gate"
for attempt in $(seq 1 150); do
    docker exec "$fixture" test -f /tmp/saas_setup_complete && break
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
docker exec "$fixture" uv venv --python /usr/bin/python3 --system-site-packages "$venv"
docker exec "$fixture" uv pip install --python "$venv/bin/python" --no-index --find-links /tmp/pi-odoo-wheelhouse \
    odoo-mcp==1.3.2 mcp==2.0.0 mcp-types==2.0.0 requests==2.33.1 \
    jsonschema==4.26.0 packaging==26.2 pathspec==1.1.1 pillow==12.2.0 \
    pygments==2.20.0 pyyaml==6.0.3 rich==15.0.0 textual==8.2.8 typer==0.26.7

# Take the unmodified benchmark snapshot BEFORE making the separate security DB.
remote_snapshot=/tmp/pi-odoo-snapshot-$(date +%s)
docker exec -e PI_ODOO_LAB_SNAPSHOT=1 "$fixture" python3 \
    "$remote/integration/snapshot.py" capture "$remote_snapshot"
docker cp "$fixture:$remote_snapshot/." "$run/snapshot/"
docker inspect "$fixture" --format '{{.Image}}' > "$run/snapshot/image-id.txt"
git rev-parse HEAD > "$run/source-commit.txt"
printf 'Prepared snapshot: %s/snapshot\n' "$run"

docker exec "$fixture" su postgres -s /bin/bash -c 'createdb -O odoo bench_read_gate'
docker exec "$fixture" bash -c "runuser -u postgres -- pg_restore --exit-on-error -d bench_read_gate < $remote_snapshot/database.dump"
docker exec "$fixture" bash -c "odoo shell --config /etc/odoo/odoo.conf -d bench_read_gate --no-http < $remote/integration/read_gate.py"
set +e
docker exec -e PYTHONPATH="$remote/agent/src:$remote:/tmp/pi-odoo-mcp-source" -e READ_GATE_ROOT="$logs" \
    "$fixture" "$venv/bin/python" -m integration.read_gate
result=$?
set -e
docker cp "$fixture:$logs/." "$run/read-gate/"
exit "$result"
