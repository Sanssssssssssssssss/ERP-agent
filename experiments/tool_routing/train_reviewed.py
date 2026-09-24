"""Small reviewed-label adaptation; masked unknowns and family-balanced fitting."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import random
import time

from .build_cases import read
from .laya_probe import WORK, sha
from .model_comparison import verdict
from .reviewed_dataset import training_labels
from .train_head import concrete_questions, chosen, fit_epoch


def score(rows):
    return {'cases':len(rows),'pass':sum(r['status']=='pass' for r in rows),
            'fail':sum(r['status']=='fail' for r in rows),'needs_review':sum(r['status']=='needs_review' for r in rows),
            'missing_required':sum(bool(r['missing_required']) for r in rows),
            'unrelated':sum(bool(r['unrelated']) for r in rows),
            'mean_groups':sum(len(r['selected_groups']) for r in rows)/len(rows)}


def main(args):
    os.environ['HF_HOME']=str(WORK/'hf-cache');os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    import laya
    import torch
    from laya.common import build_sequence,render_options
    random.seed(20260924);torch.manual_seed(20260924);torch.set_num_threads(4)
    manifest=read(args.source/'frozen.json')
    for name in ['cases.json','groups.json']:
        assert sha(args.source/name)==manifest['hashes'][name]
    cases=read(args.source/'cases.json');groups=read(args.source/'groups.json');names=list(groups)
    train=[r for r in cases if r['split']=='train'];dev=[r for r in cases if r['split']=='dev'];test=[r for r in cases if r['split']=='test']
    labels={r['id']:training_labels(r,names) for r in train}
    frequencies=Counter(r['business_group'] for r in train)
    known=Counter(g for r in train for g,v in labels[r['id']].items() if v is not None)
    positive=Counter(g for r in train for g,v in labels[r['id']].items() if v==1)
    lock=read(WORK/'model-lock.json');model_dir=WORK/'model-multilingual'
    assert sha(model_dir/'model.safetensors')==lock['files_sha256']['model.safetensors']
    agent=laya.load(str(model_dir.resolve()),device='cuda');assert agent.device.type=='cuda'
    assert agent._fast is None, 'Training must use live model weights, not copied fast-path weights.'
    specs=[concrete_questions(groups,label,first) for label in ['A','B'] for first in [False,True]]
    args.output.mkdir(parents=True,exist_ok=False)
    lengths={};encoded={}
    for r in cases:
        length=len(agent.tok(r['state'].replace(agent.tok.mask_token,' '),add_special_tokens=False)['input_ids'])+260
        assert length<=8192, f'Projection exceeds model context: {r["id"]}'
        lengths[r['id']]=length
        if r['split']=='train':
            encoded[r['id']]=[agent._encode_state(r['state'],names,{g:agent._to_internal(q) for g,q in spec.items()},max_len=length) for spec in specs]
    for spec in specs:
        for q in spec.values():
            internal=agent._to_internal(q);opts=render_options(internal)
            assert all(len(agent.tok(' '+s,add_special_tokens=False)['input_ids'])<=48 for s in opts)
            full_head=sum(1+len(agent.tok(' '+s,add_special_tokens=False)['input_ids']) for s in opts)
            full_head+=len(agent.tok('choice question: '+internal['ins'],add_special_tokens=False)['input_ids'])
            assert full_head<=agent.cfg['head_max_len']
            _,markers=build_sequence(agent.tok,'',internal,8192,agent.cfg['head_max_len']);assert len(markers)==2
    frozen={'source_sha256':sha(args.source/'frozen.json'),'model_lock':lock,'seed':20260924,
        'script_sha256':sha(__file__),'fit_helper_sha256':sha(fit_epoch.__code__.co_filename),
        'epochs':args.epochs,'lr':3e-5,'batch':16,'questions':specs,'max_len_by_case':lengths,
        'inference_question_batch':'At most 4096 padded tokens per forward; one full question if longer. State is not truncated.',
        'train_positive':dict(positive),'train_judged':dict(known),'family_counts':dict(frequencies),
        'zero_positive_groups':[g for g in names if not positive[g]],
        'positive_families':{g:sorted({r['business_group'] for r in train if labels[r['id']][g]==1}) for g in names},
        'selection':'Dev missing-required + unrelated case counts, then fewer groups; no test-based epoch selection.',
        'limits':['28 train nodes are a learning diagnostic, not sufficient for ten-capability production acceptance.',
                  'Optional preloads and uncertain groups are masked, not positive or negative.',
                  'Encoder frozen; official complete decision head trained. No probability calibration claim.']}
    (args.output/'frozen.json').write_text(json.dumps(frozen,indent=2),encoding='utf8')
    (args.output/'sources').mkdir()
    for source in [__file__,fit_epoch.__code__.co_filename,training_labels.__code__.co_filename]:
        (args.output/'sources'/Path(source).name).write_bytes(Path(source).read_bytes())
    calls=tokens=forwards=0

    def evaluate(subset,tag,variant=0):
        nonlocal calls,tokens,forwards
        agent.model.eval();results=[]
        with (args.output/(tag+'.jsonl')).open('x',encoding='utf8') as log:
            for r in subset:
                start=time.perf_counter();length=lengths[r['id']]
                width=max(1,min(len(names),4096//length))
                out={'answers':{},'usage':{'input_tokens':0,'output_tokens':0}}
                # Questions are independent; batch sizing bounds memory, not evidence or runtime.
                for offset in range(0,len(names),width):
                    part=agent.system_one(r['state'],{g:specs[variant][g] for g in names[offset:offset+width]},max_len=length)
                    out['answers'].update(part['answers']);out['usage']['input_tokens']+=part['usage']['input_tokens']
                    forwards+=1
                assert agent.device.type=='cuda'
                calls+=1;tokens+=out['usage']['input_tokens']
                selected=[g for g in names if chosen(out['answers'][g],'A' if variant<2 else 'B')]
                row={'id':r['id'],'response':out,'latency_ms':(time.perf_counter()-start)*1000,**verdict(selected,r)}
                results.append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
        s=score(results);print(json.dumps({'stage':tag,**s}),flush=True)
        return results,s

    summary={'baseline':{},'epochs':[],'paid_calls':0,'odoo_calls':0}
    for split,subset in [('train',train),('dev',dev),('test',test)]:
        _,summary['baseline'][split]=evaluate(subset,'base-'+split)
    base_reverse,_=evaluate(test,'base-test-reverse',1)
    agent.model.requires_grad_(False);parts=('head','type_emb','scorer')
    for name in parts:getattr(agent.model,name).requires_grad_(True)
    params=[p for p in agent.model.parameters() if p.requires_grad]
    summary['trainable_parameters']=sum(p.numel() for p in params)
    optimizer=torch.optim.AdamW(params,lr=3e-5,weight_decay=.01)
    best=None;step=training_tokens=0
    for epoch in range(args.epochs):
        rng=random.Random(20260924+epoch);items=[]
        for r in train:
            for gi,g in enumerate(names):
                target=labels[r['id']][g]
                if target is None:continue
                variant=rng.randrange(4);positive_key='A' if variant<2 else 'B'
                positive_slot=list(specs[variant][g]['criteria']).index(positive_key)
                weight=len(train)/(len(frequencies)*frequencies[r['business_group']])
                if target:weight*=min(8.,max(1.,((known[g]-positive[g])/max(1,positive[g]))**.5))
                items.append({**encoded[r['id']][variant][gi],'label':positive_slot if target else 1-positive_slot,'weight':weight})
        rng.shuffle(items)
        step,loss,used=fit_epoch(agent,items,optimizer,params,step);training_tokens+=used
        _,result=evaluate(dev,f'epoch-{epoch+1}-dev')
        rank=(result['missing_required']+result['unrelated'],result['mean_groups'])
        summary['epochs'].append({'epoch':epoch+1,'loss':loss,'optimizer_steps':step,'dev':result})
        if best is None or rank<best:
            best=rank;summary['selected_epoch']=epoch+1
            torch.save({k:v.detach().cpu() for k,v in agent.model.state_dict().items() if k.split('.')[0] in parts},args.output/'decision_head.pt')
        (args.output/'training.json').write_text(json.dumps(summary['epochs'],indent=2),encoding='utf8')
    agent.model.load_state_dict(torch.load(args.output/'decision_head.pt',weights_only=True),strict=False)
    summary['candidate']={}
    for split,subset in [('train',train),('dev',dev),('test',test)]:
        rs,summary['candidate'][split]=evaluate(subset,'candidate-'+split)
        if split=='test':candidate=rs
    reverse,_=evaluate(test,'candidate-test-reverse',1)
    forward=read_lines(args.output/'base-test.jsonl')
    summary['order_consistency']={tag:sum(a['selected_groups']==b['selected_groups'] for a,b in zip(fwd,rev))/len(test)
        for tag,fwd,rev in [('baseline',forward,base_reverse),('candidate',candidate,reverse)]}
    summary['controls']={tag:score([verdict(selected,r) for r in test]) for tag,selected in [('base_only',[]),('always_actions',['actions'])]}
    baseline_dev=summary['baseline']['dev'];candidate_dev=summary['candidate']['dev']
    summary['dev_improved']=(candidate_dev['missing_required']+candidate_dev['unrelated'],candidate_dev['mean_groups']) < (
        baseline_dev['missing_required']+baseline_dev['unrelated'],baseline_dev['mean_groups'])
    summary.update(logical_routing_decisions=calls,sdk_calls=forwards,inference_encoder_tokens=tokens,training_encoder_tokens=training_tokens,
                   optimizer_steps=step,head_sha256=sha(args.output/'decision_head.pt'))
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary),flush=True)


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--epochs',type=int,default=4)
    main(parser.parse_args())
