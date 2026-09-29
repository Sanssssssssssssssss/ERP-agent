"""Real local IPC checks; no GPU, paid model or business write."""
import json
import os
import subprocess
import sys
from urllib.error import HTTPError

import pytest

from erp_harness.providers.selector_service import SelectorService, decide


def test_service_survives_separate_clients_and_closes_with_host(tmp_path, monkeypatch):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'python': sys.executable}))
    fixture = tmp_path / 'selector.py'
    fixture.write_text("from erp_harness.providers.selector_service import serve\n"
                       "import os,sys\n"
                       "assert not any(k.startswith(('ODOO_', 'COMMAND_CODE_', 'LLM_')) for k in os.environ)\n"
                       "serve(lambda packet: {'id': packet['id'], 'pid': os.getpid()}, sys.argv[2])\n")
    popen = subprocess.Popen
    def launch(command, **kwargs):
        command[5] = str(fixture)
        return popen(command, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    monkeypatch.setenv('ODOO_API_KEY', 'must-not-leak')
    monkeypatch.setenv('COMMAND_CODE_API_KEY', 'must-not-leak')
    service = SelectorService(config, tmp_path / 'routing', 'run-one')
    try:
        first = decide(service.endpoint, {'id': 'before-approval', 'run_id': 'run-one'})
        second = decide(service.endpoint, {'id': 'after-approval', 'run_id': 'run-one'})
        ready = json.loads((tmp_path / 'routing/service.json').read_text())
        assert first['pid'] == second['pid'] == ready['pid']
        for endpoint, packet in [({**service.endpoint, 'token': 'wrong'}, {'run_id': 'run-one'}),
                                  (service.endpoint, {'run_id': 'other-run'})]:
            with pytest.raises(HTTPError):
                decide(endpoint, packet)
        assert ready['startup_ms'] > 0 and 'token' not in ready
    finally:
        service.close()
    assert service.process.poll() == 0
    assert not (tmp_path / 'routing/service.stderr.log').read_text()
    service.close()  # Terminalization and host close may both clean up.
