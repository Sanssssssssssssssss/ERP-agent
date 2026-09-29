"""Dev-only ablation: expose the preceding assistant's intent, never its next response."""
import argparse
import json
import os
from pathlib import Path
import time

from .build_cases import TOKEN, read as read_request
from .decision_dataset import OUTPUT
from .laya_probe import WORK, read, sha
from .train_head import chosen
from .train_decider import metrics


def with_prior_intent(state, request):
    previous = next((m for m in reversed(request['messages']) if m['role']=='assistant'), {})
    hint={k:TOKEN.sub('[APPROVAL_TOKEN_REDACTED]',previous[k][-1200:]) for k in
          ['content','reasoning_content'] if isinstance(previous.get(k),str) and previous[k].strip()}
    return json.dumps({**json.loads(state),'previous_assistant_intent':
        {'evidence_status':'Previous plan only; latest tool results and current user scope take precedence.',**hint}},ensure_ascii=False)


def main(empty_control=False, source=None, output=None):
    os.environ['HF_HOME']=str(WORK/'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    import laya
    import torch
    folder=source or OUTPUT/'full-head-accum-v1';frozen=read(folder/'frozen.json');summary=read(folder/'summary.json')
    assert sha(folder/'decision_head.pt')==summary['head_sha256']
    assert sha(WORK/'model-multilingual/model.safetensors')==frozen['model_lock']['files_sha256']['model.safetensors']
    assert sha(OUTPUT/'dataset-v2/cases.jsonl')==frozen['dataset_sha256']
    pool={c['id']:c for c in map(json.loads,(OUTPUT/'dataset-v2/cases.jsonl').read_text(encoding='utf8').splitlines())}
    states={r['id']:r['state'] for r in map(json.loads,(folder/'states.jsonl').read_text(encoding='utf8').splitlines())}
    selected={}
    dev=sorted((c for c in frozen['cases'] if c['split']=='dev'),key=lambda c:c['id'])
    for g in [None,'actions','diagnostics','cross_instance','migration']:
        for c in [c for c in dev if (g in c['target_groups'] if g else not c['target_groups'])][:2]:selected[c['id']]=c
    out=output or OUTPUT/('prior-intent-control-v1' if empty_control else 'prior-intent-v1');out.mkdir(exist_ok=False)
    (out/'frozen.json').write_text(json.dumps({'script_sha256':sha(__file__),'ids':list(selected),
        'empty_intent_control':empty_control,
        'source_states_sha256':sha(folder/'states.jsonl'),
        'head_sha256':sha(folder/'decision_head.pt'),'selection':'First two dev nodes per observed group/base-only; not selected by model outcome.',
        'limits':'Prior intent may be stale or wrong. This is a routing hint, never authorization.'},indent=2),encoding='utf8')
    agent=laya.load(str((WORK/'model-multilingual').resolve()),device='cuda');specs=frozen['questions'][0];names=list(specs)
    rows=[]
    with (out/'predictions.jsonl').open('x',encoding='utf8') as log:
        for model,base_file in [('base','base-dev-choice.jsonl'),('fitted',f'epoch-{summary["selected_epoch"]}-dev.jsonl')]:
            if model=='fitted':agent.model.load_state_dict(torch.load(folder/'decision_head.pt',weights_only=True),strict=False)
            base={r['id']:r for r in map(json.loads,(folder/base_file).read_text(encoding='utf8').splitlines())}
            for key,c in selected.items():
                source=pool[key];assert sha(source['request_path'])==source['request_sha256']
                request={'messages':[]} if empty_control else read_request(source['request_path'])
                state=with_prior_intent(states[key],request)
                size=len(agent.tok(state,add_special_tokens=False)['input_ids'])+260;assert size<=8192
                start=time.perf_counter();result=agent.system_one(state,specs,max_len=size)
                assert agent.device.type=='cuda'
                row={'id':key,'model':model,'state':state,'max_len':size,'response':result,
                    'baseline_groups':base[key]['selected_groups'],
                    'selected_groups':[g for g in names if chosen(result['answers'][g],'A')],
                    'latency_ms':(time.perf_counter()-start)*1000}
                rows.append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
    scores={}
    for model in ['base','fitted']:
        rs=[r for r in rows if r['model']==model];y=torch.tensor([[g in selected[r['id']]['target_groups'] for g in names] for r in rs])
        scores[model]={k:metrics(torch.tensor([[g in r[k] for g in names] for r in rs]),y,names)
                      for k in ['baseline_groups','selected_groups']}
    scores.update(sdk_calls=len(rows),encoder_input_tokens=sum(r['response']['usage']['input_tokens'] for r in rows),paid_api_calls=0,odoo_calls=0)
    (out/'summary.json').write_text(json.dumps(scores,indent=2),encoding='utf8');print(json.dumps({'sdk_calls':len(rows)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--empty-control',action='store_true',help='Keep identical JSON formatting and hint wrapper, omit only prior plan text.')
    parser.add_argument('--source',type=Path,help='Completed full-head experiment directory.')
    parser.add_argument('--output',type=Path,help='New result directory; existing directories are never overwritten.')
    args=parser.parse_args()
    main(args.empty_control,args.source,args.output)
