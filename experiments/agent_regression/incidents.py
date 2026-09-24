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


async def prepare(directory=DEFAULT, case_ids=("A04", "B02", "B03")):
    from erp_harness.app.worker import child_environment
    from erp_harness.tools.sops import build_sop_payload

    directory = Path(directory)
    known = {spec['id']: spec for spec in INCIDENTS}
    if not case_ids or len(set(case_ids)) != len(case_ids) or set(case_ids) - (known.keys() | {'B02', 'B03'}):
        raise ValueError('Unknown or duplicate case selection')
    if (directory / 'freeze.json').exists():
        frozen = verify(directory)
        if {row['case'] for row in frozen['cases']} != set(case_ids):
            raise ValueError('Selection differs from frozen batch; use a new directory')
        return frozen
    rows, sources = [], {}

    def capture(path):
        path = Path(path)
        sources[str(path.relative_to(ROOT))] = digest(path.read_bytes())
        return read(path)

    for spec in [known[key] if key in known else capture(ORIGINAL / 'cases' / key / 'manifest.json') for key in case_ids]:
        key = spec['id']
        source = ROOT / spec['source'] if key in known else ORIGINAL / 'cases' / key / 'request.json'
        original = capture(source)
        if digest(source.read_bytes()) != spec['request_sha256']:
            raise ValueError('Original trace hash differs: ' + key)
        folder = directory / 'cases' / key
        write_once(folder / 'request.json', original)
        write_once(folder / 'manifest.json', spec)
        if key in known:
            for suffix in ('meta', 'output'):
                stem = source.name.removesuffix('.request.json')
                write_once(folder / f'source.{suffix}.json', capture(source.with_name(stem + '.' + suffix + '.json')))
        if key == 'A04':
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
        elif key == 'D04':
            from erp_harness.app import conversation
            from erp_harness.providers.openai_compatible import _tool_to_openai

            message = original['messages'][38]
            assert message['name'] == 'read_business_status' and message['tool_call_id'] == spec['tool_call_id']
            assert json.loads(message['content'])['status'] == 'unavailable'
            call = next(c for m in original['messages'] for c in m.get('tool_calls', []) if c['id'] == spec['tool_call_id'])
            args = json.loads(call['function']['arguments'])
            # The operator explicitly chose the whole conversation. Never invent
            # a selected business or a successful readback in this request.
            with patch.object(conversation, '_BUSINESS_CONTEXT', {}), patch.object(
                    conversation, '_odoo_reads', side_effect=AssertionError('Unbound status must not read Odoo')):
                result = await conversation._read_business_status(spec['tool_call_id'], args)
            candidate, patches = copy.deepcopy(original), []
            replace(candidate, original, patches, spec['allowed_patch_paths'], '/messages/38/content', result.text,
                    'erp_harness.app.conversation._read_business_status; no selected business')
            replace(candidate, original, patches, spec['allowed_patch_paths'], '/tools/1',
                    _tool_to_openai(conversation.READ_BUSINESS_STATUS),
                    'erp_harness.app.conversation.READ_BUSINESS_STATUS -> _tool_to_openai')
            variants = [('baseline', original, []), ('candidate', candidate, patches)]
        else:
            write_once(folder / 'source.output.json', capture(ORIGINAL / 'cases' / key / 'source.output.json'))
            # Existing controls stay byte-for-byte semantically identical. They
            # check model intent, not the newly added runtime guard's execution.
            variants = [('protection', original, [])]
        for arm, payload, patches in variants:
            allowed = spec['allowed_patch_paths'] if key in known else []
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
        'src/erp_harness/app/sale_view.py', 'src/erp_harness/app/business_status.py',
        'src/erp_harness/app/conversation.py', 'src/erp_harness/app/host.py',
        'src/erp_harness/providers/openai_compatible.py')]]
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
    parser.add_argument('--case', action='append', dest='cases', help='Select observed incidents or existing B02/B03 controls when freezing')
    args = parser.parse_args()
    if args.paid:
        if args.cases:
            parser.error('Paid execution uses the entire frozen batch; select cases during preparation')
        asyncio.run(run(args.directory))
    else:
        async def offline():
            # Windows creates a local socketpair when starting its event loop.
            # Block network only after that initialization, before any builder.
            with patch('socket.socket.connect', side_effect=RuntimeError('Preparation is offline')):
                return await prepare(args.directory, args.cases or ('A04', 'B02', 'B03'))
        frozen = asyncio.run(offline())
        print({'prepared': frozen['maximum_posts'], 'paid_calls': 0})
