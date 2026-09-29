"""Observed IR01/IR02: one POST per arm; candidate tool results come from runtime code."""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

from .freeze import read, digest, write_once
from .prepare import replace, validate_patches
from .runner import complete

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.runtime/interrupted-recovery-20260930'


async def prepare():
    from erp_harness.app import conversation
    from erp_harness.app.business_status import build_status_context
    from erp_harness.app.host import _connection_identity

    specs = read(Path(__file__).with_suffix('.json'))
    state = read(OUT / 'observed/workbench-state.json')
    incident = read(OUT / 'frozen.json')
    bid = incident['business_id']
    sid = state['businesses'][bid]['session_id']
    env = read(ROOT / '.runtime/enterprise-validation-20260922/connection.json')
    with patch.dict(os.environ, {**env, 'PI_AGENT_SESSION_ID': sid}):
        bound = build_status_context(state, bid, sid, _connection_identity())
        with patch.object(conversation, '_BUSINESS_CONTEXT', bound):
            fresh = (await conversation._read_business_status('recovery-readback', {})).details
        assert fresh['success'], fresh
        # This arm replays the observed absent-file handoff, not an invented unselected business.
        with patch.dict(os.environ, {'ERP_CONVERSATION_BUSINESS': str(OUT / 'absent-context.json'),
                                    'ERP_CONVERSATION_BUSINESS_ID': bid}):
            missing = conversation.load_business_context()
    rows = []
    for spec, result in zip(specs, [missing, fresh]):
        source = ROOT / spec['source']
        assert digest(source.read_bytes()) == spec['request_sha256']
        original = read(source)
        candidate, patches = copy.deepcopy(original), []
        replace(candidate, original, patches, spec['allowed_patch_paths'], spec['allowed_patch_paths'][0],
                json.dumps(result, ensure_ascii=False), 'production conversation/business_status readback')
        validate_patches(original, candidate, patches, spec['allowed_patch_paths'])
        for arm, payload in [('baseline', original), ('candidate', candidate)]:
            directory = OUT / 'model' / spec['id'] / arm
            write_once(directory / 'request.json', payload)
            rows.append({'case': spec['id'], 'arm': arm, 'path': str(directory.relative_to(OUT)),
                         'sha256': digest((directory / 'request.json').read_bytes())})
        write_once(OUT / 'model' / spec['id'] / 'patches.json', patches)
    write_once(OUT / 'fresh-status.json', fresh)
    files = ['src/erp_harness/app/conversation.py', 'src/erp_harness/app/business_status.py',
             'src/erp_harness/erp/invoice_mail.py', 'experiments/agent_regression/interrupted_recovery.py']
    write_once(OUT / 'model/frozen.json', {'branches': rows, 'maximum_posts': 4, 'retries': 0,
               'tool_execution': False, 'source_hashes': {p: digest((ROOT / p).read_bytes()) for p in files}})
    print('Frozen two observed nodes, four single-POST branches. Fresh Odoo reads only.')


async def paid():
    frozen = read(OUT / 'model/frozen.json')
    for p, h in frozen['source_hashes'].items():
        assert digest((ROOT / p).read_bytes()) == h, p
    for row in frozen['branches']:
        folder = OUT / row['path']
        assert digest((folder / 'request.json').read_bytes()) == row['sha256']
        result = await complete(read(folder / 'request.json'), folder / 'api',
                                api_key=os.environ['COMMAND_CODE_API_KEY'])
        print(json.dumps({'case': row['case'], 'arm': row['arm'], 'posts': result.get('posts'),
                          'error': result.get('error'), 'usage': result.get('usage')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paid', action='store_true')
    asyncio.run(paid() if parser.parse_args().paid else prepare())
