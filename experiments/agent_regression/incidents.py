"""Replay observed incidents and existing controls with the one-POST transport."""
import argparse
import asyncio
import copy
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from .cases import ROOT, DEFAULT as ORIGINAL, INCIDENTS
from .freeze import canonical, digest, read, write_once
from .oracle import evaluate
from .prepare import replace, validate_patches
from .runner import complete

DEFAULT = ROOT / '.runtime/agent-regression-guards-20260924'


def prepare(directory=DEFAULT):
    from erp_harness.app.worker import child_environment
    from erp_harness.tools.sops import build_sop_payload

    directory = Path(directory)
    if (directory / 'freeze.json').exists():
        return verify(directory)
    rows, sources = [], {}

    def capture(path):
        path = Path(path)
        sources[str(path.relative_to(ROOT))] = digest(path.read_bytes())
        return read(path)

    for spec in [*INCIDENTS, *[capture(ORIGINAL / 'cases' / key / 'manifest.json') for key in ('B02', 'B03')]]:
        key = spec['id']
        source = ROOT / spec['source'] if key == 'A04' else ORIGINAL / 'cases' / key / 'request.json'
        original = capture(source)
        if digest(source.read_bytes()) != spec['request_sha256']:
            raise ValueError('Original trace hash differs: ' + key)
        folder = directory / 'cases' / key
        write_once(folder / 'request.json', original)
        write_once(folder / 'manifest.json', spec)
        if key == 'A04':
            for suffix in ('meta', 'output'):
                write_once(folder / f'source.{suffix}.json', capture(source.with_name('0003.' + suffix + '.json')))
            assert read(folder / 'source.meta.json')['tool_call_ids'] == ['call_00_oJowpkIPGg6XLPF711UK1009']
            historical = read(folder / 'source.output.json')
            assert any(c['arguments'].get('method') == 'confirm' for c in historical['tool_calls'])
            message = original['messages'][7]
            assert message['name'] == 'get_odoo_sop' and message['tool_call_id'] == spec['tool_call_id']
            call = next(c for m in original['messages'] for c in m.get('tool_calls', []) if c['id'] == spec['tool_call_id'])
            args = json.loads(call['function']['arguments'])
            # The real worker constructs this policy. No test-only method catalogue.
            env = child_environment('offline-replay', 'offline-replay')
            policy = {k: v for k, v in env.items() if k == 'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS'}
            write_once(folder / 'policy.json', policy)
            with patch.dict(os.environ, policy, clear=True):
                result = build_sop_payload(args['sop_id'], args['inputs'], read_locator='find_records')
            candidate, patches = copy.deepcopy(original), []
            replace(candidate, original, patches, spec['allowed_patch_paths'], '/messages/7/content',
                    json.dumps(result, ensure_ascii=False, separators=(',', ':')),
                    'erp_harness.tools.sops.build_sop_payload; erp_harness.app.worker.child_environment')
            variants = [('baseline', original, []), ('candidate', candidate, patches)]
        else:
            write_once(folder / 'source.output.json', capture(ORIGINAL / 'cases' / key / 'source.output.json'))
            # Existing controls stay byte-for-byte semantically identical. They
            # check model intent, not the newly added runtime guard's execution.
            variants = [('protection', original, [])]
        for arm, payload, patches in variants:
            allowed = spec['allowed_patch_paths'] if key == 'A04' else []
            validate_patches(original, payload, patches, allowed)
            assert payload['model'] == 'deepseek/deepseek-v4-flash' and payload['reasoning_effort'] == 'high'
            assert not any(k in payload for k in ('max_tokens', 'max_completion_tokens'))
            target = directory / 'prepared' / key / arm
            write_once(target / 'request.json', payload)
            write_once(target / 'patches.json', patches)
            rows.append({'case': key, 'arm': arm, 'allowed_paths': allowed})
    producers = [*Path(__file__).parent.glob('*.py'), *[ROOT / p for p in (
        'src/erp_harness/tools/sops.py', 'src/erp_harness/app/worker.py',
        'src/erp_harness/erp/_odoo_core/write_policy.py', 'src/erp_harness/erp/business_operations.py',
        'src/erp_harness/erp/actions.py', 'src/erp_harness/erp/invoice_mail.py',
        'src/erp_harness/app/sale_view.py', 'src/erp_harness/app/business_status.py')]]
    frozen = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'cases': rows, 'maximum_posts': len(rows), 'timeout': None, 'retries': 0,
              'tool_execution': False, 'sources': sources,
              'producer_hashes': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in producers},
              'files': {str(p.relative_to(directory)): digest(p.read_bytes()) for p in directory.rglob('*.json')}}
    write_once(directory / 'freeze.json', frozen)
    return verify(directory)


def verify(directory=DEFAULT):
    directory = Path(directory)
    frozen = read(directory / 'freeze.json')
    for name, expected in {**frozen['sources'], **frozen['producer_hashes']}.items():
        assert digest((ROOT / name).read_bytes()) == expected, 'Source changed: ' + name
    for name, expected in frozen['files'].items():
        assert digest((directory / name).read_bytes()) == expected, 'Frozen input changed: ' + name
    assert len(frozen['cases']) == frozen['maximum_posts']
    assert len({(r['case'], r['arm']) for r in frozen['cases']}) == len(frozen['cases'])
    for row in frozen['cases']:
        folder = directory / 'prepared' / row['case'] / row['arm']
        validate_patches(read(directory / 'cases' / row['case'] / 'request.json'),
                         read(folder / 'request.json'), read(folder / 'patches.json'), row['allowed_paths'])
    return frozen


async def run(directory=DEFAULT):
    directory = Path(directory)
    frozen = verify(directory)
    # Refuse partial restart too: no previously started branch is silently retried.
    if list((directory / 'results').glob('*/*/started.json')):
        raise RuntimeError('This batch has already started; inspect receipts without rerunning')
    for row in frozen['cases']:
        verify(directory)
        key, arm = row['case'], row['arm']
        payload = read(directory / 'prepared' / key / arm / 'request.json')
        output = await complete(payload, directory / 'results' / key / arm,
                                api_key=os.environ['COMMAND_CODE_API_KEY'])
        verdict = evaluate(read(directory / 'cases' / key / 'manifest.json'), payload, output,
                           read(directory / 'cases' / key / 'source.output.json'))
        write_once(directory / 'results' / key / arm / 'verdict.json', verdict)
        print(json.dumps({'case': key, 'arm': arm, 'verdict': verdict['status'],
                          'posts': output['posts'], 'usage': output['usage']}, ensure_ascii=False), flush=True)
        if verdict['status'] == 'inconclusive':
            raise RuntimeError('Provider attempt incomplete; no retry or further branch')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--paid', action='store_true')
    args = parser.parse_args()
    if args.paid:
        asyncio.run(run(args.directory))
    else:
        with patch('socket.socket.connect', side_effect=RuntimeError('Preparation is offline')):
            frozen = prepare(args.directory)
        print({'prepared': frozen['maximum_posts'], 'paid_calls': 0})
