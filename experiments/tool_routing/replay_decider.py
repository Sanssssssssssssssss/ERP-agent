"""Replay a frozen scorer and audit input changes; local Laya only, no tool execution."""
import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import time

from .decision_dataset import FIXTURES, OUTPUT, state_from_request
from .build_cases import read as read_request
from .laya_probe import WORK, read, sha
from .train_decider import metrics


def main(args):
    os.environ['HF_HOME'] = str(WORK/'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import laya
    import torch

    data = OUTPUT/'dataset-v2'
    pool = {c['id']: c for c in map(json.loads, (data/'cases.jsonl').read_text(encoding='utf8').splitlines())}
    frozen = read(args.scorer/'frozen.json')
    assert sha(data/'cases.jsonl') == frozen['dataset_sha256']
    assert sha(args.scorer/'scorer.pt') == read(args.scorer/'summary.json')['scorer_sha256']
    groups = read(data/'groups.json'); names = list(groups); specs = frozen['questions']['A']
    baseline = {r['id']: r for r in map(json.loads, (args.scorer/'baseline.jsonl').read_text(encoding='utf8').splitlines()) if r['label']=='A'}
    heldout = list(map(json.loads, (args.scorer/'candidate-test.jsonl').read_text(encoding='utf8').splitlines()))
    selected = [pool[r['id']] for r in frozen['cases']]
    args.output.mkdir(parents=True, exist_ok=False)
    summary = {'paid_api_calls': 0, 'odoo_calls': 0}

    def score(rows, result_field='response'):
        p = torch.tensor([[r[result_field]['answers'][g]['noul'] for g in names] for r in rows])
        y = torch.tensor([[g in pool[r['id']]['target_groups'] for g in names] for r in rows])
        return metrics(p, y, names)

    # Original-pool rows were all filtered to dynamic when dataset-v2 was built.
    is_dynamic = lambda c: c['source']=='original_pool' or c.get('dynamic_router_applicable', False)
    summary['sdk_heldout'] = {}
    for name, rows in [('all', heldout), ('dynamic', [r for r in heldout if is_dynamic(pool[r['id']])])]:
        truth = [set(pool[r['id']]['target_groups']) for r in rows]
        previous = [set(pool[r['id']]['active_capabilities']) for r in rows]
        tool_count = lambda gs: len({t for g in gs for t in groups[g]['tools']})
        summary['sdk_heldout'][name] = {
            'baseline_laya': score([baseline[r['id']] for r in rows]), 'fitted_laya': score(rows),
            'historical_active_covers_observed': sum(a<=b for a,b in zip(truth,previous))/len(rows),
            'historical_mean_optional_tools': sum(map(tool_count, previous))/len(rows),
            'candidate_mean_optional_tools': sum(tool_count(r['selected_groups']) for r in rows)/len(rows)}
    cache = torch.load(args.scorer/'features.pt', weights_only=True)
    agent = laya.load(str((WORK/'model-multilingual').resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    fitted = torch.load(args.scorer/'scorer.pt', weights_only=True)
    scorer = copy.deepcopy(agent.model.scorer).float().cpu(); scorer.load_state_dict(fitted); scorer.eval()
    test_idx = [i for i,c in enumerate(selected) if c['split']=='test']
    with torch.no_grad():
        cached = scorer(cache['features'][test_idx,0].float()).squeeze(-1).softmax(-1)[...,1]
    actual = torch.tensor([[r['response']['answers'][g]['noul'] for g in names] for r in heldout])
    summary['cache_vs_sdk'] = {'different_binary_decisions': int(((cached>=.5)!=(actual>=.5)).sum()),
        'total_binary_decisions': actual.numel(), 'different_sets': int(((cached>=.5)!=(actual>=.5)).any(1).sum()),
        'max_probability_difference': float((cached-actual).abs().max())}

    # Cover the audited goal/host/depth/window defects across five train/dev families.
    # No outcome-based sampling. Training controls diagnose inputs, not generalization.
    controls = []
    for family in ['enterprise:E02','enterprise:SALE','erpbench:2205','erpbench:2008','enterprise:S01499']:
        pool_family = sorted((c for c in selected if c['business_group']==family and c['split']!='test'),key=lambda c:c['id'])
        for clipped in [True,False]:
            eligible=[c for c in pool_family if bool(baseline[c['id']]['state_tokens_dropped'])==clipped]
            if eligible: controls.append(eligible[0])
    reviews = read(FIXTURES/'reviewed_holdout.json')['cases']
    freeze = {'script_sha256': sha(__file__), 'state_builder_sha256': sha(state_from_request.__code__.co_filename),
        'scorer_sha256': sha(args.scorer/'scorer.pt'), 'review_sha256': sha(FIXTURES/'reviewed_holdout.json'),
        'control_ids': [c['id'] for c in controls], 'threshold': .5,
        'purpose': 'Input diagnostic only; no weight/threshold fitting or test-based choice.'}
    (args.output/'frozen.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf8')
    rows=[]
    with (args.output/'predictions.jsonl').open('x',encoding='utf8') as log:
        def run(c, variant, state, max_len, model):
            start=time.perf_counter(); result=agent.system_one(state,specs,max_len=max_len)
            assert agent.device.type=='cuda', 'Unexpected SDK hardware fallback.'
            row={'id':c['id'],'variant':variant,'model':model,'state':state,'max_len':max_len,
                 'latency_ms':(time.perf_counter()-start)*1000,'response':result,
                 'selected_groups':[g for g in names if result['answers'][g]['noul']>=.5]}
            internal={g:agent._to_internal(q) for g,q in specs.items()}
            empty=agent._encode_state('',names,internal,max_len=max_len)
            size=len(agent.tok(state,add_special_tokens=False)['input_ids'])
            row['state_tokens_dropped']=max(0,size-min(max_len-len(e['ids']) for e in empty))
            rows.append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            return row
        for model in ['baseline','fitted']:
            if model=='fitted': agent.model.scorer.load_state_dict(fitted)
            for c in controls:
                run(c,'frozen_input',c['state'],1024,model)
                assert sha(c['request_path'])==c['request_sha256']
                state=state_from_request(read_request(c['request_path']))
                # Diagnostic removes SDK truncation; it is not a deployed latency budget.
                max_len=len(agent.tok(state,add_special_tokens=False)['input_ids'])+260
                assert max_len<=8192, 'Input exceeds supported context; do not silently discard it.'
                run(c,'repaired_input',state,max_len,model)
        review_rows=[]
        for r in reviews:
            c=pool[r['id']]
            # Keep the original frozen input: these labels never participated in tuning.
            old=next((h for h in heldout if h['id']==r['id']),None)
            prediction=old or run(c,'reviewed_frozen_input',c['state'],1024,'fitted')
            chosen=set(prediction['selected_groups'])
            allowed = sorted(chosen) in [sorted(s) for s in r['allowed_injection_sets']]
            unrelated = sorted(chosen & set(r['unrelated_groups']))
            missing = sorted(set(r['required_groups']) - chosen)
            review_rows.append({'id':r['id'],'selected_groups':sorted(chosen),
                'assessment': 'accepted' if allowed else 'unsupported_inclusion' if unrelated else 'needs_review',
                'unrelated_selected':unrelated, 'missing_required_groups':missing})
    summary['input_controls']={model:{variant:score([r for r in rows if r['model']==model and r['variant']==variant])
        for variant in ['frozen_input','repaired_input']} for model in ['baseline','fitted']}
    summary['reviewed_holdout']=review_rows
    summary['new_sdk_calls']=len(rows)
    summary['new_encoder_input_tokens']=sum(r['response']['usage']['input_tokens'] for r in rows)
    summary['new_truncated_encodings']=dict(Counter(r['variant'] for r in rows if r['state_tokens_dropped']))
    summary['reused_test_predictions']=sum(any(h['id']==r['id'] for h in heldout) for r in reviews)
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:summary[k] for k in ['new_sdk_calls','new_truncated_encodings','cache_vs_sdk']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scorer',type=Path,default=OUTPUT/'scorer-v1')
    p.add_argument('--output',type=Path,default=OUTPUT/'input-and-review-v2')
    main(p.parse_args())
