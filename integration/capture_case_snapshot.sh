#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

case_name=${1:?Pass the benchmark task directory name}
[[ "$case_name" =~ ^[a-z0-9][a-z0-9._-]+$ ]]
environment="bench/tasks/$case_name/environment"
test -f "$environment/Dockerfile"

stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
image="pi-odoo-snapshot-$case_name"
fixture="pi-odoo-stage3-fixture-$stamp"
finish() {
    docker rm -f "$fixture" >/dev/null 2>&1 || true
}
trap finish EXIT

docker build -q -t "$image" "$environment"
docker run -d --name "$fixture" --label pi-odoo-harness-lab=stage3 "$image" >/dev/null
./integration/prepare_read_gate.sh "$fixture" stage3 "$case_name"
