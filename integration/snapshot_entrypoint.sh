#!/usr/bin/env bash
# Restore before Odoo starts: no cached registry and no second scenario seeding.
set -euo pipefail
test "${PI_ODOO_LAB_SNAPSHOT:-}" = 1
test -f /snapshot/manifest.json
mkdir -p /logs/agent
pg_ctlcluster 18 main start
python3 /lab-snapshot.py restore /snapshot
# Match the benchmark entrypoint: setup answers are not agent resources.
rm -f -- /setup/setup_scenario.py /setup/scenario_data.json
export HOST=127.0.0.1 USER=odoo PASSWORD=odoo ODOO_DB=bench
touch /tmp/saas_setup_complete
# Identical A/B fixture condition: no autonomous cron writes during the trial.
exec odoo --config /etc/odoo/odoo.conf --http-port=8069 --max-cron-threads=0
