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
if ! command -v harbor >/dev/null; then
    export PATH="$PWD/.runtime/harbor-wsl/bin:$PATH"
fi
command -v harbor >/dev/null
printf 'API key and endpoint loaded from .runtime/control.env; starting one configured baseline.\n'
exec .runtime/venv-linux/bin/python -m integration.report --run-config "${1:?Provide a baseline config}"
