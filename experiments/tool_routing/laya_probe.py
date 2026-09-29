"""Local capability-injection experiment. Never calls ERP or a generative provider."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.runtime/laya-routing-20260924'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def questions(groups):
    # Group selection affects visibility only. The decision model cannot approve or execute.
    return {name: {'type': 'noul',
        'instructions': ('Select tool groups for the NEXT assistant response. Ordinary record search, '
                         'read, aggregation, field metadata, SOP, supply context and invoice eligibility '
                         'are already base tools. Does the next response need this optional group? '
                         f'{name}: {group["description"]} Tools: ' + ', '.join(group['tools'])),
        'criteria': {'true': 'Include this group for the next response.',
                     'false': 'Base tools or other groups suffice; do not add this group.'}}
        for name, group in groups.items()}


def choose(result, groups, active):
    """Fail back to the historical active set on malformed model answers, without permissions."""
    answers = result.get('answers') if isinstance(result, dict) else None
    if not isinstance(answers, dict) or set(answers) != set(groups) or any(not isinstance(a, dict) for a in answers.values()):
        return {'suggested': [], 'published_groups': sorted(active), 'fallback': True}
    probabilities = [answers[n].get('noul') for n in groups]
    if any(isinstance(p, bool) or not isinstance(p, (float, int)) or not 0 <= p <= 1 for p in probabilities):
        return {'suggested': [], 'published_groups': sorted(active), 'fallback': True}
    selected = sorted(n for n in groups if answers[n]['noul'] >= 0.5)
    return {'suggested': selected, 'published_groups': sorted(set(active) | set(selected)), 'fallback': False}


def score(case, decision, groups):
    # Historical calls are a coverage reference, never the only permitted trajectory.
    oracle = case['oracle']
    needed = set(oracle.get('expected_capability_injection',
                           oracle['observed_required_capabilities']))
    active, suggested = set(case['active_capabilities']), set(decision['suggested'])
    new = needed - active
    emitted = set(decision['published_groups'])
    return {'historical_required_groups': sorted(needed), 'new_groups_needed': sorted(new),
        'baseline_covers_observed': needed <= active, 'candidate_covers_observed': needed <= emitted,
        'raw_multiselect_covers_observed': needed <= suggested,
        'raw_exact_observed_set': needed == suggested,
        'reviewed_set_match': (sorted(emitted) in [sorted(s) for s in oracle['allowed_injection_sets']]
                              if oracle.get('allowed_injection_sets') is not None else None),
        'missed_new_groups': sorted(new - suggested),
        'extra_groups_vs_observation': sorted(suggested - needed),
        'added_visible_tools': len({t for n in emitted - active for t in groups[n]['tools']}),
        'scorable': oracle.get('response_known', True) and not oracle.get('needs_review', False)}


def summarize(rows):
    eligible = [r for r in rows if r['score']['scorable']]
    new = [r for r in eligible if r['score']['new_groups_needed']]
    def rate(field, subset=eligible):
        return sum(bool(r['score'][field]) for r in subset) / len(subset) if subset else None
    ms = sorted(r['latency_ms'] for r in rows)
    return {'nodes': len(rows), 'coverage_proxy_nodes': len(eligible), 'new_group_nodes': len(new),
        'baseline_observed_coverage': rate('baseline_covers_observed'),
        'candidate_observed_coverage': rate('candidate_covers_observed'),
        'new_group_observed_coverage': rate('candidate_covers_observed', new),
        'raw_exact_observed_set': rate('raw_exact_observed_set'),
        'raw_multiselect_observed_coverage': rate('raw_multiselect_covers_observed'),
        'mean_extra_groups_vs_observation': statistics.mean(len(r['score']['extra_groups_vs_observation']) for r in eligible) if eligible else None,
        'mean_added_visible_tools': statistics.mean(r['score']['added_visible_tools'] for r in rows) if rows else None,
        'model_truncated_nodes': sum(r['state_tokens_dropped'] > 0 for r in rows),
        'input_tokens': sum(r['response']['usage']['input_tokens'] for r in rows),
        'output_tokens': sum(r['response']['usage']['output_tokens'] for r in rows),
        'p50_ms': statistics.median(ms) if ms else None,
        'p95_ms': ms[min(len(ms)-1, int(len(ms)*0.95))] if ms else None,
        'fallbacks': sum(r['decision']['fallback'] for r in rows)}


def main(args):
    os.environ['HF_HOME'] = str(WORK/'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    import laya
    import torch
    from laya.common import build_sequence, serialize_state

    pool = [json.loads(line) for line in (args.dataset/'cases.jsonl').read_text(encoding='utf8').splitlines()]
    cases = [c for c in pool if c['dynamic_router_applicable']]
    catalog = read(args.dataset/'catalog.json')
    groups = catalog['capability_groups']
    specs = questions(groups)
    assert len(specs) == 10
    args.output.mkdir(parents=True, exist_ok=False)
    lock = {'dataset_sha256': sha(args.dataset/'cases.jsonl'), 'catalog_sha256': sha(args.dataset/'catalog.json'),
            'pool_nodes': len(pool), 'dynamic_nodes': len(cases), 'excluded_non_dynamic_nodes': len(pool)-len(cases),
            'script_sha256': sha(__file__), 'questions': specs, 'threshold': 0.5,
            'threshold_meaning': 'Uncalibrated experimental decision boundary, not an accuracy guarantee.',
            'laya_version': laya.__version__, 'torch_version': torch.__version__,
            'model_lock': read(WORK/'model-lock.json'), 'requested_device': args.device,
            'publication_policy': 'Node-local preload union with historical active; no removal, no execution.',
            'business_state_claim': 'None. Historical node replay does not prove closed-loop success or saved main-model requests.'}
    (args.output/'frozen.json').write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding='utf8')
    started = time.perf_counter()
    agent = laya.load(str(args.model_dir.resolve()), device=args.device)
    load_seconds = time.perf_counter() - started
    assert agent.device.type == args.device, 'Unexpected hardware fallback; report it before measuring.'
    # Checkpoint tokenizer compatibility patches are upstream behavior; record effective files too.
    effective_config = {str(p.relative_to(args.model_dir)): sha(p) for p in args.model_dir.rglob('*.json') if '.cache' not in p.parts}
    rows = []
    first_seconds = None
    with (args.output/'predictions.jsonl').open('x', encoding='utf8') as log:
        for index, case in enumerate(cases):
            state = case['input']['state']  # The only case content given to Laya; oracle stays outside.
            state_ids = agent.tok(serialize_state(state).replace(agent.tok.mask_token, ' '), add_special_tokens=False)['input_ids']
            budgets = []
            for q in specs.values():
                head, _ = build_sequence(agent.tok, '', agent._to_internal(q), agent.cfg['max_len'], agent.cfg['head_max_len'], state_ids=[])
                budgets.append(max(0, agent.cfg['max_len'] - len(head)))
            if agent.device.type == 'cuda':
                torch.cuda.synchronize()
            tick = time.perf_counter()
            response = agent.system_one(state, specs)
            if agent.device.type == 'cuda':
                torch.cuda.synchronize()
            elapsed = (time.perf_counter() - tick) * 1000
            if first_seconds is None:
                first_seconds = elapsed / 1000
            decision = choose(response, groups, case['active_capabilities'])
            row = {'id': case['id'], 'split': case['split'], 'business_group': case['business_group'],
                   'source_run': case['source_run'], 'categories': case['categories'],
                   'state_sha256': hashlib.sha256(state.encode()).hexdigest(),
                   'state_tokens_before_model': len(state_ids), 'smallest_state_budget': min(budgets),
                   'state_tokens_dropped': max(0, len(state_ids)-min(budgets)),
                   'latency_ms': elapsed, 'response': response, 'decision': decision,
                   'score': score(case, decision, groups)}
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False)+'\n'); log.flush()
            if index % 25 == 0:
                print(json.dumps({'completed': index+1, 'total': len(cases)}, ensure_ascii=False), flush=True)
    summary = {'all': summarize(rows), 'by_split': {s: summarize([r for r in rows if r['split']==s]) for s in sorted({r['split'] for r in rows})},
               'by_business': {s: summarize([r for r in rows if r['business_group']==s]) for s in sorted({r['business_group'] for r in rows})},
               'by_model_truncation': {str(k): summarize([r for r in rows if (r['state_tokens_dropped']>0)==k]) for k in [False,True]},
               'reviewed_sets': {'evaluated': sum(r['score']['reviewed_set_match'] is not None for r in rows),
                                 'matches': sum(r['score']['reviewed_set_match'] is True for r in rows),
                                 'other_sets_need_review': sum(r['score']['reviewed_set_match'] is False for r in rows)},
               'suggested_frequency': dict(Counter(n for r in rows for n in r['decision']['suggested'])),
               'load_seconds': load_seconds, 'first_inference_seconds': first_seconds,
               'warm_latency': summarize(rows[1:]), 'effective_config_sha256': effective_config,
               'cuda_peak_allocated_bytes': torch.cuda.max_memory_allocated() if agent.device.type=='cuda' else None,
               'generative_api_calls': 0, 'odoo_calls': 0, 'decision_model_calls': len(rows),
               'limits': ['Historical behavior coverage is a proxy, not business correctness.',
                          'Keeping active capabilities can hide router mistakes; inspect raw selection and new-group scores.',
                          'Additional schemas are preload overhead, never claimed as schema savings.',
                          'No threshold fitting on this pool; multilingual probabilities are not calibrated for ERP.',
                          'End-to-end request and cost savings remain unmeasured.']}
    (args.output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary['all'], ensure_ascii=False), flush=True)


def self_check():
    groups={'actions': {'tools':['execute_method']}, 'accounting': {'tools':['aging']}}
    assert choose({'answers':{}},groups,['actions']) == {'suggested':[], 'published_groups':['actions'], 'fallback':True}
    for invalid in [None, {'answers':None}, {'answers':{'actions':None, 'accounting':{}}}]:
        assert choose(invalid,groups,['actions'])['fallback']
    r={'answers': {'actions': {'noul':0.9}, 'accounting':{'noul':float('nan')}}}
    assert choose(r,groups,[])['fallback']
    r['answers']['accounting']['noul']=0.1
    assert choose(r,groups,[])['published_groups']==['actions']
    assert choose(r,groups,['accounting'])['published_groups']==['accounting','actions']
    assert not any(k in choose(r,groups,[]) for k in ['approval','execute','authorized'])


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=WORK/'dataset')
    parser.add_argument('--model-dir',type=Path,default=WORK/'model-multilingual')
    parser.add_argument('--output',type=Path,default=WORK/'noul-v1')
    parser.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    parser.add_argument('--self-check',action='store_true')
    args=parser.parse_args()
    if args.self_check:
        self_check()
        print('PASS: selection cannot authorize writes; malformed results preserve active visibility.')
    else:
        main(args)
