#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python3 /tests/selfcheck.py
python3 /tmp/pi-odoo-mvp/verifier.py --check /logs/agent/mvp-baseline.json
