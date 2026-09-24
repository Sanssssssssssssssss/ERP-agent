"""Reviewed-label training with complete format coverage and frozen dev selection."""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import time
from types import SimpleNamespace
from unittest.mock import patch

from .build_cases import read
from .laya_probe import WORK, sha
from .model_comparison import verdict
from .reviewed_dataset import training_labels, validate
from .train_head import concrete_questions, chosen, fit_epoch


def score(rows):
    return {'cases':len(rows),'pass':sum(r['status']=='pass' for r in rows),
            'fail':sum(r['status']=='fail' for r in rows),'needs_review':sum(r['status']=='needs_review' for r in rows),
            'missing_required':sum(bool(r['missing_required']) for r in rows),
            'unrelated':sum(bool(r['unrelated']) for r in rows),
            'mean_groups':sum(len(r['selected_groups']) for r in rows)/len(rows)}


def training_items(cases, names, specs, encoded):
    """Every judged pair appears in all formats; unknowns never become negatives."""
    labels={r['id']:training_labels(r,names) for r in cases}
    frequencies=Counter(r['business_group'] for r in cases)
    known=Counter(g for r in cases for g,v in labels[r['id']].items() if v is not None)
    positive=Counter(g for r in cases for g,v in labels[r['id']].items() if v==1)
    items=[]
    for r in cases:
        for gi,g in enumerate(names):
            target=labels[r['id']][g]
            if target is None:continue
            weight=len(cases)/(len(frequencies)*frequencies[r['business_group']])
            if target:weight*=min(8.,max(1.,((known[g]-positive[g])/max(1,positive[g]))**.5))
            for variant,spec in enumerate(specs):
                slot=list(spec[g]['criteria']).index('A' if variant<2 else 'B')
                items.append({**encoded[r['id']][variant][gi], 'label':slot if target else 1-slot,
                              'weight':weight,'case_id':r['id'],'group':g,'variant':variant,'semantic_target':target})
    return items


def decision_metrics(decisions):
    """Equal weight per capability and then per observed class; log missing classes."""
    buckets=defaultdict(list)
    for d in decisions:
        if d['target'] is not None:buckets[(d['group'],d['target'])].append(d)
    by_group=defaultdict(dict)
    for (group,target),rows in buckets.items():
        by_group[group][str(target)]={'count':len(rows), 'ce':sum(r['ce'] for r in rows)/len(rows),
            'correct':sum(r['selected']==bool(target) for r in rows)}
    ce=sum(sum(v['ce'] for v in classes.values())/len(classes) for classes in by_group.values())/len(by_group)
    return {'balanced_ce':ce,'by_group':dict(by_group)}


@contextmanager
def encoder_cache(agent, directory):
    """Disk-backed exact single-row cache; official decision-head forward stays intact."""
    import torch
    directory.mkdir(exist_ok=True)
    original=agent.model.encoder.forward
    stats={'misses':0,'hits':0,'bytes':0,'encoder_tokens':0}

    def forward(input_ids,attention_mask):
        assert input_ids.shape[0]==1 and not agent.model.encoder.training
        assert not any(p.requires_grad for p in agent.model.encoder.parameters())
        key=hashlib.sha256(input_ids.cpu().numpy().tobytes()+attention_mask.cpu().numpy().tobytes()+
            str((tuple(input_ids.shape),torch.is_autocast_enabled('cuda'),torch.get_autocast_dtype('cuda'))).encode()).hexdigest()
        path=directory/(key+'.pt')
        if path.exists():
            h=torch.load(path,map_location=agent.device,weights_only=True);stats['hits']+=1
        else:
            with torch.no_grad():h=original(input_ids=input_ids,attention_mask=attention_mask).last_hidden_state.detach()
            torch.save(h.cpu(),path);stats['misses']+=1;stats['bytes']+=path.stat().st_size
            stats['encoder_tokens']+=int(attention_mask.sum())
        return SimpleNamespace(last_hidden_state=h)

    with patch.object(agent.model.encoder,'forward',side_effect=forward):yield stats


def main(args):
    os.environ['HF_HOME']=str(WORK/'hf-cache');os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    import laya
    import torch
    from laya.common import collate_items,render_options
    seed=20260924
    random.seed(seed);torch.manual_seed(seed);torch.set_num_threads(4)
    manifest=read(args.source/'frozen.json')
    for name in ['cases.json','groups.json']:assert sha(args.source/name)==manifest['hashes'][name]
    cases=read(args.source/'cases.json');groups=read(args.source/'groups.json');names=list(groups)
    splits={s:[r for r in cases if r['split']==s] for s in ['train','dev','test']}
    validate(splits['train'],splits['test'],groups,splits['dev'])
    lock=read(WORK/'model-lock.json');model_dir=WORK/'model-multilingual'
    assert sha(model_dir/'model.safetensors')==lock['files_sha256']['model.safetensors']
    args.output.mkdir(parents=True,exist_ok=False)
    agent=laya.load(str(model_dir.resolve()),device='cuda')
    assert agent.device.type=='cuda' and agent._fast is None and list(agent.temperature)==[1.,1.,1.]
    specs=[concrete_questions(groups,label,first) for label in ['A','B'] for first in [False,True]]
    encoded={};lengths={}
    for r in cases:
        length=len(agent.tok(r['state'].replace(agent.tok.mask_token,' '),add_special_tokens=False)['input_ids'])+260
        assert length<=8192, r['id']
        lengths[r['id']]=length
        encoded[r['id']]=[agent._encode_state(r['state'],names,{g:agent._to_internal(q) for g,q in spec.items()},max_len=length) for spec in specs]
    for spec in specs:
        for q in spec.values():
            internal=agent._to_internal(q);opts=render_options(internal)
            assert all(len(agent.tok(' '+s,add_special_tokens=False)['input_ids'])<=48 for s in opts)
            head=sum(1+len(agent.tok(' '+s,add_special_tokens=False)['input_ids']) for s in opts)
            assert head+len(agent.tok('choice question: '+internal['ins'],add_special_tokens=False)['input_ids'])<=agent.cfg['head_max_len']
    items=training_items(splits['train'],names,specs,encoded)
    # Exercise the real SDK collator before any expensive forward. `target` is reserved
    # for soft-label vectors; experiment metadata must not shadow its input fields.
    collate_items([items[:16]],agent.tok.pad_token_id)
    positives=Counter(i['group'] for i in items if i['semantic_target']==1 and i['variant']==0)
    frozen={'source_sha256':sha(args.source/'frozen.json'),'model_lock':lock,'seed':seed,
        'epochs_max':args.epochs,'epochs_min':args.min_epochs,'patience':args.patience,'lr':3e-5,'batch':16,'microbatch':1,
        'questions':specs,'max_len_by_case':lengths,'train_items_per_epoch':len(items),
        'train_positive':dict(positives),'zero_positive_groups':[g for g in names if not positives[g]],
        'selection':'Lowest dev capability/class-balanced CE over all four formats, including epoch 0. Test evaluated after selection.',
        'early_stop':'After minimum epochs, stop after patience consecutive epochs without dev CE improvement of at least 0.001.',
        'limits':['Existing reviewed pool, not a blind benchmark. Missing positive classes cannot be accepted.',
                  'Same family/positive weights as prior run; full format coverage and training duration are changed.',
                  'No state truncation. Single-row encoder cache preserves dtype and the official head forward.'],
        'versions':{'torch':torch.__version__,'device':torch.cuda.get_device_name(),'amp':str(agent.dtype)}}
    cache_dir=args.output/'encoder-cache'
    if args.cache_from:
        prior=read(args.cache_from/'frozen.json')
        assert prior['model_lock']==lock and prior['versions']==frozen['versions'], 'Cache model/runtime changed.'
        cache_dir=args.cache_from/'encoder-cache'
        assert cache_dir.is_dir()
        frozen['encoder_cache_source']={'path':str(cache_dir.resolve()),'manifest_sha256':sha(args.cache_from/'frozen.json')}
    (args.output/'sources').mkdir()
    source_files=[Path(__file__),Path(fit_epoch.__code__.co_filename),Path(training_labels.__code__.co_filename)]
    frozen['sources']={p.name:sha(p) for p in source_files}
    for source in source_files:(args.output/'sources'/source.name).write_bytes(source.read_bytes())
    (args.output/'frozen.json').write_text(json.dumps(frozen,indent=2),encoding='utf8')
    agent.model.requires_grad_(False);parts=('head','type_emb','scorer')
    for name in parts:getattr(agent.model,name).requires_grad_(True)
    params=[p for p in agent.model.parameters() if p.requires_grad]
    usage=Counter();start=time.perf_counter()

    def save(name):
        torch.save({k:v.detach().cpu() for k,v in agent.model.state_dict().items() if k.split('.')[0] in parts},args.output/name)

    @torch.no_grad()
    def evaluate(subset,tag):
        agent.model.eval();routes=[];decisions=[]
        with (args.output/(tag+'.jsonl')).open('x',encoding='utf8') as log:
            for r in subset:
                labels=training_labels(r,names)
                for variant in range(4):
                    selected=[];detail=[]
                    for gi,g in enumerate(names):
                        item=encoded[r['id']][variant][gi]
                        logits,_=agent._infer(collate_items([[item]],agent.tok.pad_token_id))
                        assert agent.device.type=='cuda' and torch.isfinite(logits).all()
                        slot=list(specs[variant][g]['criteria']).index('A' if variant<2 else 'B')
                        prediction=int(logits.argmax(-1))==slot
                        target=labels[g];label=slot if target else 1-slot
                        ce=None if target is None else float(torch.nn.functional.cross_entropy(logits,torch.tensor([label],device=agent.device)))
                        d={'group':g,'target':target,'selected':prediction,'ce':ce,'logits':logits.cpu().tolist()[0]}
                        decisions.append(d);detail.append(d)
                        if prediction:selected.append(g)
                        usage['head_forwards']+=1;usage['inference_token_presentations']+=len(item['ids'])
                    row={'id':r['id'],'variant':variant,'decisions':detail,**verdict(selected,r)}
                    routes.append(row);log.write(json.dumps(row)+'\n');log.flush()
        result={**score(routes),**decision_metrics(decisions)}
        result['by_variant']={str(v):score([r for r in routes if r['variant']==v]) for v in range(4)}
        print(json.dumps({'stage':tag,**{k:result[k] for k in ['cases','pass','missing_required','unrelated','balanced_ce']}}),flush=True)
        return routes,result

    summary={'paid_calls':0,'odoo_calls':0,'epochs':[],'selected_epoch':0,'trainable_parameters':sum(p.numel() for p in params)}
    with encoder_cache(agent,cache_dir) as cache_stats:
        _,base_train=evaluate(splits['train'],'base-train')
        _,base_dev=evaluate(splits['dev'],'base-dev')
        summary['baseline']={'train':base_train,'dev':base_dev}
        best=base_dev['balanced_ce'];save('decision_head.pt');save('base_head.pt')
        optimizer=torch.optim.AdamW(params,lr=3e-5,weight_decay=.01)
        step=stale=0
        for epoch in range(1,args.epochs+1):
            shuffled=list(items);random.Random(seed+epoch).shuffle(shuffled);norms=[]
            step,loss,tokens=fit_epoch(agent,shuffled,optimizer,params,step,microbatch=1,diagnostics=norms)
            usage['training_token_presentations']+=tokens
            _,dev=evaluate(splits['dev'],f'epoch-{epoch}-dev')
            train=None
            if epoch%2==0:_,train=evaluate(splits['train'],f'epoch-{epoch}-train')
            row={'epoch':epoch,'optimizer_steps':step,'training_loss':loss,'dev':dev,'train':train,
                 'gradient_norm_mean':sum(norms)/len(norms),'gradient_norm_max':max(norms)}
            improvement=best-dev['balanced_ce']
            stale=0 if improvement>=.001 else stale+1
            if improvement>0:
                best=dev['balanced_ce'];summary['selected_epoch']=epoch;save('decision_head.pt')
            summary['epochs'].append(row)
            (args.output/'training.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
            torch.save({'epoch':epoch,'step':step,'optimizer':optimizer.state_dict(),
                'head':{k:v.detach().cpu() for k,v in agent.model.state_dict().items() if k.split('.')[0] in parts}},args.output/'latest.pt')
            if epoch>=args.min_epochs and stale>=args.patience:
                summary['stop_reason']='dev_plateau';break
        else:summary['stop_reason']='epoch_ceiling; inspect train convergence before attributing errors to generalization'
        agent.model.load_state_dict(torch.load(args.output/'decision_head.pt',weights_only=True),strict=False)
        summary['candidate']={}
        candidate_routes={}
        for split,subset in splits.items():candidate_routes[split],summary['candidate'][split]=evaluate(subset,'candidate-'+split)
        agent.model.load_state_dict(torch.load(args.output/'base_head.pt',weights_only=True),strict=False)
        _,summary['baseline']['test']=evaluate(splits['test'],'base-test')
        agent.model.load_state_dict(torch.load(args.output/'decision_head.pt',weights_only=True),strict=False)
    # Uncached official SDK after weight reload is the final inference-path check.
    parity=[];agent.model.eval()
    with (args.output/'sdk-parity.jsonl').open('x',encoding='utf8') as log:
        for r in splits['test']:
            for variant in range(4):
                expected=next(x for x in candidate_routes['test'] if x['id']==r['id'] and x['variant']==variant)
                selected=[];max_delta=0.
                for g in names:
                    out=agent.system_one(r['state'],{g:specs[variant][g]},max_len=lengths[r['id']])
                    assert agent.device.type=='cuda'
                    positive='A' if variant<2 else 'B'
                    if chosen(out['answers'][g],positive):selected.append(g)
                    d=next(d for d in expected['decisions'] if d['group']==g)
                    slot=list(specs[variant][g]['criteria']).index(positive)
                    p=float(torch.tensor(d['logits']).softmax(-1)[slot])
                    max_delta=max(max_delta,abs(p-out['answers'][g]['probabilities'][positive]))
                    usage['sdk_calls']+=1;usage['sdk_input_tokens']+=out['usage']['input_tokens']
                row={'id':r['id'],'variant':variant,'max_probability_delta':max_delta,
                     'same_set':sorted(selected)==expected['selected_groups'],**verdict(selected,r)}
                parity.append(row);log.write(json.dumps(row)+'\n');log.flush()
    summary.update(optimizer_steps=step,cache=cache_stats,usage=dict(usage),elapsed_seconds=time.perf_counter()-start,
        sdk_parity={'same_sets':sum(r['same_set'] for r in parity),'cases':len(parity),'max_probability_delta':max(r['max_probability_delta'] for r in parity)},
        dev_improved=best<base_dev['balanced_ce'],head_sha256=sha(args.output/'decision_head.pt'))
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    assert all(r['same_set'] and r['max_probability_delta']<.001 for r in parity), 'Inspect cached/live inference mismatch.'
    print(json.dumps({'stage':'done','selected_epoch':summary['selected_epoch'],'test':summary['candidate']['test'],'parity':summary['sdk_parity']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--epochs',type=int,default=24);parser.add_argument('--min-epochs',type=int,default=12)
    parser.add_argument('--patience',type=int,default=6)
    parser.add_argument('--cache-from',type=Path,help='Reuse only encoder features from an identical locked model/runtime.')
    args=parser.parse_args()
    if not 1<=args.min_epochs<=args.epochs or args.patience<1:parser.error('Invalid epoch/patience settings.')
    main(args)
