"""Forum-guided local control: concrete options and Laya's complete decision head.

Uses the official model forward; encoder stays frozen. No ERP/provider calls.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import random
import time

from .build_cases import read as read_request
from .decision_dataset import FIXTURES, OUTPUT, state_from_request
from .laya_probe import WORK, questions, read, sha
from .train_decider import metrics
from .reviewed_dataset import require_reference_opt_in


def concrete_questions(groups, true_label='A', true_first=False):
    false_label = 'B' if true_label == 'A' else 'A'
    result = {}
    for name, group in groups.items():
        choices = [(false_label, 'Use base tools, another capability, ask for clarification, or finish. This capability is not needed next.'),
                   (true_label, 'Use this capability next: ' + group['description'])]
        result[name] = {'type': 'choice', 'instructions':
            'Which option fits the NEXT assistant step given the task and latest observations? '
            'Record search, reads, aggregation, schema lookup, SOP and verification are already available.',
            'criteria': dict(reversed(choices) if true_first else choices)}
    return result


def chosen(answer, positive):
    # SDK probabilities are rounded; a displayed .5000 must not override its choice.
    return answer['noul'] >= .5 if positive == 'noul' else answer['choice'] == positive


def fit_epoch(agent, items, optimizer, params, step=0):
    """Exact 16-example accumulation (last batch may be smaller), official forward."""
    import torch
    from laya.common import collate_items
    assert not any(p.requires_grad for p in agent.model.encoder.parameters()), 'Encoder must remain frozen.'
    agent.model.train(); agent.model.encoder.eval()
    total=tokens=0
    for start in range(0,len(items),16):
        batch_items=items[start:start+16]
        optimizer.zero_grad(set_to_none=True)
        offset=0
        while offset<len(batch_items):
            chunk=[];width=0
            while offset<len(batch_items) and len(chunk)<4:
                item=batch_items[offset];needed=max(width,len(item['ids']))
                if chunk and needed*(len(chunk)+1)>4096:break
                chunk.append(item);width=needed;offset+=1
            batch=collate_items([chunk],agent.tok.pad_token_id)
            inputs={k:batch[k].to(agent.device) for k in ['input_ids','attention_mask','marker_pos','marker_mask','qtype']}
            target=batch['label'].to(agent.device)
            weight=torch.tensor([c.get('weight',1.) for c in chunk],device=agent.device)
            with torch.autocast(agent.device.type,dtype=torch.bfloat16):
                logits,_=agent.model(**inputs)
                loss=(torch.nn.functional.cross_entropy(logits,target,reduction='none')*weight).sum()
            assert torch.isfinite(loss), 'Non-finite training loss.'
            (loss/len(batch_items)).backward()
            total+=loss.item();tokens+=int(batch['attention_mask'].sum())
        step+=1
        for group in optimizer.param_groups:group['lr']=3e-5*min(1.,step/20)
        torch.nn.utils.clip_grad_norm_(params,1.);optimizer.step()
    return step,total/len(items),tokens


def main(args):
    require_reference_opt_in(args.allow_reference_labels)
    os.environ['HF_HOME'] = str(WORK/'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    import laya
    import torch

    seed = 20260924
    random.seed(seed); torch.manual_seed(seed); torch.set_num_threads(4)
    old = read(OUTPUT/'scorer-v1/frozen.json')
    dataset = OUTPUT/'dataset-v2/cases.jsonl'
    assert sha(dataset) == old['dataset_sha256']
    pool = {c['id']: c for c in map(json.loads, dataset.read_text(encoding='utf8').splitlines())}
    cases = [pool[c['id']] for c in old['cases']]
    reviews = read(FIXTURES/'reviewed_holdout.json')['cases']
    review_extra = [pool[r['id']] for r in reviews if r['id'] not in {c['id'] for c in cases}]
    groups = read(OUTPUT/'dataset-v2/groups.json'); names = list(groups)
    specs = [concrete_questions(groups, label, first) for label in ['A','B'] for first in [False,True]]
    model_dir = WORK/'model-multilingual'
    lock = read(WORK/'model-lock.json')
    assert sha(model_dir/'model.safetensors') == lock['files_sha256']['model.safetensors']
    args.output.mkdir(parents=True, exist_ok=False)
    agent = laya.load(str(model_dir.resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    assert agent.temperature == [1.,1.,1.] or list(agent.temperature) == [1.,1.,1.]
    encoded = {}; lengths = {}; states = {}
    for c in [*cases, *review_extra]:
        assert sha(c['request_path']) == c['request_sha256']
        state = state_from_request(read_request(c['request_path']))
        size = len(agent.tok(state.replace(agent.tok.mask_token,' '), add_special_tokens=False)['input_ids'])
        max_len = size + 260
        if max_len > 8192:
            raise ValueError(f'Input exceeds supported context: {c["id"]}')
        states[c['id']], lengths[c['id']] = state, max_len
        if c['split'] == 'train':
            encoded[c['id']] = [agent._encode_state(state,names,{g:agent._to_internal(q) for g,q in spec.items()},max_len=max_len)
                                 for spec in specs]
    frozen = {'seed':seed,'epochs':args.epochs,'lr':3e-5,'effective_batch':16,'script_sha256':sha(__file__),
        'input_helper_sha256':sha(state_from_request.__code__.co_filename),'dataset_sha256':sha(dataset),
        'sample_sha256':sha(OUTPUT/'scorer-v1/frozen.json'),'model_lock':lock,
        'cases':[{k:c[k] for k in ['id','split','business_group','target_groups']} for c in cases],
        'review_sha256':sha(FIXTURES/'reviewed_holdout.json'),'questions':specs,
        'max_len_by_case':lengths,'split_counts':dict(Counter(c['split'] for c in cases)),
        'selection':'Best dev macro F1; same previously inspected heldout, no new unseen-business claim.',
        'limits':['Historical tool calls are weak labels, not business correctness.','Only the latest three observations are summarized.'],
        'sources':['https://github.com/NandhaKishorM/laya/issues/171',
                   'https://n.demir.io/articles/testing-and-fine-tuning-laya/']}
    (args.output/'frozen.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2),encoding='utf8')
    (args.output/'states.jsonl').write_text(''.join(json.dumps({'id':k,'state':v},ensure_ascii=False)+'\n' for k,v in states.items()),encoding='utf8')
    calls = tokens = reused = 0
    if args.baseline_from:
        prior=read(args.baseline_from/'frozen.json')
        for key in ['questions','model_lock','dataset_sha256','max_len_by_case','input_helper_sha256']:
            assert frozen[key]==prior[key], f'Reused baseline differs: {key}'
        assert sha(args.output/'states.jsonl')==sha(args.baseline_from/'states.jsonl')

    def evaluate(subset, tag, spec=specs[0], probability_key='A'):
        nonlocal calls,tokens,reused
        rows=[]; agent.model.eval()
        prior_rows={}
        if args.baseline_from and tag.startswith('base-'):
            source=args.baseline_from/(tag+'.jsonl')
            if source.exists():
                prior_rows={r['id']:r for r in map(json.loads,source.read_text(encoding='utf8').splitlines())}
        with (args.output/(tag+'.jsonl')).open('x',encoding='utf8') as log:
            for c in subset:
                start=time.perf_counter()
                if c['id'] in prior_rows:
                    result=prior_rows[c['id']]['response'];reused+=1
                    elapsed=prior_rows[c['id']]['latency_ms']
                else:
                    result=agent.system_one(states[c['id']],spec,max_len=lengths[c['id']])
                    elapsed=(time.perf_counter()-start)*1000
                    calls+=1;tokens+=result['usage']['input_tokens']
                assert agent.device.type=='cuda', 'Unexpected SDK fallback.'
                p=[result['answers'][g].get('noul') if probability_key=='noul' else
                   result['answers'][g]['probabilities'][probability_key] for g in names]
                row={'id':c['id'],'probabilities':p,
                     'selected_groups':[g for g in names if chosen(result['answers'][g],probability_key)],
                     'latency_ms':elapsed,'response':result,'reused':c['id'] in prior_rows}
                rows.append(row); log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
        p=torch.tensor([[g in r['selected_groups'] for g in names] for r in rows]); y=torch.tensor([[g in c['target_groups'] for g in names] for c in subset])
        return rows,metrics(p,y,names)

    dev=[c for c in cases if c['split']=='dev']; test=[c for c in cases if c['split']=='test']
    old_spec={g:{**q,'labels':{'true':'A','false':'B'}} for g,q in questions(groups).items()}
    summary={'format_dev':{},'epochs':[],'paid_api_calls':0,'odoo_calls':0}
    for tag,spec,key in [('noul',old_spec,'noul'),('choice',specs[0],'A'),('choice_reverse',specs[1],'A')]:
        rows,score=evaluate(dev,'base-dev-'+tag,spec,key);summary['format_dev'][tag]=score
        print(json.dumps({'stage':'format','variant':tag,'macro_f1':score['macro_f1_observed_groups']},ensure_ascii=False),flush=True)
    base_rows,summary['base_test']=evaluate(test,'base-test')
    agent.model.requires_grad_(False)
    parts=('head','type_emb','scorer')
    for name in parts: getattr(agent.model,name).requires_grad_(True)
    params=[p for p in agent.model.parameters() if p.requires_grad]
    summary['trainable_parameters']=sum(p.numel() for p in params)
    train=[c for c in cases if c['split']=='train']
    counts=torch.tensor([sum(g in c['target_groups'] for c in train) for g in names],dtype=torch.float32)
    positive_weights=((len(train)-counts)/counts.clamp_min(1)).sqrt().clamp(1,8)
    optimizer=torch.optim.AdamW(params,lr=3e-5,weight_decay=.01)
    best=-1;step=0;training_tokens=0;start_train=time.perf_counter()
    print(json.dumps({'stage':'training','trainable_parameters':summary['trainable_parameters']}),flush=True)
    for epoch in range(args.epochs):
        rng=random.Random(seed+epoch); items=[]
        for c in train:
            for group_index,g in enumerate(names):
                variant=rng.randrange(4); positive='A' if variant<2 else 'B'
                label=list(specs[variant][g]['criteria']).index(positive)
                label=label if g in c['target_groups'] else 1-label
                items.append({**encoded[c['id']][variant][group_index], 'label':label,
                              'weight':float(positive_weights[group_index]) if g in c['target_groups'] else 1.})
        rng.shuffle(items)
        step,mean_loss,used_tokens=fit_epoch(agent,items,optimizer,params,step)
        training_tokens+=used_tokens
        _,score=evaluate(dev,f'epoch-{epoch+1}-dev')
        row={'epoch':epoch+1,'mean_example_loss':mean_loss,'optimizer_steps':step,'dev':score};summary['epochs'].append(row)
        if score['macro_f1_observed_groups']>best:
            best=score['macro_f1_observed_groups'];summary['selected_epoch']=epoch+1
            torch.save({k:v.detach().cpu() for k,v in agent.model.state_dict().items() if k.split('.')[0] in parts},args.output/'decision_head.pt')
        (args.output/'training.json').write_text(json.dumps(summary['epochs'],indent=2),encoding='utf8')
        print(json.dumps({'epoch':epoch+1,'dev_macro_f1':best,'selected_epoch':summary['selected_epoch']}),flush=True)
    summary['training_seconds']=time.perf_counter()-start_train
    agent.model.load_state_dict(torch.load(args.output/'decision_head.pt',weights_only=True),strict=False)
    rows,summary['candidate_test']=evaluate(test,'candidate-test')
    reversed_rows,summary['candidate_dev_reverse']=evaluate(dev,'candidate-dev-reverse',specs[1])
    selected_dev=list(map(json.loads,(args.output/f'epoch-{summary["selected_epoch"]}-dev.jsonl').read_text(encoding='utf8').splitlines()))
    summary['dev_order_set_consistency']=sum(a['selected_groups']==b['selected_groups'] for a,b in zip(selected_dev,reversed_rows))/len(dev)
    extra,_=evaluate(review_extra,'candidate-review-extra') if review_extra else ([],None)
    all_predictions={r['id']:r for r in [*rows,*extra]};summary['review']=[]
    for review in reviews:
        selected=set(all_predictions[review['id']]['selected_groups']);unrelated=sorted(selected & set(review['unrelated_groups']))
        summary['review'].append({'id':review['id'],'selected_groups':sorted(selected),
            'allowed_set':sorted(selected) in [sorted(s) for s in review['allowed_injection_sets']],
            'missing_required':sorted(set(review['required_groups'])-selected),'unrelated_selected':unrelated})
    dynamic=[i for i,c in enumerate(test) if c['source']=='original_pool' or c.get('dynamic_router_applicable')]
    truth=torch.tensor([[g in test[i]['target_groups'] for g in names] for i in dynamic])
    summary['dynamic']={tag:metrics(torch.tensor([[g in rs[i]['selected_groups'] for g in names] for i in dynamic]),truth,names)
        for tag,rs in [('base',base_rows),('candidate',rows)]}
    summary.update(sdk_calls=calls,reused_sdk_calls=reused,encoder_inference_tokens=tokens,training_encoder_tokens=training_tokens,
                   training_batches=step,head_sha256=sha(args.output/'decision_head.pt'))
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'stage':'done','sdk_calls':calls,'selected_epoch':summary['selected_epoch'],
                      'candidate_dynamic':summary['dynamic']['candidate']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=OUTPUT/'full-head-accum-v1')
    p.add_argument('--epochs',type=int,default=4)
    p.add_argument('--baseline-from',type=Path)
    p.add_argument('--allow-reference-labels',action='store_true',help='Explicitly reproduce training on unreviewed historical next-call labels.')
    main(p.parse_args())
