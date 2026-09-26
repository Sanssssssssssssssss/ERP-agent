"""Rebuild historical decision inputs from as-of host evidence, never future outcomes."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from functools import lru_cache
from unittest.mock import patch

from erp_harness.app.routing_state import VERSION, build_routing_state, ledger_state, sop_requirements
from erp_harness.erp.store import ActionStore
from erp_harness.context.world import WorldStore
from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS
from erp_harness.tools.sops import SOPS
from .reviewed_dataset import verify_source

ROOT=Path(__file__).resolve().parents[2]
read=lambda p:json.loads(p.read_text(encoding='utf8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
stamp=lambda value:datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


@lru_cache(maxsize=128)
def restart_times(run):
    """A new worker's first request follows the recorded awaiting -> running transition."""
    host=run.parents[3]/'host-events.jsonl' if len(run.parents)>3 else None
    if host is None or not host.exists():
        return None
    restarts=[];pending=False;previous=None
    for line in host.read_text(encoding='utf8').splitlines():
        event=json.loads(line);data=event.get('data',{})
        if data.get('run_id')!=run.name:
            continue
        if event.get('event')=='changed' and data.get('type')=='run_changed':
            status=data.get('status')
            if status=='running' and previous in {None,'awaiting_approval'}:
                pending=True
            previous=status
        if event.get('event')=='run_trace' and data.get('type')=='request_started' and pending:
            restarts.append(stamp(data['started_at']));pending=False
    return restarts or None


def replay_world(run, cutoff, directory):
    """Replay recorded events only; no final record view or Odoo requests."""
    path=run/'world-observations.jsonl';resets=restart_times(run)
    if not path.exists() or resets is None or cutoff is None:
        return None, None
    reset=max((t for t in resets if t<=cutoff),default=0)
    # Replay has no configured ERP connection; identities come only from signed historical receipts.
    with patch('erp_harness.context.world._safe_identities',return_value=('',{})):
        world=WorldStore(directory/'world.jsonl')
    identity=None
    for line in path.read_text(encoding='utf8').splitlines():
        event=json.loads(line)
        when=stamp(event.get('finished_at',event.get('at')))
        if when>cutoff:
            continue
        if event['type']=='world_invalidation':
            for owner in event.get('identity_ids',[]):
                world._generation[owner]=world._generation.get(owner,0)+1
                for (who,_,_),record in world._records.items():
                    if who==owner:
                        record.update(stale=True,requires_reobserve=True)
                        for field in record['fields'].values(): field['stale']=True
        elif event['type']=='world_observation':
            identity=event['identity'] if identity is None else identity
            if event['identity']!=identity:
                return None,None  # Cannot establish one current host identity from this legacy evidence.
            owner=identity['identity_id']
            world._generation[owner]=max(world._generation.get(owner,0),event.get('generation',0))
            world._merge(event,recovering=when<reset)
            world._receipts[event['receipt_id']]=event
            world._by_call[event['call_id']]=event
    return world,identity


def as_of_state(path, request):
    run=path.parent.parent
    meta=path.with_name(path.name.replace('.request.','.meta.'))
    started=read(meta).get('started_at') if meta.exists() else None
    ledger=None;stage=None;cutoff=stamp(started) if started else None
    if (run/'odoo-actions.sqlite3').exists() and cutoff is not None:
        host_file=run.parent.parent/'workbench-state.json'
        host=read(host_file) if host_file.exists() else {}
        approvals={a.get('action_id'):a for a in host.get('approvals',{}).values()}
        observed=[]
        for row in ActionStore.read_receipts(run/'odoo-actions.sqlite3'):
            if row.get('created_at',float('inf'))>cutoff:
                continue
            status='unknown';approval=approvals.get(row['action_id'],{})
            decided=approval.get('decided_at')
            if row.get('finished_at') and row['finished_at']<=cutoff:
                status=row['status']
            elif row.get('sent_at') and row['sent_at']<=cutoff:
                status='sending'
            elif row.get('claimed_at') and row['claimed_at']<=cutoff:
                status='executing'
            elif decided and datetime.fromisoformat(decided.replace('Z','+00:00')).timestamp()<=cutoff:
                status='rejected' if approval.get('status')=='rejected' else 'approved'
            elif approval:
                status='pending_approval'
            observed.append({k:row[k] for k in ['action_id','payload']} | {'status':status})
        ledger=ledger_state(observed,complete=all(r['status']!='unknown' for r in observed))
        specification=run/'task-sources.json'
        if specification.exists() and (run/'instruction.txt').exists():
            spec=read(specification)
            if spec.get('instruction_sha256')==sha(run/'instruction.txt'):
                stage=spec.get('stage')  # Immutable run contract; never the final business object.
    # World may have externalized consumed reads. Restore only receipts already present in this prefix.
    request=json.loads(json.dumps(request))
    by_call={m.get('tool_call_id'):m for m in request['messages'] if m.get('role')=='tool'}
    world_file=run/'world-observations.jsonl'
    if world_file.exists():
        for line in world_file.read_text(encoding='utf8').splitlines():
            event=json.loads(line)
            if event.get('type')=='world_observation' and event.get('call_id') in by_call:
                _,payload=WorldStore._visible_payload(event)
                if payload is not None:
                    by_call[event['call_id']]['content']=json.dumps(payload,ensure_ascii=False)
    sop_file=run/'sop-events.jsonl'
    events=[json.loads(line) for line in sop_file.read_text(encoding='utf8').splitlines()] if sop_file.exists() else []
    required,_=sop_requirements([r for r in events if r.get('tool_call_id') in by_call],CAPABILITY_GROUPS,SOPS)
    # Old probes without a host SOP log still contain the exact successful contract.
    if not sop_file.exists():
        contracts=[]
        for message in by_call.values():
            try: payload=json.loads(message.get('content',''))
            except (ValueError,TypeError): continue
            if isinstance(payload,dict) and payload.get('success') is True and isinstance(payload.get('sop'),dict):
                contracts.append({'event':'end','success':True,'tool':'get_odoo_sop',
                    'tool_call_id':message['tool_call_id'],'required_tools':payload['sop'].get('required_tools',[])})
        required.update(sop_requirements(contracts,CAPABILITY_GROUPS,SOPS)[0])
    calls=next((m.get('tool_calls',[]) for m in reversed(request['messages']) if m.get('role')=='assistant'),[])
    owners={tool:g for g,spec in CAPABILITY_GROUPS.items() for tool in spec['tools']}
    for call in calls:
        name=call['function']['name'];message=by_call.get(call['id'],{})
        if message.get('content')==f'Tool {name} not found' and name.removeprefix('mcp_odoo_') in owners:
            required.add(owners[name.removeprefix('mcp_odoo_')])
    with tempfile.TemporaryDirectory(prefix='laya-state-') as temporary:
        world,identity=replay_world(run,cutoff,Path(temporary))
        return build_routing_state(request,stage=stage,ledger=ledger,world=world,identity=identity,required=required,
            goal=(run/'instruction.txt').read_text(encoding='utf8') if stage is not None else None)


def freeze(source, output, live_source=None):
    assert not output.exists()
    manifest=read(source/'frozen.json')
    assert sha(source/'cases.json')==manifest['hashes']['cases.json']
    groups=read(source/'groups.json');rows=[];revisions=[]
    for original in read(source/'cases.json'):
        row=json.loads(json.dumps(original));path=Path(row['request_path'])
        request=verify_source(row)
        row['state']=json.dumps(as_of_state(path,request),ensure_ascii=False,separators=(',',':'))
        # v2 accepted optional actions for an unfinished task; v3 measures proactive availability.
        # Keep other optional/uncertain groups untouched; this is a versioned label change, not an old score revision.
        allowed=row['allowed_injection_sets']
        if any('actions' in a for a in allowed) and 'actions' not in row['uncertain_groups']:
            row['required_groups']=sorted(set(row['required_groups'])|{'actions'})
            row['allowed_injection_sets']=[sorted(set(a)|{'actions'}) for a in allowed]
            row['allowed_injection_sets']=list({tuple(a):a for a in row['allowed_injection_sets']}.values())
            row['unrelated_groups']=[g for g in row['unrelated_groups'] if g!='actions']
            if row['required_groups']!=original['required_groups']:
                revisions.append({'id':row['id'],'old_allowed':allowed,'new_allowed':row['allowed_injection_sets'],
                    'basis':original['rationale'],'reason':'Task-scoped proactive availability, not exact next-call prediction.'})
        row.pop('teacher_request',None);row.pop('teacher_request_sha256',None)
        rows.append(row)
    if live_source:
        # These prefixes were inspected in the integration audit. Family splits remain unchanged.
        reviewed={'E01':{'split':'test','write':5,'final':13},
                  'E02':{'split':'dev','write':26,'final':61},
                  'E03':{'split':'train','write':21,'final':33}}
        for case, review in reviewed.items():
            run=live_source/case/'profile/data/runs'/read(live_source/case/'summary.json')['run_id']
            for path in sorted((run/'requests').glob('*.request.json')):
                number=int(path.name[:4]);state=as_of_state(path,read(path))
                pending=state['action_ledger']['unresolved']
                if number not in {1,review['write'],review['final']} and not (pending and all(a['status'] in {'pending_approval','approved'} for a in pending)):
                    continue
                needed=[] if number==review['final'] else ['actions']
                rows.append({'id':f'host-live:{case}:{number:04d}','business_group':'enterprise:'+case,
                    'split':review['split'],'source_kind':'historical_live_prefix','review_status':'prefix_reviewed',
                    'reviewer':'codex-main','request_path':str(path.resolve()),'request_sha256':sha(path),
                    'required_groups':needed,'allowed_injection_sets':[needed],
                    'unrelated_groups':[g for g in groups if g not in needed],'uncertain_groups':[],
                    'rationale':('Readback covers task completion; only reporting remains.' if not needed else
                        'Confirmed task still needs mutation capability; explicit live pending/approved action or reviewed unfinished work.'),
                    'source_pointers':[{'json_pointers':['/messages'],'purpose':'Complete historical prefix; no future call enters state.'}],
                    'state':json.dumps(state,ensure_ascii=False,separators=(',',':'))})
    from .reviewed_dataset import validate
    validate(*[[r for r in rows if r['split']==s] for s in ['train','test']],groups,
             [r for r in rows if r['split']=='dev'])
    output.mkdir(parents=True)
    for name,value in [('cases.json',rows),('groups.json',groups),('label-revisions.json',revisions)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    frozen={'projection':VERSION,'parent_manifest':str(source/'frozen.json'),'parent_sha256':sha(source/'frozen.json'),
        'hashes':{n:sha(output/n) for n in ['cases.json','groups.json','label-revisions.json']},
        'sources':{str(p):sha(p) for p in [Path(__file__),ROOT/'src/erp_harness/app/routing_state.py',ROOT/'src/erp_harness/context/world.py']},
        'limits':['Labels are versioned; original dataset and scores preserved. No future results enter input.',
                  'Missing historical ledger/approval timestamps remain unknown. Historical observations are not fresh reads.',
                  'Previously inspected development corpus; final live business comparison is separate.']}
    (output/'frozen.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'nodes':len(rows),'splits':dict(Counter(r['split'] for r in rows)),
        'label_revisions':len(revisions),'ledger_complete':sum(json.loads(r['state'])['action_ledger']['complete'] for r in rows)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live-source',type=Path)
    args=parser.parse_args();freeze(args.source,args.output,args.live_source)
