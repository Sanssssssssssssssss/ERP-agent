#!/usr/bin/env bash
# This fixture name is intentionally fixed; never attach to a production Odoo.
set -euo pipefail
cd "$(dirname "$0")/.."
fixture=pi-odoo-stage1-fixture-20260903-r2
docker inspect "$fixture" >/dev/null
for attempt in $(seq 1 150); do
    docker exec "$fixture" test -f /tmp/saas_setup_complete && break
    test "$(docker inspect "$fixture" --format '{{.State.Running}}')" = true
    sleep 2
done
docker exec "$fixture" test -f /tmp/saas_setup_complete
mkdir -p .runtime/stage1/fixture .runtime/stage1/read-gate
docker exec "$fixture" mkdir -p /tmp/pi-odoo-mcp-source /tmp/pi-odoo-harness/agent/src
docker cp mcp/src/odoo_mcp "$fixture:/tmp/pi-odoo-mcp-source/"
docker cp integration "$fixture:/tmp/pi-odoo-harness/"
for package in pi_ai pi_agent pi_coding; do
    docker cp "agent/src/$package" "$fixture:/tmp/pi-odoo-harness/agent/src/"
done
docker cp .runtime/wheelhouse-py312 "$fixture:/tmp/pi-odoo-wheelhouse"
docker exec "$fixture" uv pip install --system --no-index --find-links /tmp/pi-odoo-wheelhouse \
    odoo-mcp==1.3.2 mcp==2.0.0 mcp-types==2.0.0 requests==2.33.1 \
    jsonschema==4.26.0 packaging==26.2 pathspec==1.1.1 pillow==12.2.0 \
    pygments==2.20.0 pyyaml==6.0.3 rich==15.0.0 textual==8.2.8 typer==0.26.7

# Take the unmodified benchmark snapshot BEFORE making the separate security DB.
docker exec "$fixture" su postgres -s /bin/bash -c 'pg_dump -Fc -d bench -f /tmp/bench-seed.dump'
docker cp "$fixture:/tmp/bench-seed.dump" .runtime/stage1/fixture/bench.dump
docker cp "$fixture:/etc/odoo/api_key" .runtime/stage1/fixture/api_key
docker exec "$fixture" tar -C /var/lib/odoo -cf /tmp/bench-filestore.tar filestore
docker cp "$fixture:/tmp/bench-filestore.tar" .runtime/stage1/fixture/filestore.tar
docker inspect "$fixture" --format '{{.Image}}' > .runtime/stage1/fixture/image-id.txt
sha256sum .runtime/stage1/fixture/bench.dump .runtime/stage1/fixture/filestore.tar \
    > .runtime/stage1/fixture/checksums.txt

docker exec "$fixture" su postgres -s /bin/bash -c 'createdb -O odoo bench_read_gate'
docker exec "$fixture" su postgres -s /bin/bash -c 'pg_restore -d bench_read_gate /tmp/bench-seed.dump'
docker exec "$fixture" bash -c 'odoo shell --config /etc/odoo/odoo.conf -d bench_read_gate --no-http < /tmp/pi-odoo-harness/integration/read_gate.py'
set +e
docker exec -e PYTHONPATH=/tmp/pi-odoo-harness/agent/src:/tmp/pi-odoo-harness:/tmp/pi-odoo-mcp-source \
    "$fixture" python3 -m integration.read_gate
result=$?
set -e
docker cp "$fixture:/logs/agent/." .runtime/stage1/read-gate/
exit "$result"
