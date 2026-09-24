#!/usr/bin/env bash
set -euo pipefail
CASE="${1:?2003 or 2156}"
[[ "$CASE" == 2003 || "$CASE" == 2156 ]]
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$ROOT/.runtime/enterprise-validation-20260922/bench"
python3 "$ROOT/experiments/enterprise_validation/benchmark.py" --verify
[[ ! -d "$RUN/jobs/$CASE" && ! -e "$RUN/logs/$CASE.log" ]] || { echo 'Existing attempt; refusing paid rerun'; exit 2; }
export PYTHONPATH="$ROOT" PYTHON_DOTENV_DISABLED=1 LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1 ERP_MEMORY_MODE=off
export OPENAI_API_KEY="${LLM_API_KEY:?}" OPENAI_BASE_URL="${LLM_BASE_URL:?}"
[[ "$OPENAI_BASE_URL" == https://api.commandcode.ai/provider/v1 ]]
export PI_ODOO_SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
export WORKBENCH_BACKEND_WHEEL="$ROOT/dist/erp_harness-0.5.4-py3-none-any.whl"
export TMPDIR="$RUN/tmp"
cd "$ROOT"
exec harbor run -c "$RUN/configs/$CASE.json" > "$RUN/logs/$CASE.log" 2>&1
