"""Compare competitive capability selection with equal-size historical controls.

Local Laya only. Predicting one capability is a diagnostic, not a complete router.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import time

from .decision_dataset import FIXTURES, OUTPUT
from .laya_probe import WORK, read, sha
from .train_decider import metrics
from .train_head import concrete_questions, chosen


def question(groups, neutral=False, reverse=False):
    options = {'base_only': 'Use ordinary record search, reads, aggregation, schema or SOP lookup, or finish.'}
    options.update({g: spec['description'] for g, spec in groups.items()})
    labels = {chr(65+i) if neutral else g: g for i, g in enumerate(options)}
    criteria = {label: options[g] for label, g in labels.items()}
    if reverse:
        criteria = dict(reversed(list(criteria.items())))
    return {'route': {'type': 'choice', 'instructions':
        'Choose the single most relevant capability for the NEXT assistant step. '
        'Select ordinary reads or finish if no optional capability is needed. '
        'Use the current task and latest observations.', 'criteria': criteria}}, labels


def selected(answer, labels):
    group = labels[answer['choice']]
    return [] if group == 'base_only' else [group]


def rows(path):
    return list(map(json.loads, path.read_text(encoding='utf8').splitlines()))


def main(args):
    os.environ['HF_HOME'] = str(WORK/'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import laya
    import torch
    from laya.common import render_options, build_sequence

    source = OUTPUT/'full-head-accum-v1'
    frozen = read(source/'frozen.json')
    dataset = OUTPUT/'dataset-v2/cases.jsonl'
    assert sha(dataset) == frozen['dataset_sha256']
    model_dir = WORK/'model-multilingual'
    assert sha(model_dir/'model.safetensors') == frozen['model_lock']['files_sha256']['model.safetensors']
    groups = read(OUTPUT/'dataset-v2/groups.json'); names = list(groups)
    pool = {c['id']: c for c in rows(dataset)}
    reviews = read(FIXTURES/'reviewed_holdout.json')['cases']
    dev = [pool[c['id']] for c in frozen['cases'] if c['split'] == 'dev']
    cases = {c['id']: c for c in [*dev, *(pool[r['id']] for r in reviews)]}
    states = {r['id']: r['state'] for r in rows(source/'states.jsonl')}
    baseline = {r['id']: r for filename in ['base-dev-choice', 'base-test'] for r in rows(source/(filename+'.jsonl'))}
    specs = {f'{label}_{order}': question(groups, label == 'neutral', order == 'reverse')
             for label in ['semantic', 'neutral'] for order in ['forward', 'reverse']}
    args.output.mkdir(parents=True, exist_ok=False)
    agent = laya.load(str(model_dir.resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    encoding = {}
    for name, (spec, labels) in specs.items():
        q = agent._to_internal(spec['route'])
        options = render_options(q)
        assert all(len(agent.tok(' '+s, add_special_tokens=False)['input_ids']) <= 48 for s in options)
        full_head = sum(1+len(agent.tok(' '+s, add_special_tokens=False)['input_ids']) for s in options)
        full_head += len(agent.tok('choice question: '+q['ins'], add_special_tokens=False)['input_ids'])
        assert full_head <= agent.cfg['head_max_len'], (name, full_head)
        ids, markers = build_sequence(agent.tok, '', q, 8192, agent.cfg['head_max_len'])
        assert len(markers) == len(labels)
        encoding[name] = {'head_tokens_with_separators': len(ids), 'head_text': agent.tok.decode(ids)}
    meta = {'script_sha256': sha(__file__), 'source_frozen_sha256': sha(source/'frozen.json'),
        'states_sha256': sha(source/'states.jsonl'), 'review_sha256': sha(FIXTURES/'reviewed_holdout.json'),
        'model_lock': frozen['model_lock'], 'ids': list(cases), 'specs': specs, 'encoding': encoding,
        'limits': ['One optional group maximum; compound requirements can be missed.',
                   'Historical baseline responses reused; no fresh main-model request.',
                   'Previously inspected development and reviewed nodes, not unseen test data.']}
    (args.output/'frozen.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf8')
    calls = tokens = 0; predictions = {name: {} for name in specs}
    with (args.output/'predictions.jsonl').open('x', encoding='utf8') as log:
        for case_id, case in cases.items():
            assert sha(case['request_path']) == case['request_sha256']
            state = states[case_id]
            length = len(agent.tok(state.replace(agent.tok.mask_token, ' '), add_special_tokens=False)['input_ids'])+260
            assert length <= 8192
            if case_id not in baseline:
                result = agent.system_one(state, concrete_questions(groups), max_len=length)
                calls += 1; tokens += result['usage']['input_tokens']
                baseline[case_id] = {'id': case_id, 'response': result,
                    'selected_groups': [g for g in names if chosen(result['answers'][g], 'A')]}
                log.write(json.dumps({'variant': 'binary_extra', **baseline[case_id]}, ensure_ascii=False)+'\n'); log.flush()
            for variant, (spec, labels) in specs.items():
                start = time.perf_counter(); result = agent.system_one(state, spec, max_len=length)
                assert agent.device.type == 'cuda', 'Unexpected device fallback.'
                calls += 1; tokens += result['usage']['input_tokens']
                row = {'id': case_id, 'variant': variant, 'response': result,
                    'selected_groups': selected(result['answers']['route'], labels),
                    'latency_ms': (time.perf_counter()-start)*1000}
                predictions[variant][case_id] = row['selected_groups']
                log.write(json.dumps(row, ensure_ascii=False)+'\n'); log.flush()
    for n in [1, 2]:
        predictions[f'binary_top{n}'] = {key: sorted(names, key=lambda g: (-r['response']['answers'][g]['probabilities']['A'], g))[:n]
            if r['selected_groups'] else [] for key, r in baseline.items() if key in cases}
    predictions['binary_all'] = {key: baseline[key]['selected_groups'] for key in cases}
    predictions['always_actions'] = {key: ['actions'] for key in cases}
    result = {'variants': {}, 'order_consistency': {}, 'sdk_calls': calls, 'encoder_input_tokens': tokens,
              'paid_api_calls': 0, 'odoo_calls': 0, 'fresh_main_model_calls': 0}
    y = torch.tensor([[g in c['target_groups'] for g in names] for c in dev])
    for variant, pred in predictions.items():
        review_scores = []
        for r in reviews:
            selected_groups = set(pred[r['id']])
            review_scores.append({'id': r['id'], 'selected_groups': sorted(selected_groups),
                'allowed_set': sorted(selected_groups) in [sorted(s) for s in r['allowed_injection_sets']],
                'missing_required': sorted(set(r['required_groups'])-selected_groups),
                'unrelated_selected': sorted(selected_groups & set(r['unrelated_groups']))})
        score = metrics(torch.tensor([[g in pred[c['id']] for g in names] for c in dev]), y, names)
        result['variants'][variant] = {'dev': score, 'review': review_scores,
            'dev_mean_optional_tools': statistics.mean(sum(len(groups[g]['tools']) for g in pred[c['id']]) for c in dev)}
    for label in ['semantic', 'neutral']:
        result['order_consistency'][label] = sum(predictions[label+'_forward'][key] == predictions[label+'_reverse'][key] for key in cases)/len(cases)
    (args.output/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'sdk_calls': calls, 'order_consistency': result['order_consistency']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT/'competitive-v1')
    main(parser.parse_args())
