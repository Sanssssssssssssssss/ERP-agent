"""Fit only Laya's existing final scorer; historical task families remain disjoint."""
import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import random
import time

from .decision_dataset import OUTPUT
from .laya_probe import WORK, questions, read, sha


def metrics(probabilities, targets, names):
    import torch
    predicted = probabilities >= .5
    truth = targets.bool()
    tp, fp, fn = ((predicted & truth).sum(0), (predicted & ~truth).sum(0), (~predicted & truth).sum(0))
    supported = truth.sum(0) > 0
    f1 = 2 * tp / (2 * tp + fp + fn).clamp_min(1)
    return {'nodes': len(truth), 'observed_set_match': (predicted == truth).all(1).float().mean().item(),
            'covers_observed': (~(truth & ~predicted)).all(1).float().mean().item(),
            'nonempty_observed_nodes': int(truth.any(1).sum()),
            'covers_nonempty_observed': (~(truth & ~predicted))[truth.any(1)].all(1).float().mean().item() if truth.any() else None,
            'base_only_nodes_with_extra_groups': int((~truth.any(1) & predicted.any(1)).sum()),
            'macro_f1_observed_groups': f1[supported].mean().item() if supported.any() else None,
            'mean_extra_groups': (predicted & ~truth).sum(1).float().mean().item(),
            'mean_selected_groups': predicted.sum(1).float().mean().item(),
            'per_group': {g: {'positives': int(truth[:, i].sum()), 'tp': int(tp[i]), 'fp': int(fp[i]), 'fn': int(fn[i])} for i,g in enumerate(names)}}


def sample(cases, names, cap=64):
    # Keep rare groups; bound repeated actions/base rows without manufacturing positives.
    selected = {}
    for split in ['train', 'dev', 'test']:
        pool = sorted((c for c in cases if c['split'] == split), key=lambda c: c['state_sha256'])
        for group in [None, *names]:
            eligible = [c for c in pool if (group in c['target_groups'] if group else not c['target_groups'])]
            for c in eligible[:cap]:
                selected[c['id']] = c
    return list(selected.values())


def main(args):
    os.environ['HF_HOME'] = str(WORK/'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    import laya
    import torch
    from laya.common import serialize_state

    torch.manual_seed(20260924); random.seed(20260924)
    if not 256 <= args.max_len <= 8192:
        raise ValueError('max_len must be between 256 and 8192')
    groups = read(args.dataset/'groups.json'); names = list(groups)
    all_cases = [json.loads(s) for s in (args.dataset/'cases.jsonl').read_text(encoding='utf8').splitlines()]
    cases = sample(all_cases, names, args.per_group)
    specs = {label: {g: {**q, 'labels': {'true': label, 'false': 'B' if label == 'A' else 'A'}}
                     for g,q in questions(groups).items()} for label in ['A','B']}
    args.output.mkdir(parents=True, exist_ok=False)
    frozen = {'dataset_sha256': sha(args.dataset/'cases.jsonl'), 'script_sha256': sha(__file__),
              'cases': [{k:c[k] for k in ['id','split','business_group','state_sha256','target_groups']} for c in cases],
              'questions': specs, 'model_lock': read(WORK/'model-lock.json'), 'seed': 20260924,
              'epochs': args.epochs, 'learning_rate': .0003, 'batch_size': 512, 'max_len': args.max_len,
              'trainable': 'Existing LayerNorm/MLP scorer only; encoder, transformer head and type embedding frozen.',
              'selection': 'Highest dev macro F1 across observed groups; labels are historical coverage proxies.',
              'split_counts': dict(Counter(c['split'] for c in cases)),
              'positive_groups': {s: dict(Counter(g for c in cases if c['split']==s for g in c['target_groups'])) for s in ['train','dev','test']}}
    (args.output/'frozen.json').write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding='utf8')
    agent = laya.load(str((WORK/'model-multilingual').resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    for param in agent.model.parameters(): param.requires_grad_(False)
    captured = []
    hook = agent.model.scorer.register_forward_pre_hook(lambda _m, values: captured.append(values[0].detach().cpu()))
    features, baseline, token_counts, truncated = [], [], [], []
    cache_start = time.perf_counter()
    with (args.output/'baseline.jsonl').open('x', encoding='utf8') as log:
        for index,c in enumerate(cases):
            pair, predictions = [], []
            for label in ['A','B']:
                captured.clear()
                result = agent.system_one(c['state'], specs[label], max_len=args.max_len)
                assert agent.device.type == 'cuda' and len(captured) == 1, 'Hardware fallback/repeated forward must be investigated.'
                pair.append(captured[0]); predictions.append([result['answers'][g]['noul'] for g in names])
                state_size = len(agent.tok(serialize_state(c['state']), add_special_tokens=False)['input_ids'])
                empty = agent._encode_state('', names, {g:agent._to_internal(q) for g,q in specs[label].items()}, max_len=args.max_len)
                dropped = max(0, state_size - min(args.max_len-len(e['ids']) for e in empty))
                log.write(json.dumps({'id':c['id'],'label':label,'response':result,'state_tokens_dropped':dropped}, ensure_ascii=False)+'\n');log.flush()
                token_counts.append(result['usage']['input_tokens']); truncated.append(dropped>0)
            features.append(torch.stack(pair)); baseline.append(predictions)
            if index % 25 == 0: print(json.dumps({'feature_nodes':index+1,'total':len(cases)}),flush=True)
    hook.remove()
    x = torch.stack(features)  # [node, label mapping, capability, option, hidden]
    y = torch.tensor([[g in c['target_groups'] for g in names] for c in cases], dtype=torch.long)
    base = torch.tensor(baseline)
    torch.save({'features':x, 'targets':y, 'baseline':base}, args.output/'features.pt')
    scorer = copy.deepcopy(agent.model.scorer).float().cuda()
    for p in scorer.parameters(): p.requires_grad_(True)
    optimizer = torch.optim.AdamW(scorer.parameters(),lr=.0003,weight_decay=.01)
    indices = {s: torch.tensor([i for i,c in enumerate(cases) if c['split']==s],dtype=torch.long) for s in ['train','dev','test']}
    assert all(len(v) for v in indices.values()), 'Need train/dev/test families before fitting.'
    train_x = x[indices['train']].reshape(-1,2,x.shape[-1]).float().cuda()
    train_y = y[indices['train']][:,None,:].expand(-1,2,-1).reshape(-1).cuda()
    counts = y[indices['train']].sum(0).float()
    positive_weights = ((len(indices['train'])-counts)/counts.clamp_min(1)).clamp(1,30)
    weights = torch.where(y[indices['train']].bool(), positive_weights, 1.)[:,None,:].expand(-1,2,-1).reshape(-1).cuda()

    def predict(which):
        with torch.no_grad():
            inp=x[which].float().cuda()
            return scorer(inp).squeeze(-1).softmax(-1)[...,1].cpu()

    history=[];best=-1.;best_epoch=None
    with (args.output/'training.jsonl').open('x',encoding='utf8') as log:
        for epoch in range(args.epochs):
            scorer.train(); permutation=torch.randperm(len(train_y),device='cuda'); total_loss=0.
            for start in range(0,len(permutation),512):
                batch=permutation[start:start+512]
                logits=scorer(train_x[batch]).squeeze(-1)
                loss=(torch.nn.functional.cross_entropy(logits,train_y[batch],reduction='none')*weights[batch]).mean()
                optimizer.zero_grad(set_to_none=True);loss.backward();optimizer.step();total_loss+=loss.item()
            scorer.eval();dev=predict(indices['dev']);target=y[indices['dev']]
            scores=metrics(dev[:,0],target,names)
            consistency=((dev[:,0]>=.5)==(dev[:,1]>=.5)).all(1).float().mean().item()
            # Development only; no test metric is read during checkpoint selection.
            criterion=scores['macro_f1_observed_groups']
            if criterion>best:
                best=criterion;best_epoch=epoch+1
                torch.save({k:v.detach().cpu() for k,v in scorer.state_dict().items()},args.output/'scorer.pt')
            row={'epoch':epoch+1,'loss_sum':total_loss,'dev':scores,'dev_label_set_consistency':consistency}
            log.write(json.dumps(row)+'\n');log.flush();history.append(row)
            if epoch%10==0:print(json.dumps({'epoch':epoch+1,'dev_macro_f1':criterion,'best_epoch':best_epoch}),flush=True)
    scorer.load_state_dict(torch.load(args.output/'scorer.pt',weights_only=True));scorer.eval()
    summary={'selected_epoch':best_epoch,'trainable_parameters':sum(p.numel() for p in scorer.parameters()),
             'feature_and_training_seconds':time.perf_counter()-cache_start,'cached_model_calls':len(cases)*2,
             'cached_encoder_input_tokens':sum(token_counts),'truncated_encodings':sum(truncated),
             'by_split':{},'generation_api_calls':0,'odoo_calls':0,'promotion':'not_evaluated_closed_loop'}
    for split,idx in indices.items():
        pred=predict(idx);truth=y[idx]
        summary['by_split'][split]={'baseline':metrics(base[idx,0],truth,names),'candidate':metrics(pred[:,0],truth,names),
            'label_set_consistency':((pred[:,0]>=.5)==(pred[:,1]>=.5)).all(1).float().mean().item()}
    # Re-run the held-out nodes through the real SDK with the fitted scorer installed.
    agent.model.scorer.load_state_dict(scorer.state_dict());agent.model.eval()
    actual=[];usage=0;latencies=[]
    with (args.output/'candidate-test.jsonl').open('x',encoding='utf8') as log:
        for index in indices['test'].tolist():
            c=cases[index];start=time.perf_counter();result=agent.system_one(c['state'],specs['A'],max_len=args.max_len)
            elapsed=(time.perf_counter()-start)*1000;latencies.append(elapsed)
            p=[result['answers'][g]['noul'] for g in names];actual.append(p);usage+=result['usage']['input_tokens']
            log.write(json.dumps({'id':c['id'],'response':result,'selected_groups':[g for g,v in zip(names,p) if v>=.5],
                                  'latency_ms':elapsed},ensure_ascii=False)+'\n');log.flush()
    summary['sdk_test']=metrics(torch.tensor(actual),y[indices['test']],names)
    summary['sdk_test_calls']=len(actual);summary['sdk_encoder_input_tokens']=usage
    summary['sdk_p50_ms']=sorted(latencies)[len(latencies)//2]
    summary['scorer_sha256']=sha(args.output/'scorer.pt')
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'selected_epoch':best_epoch,'sdk_test':summary['sdk_test']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,default=OUTPUT/'dataset-v2')
    p.add_argument('--output',type=Path,default=OUTPUT/'scorer-v1')
    p.add_argument('--per-group',type=int,default=64)
    p.add_argument('--epochs',type=int,default=60)
    p.add_argument('--max-len',type=int,default=1024)
    main(p.parse_args())
