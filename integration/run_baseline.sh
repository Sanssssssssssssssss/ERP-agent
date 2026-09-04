#!/usr/bin/env bash
# Keep credential expansion inside Bash, not a PowerShell -> WSL command string.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .runtime/control.env
set +a
export OPENAI_API_KEY="${LLM_API_KEY:?Missing LLM_API_KEY in .runtime/control.env}"
export OPENAI_BASE_URL="${LLM_BASE_URL:?Missing LLM_BASE_URL in .runtime/control.env}"
export PYTHONPATH="$PWD/agent/src:$PWD"
git -c safe.directory="$PWD" diff --quiet
git -c safe.directory="$PWD" diff --cached --quiet
export PI_ODOO_SOURCE_COMMIT="$(git -c safe.directory="$PWD" rev-parse HEAD)"
config=${1:?Provide a baseline config}
if grep -q 'configs/harbor-snapshot-compose.yml' "$config"; then
    snapshot_sha=$(python3 -c 'import json,sys; v={a["kwargs"]["snapshot_sha256"] for a in json.load(open(sys.argv[1]))["agents"]}; assert len(v)==1; print(v.pop())' "$config")
    mapfile -t snapshots < <(
        find .runtime/stage3 -path '*/snapshot/manifest.json' -type f -print0 \
            | while IFS= read -r -d '' manifest; do
                test "$(sha256sum "$manifest" | cut -d' ' -f1)" = "$snapshot_sha" \
                    && dirname "$manifest"
            done
    )
    test "${#snapshots[@]}" = 1
    export PI_ODOO_LAB_ROOT="$PWD"
    export PI_ODOO_SNAPSHOT="$(realpath "${snapshots[0]}")"
fi
if ! command -v harbor >/dev/null; then
    export PATH="$PWD/.runtime/harbor-wsl/bin:$PATH"
fi
command -v harbor >/dev/null
printf 'API key and endpoint loaded from .runtime/control.env; starting one configured baseline.\n'
exec .runtime/venv-linux/bin/python -m integration.report --run-config "$config"
