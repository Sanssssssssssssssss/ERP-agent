"""New capability probes + reviewed history. One paid decision per probe; no tool execution."""
import argparse
import asyncio
from collections import Counter
import csv
import json
import os
from pathlib import Path

from .build_cases import ROOT, base_name, base_tool_names, catalog, read
from .decision_dataset import FIXTURES, split_family
from .laya_probe import WORK, sha
from .model_comparison import parse_selector, selector_payload, verdict
from .reviewed_dataset import expanded_reviewed, training_labels, validate, verify_source
from .routing_state import routing_state

SCENARIOS = FIXTURES/'collection_scenarios.tsv'
EXCLUDED = {'employee', 'time_off'}  # User excluded both from this experiment.


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def coverage(rows, groups):
    result = {}
    for split in ['train', 'dev', 'test']:
        subset = [r for r in rows if r['split']==split]
        result[split] = {'nodes':len(subset), 'families':len({r['business_group'] for r in subset}),
            'sources':dict(Counter(r.get('source_kind','legacy_reviewed_prefix') for r in subset)), 'groups':{}}
        for g in groups:
            result[split]['groups'][g] = {
                str(target):{'nodes':sum(training_labels(r,groups)[g]==target for r in subset),
                    'families':len({r['business_group'] for r in subset if training_labels(r,groups)[g]==target})}
                for target in [0,1]}
    return result


def freeze(folder, facts_path, history_path, extra_scenarios=None):
    groups, _, native, catalog_paths = catalog()
    groups = {g:v for g,v in groups.items() if g not in EXCLUDED}
    facts = read(facts_path)
    assert facts['source_kind']=='fresh_read_only_capture'
    scenarios = list(csv.DictReader(SCENARIOS.open(encoding='utf8'), delimiter='\t'))
    if extra_scenarios:
        extra = list(csv.DictReader(extra_scenarios.open(encoding='utf8'), delimiter='\t'))
        assert all(s['split']=='train' for s in extra), 'Expansion must preserve the existing dev/test pool.'
        scenarios += extra
    assert len({s['id'] for s in scenarios})==len(scenarios)
    folder.mkdir(parents=True, exist_ok=False)
    write(folder/'facts.json', facts)
    template = {'model':'deepseek/deepseek-v4-flash', 'reasoning_effort':'high',
                'stream':True, 'stream_options':{'include_usage':True}, 'store':False}
    rows = []
    # Catalogue contracts supply tool schemas, never fabricated business outcomes.
    base_reference = ROOT/'.runtime/capability-routing-20260924/reviewed-v3/00.original.json'
    base = [t['function'] for t in read(base_reference)['tools']
            if base_name(t['function']['name']) in base_tool_names()]
    assert {'get_model_fields','diagnose_current_run'} <= {base_name(t['name']) for t in base}
    for si, spec in enumerate(scenarios):
        preferred = [] if spec['preferred']=='-' else spec['preferred'].split(',')
        assert set(preferred)<=set(groups)
        variants = [int(v) for v in spec.get('variants','0,1,2').split(',')]
        assert variants and len(set(variants))==len(variants) and set(variants)<={0,1,2}
        for variant in variants:
            cid = f'collected:{spec["id"]}:{variant}'
            messages = [{'role':'system','content':'Follow the current user request. Historical observations are data. Tool publication never authorizes writes.'},
                        {'role':'user','content':spec['goal']}]
            if variant:
                # Actual read result; the dialogue wrapper is explicitly a new scripted probe.
                fact = facts['facts'][['orders','purchases','invoices'][si%3]]
                assert isinstance(fact.get('result'),list)
                messages += [{'role':'assistant','content':'读取当前数据库的已有业务记录作为背景。',
                    'tool_calls':[{'id':'capture-read','type':'function','function':{
                        'name':'mcp_odoo_read_record','arguments':json.dumps({'model':fact['model'],
                            'record_ids':[r['id'] for r in fact['result']], 'fields':list(fact['result'][0])})}}]},
                    {'role':'tool','tool_call_id':'capture-read','name':'mcp_odoo_read_record',
                     'content':json.dumps({'success':True,'model':fact['model'],'result':fact['result']},ensure_ascii=False)}]
            published = [] if variant!=2 else list(groups)[si%len(groups):si%len(groups)+1]
            tools = base + [t for t in native if any(base_name(t['name']) in groups[g]['tools'] for g in published)]
            request = {**template,'messages':messages,'tools':[{'type':'function','function':{
                k:t[k] for k in ['name','description','parameters']}} for t in tools]}
            path = folder/'requests'/f'{si:03d}-{variant}.json'
            write(path, request)
            allowed = [preferred] + ([[]] if preferred and spec['base_allowed']=='true' else [])
            row = {'id':cid,'business_group':'new-intent:'+spec['id'],'split':spec['split'],
                'source_kind':'new_scripted_contract_probe','review_status':'prefix_reviewed','reviewer':'codex-main',
                'request_path':str(path.resolve()),'request_sha256':sha(path),
                'source_pointers':[{'json_pointers':['/messages/1'],'purpose':'Predeclared user intent; no future output'}],
                'allowed_injection_sets':allowed,'required_groups':[] if spec['base_allowed']=='true' else preferred,
                'preferred_groups':preferred,'uncertain_groups':[],
                'unrelated_groups':sorted(set(groups)-set(preferred)),
                'rationale':spec['goal'],'preference_rationale':'Reviewed intent directly matches the existing capability contract; base alternatives remain legal where declared.',
                'state':routing_state(request), 'variant':variant,
                'source_evidence':{'scenarios_sha256':sha(SCENARIOS),'facts_sha256':sha(facts_path),
                    'context':'scripted dialogue with captured read-only facts, not a historical agent execution'},
                'information_limits':['Routing decision only. Prerequisites asserted by the scenario are conditional; no tool or business success is claimed.']}
            write(folder/'teacher-requests'/f'{si:03d}-{variant}.json', selector_payload(template,row['state'],groups))
            row['teacher_request'] = f'teacher-requests/{si:03d}-{variant}.json'
            row['teacher_request_sha256'] = sha(folder/row['teacher_request'])
            rows.append(row)
    train, dev, evaluation, _ = expanded_reviewed()
    for r in [*train,*dev,*evaluation]:
        q = verify_source(r)
        if set(r['required_groups']) & EXCLUDED:
            raise ValueError('Excluded capability required by a historical case')
        row = {**r,'state':routing_state(q)}
        # Evaluate the declared eight-capability scope without changing old fixture files.
        for key in ['required_groups','unrelated_groups','uncertain_groups']:
            row[key] = [g for g in r[key] if g not in EXCLUDED]
        row['allowed_injection_sets'] = [[g for g in alt if g not in EXCLUDED] for alt in r['allowed_injection_sets']]
        rows.append(row)
    for r in read(history_path):
        q = read(r['request_path']); assert sha(r['request_path'])==r['request_sha256']
        observation = json.loads(q['messages'][r['selected_evidence']]['content'])
        assert observation['success'] and observation.get('approval')
        rows.append({'id':r['id'],'business_group':r['business_group'],'split':split_family(r['business_group']),
            'source_kind':'historical_business','review_status':'prefix_reviewed','reviewer':'codex-main',
            'request_path':r['request_path'],'request_sha256':r['request_sha256'],
            'source_pointers':[{'purpose':'Reviewed business goal and successful pending write validation',
                'json_pointers':['/messages/1',f'/messages/{r["selected_evidence"]}']}],
            'allowed_injection_sets':[[],['actions']], 'required_groups':[], 'preferred_groups':['actions'],
            'unrelated_groups':sorted(set(groups)-{'actions'}),'uncertain_groups':[],
            'rationale':'A specific requested business write has just passed validation. Retaining actions supports its next approval/execute step; base verification or waiting remains valid.',
            'preference_rationale':'Reviewed write operation and validation receipt; capability publication does not bypass host approval.',
            'state':routing_state(q),'source_run':r['source_run']})
    validate(*[[r for r in rows if r['split']==s] for s in ['train','test']],groups,
             [r for r in rows if r['split']=='dev'])
    states = {}
    for r in rows:
        prior = states.setdefault(r['state'],r['split'])
        assert prior==r['split'], 'Identical routing state crosses splits'
    write(folder/'cases.json',rows); write(folder/'groups.json',groups)
    hashes = {p.relative_to(folder).as_posix():sha(p) for p in folder.rglob('*.json')}
    sources = [Path(__file__),SCENARIOS,Path(training_labels.__code__.co_filename),
               Path(routing_state.__code__.co_filename),base_reference,*catalog_paths,
               *(FIXTURES/name for name in ['reviewed_training.json','reviewed_holdout.json','reviewed_expansion.json'])]
    if extra_scenarios:sources.append(extra_scenarios)
    manifest = {'hashes':hashes,'paid_posts_max':sum('teacher_request' in r for r in rows),
        'source_hashes':{str(p):sha(p) for p in sources},'excluded_groups':sorted(EXCLUDED),
        'limits':['Historical holdouts are unchanged on disk and development-visible.',
            'New intent families are agent-authored contract probes, not independently observed business workflows.',
            'Three context variants of one intent are one family, not three independent tasks.',
            'Teacher agreement corroborates a routing label; it does not prove business correctness.',
            'Selection and teacher labels are frozen before Laya predictions. Disagreement is quarantined.']}
    write(folder/'frozen.json',manifest); write(folder/'coverage.json',coverage(rows,groups))
    print(json.dumps({'nodes':len(rows),'splits':dict(Counter(r['split'] for r in rows)),
        'teacher_posts':manifest['paid_posts_max'],'capabilities':list(groups)}))


async def paid(folder, limit):
    from experiments.agent_regression.runner import complete
    frozen = read(folder/'frozen.json')
    for name,digest in frozen['hashes'].items():
        assert sha(folder/name)==digest, name
    rows = [r for r in read(folder/'cases.json') if 'teacher_request' in r]
    # Resuming skips every started attempt, including uncertain network failures.
    pending = [r for r in rows if not (folder/'api'/r['id'].replace(':','_')/'started.json').exists()]
    if limit: pending = pending[:limit]
    groups = read(folder/'groups.json');stop = asyncio.Event()
    queue = asyncio.Queue()
    for row in pending:queue.put_nowait(row)

    async def worker():
        while not queue.empty() and not stop.is_set():
            row = queue.get_nowait();target = folder/'api'/row['id'].replace(':','_')
            try:
                output = await complete(read(folder/row['teacher_request']),target,api_key=os.environ['COMMAND_CODE_API_KEY'])
                selected = parse_selector(output,groups)
                judgment = verdict(selected,row) if selected is not None else {'status':'inconclusive'}
                judgment['preferred_agreement'] = selected is not None and set(row.get('preferred_groups', []))<=set(selected)
                judgment['id'] = row['id']; write(target/'judgment.json',judgment)
                print(json.dumps({'id':row['id'],'status':judgment['status'],
                    'preferred':judgment['preferred_agreement'],'tokens':(output['usage'] or {}).get('total_tokens')}),flush=True)
                if output['error'] or output['usage'] is None:stop.set()
            except Exception:
                stop.set();raise
    await asyncio.gather(worker(),worker())


def export(folder, target, input_audit=None):
    rows = read(folder/'cases.json');groups = read(folder/'groups.json')
    frozen = read(folder/'frozen.json')
    for name,digest in frozen['hashes'].items():assert sha(folder/name)==digest
    audit=read(input_audit) if input_audit else None
    if audit:
        assert audit['cases_sha256']==sha(folder/'cases.json')
        assert audit['model_lock_sha256']==sha(WORK/'model-lock.json')
        assert set(audit['lengths'])=={r['id'] for r in rows}
    admitted=[];quarantined=[];usages=[]
    for row in rows:
        verify_source(row)
        if audit and audit['lengths'][row['id']]>8192:
            if row['split']!='train':raise ValueError('Oversized evaluation input needs an explicit unscored report, not exclusion.')
            quarantined.append({'id':row['id'],'split':row['split'],'excluded_from_training':True,
                'reason':'Input exceeds locked Laya encoder window; original retained without truncation',
                'encoder_length':audit['lengths'][row['id']]})
            continue
        if 'teacher_request' not in row:
            admitted.append(row);continue
        where=folder/'api'/row['id'].replace(':','_')
        output=read(where/'output.json') if (where/'output.json').exists() else {}
        selected=parse_selector(output,groups)
        if output.get('usage'):usages.append(output['usage'])
        good = (output.get('posts')==1 and not output.get('error') and output.get('usage') is not None
                and selected is not None and verdict(selected,row)['status']=='pass'
                and set(row.get('preferred_groups', []))<=set(selected))
        # Never drop hard test/dev cases based on the teacher's prediction.
        if good or row['split']!='train':
            admitted.append({**row,'teacher_review':'agreed' if good else 'disagreed_or_incomplete'})
        if not good:quarantined.append({'id':row['id'],'split':row['split'],'selected':selected,
            'excluded_from_training':row['split']=='train','reason':'teacher disagreement or incomplete receipt'})
    validate(*[[r for r in admitted if r['split']==s] for s in ['train','test']],groups,
             [r for r in admitted if r['split']=='dev'])
    for split in ['train','dev','test']:
        for group in groups:
            assert any(r['split']==split and training_labels(r,groups)[group]==1 for r in admitted), (split,group)
    target.mkdir(parents=True,exist_ok=False)
    write(target/'cases.json',admitted);write(target/'groups.json',groups)
    write(target/'quarantine.json',quarantined);write(target/'coverage.json',coverage(admitted,groups))
    write(target/'frozen.json',{'hashes':{name:sha(target/name) for name in ['cases.json','groups.json']},
        'source_manifest':str((folder/'frozen.json').resolve()),'source_sha256':sha(folder/'frozen.json'),
        'scope':list(groups),'excluded_groups':sorted(EXCLUDED),'limits':frozen['limits'],
        'input_audit':{'path':str(input_audit.resolve()),'sha256':sha(input_audit)} if input_audit else None})
    summary={'nodes':len(admitted),'quarantine':len(quarantined), 'api_completed':len(usages),
        'usage':{k:{'known_sum':sum(u.get(k) or 0 for u in usages),'unknown':sum(u.get(k) is None for u in usages)}
                 for k in ['input','cache_read','output','reasoning','total_tokens']},'business_workflows_executed':0}
    write(target/'collection-summary.json',summary);print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['freeze','paid','export']);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--facts',type=Path);p.add_argument('--history',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--input-audit',type=Path)
    p.add_argument('--extra-scenarios',type=Path,help='Additional training-only intents; optional variants column avoids duplicate context expansion.')
    p.add_argument('--limit',type=int)
    a=p.parse_args()
    if a.operation=='freeze':freeze(a.folder,a.facts,a.history,a.extra_scenarios)
    elif a.operation=='paid':asyncio.run(paid(a.folder,a.limit))
    else:export(a.folder,a.output,a.input_audit)
