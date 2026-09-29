"""Replay MC01's real incomplete-consumption state; optional one model POST, no writes."""
import argparse
import asyncio
import copy
import json
import os
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from erp_harness.erp.actions import NativeActions
from erp_harness.erp.gateway import Json2ReadClient
from erp_harness.erp.reads import NativeReads
from erp_harness.erp.store import ActionStore
from .freeze import digest, read, write_once
from .runner import complete

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.runtime/e02-consumption-fix-20260929/local'


async def main(paid):
    if paid:
        frozen = read(OUT / 'frozen.json')
        assert digest((OUT / 'candidate.json').read_bytes()) == frozen['candidate_sha256']
        for name, sha in frozen['sources'].items():
            assert digest((ROOT / name).read_bytes()) == sha
        result = await complete(read(OUT / 'candidate.json'), OUT / 'api', api_key=os.environ['COMMAND_CODE_API_KEY'])
        print(json.dumps({k: result[k] for k in ('posts', 'error', 'tool_calls', 'usage')}, ensure_ascii=False))
        return
    case = read(ROOT / 'experiments/agent_regression/manufacturing_consumption.json')[0]
    source = ROOT / case['source']
    assert digest(source.read_bytes()) == case['request_sha256']
    env = read(ROOT / '.runtime/laya-additive-live-20260929/environment.json')
    account = read(ROOT / '.runtime/enterprise-validation-20260922/snapshots/baseline-10000/accounts.json')['manager']
    native = NativeReads(Json2ReadClient(url=env['url'], db=env['databases']['E02'], username=account['login'],
        api_key=account['api_key'], context={'allowed_company_ids': account['company_ids']}))
    OUT.mkdir(parents=True, exist_ok=True)
    writer = Mock()
    os.environ.update(ODOO_MCP_ENABLE_WRITES='1', ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS='mrp.production.button_mark_done')
    historical = read(source.with_name('0037.output.json'))['tool_calls'][0]
    with closing(ActionStore(OUT / 'preflight.sqlite3')) as store:
        actions = NativeActions(native, store=store, clients={'default': writer}, approval_mode='host')
        result = actions.execute_method(**historical['arguments'])
        assert result['success'] is False and 'set_qty_producing' in result.get('error', ''), result
        assert not result.get('approval_required') and not store.summary()['receipts']
        writer.execute_method.assert_not_called()
    original = read(source)
    candidate = copy.deepcopy(original)
    candidate['messages'].extend([
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id': historical['id'], 'type': 'function',
         'function': {'name': historical['name'], 'arguments': json.dumps(historical['arguments'])}}]},
        {'role': 'tool', 'tool_call_id': historical['id'], 'content': json.dumps(result, ensure_ascii=False)},
    ])
    assert candidate['messages'][:-2] == original['messages']
    write_once(OUT / 'candidate.json', candidate)
    write_once(OUT / 'preflight.json', result)
    write_once(OUT / 'frozen.json', {'source': case, 'branch': 'Original 0037 request plus its actual tool intent and candidate runtime preflight; no approval injected',
        'candidate_sha256': digest((OUT / 'candidate.json').read_bytes()), 'erp_writes': 0,
        'sources': {name: digest((ROOT / name).read_bytes()) for name in
                    ('src/erp_harness/erp/business_operations.py', 'experiments/agent_regression/consumption_recovery.py')}})
    print('Real failed state rejected before approval or write; corrective branch frozen.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paid', action='store_true')
    asyncio.run(main(parser.parse_args().paid))
