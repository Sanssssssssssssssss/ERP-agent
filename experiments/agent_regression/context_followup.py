"""Frozen ten-request follow-up; reuse the regression pool's one-POST transport."""
import argparse
import asyncio
import copy
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from .cases import ROOT
from .freeze import canonical, digest, read, write_once
from .oracle import evaluate
from .prepare import replace, validate_patches
from .runner import complete

OLD = ROOT / '.runtime/agent-regression-20260923'
DEFAULT = ROOT / '.runtime/agent-regression-context-20260923'


def prepare(directory=DEFAULT):
    from erp_harness.app.host import build_task_contract
    from erp_harness.app.runner import build_approval_resume_message, build_business_system_prompt
    from erp_harness.context.projection import project_read_history
    from erp_harness.context.world import WorldStore
    from erp_harness.runtime.messages import ToolResultMessage

    directory = Path(directory).resolve()
    if (directory / 'freeze.json').exists():
        return verify(directory)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    sources = {}
    def source(path):
        path = Path(path)
        sources[str(path.relative_to(ROOT))] = digest(path.read_bytes())
        return read(path)
    cases = []
    for name in ('B01', 'A03', 'B02', 'E02'):
        replacements = []
        if name != 'E02':
            original = source(OLD / 'prepared' / name / 'candidate.json')
            oracle = source(OLD / 'cases' / name / 'manifest.json')
            reference = source(OLD / 'cases' / name / 'source.output.json')
            write_once(directory / 'cases' / name / 'reference.json', reference)
            if name == 'B01':
                business = source(OLD / 'cases/B01/business.json')['business']
                stage = build_task_contract(business, 'frozen confirmed phase')['stage']
                i = max(i for i, m in enumerate(original['messages']) if m['role'] == 'user'
                        and m.get('content', '').startswith('The desktop host has completed human approval'))
                replacements.append((f'/messages/{i}/content', build_approval_resume_message(stage),
                                     'erp_harness.app.runner.build_approval_resume_message'))
        else:
            oracle = source(directory / 'e02-oracle.json')
            audit = source(ROOT / '.runtime/enterprise-validation-20260922/supply-projection-audit.json')
            replay = source(ROOT / '.runtime/supply-first-projection-20260923/result.json')
            original = source(replay['source_request'])
            assert digest(Path(replay['source_request']).read_bytes()) == replay['source_request_sha256']
            trace = Path(replay['source_trace'])
            sources[str(trace.relative_to(ROOT))] = digest(trace.read_bytes())
            assert sources[str(trace.relative_to(ROOT))] == replay['source_trace_sha256']
            lines = trace.read_bytes().splitlines(keepends=True)[:audit['source']['line_number']]
            store_path = directory / 'world-observations.jsonl'
            store_path.write_bytes(b''.join(lines))
            receipt = json.loads(lines[-1])
            with patch.dict(os.environ, {'ODOO_URL': 'http://offline.invalid', 'ODOO_DB': 'replay',
                                        'ODOO_USERNAME': 'offline', 'ODOO_PASSWORD': 'offline-only'}, clear=True):
                world = WorldStore(store_path, projection_path=directory / 'world-projections.jsonl')
            i, message = next((i, m) for i, m in enumerate(original['messages'])
                              if m.get('tool_call_id') == receipt['call_id'])
            assert digest(message['content'].encode()) == receipt['result_sha256']
            projected = project_read_history(world, [ToolResultMessage(tool_call_id=receipt['call_id'],
                tool_name='mcp_odoo_read_supply_context', content=message['content'])])[0].text
            assert projected != message['content']
            assert 'read_observation' in {t['function']['name'] for t in original['tools']}
            replacements.append((f'/messages/{i}/content', projected,
                                 'erp_harness.context.projection.project_read_history'))
        # The simulated business clock belongs to the frozen task, not today's rerun.
        dates = [match[1] for m in original['messages'] if m.get('name') == 'get_current_time'
                 if (match := re.search(r'"local_date"\s*:\s*"([0-9-]+)"', m['content']))]
        if dates:
            runtime_date = dates[-1]
        else:
            meta = source(OLD / 'cases' / name / 'source.meta.json')
            runtime_date = datetime.fromisoformat(meta['started_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        system = build_business_system_prompt(sop_mode='controlled', tool_mode='dynamic',
            runtime_date=runtime_date, runtime_timezone='UTC+0800')
        write_once(directory / 'cases' / name / 'oracle.json', oracle)
        assert original['model'] == 'deepseek/deepseek-v4-flash' and original['reasoning_effort'] == 'high'
        assert not any(k in original for k in ('max_tokens', 'max_completion_tokens'))
        write_once(directory / 'cases' / name / 'request.json', original)
        variants = [('baseline', [])]
        if replacements:
            variants.append(('resume' if name == 'B01' else 'projection', list(replacements)))
        variants.append(('combined' if replacements else 'system',
                         [*replacements, ('/messages/0/content', system, 'erp_harness.app.runner.build_business_system_prompt')]))
        for arm, changes in variants:
            candidate, patches = copy.deepcopy(original), []
            allowed = [entry[0] for entry in changes]
            for path, value, producer in changes:
                replace(candidate, original, patches, allowed, path, value, producer)
            validate_patches(original, candidate, patches, allowed)
            out = directory / 'prepared' / name / arm
            write_once(out / 'request.json', candidate)
            write_once(out / 'patches.json', patches)
            cases.append({'case': name, 'arm': arm, 'allowed_paths': allowed,
                          'request_sha256': digest(canonical(candidate))})
    assert len(cases) == 10
    files = [p for p in directory.rglob('*.json') if p.name != 'freeze.json']
    producers = ['src/erp_harness/app/runner.py', 'src/erp_harness/app/business.py', 'src/erp_harness/app/host.py',
                 'src/erp_harness/context/projection.py', 'src/erp_harness/context/world.py']
    producers += [str(p.relative_to(ROOT)) for p in Path(__file__).parent.glob('*.py')]
    frozen = {'source_commit': commit, 'maximum_posts': 10, 'cases': cases,
        'historical_baseline': '0b08fb2 prepared candidates; E02 is its original recorded request',
        'sources': sources, 'producer_hashes': {p: digest((ROOT / p).read_bytes()) for p in producers},
        'frozen_files': {str(p.relative_to(directory)): digest(p.read_bytes()) for p in files},
        'no_tool_execution': True, 'automatic_retries': 0, 'timeout': None}
    write_once(directory / 'freeze.json', frozen)
    return verify(directory)


def verify(directory=DEFAULT):
    directory = Path(directory)
    frozen = read(directory / 'freeze.json')
    assert len(frozen['cases']) == frozen['maximum_posts'] == 10
    assert len({(r['case'], r['arm']) for r in frozen['cases']}) == 10
    for name, expected in {**frozen['sources'], **frozen['producer_hashes']}.items():
        assert digest((ROOT / name).read_bytes()) == expected, 'Source changed: ' + name
    for name, expected in frozen['frozen_files'].items():
        assert digest((directory / name).read_bytes()) == expected, 'Frozen input changed: ' + name
    for row in frozen['cases']:
        folder = directory / 'prepared' / row['case'] / row['arm']
        request = read(folder / 'request.json')
        assert digest(canonical(request)) == row['request_sha256']
        validate_patches(read(directory / 'cases' / row['case'] / 'request.json'), request,
                         read(folder / 'patches.json'), row['allowed_paths'])
    return frozen


async def run(directory=DEFAULT, *, api_key=None):
    directory = Path(directory)
    frozen = verify(directory)
    for row in frozen['cases']:
        name, arm = row['case'], row['arm']
        payload = read(directory / 'prepared' / name / arm / 'request.json')
        assert digest(canonical(payload)) == row['request_sha256'], 'Payload changed before POST'
        output = await complete(payload, directory / 'results' / name / arm,
                                api_key=api_key or os.environ['COMMAND_CODE_API_KEY'])
        reference = directory / 'cases' / name / 'reference.json'
        verdict = evaluate(read(directory / 'cases' / name / 'oracle.json'), payload, output,
                           read(reference) if reference.exists() else None)
        write_once(directory / 'results' / name / arm / 'verdict.json', verdict)
        print({'case': name, 'arm': arm, 'verdict': verdict['status'], 'posts': output['posts'],
               'usage': output['usage']}, flush=True)
        if verdict['status'] == 'inconclusive':
            raise RuntimeError('Incomplete provider attempt; stopped without retry')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--paid', action='store_true')
    args = parser.parse_args()
    if args.paid:
        asyncio.run(run(args.directory))
    else:
        with patch('socket.socket.connect', side_effect=RuntimeError('Preparation is offline')):
            result = prepare(args.directory)
        print({'prepared': len(result['cases']), 'paid_calls': 0})
