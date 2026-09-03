#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
lab="$PWD"
export PYTHONPATH="$lab/agent/src:$lab/mcp/src:$lab"
python="$lab/.runtime/venv-linux/bin/python"
run=.runtime/stage1/check-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$run"
# MCP tests import tests.test_batch_write; run from that package's own root.
(
    cd mcp
    env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
        "$python" -m pytest tests -q -p no:cacheprovider \
        --basetemp="$(mktemp -d /tmp/pi-odoo-stage1.XXXXXX)"
) 2>&1 | tee "$run/mcp-regression.log"
"$python" -m unittest discover -s tests -v 2>&1 | tee "$run/integration.log"
printf 'STAGE1_OFFLINE_GATES_PASSED\n'
