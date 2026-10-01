"""GPU-only context A/B from frozen real prefixes. No ERP or provider calls."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / '.runtime/laya-v7-integration-20260929'
PILOT = {'2003:agent:0020','host-live:E01:0006','host-live:E01:0010','host-live:E01:0013',
         'host-live:E02:0031','host-live:E02:0053','host-live:E02:0061',
         'SALE:r_9862f2843945486eab856dc8d0f405a5:0004',
         'live:E01:0005','live:E01:0012','live:E01:0018','live:SALE:0004'}

def read(p): return json.loads(Path(p).read_text(encoding='utf8'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def lines(p): return [json.loads(s) for s in Path(p).read_text(encoding='utf8').splitlines()]
def save(p,v): Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')


def candidates(request, state):
    from experiments.tool_routing.routing_context import assemble_context
    from erp_harness.context.projection import _encode_tables
    current = assemble_context(request, state=state)
    shaped = copy.deepcopy(state)
    actions = {a['action_id']: a for a in current['action_ledger']['actions']}
    for section in ('unresolved','recent_finished'):
        shaped['action_ledger'][section] = [actions[a['action_id']] for a in state['action_ledger'][section]]
    shaped['business_facts'], _ = _encode_tables(current['observations'])
    shaped['recent_results'] = current['current_tool_events']
    shaped['available_base_tools'] = current['available_base_tools']
    for key in ('verified_action_observations','historical_evidence_refs','historical_tool_event_refs'):
        shaped[key] = current[key]
    compact = copy.deepcopy(shaped)
    compact['available_base_tools'] = copy.deepcopy(state.get('available_base_tools', []))
    # Preserve the trained layout. The new event/ledger semantics remain unchanged.
    facts = copy.deepcopy(current['observations'])
    for fact in facts:
        fact.pop('source_position', None)
    compact['business_facts'], _ = _encode_tables(facts)
    minimal = copy.deepcopy(state)
    for section in ('unresolved', 'recent_finished'):
        minimal['action_ledger'][section] = copy.deepcopy(shaped['action_ledger'][section])
    current_ids = {e['source_call_id'] for e in current['current_tool_events']}
    refs = {e['source_call_id']: e for e in current['historical_tool_event_refs']}
    for event in minimal['recent_results']:
        call_id = event.get('source_call_id')
        event['event_scope'] = 'current_result' if call_id in current_ids else 'historical_result'
        if call_id in refs:
            event['resolution'] = refs[call_id]['resolution']
            if refs[call_id]['resolution'] == 'superseded_by_host_ledger':
                event['as_of_ledger_status'] = refs[call_id]['as_of_ledger_status']
    working = copy.deepcopy(minimal)
    for event in working['recent_results']:
        if event['event_scope'] == 'historical_result' and event.get('error'):
            event.pop('error')
            event['error_details'] = 'Read source_call_id in trace; historical failure is not current status.'
    return {'clean_v7_shape': shaped, 'context_v12': current, 'clean_v7_compact': compact,
            'v7_event_scope': minimal, 'v7_working_events': working}


def prepare(output, pilot, selected):
    output.mkdir()
    config = PRIOR / 'config.json'
    baselines = {r['case_id']:r['response'] for r in lines(PRIOR/'regression-final/results.jsonl')}
    cases = read(PRIOR/'regression-final/cases.json')
    sources = [config, PRIOR/'regression-final/cases.json', PRIOR/'regression-final/results.jsonl']
    for case in ('SALE','E01'):
        folder=ROOT/'.runtime/openjev-live-20260928'/case
        run=folder/'profile/data/runs'/read(folder/'summary.json')['run_id']
        decisions={r['call_id']:r for r in lines(run/'routing/decisions.jsonl') if r.get('event')=='decision'}
        for meta in sorted((run/'requests').glob('*.meta.json')):
            m=read(meta);decision=decisions[m['routing_decision_id']]
            packet=run/'routing'/(m['routing_decision_id'].replace(':','-')+'.request.json')
            request=meta.with_name(meta.name.replace('.meta.json','.request.json'))
            key=f"live:{case}:{m['request_file']}"
            cases.append({'case_id':key,'packet':read(packet),'request_path':str(request),
                'request_sha256':sha(request),'expected':{},'split':'new_live_observation'})
            baselines[key]=decision;sources.extend([meta,packet,request,run/'routing/decisions.jsonl'])
    if pilot:
        cases=[c for c in cases if c['case_id'] in PILOT]
        assert {c['case_id'] for c in cases}==PILOT
    rows=[]
    for case in cases:
        request=Path(case['request_path']);assert sha(request)==case['request_sha256']
        sources.append(request)
        variants={k:v for k,v in candidates(read(request),case['packet']['state']).items() if k in selected}
        rows.append({'id':case['case_id'],'expected':case['expected'],'baseline':baselines[case['case_id']],
                     'original_state':case['packet']['state'],'variants':variants})
    save(output/'cases.json',rows)
    sources += [Path(__file__), output/'cases.json',ROOT/'src/erp_harness/providers/laya_worker.py',
        ROOT/'experiments/tool_routing/routing_context.py',ROOT/'src/erp_harness/context/projection.py']
    save(output/'frozen.json',{'config':str(config),'files':{str(p.resolve()):sha(p) for p in sources},
        'cases':len(rows),'variants':selected,'label_policy':'Reuse frozen expectations; new live menus unlabelled, no invented gold set.',
        'policy':'Same v7 weights/questions, GPU microbatch2; baseline reused by source hashes. No truncation, no ERP, no paid calls.'})
    print({'cases':len(rows),'candidate_calls':len(rows)*len(selected)},flush=True)


def infer(router, state):
    text=json.dumps(state,ensure_ascii=False,separators=(',',':'))
    size=len(router.agent.tok(text.replace(router.agent.tok.mask_token,' '),add_special_tokens=False)['input_ids'])
    if size+router.agent.cfg['head_max_len']+4>router.agent.cfg['max_len']:
        return {'status':'fallback','reason':'context_exceeds_model_window','state_tokens':size}
    router.question_batch_size=2
    start=time.perf_counter();result,selected=router._predict(text,router.questions,'A')
    return {'status':'ok','capabilities':selected,'state_tokens':size,'elapsed_ms':(time.perf_counter()-start)*1000,
        'probabilities':{g:a['probabilities']['A'] for g,a in result['answers'].items()},'usage':result['usage'],
        'state_sha256':hashlib.sha256(text.encode()).hexdigest(),'device':router.agent.device.type}


def summarize(output):
    cases=read(output/'cases.json');results=lines(output/'results.jsonl');by={(r['id'],r['variant']):r['response'] for r in results}
    summary={}
    for variant in ['baseline', *read(output/'frozen.json')['variants']]:
        correct=labels=positive=hit=extra=valid=0;menus=[];fails=[]
        for case in cases:
            r=case['baseline'] if variant=='baseline' else by.get((case['id'],variant),{})
            ok=r.get('status')=='ok';valid+=ok;selected=r.get('capabilities',[])
            if case['id'].startswith('live:'):menus.append({'id':case['id'],'capabilities':selected})
            for g,target in case['expected'].items():
                labels+=1;positive+=target;hit+=ok and target and g in selected
                extra+=ok and not target and g in selected;correct+=ok and ((g in selected)==target)
                if not ok or ((g in selected)!=target):fails.append({'id':case['id'],'group':g,'expected':target,'selected':g in selected})
        summary[variant]={'valid':valid,'cases':len(cases),'correct':correct,'labels':labels,'positive_selected':hit,'positive':positive,'extra':extra,'failures':fails,'live_menus':menus}
    save(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def run(output):
    frozen=read(output/'frozen.json')
    for p,h in frozen['files'].items():assert sha(p)==h,p
    with (output/'attempt.json').open('x') as f:json.dump({'paid_calls':0,'odoo_calls':0},f)
    spec=importlib.util.spec_from_file_location('local_laya_worker',ROOT/'src/erp_harness/providers/laya_worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    router=worker.CapabilityRouter(read(frozen['config'])['model'],device='cuda')
    with (output/'results.jsonl').open('x',encoding='utf8') as stream:
        for case in read(output/'cases.json'):
            for variant,state in case['variants'].items():
                response=infer(router,state);row={'id':case['id'],'variant':variant,'response':response}
                stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                print(json.dumps({'id':case['id'],'variant':variant,'status':response['status'],'selected':response.get('capabilities')}),flush=True)
    summarize(output)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','summarize']);p.add_argument('--output',type=Path,required=True);p.add_argument('--pilot',action='store_true');p.add_argument('--variants',nargs='+',default=['clean_v7_shape','context_v12','clean_v7_compact']);a=p.parse_args()
    {'prepare':lambda:prepare(a.output,a.pilot,a.variants),'run':lambda:run(a.output),'summarize':lambda:summarize(a.output)}[a.action]()
