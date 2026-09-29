"""Replay frozen real selector states on GPU; never execute tools or call a paid API."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / '.runtime/laya-v7-integration-20260929'
spec = importlib.util.spec_from_file_location('laya_worker', ROOT / 'src/erp_harness/providers/laya_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
CapabilityRouter, select_packet = worker.CapabilityRouter, worker.select_packet


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def run(output, model, variants, release_scratch=False):
    output.mkdir(parents=True, exist_ok=True)
    cases = read(PRIOR / 'regression-final/cases.json')
    router = CapabilityRouter(model, device='cuda')
    original = copy.deepcopy(router.questions)
    focused = copy.deepcopy(original)
    incremental = copy.deepcopy(original)
    trimmed = copy.deepcopy(original)
    base_first = copy.deepcopy(original)
    for group, question in trimmed.items():
        if group != 'actions':
            question['instructions'] = question['instructions'].replace(
                'Base-tool alternatives do not rule out a useful call. ', '')
            base_first[group]['instructions'] = base_first[group]['instructions'].replace(
                'Base-tool alternatives do not rule out a useful call.',
                'Base-tool alternatives rule out a useful call.')
    for group, question in incremental.items():
        if group != 'actions':
            question['instructions'] = question['instructions'].replace(
                'Base-tool alternatives do not rule out a useful call. ',
                'Use base tools for ordinary record reads. Select this capability for its distinct function, '
                'not generic exploration. ')
    for group, question in focused.items():
        question['instructions'] = (
            'Select additional tools for the next assistant response, using the confirmed task and live action ledger. '
            'Use available base tools for ordinary record or field reads. Select this group when its distinct '
            'function is supported by a current task need or an unresolved failure; hypothetical usefulness '
            'or a past error alone is insufficient. Runtime-retained groups are already available. '
            'Selecting tools does not require using them and never authorizes execution. '
            + original[group]['instructions'].split(group + ': ', 1)[-1])
    totals = {}
    # Exclusive output prevents an unnoticed duplicate/retry.
    with (output / 'results.jsonl').open('x', encoding='utf8') as stream:
        for case in cases:
            packet = case['packet']
            source = Path(case['request_path'])
            if hashlib.sha256(source.read_bytes()).hexdigest() != case['request_sha256']:
                raise ValueError('Frozen source changed: ' + case['case_id'])
            for variant in variants:
                router.questions = {'focused': focused, 'incremental': incremental, 'trimmed': trimmed,
                                    'base_first': base_first}.get(variant, original)
                candidate = copy.deepcopy(packet)
                required = set(candidate['state'].get('runtime_retained_capabilities', []))
                if variant != 'full':
                    candidate['groups'] = [g for g in packet['groups'] if g not in required]
                result = select_packet(router, candidate)
                import torch
                result['gpu_memory'] = {'allocated': torch.cuda.memory_allocated(),
                                        'reserved': torch.cuda.memory_reserved()}
                if release_scratch:
                    torch.cuda.empty_cache()
                selected = set(result['capabilities']) | (required if variant != 'full' else set())
                counts = totals.setdefault(variant, {'correct': 0, 'known': 0, 'misses': 0, 'extras': 0})
                for group, expected in case['expected'].items():
                    if type(expected) is not bool:
                        continue
                    actual = group in selected
                    counts['known'] += 1
                    counts['correct'] += actual == expected
                    counts['misses'] += expected and not actual
                    counts['extras'] += actual and not expected
                stream.write(json.dumps({'case_id': case['case_id'], 'variant': variant,
                    'selected': sorted(selected), 'result': result}, ensure_ascii=False) + '\n')
                stream.flush()
            print(case['case_id'], flush=True)
    (output / 'summary.json').write_text(json.dumps(totals, indent=2), encoding='utf8')
    (output / 'focused-questions.json').write_text(json.dumps(focused, ensure_ascii=False, indent=2), encoding='utf8')
    (output / 'incremental-questions.json').write_text(json.dumps(incremental, ensure_ascii=False, indent=2), encoding='utf8')
    (output / 'trimmed-questions.json').write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding='utf8')
    (output / 'base_first-questions.json').write_text(json.dumps(base_first, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--variants', nargs='+', choices=['full', 'subset', 'focused', 'incremental', 'trimmed', 'base_first'],
                        default=['full', 'subset', 'focused'])
    parser.add_argument('--release-scratch', action='store_true')
    args = parser.parse_args()
    run(args.output, args.model, args.variants, args.release_scratch)
