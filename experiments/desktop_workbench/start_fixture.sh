#!/usr/bin/env bash
# Disposable frozen Odoo for desktop acceptance. Never reuses an existing container.
set -euo pipefail
lab_root=$(cd "$(dirname "$0")/../.." && pwd)
snapshot="$lab_root/.runtime/stage3/20260903T195523Z-1156/snapshot"
container=pi-odoo-desktop-acceptance-20260908
receipt_dir="$lab_root/.runtime/desktop-acceptance/fixture"
image=sha256:c99d46e9d14c68b4951b316337b427e37173e49241aa9e9cfd2e865b8738916d
test -f "$snapshot/manifest.json"
test "$(sha256sum "$snapshot/manifest.json" | cut -d ' ' -f 1)" = 169886cd1c4fa78290816fe57288f0d00b68f596b15be7eaa7236acc324a8eb1
if docker container inspect "$container" >/dev/null 2>&1; then
  printf 'Refusing to reuse existing acceptance container: %s\n' "$container" >&2
  exit 1
fi
mkdir -p "$receipt_dir"
docker run --detach --name "$container" \
  --label pi-odoo-lab-purpose=desktop-acceptance \
  --publish 127.0.0.1:18069:8069 \
  --env PI_ODOO_LAB_SNAPSHOT=1 \
  --env PI_ODOO_SNAPSHOT_CASE=2262_easy_26_buy_only_net_30_no_adjacent_data \
  --volume "$lab_root/integration/snapshot_entrypoint.sh:/lab-snapshot-entrypoint.sh:ro" \
  --volume "$lab_root/integration/snapshot.py:/lab-snapshot.py:ro" \
  --volume "$snapshot:/snapshot:ro" \
  --volume "$receipt_dir:/logs/agent" \
  --entrypoint /bin/bash "$image" /lab-snapshot-entrypoint.sh
printf 'Isolated acceptance endpoint: http://127.0.0.1:18069 (bench)\n'
