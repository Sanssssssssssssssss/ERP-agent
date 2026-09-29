"""GPU replay of recorded host states; no paid API or business execution.

Only retained groups and undecided candidates change. Later business observations
remain historical, so this cannot establish closed-loop success or cache hit rate.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from erp_harness.providers.selector_service import SelectorService, decide


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def run(trial, config, output):
    output.mkdir(parents=True, exist_ok=False)
    totals = {}
    with (output/'rows.jsonl').open('x', encoding='utf8') as log:
        service = SelectorService(config, output, 'additive-offline-replay')
        try:
            for case in ('E01', 'E02', 'E03'):
                run_dir = trial/case/'profile/data/runs'/read(trial/case/'summary.json')['run_id']
                events = [json.loads(l) for l in (run_dir/'routing/decisions.jsonl').read_text(encoding='utf8').splitlines()]
                publications = [r for r in events if r.get('event') == 'publication']
                active, calls, changes, groups_per_round = [], 0, 0, []
                for number, publication in enumerate(publications, 1):
                    source = run_dir/'routing'/(publication['call_id'].replace(':', '-')+'.request.json')
                    original = read(source)
                    packet = copy.deepcopy(original)
                    required = set(publication['required_by_runtime'])
                    packet['state']['runtime_retained_capabilities'] = sorted(set(active) | required)
                    packet['groups'] = [g for g in original['groups'] if g not in set(active) | required]
                    packet['run_id'] = 'additive-offline-replay'
                    if packet['groups']:
                        result = decide(service.endpoint, packet)
                        assert result['status'] == 'ok', result.get('reason')
                        assert result['id'] == packet['id']
                        assert set(result['capabilities']) <= set(packet['groups'])
                        calls += 1
                    else:
                        result = {'capabilities': [], 'inference_skipped': True}
                    before = list(active)
                    active += sorted((set(result['capabilities']) | required) - set(active))
                    assert active[:len(before)] == before
                    changes += active != before
                    groups_per_round.append(len(active))
                    log.write(json.dumps({'case': case, 'round': number, 'source': str(source),
                        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'retained_before': before, 'candidates': packet['groups'],
                        'result': result, 'active': active, 'old_active': publication['active']}, ensure_ascii=False)+'\n')
                    log.flush()
                totals[case] = {'rounds': len(publications), 'gpu_selections': calls,
                    'publication_changes': changes, 'final_groups': active,
                    'average_groups': sum(groups_per_round)/len(groups_per_round)}
                print(case, json.dumps(totals[case]), flush=True)
        finally:
            service.close()
    (output/'summary.json').write_text(json.dumps(totals, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.trial, args.config, args.output)
