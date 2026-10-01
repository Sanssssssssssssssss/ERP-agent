"""Frozen routing pool through the real GPU worker; never executes ERP tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from experiments.tool_routing.routing_context import assemble_context, selection_batch
from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS
from .q4_inputs import ROOT, read, save, sha


def prepare(output, config, only=None):
    output.mkdir()  # Never overwrite a prior run.
    source = ROOT / '.runtime/laya-host-state-20260926/dataset-v16/cases.json'
    dataset = read(source)
    labels_path = ROOT / '.runtime/q4-routing-inputs-20260928/labels.json'
    labels = [r for split in read(labels_path).values() for r in split]
    faults = ROOT / '.runtime/q4-failure-replay-20260928/labels.json'
    labels += read(faults)
    observed_path = ROOT / 'tests/fixtures/capability_routing/openjev_observed_failures.json'
    observed = read(observed_path)
    selected = {r['id'] for r in labels}
    cases = [r for r in dataset if r['split'] in ('dev', 'test') or r['id'] in selected]
    if only:
        cases = [r for r in cases if r['id'] in only]
        assert {r['id'] for r in cases} == set(only)
    groups = read(config).get('candidate_groups', list(CAPABILITY_GROUPS))
    rows, files = [], [source, labels_path, faults, observed_path, config, Path(__file__)]
    # Failed real prefixes first; the remaining pool is still evaluated in full.
    cases.sort(key=lambda r: (r['id'] != 'host-live:E01:0005', r['id'] != 'phase:8ba212155df45c8d23be', r['id'] not in selected, r['id']))
    for case in cases:
        path = Path(case['request_path'])
        assert sha(path) == case['request_sha256'], case['id']
        files.append(path)
        state = case['state']
        state = json.loads(state) if isinstance(state, str) else state
        context = state if read(config).get('backend') == 'laya' else assemble_context(read(path), state=state)
        expected = {g: True for g in case['required_groups'] + case['preferred_groups']}
        expected.update({g: False for g in case['unrelated_groups']})
        for label in labels:
            if label['id'] == case['id']:
                assert label['request_sha256'] == case['request_sha256']
                if label['capability'] in expected:
                    assert expected[label['capability']] == label['target'], case['id']
                expected[label['capability']] = label['target']
        packet = {'id': hashlib.sha256(case['id'].encode()).hexdigest()[:24], 'groups': groups,
                  'context_version': context['version']}
        packet.update({'state': context} if read(config).get('backend') == 'laya' else
                      {'messages': selection_batch(context, groups)})
        rows.append({'case_id': case['id'], 'split': case['split'], 'source_kind': case.get('source_kind'),
            'request_path': str(path), 'request_sha256': sha(path), 'expected': expected,
            'uncertain_groups': case['uncertain_groups'], 'packet': packet})
    by_id = {r['case_id']: r for r in rows}
    for failure in observed:
        if only and failure['case_id'] not in only:
            continue
        row = by_id[failure['case_id']]
        assert row['request_sha256'] == failure['request_sha256']
        assert row['expected'][failure['capability']] == failure['expected']
    assert only or selected <= {r['case_id'] for r in rows}
    save(output / 'cases.json', rows)
    files += [output / 'cases.json'] + list((ROOT / 'src/erp_harness').rglob('*.py'))
    save(output / 'frozen.json', {'files': {str(p.resolve()): sha(p) for p in files},
        'config': str(config.resolve()), 'cases': len(rows), 'labelled_decisions': sum(len(r['expected']) for r in rows),
        'policy': ('Selected historical probes.' if only else 'All v16 dev/test prefixes plus all Q4 regression cases.') + ' Unknown labels remain unscored. No tools, paid calls or retries.'})
    print(json.dumps({'cases': len(rows), 'labelled_decisions': sum(len(r['expected']) for r in rows)}))


def run(output):
    frozen = read(output / 'frozen.json')
    for path, digest in frozen['files'].items():
        assert sha(path) == digest, path
    config = read(frozen['config'])
    save(output / 'attempt.json', {'pid': os.getpid()})
    worker_dir = output / 'worker'
    worker_dir.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LLM_', 'ODOO_', 'COMMAND_CODE_'))}
    rows = read(output / 'cases.json')
    results = []
    with (worker_dir / 'worker.stderr.log').open('wb') as stderr, (output / 'results.jsonl').open('x', encoding='utf8') as stream:
        process = subprocess.Popen([config['python'], '-P', '-u', '-X', 'utf8',
            str(ROOT / ('src/erp_harness/providers/laya_worker.py' if config.get('backend') == 'laya' else 'experiments/tool_routing/openjev_worker.py')), frozen['config'], str(worker_dir.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, encoding='utf8', env=env)
        try:
            for row in rows:
                process.stdin.write(json.dumps(row['packet'], ensure_ascii=False) + '\n')
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                assert response['id'] == row['packet']['id'] and response['scope'] == 'capability_publication_only'
                decisions = response.get('result', {}).get('decisions', {})
                valid = response['status'] == 'ok' and set(decisions) == set(row['packet']['groups'])
                if valid:
                    assert all(d['answer'] in ('A', 'B') for d in decisions.values())
                    assert response['capabilities'] == [g for g in row['packet']['groups'] if decisions[g]['answer'] == 'A']
                failures = [{'group': g, 'expected': target, 'actual': decisions.get(g, {}).get('answer')}
                    for g, target in row['expected'].items() if not valid or (decisions[g]['answer'] == 'A') != target]
                item = {'case_id': row['case_id'], 'valid': valid, 'failures': failures,
                        'judged': len(row['expected']), 'response': response}
                stream.write(json.dumps(item, ensure_ascii=False) + '\n'); stream.flush()
                results.append(item)
                print(json.dumps({'completed': len(results), 'total': len(rows), 'case': row['case_id'],
                                  'valid': valid, 'failures': failures}), flush=True)
        finally:
            process.stdin.close()
            process.wait()
    save(output / 'summary.json', {'cases': len(results), 'completed': len(results) == len(rows),
        'valid': sum(r['valid'] for r in results), 'judged': sum(r['judged'] for r in results),
        'correct': sum(r['judged'] - len(r['failures']) for r in results),
        'failed_cases': [r['case_id'] for r in results if r['failures']], 'paid_calls': 0, 'odoo_calls': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--cases', nargs='+')
    args = parser.parse_args()
    prepare(args.output.resolve(), args.config.resolve(), args.cases) if args.action == 'prepare' else run(args.output.resolve())
