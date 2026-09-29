"""Same-input pretrained control and real Laya abstention check. No API or ERP calls."""
import argparse
from collections import Counter
import gc
import json
from pathlib import Path
import statistics

from .build_cases import read
from .laya_probe import sha
from .model_comparison import verdict
from .reviewed_dataset import verify_source
from .router import CapabilityRouter
from .routing_state import routing_state
from .train_reviewed import score


def main(args):
    import laya
    import torch
    torch.set_num_threads(4)
    manifest=read(args.source/'frozen.json')
    assert sha(args.source/'cases.json')==manifest['hashes']['cases.json']
    cases=[r for r in read(args.source/'cases.json') if r['split']=='test']+read(args.phases)
    assert len(cases)==len({r['id'] for r in cases})
    for case in cases:
        case['state']=routing_state(verify_source(case))
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8')
    config=read(args.model/'rl_agent_config.json')
    window={k:config[k] for k in ['max_len','head_max_len']}
    files=[Path(__file__),Path(CapabilityRouter.route.__code__.co_filename),Path(routing_state.__code__.co_filename)]
    frozen={'cases_sha256':sha(args.output/'cases.json'),'source_sha256':sha(args.source/'frozen.json'),
            'phases_sha256':sha(args.phases),'model_sha256':sha(args.model/'model.safetensors'),
            'base_model_sha256':sha(args.base_model/'model.safetensors'),'model_path':str(args.model.resolve()),
            'sources':{str(p):sha(p) for p in files},'inference_window':window,'paid_calls':0,'odoo_calls':0,
            'limits':['Previously inspected cases, not a blind benchmark. Abstention is not a correct route.',
                      'Label swap was chosen after inspecting prior format behavior; no new accuracy claim from selection.']}
    (args.output/'frozen.json').write_text(json.dumps(frozen,indent=2),encoding='utf8')
    (args.output/'sources').mkdir()
    for file in files:(args.output/'sources'/file.name).write_bytes(file.read_bytes())
    questions=read(args.model/'questions.json')
    agent=laya.load(str(args.base_model.resolve()),device='cuda');baseline=[]
    with (args.output/'pretrained.jsonl').open('x',encoding='utf8') as log:
        for case in cases:
            assert len(agent.tok(case['state'],add_special_tokens=False)['input_ids'])+window['head_max_len']+4<=window['max_len']
            answer=agent.system_one(case['state'],questions,**window)['answers'];assert agent.device.type=='cuda'
            row={'id':case['id'],**verdict([g for g,a in answer.items() if a['choice']=='A'],case)}
            baseline.append(row);log.write(json.dumps(row)+'\n');log.flush()
    agent.model.cpu();del agent;gc.collect();torch.cuda.empty_cache()
    if args.pretrained_only:
        summary={'pretrained_same_input':score(baseline),'inference_window':window,'paid_calls':0,'odoo_calls':0}
        (args.output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
        print(json.dumps(summary),flush=True)
        return
    router=CapabilityRouter(args.model);rows=[]
    with (args.output/'candidate.jsonl').open('x',encoding='utf8') as log:
        for case in cases:
            decision=router.route(verify_source(case),verify_labels=True)
            selected=decision.get('capabilities',decision.get('candidate_capabilities'))
            row={'id':case['id'],'decision':decision,**verdict(selected,case)}
            rows.append(row);log.write(json.dumps(row)+'\n');log.flush()
            print(json.dumps({'checked':len(rows),'total':len(cases)}),flush=True)
    accepted=[r for r in rows if r['decision']['status']=='ok']
    fallback=[r for r in rows if r['decision']['status']=='fallback']
    summary={'pretrained_same_input':score(baseline),'single_pass':score(rows),
             'guarded_accepted':score(accepted),'fallback_count':len(fallback),
             'fallback_previous_status':dict(Counter(r['status'] for r in fallback)),
             'fallback_ids':[r['id'] for r in fallback],
             'warm_primary_p50_ms':statistics.median(r['decision']['primary_elapsed_ms'] for r in rows[1:]),
             'warm_guarded_p50_ms':statistics.median(r['decision']['elapsed_ms'] for r in rows[1:]),
             'inference_calls':sum(r['decision']['inference_calls'] for r in rows),
             'paid_calls':0,'odoo_calls':0}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source','phases','model','base-model','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--pretrained-only',action='store_true',help='Recheck the pretrained control without repeating candidate inference')
    main(p.parse_args())
