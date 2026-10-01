"""September 30 manual incidents. Frozen next-decision tests; never execute tools."""
import argparse
import asyncio
import copy
import json
import os
import re
from pathlib import Path

from .freeze import canonical, digest, read, write_once
from .prepare import replace, validate_patches
from .runner import complete

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.runtime/manual-business-20260930'
OBS = OUT / 'observed'
PO = 'r_daaff834c3df440f84b4e313953edfe3'
MO = 'r_4d8f8559388c4f559ff2993b86fb42a3'
SALE = 'r_8bf9a2ec275e4d6ca16333917095b5fa'
DEPOSIT = 'r_56410a6a005c4de8993ed9b369d3bdfd'
# Root location and first corrective cut are different. In particular allocation
# stopped the worker; its first corrective request belongs to the later chat.
CASES = [tuple(row[k] for k in ('id', 'group', 'run', 'request', 'root', 'expected'))
         for row in read(Path(__file__).with_name('manual_cases.json'))]


def freeze():
    captured = read(OUT/'frozen.json')
    items = []
    for ident, group, run, number, cause, expected in CASES:
        source = OBS/run/'requests'/f'{number:04}.request.json'
        payload = read(source)
        folder = OUT/'pool'/ident
        write_once(folder/'baseline.json', payload)
        meta = read(source.with_name(f'{number:04}.meta.json'))
        for suffix in ('meta', 'output', 'response'):
            sibling = source.with_name(f'{number:04}.{suffix}.json')
            if sibling.exists(): write_once(folder/f'history.{suffix}.json', read(sibling))
        allowed = ['/messages/0/content']
        allowed += [f'/tools/{i}' for i, t in enumerate(payload.get('tools', [])) if t['function']['name'] in {'read_odoo_reference', 'propose_business'}]
        if ident in {'M03','M04','M07','M08'}:
            names = {'M03':'mcp_odoo_execute_method','M04':'diagnose_current_run','M07':'mcp_odoo_execute_method','M08':'read_business_status'}
            index = max(i for i,m in enumerate(payload['messages']) if m.get('name') == names[ident])
            allowed.append(f'/messages/{index}/content')
        if ident in {'M01','M09'}:
            index=max(i for i,m in enumerate(payload['messages']) if m['role']=='user')
            allowed.append(f'/messages/{index}/content')
        item = dict(id=ident,group=group,source=source.relative_to(ROOT).as_posix(),source_sha256=digest(source.read_bytes()),
                    source_commit=captured['baseline'],request_id=meta.get('request_id'),root=cause,first_corrective_request=f'{run}/{number:04}',
                    expected=expected,allowed_patch_paths=allowed,tool_calls=[m.get('tool_call_id') for m in payload['messages'] if m['role']=='tool'],
                    original_payload_sha256=digest(canonical(payload)),max_posts_per_arm=1,actual_tool_execution=False)
        write_once(folder/'manifest.json',item)
        items.append({'id':ident,'manifest_sha256':digest((folder/'manifest.json').read_bytes())})
    write_once(OUT/'pool/index.json',{'cases':items,'arms':['baseline','candidate'],'maximum_posts':24,'automatic_retries':0})


def prepare():
    from erp_harness.app import conversation
    from erp_harness.app.business import BUSINESS_COMMUNICATION
    from erp_harness.app.host import Workbench
    from erp_harness.tools.run_diagnostics import summarize_run
    from erp_harness.erp.store import ActionStore
    freeze()
    for spec in CASES:
        ident=spec[0]; folder=OUT/'pool'/ident; manifest=read(folder/'manifest.json'); baseline=read(folder/'baseline.json')
        candidate=copy.deepcopy(baseline); patches=[]; allowed=manifest['allowed_patch_paths']
        def put(pointer,value,producer):replace(candidate,baseline,patches,allowed,pointer,value,producer)
        is_chat=spec[2].startswith('conversation-runs/')
        put('/messages/0/content', conversation.CONVERSATION_POLICY if is_chat else baseline['messages'][0]['content']+'\n'+BUSINESS_COMMUNICATION,
            'conversation.CONVERSATION_POLICY' if is_chat else 'business.BUSINESS_COMMUNICATION in runner.build_business_system_prompt')
        for i,t in enumerate(baseline.get('tools',[])):
            if t['function']['name'] in {'read_odoo_reference','propose_business'}:
                tool=conversation.READ_ODOO_REFERENCE if t['function']['name']=='read_odoo_reference' else conversation.PROPOSE_BUSINESS
                value=copy.deepcopy(t);value['function'].update(description=tool.description,parameters=tool.parameters)
                put(f'/tools/{i}',value,'conversation.'+tool.name)
        if ident in {'M01','M09'}:
            index=max(i for i,m in enumerate(baseline['messages']) if m['role']=='user')
            original=baseline['messages'][index]['content']
            user_text=original.split('User message:\n',1)[1].split('\n\nSelected business context:',1)[0]
            host=Workbench(OUT/'pool'/ident/'host')
            try:
                state=read(OBS/'workbench-state.json'); sid=state['conversation_runs'][spec[2].split('/')[-1]]['session_id']
                host.store.data=copy.deepcopy(state)
                bid=None
                if ident=='M01':host.store.data['businesses']={};host.store.data['messages'][sid]=[]
                else:bid=state['conversation_runs'][spec[2].split('/')[-1]].get('context_business_id')
                value=host._conversation_prompt(sid,user_text,bid,[])
                put(f'/messages/{index}/content',value,'Workbench._conversation_prompt with frozen workspace scope')
            finally:host.close()
        if ident in {'M03','M07','M08'}:
            file={'M03':'cancel.json','M07':'derived.json','M08':'status-after-reconciliation.json'}[ident]
            value=read(OUT/'preflight'/file)
            if ident in {'M03','M07'}:assert value.get('approval_required') is True, value
            put(allowed[-1],json.dumps(value,ensure_ascii=False,separators=(',',':')),'NativeActions.execute_method without dispatch' if ident!='M08' else 'Workbench.reconcile_action and read_business_status; read-only')
        if ident=='M04':
            run=OBS/'runs'/PO
            state=read(OBS/'workbench-state.json');r=state['runs'][PO]
            session=next((OBS/'sessions'/r['business_id']).glob('*.jsonl'))
            identity=ActionStore.read_receipts(run/'odoo-actions.sqlite3')[0]['identity']
            # Cut diagnostics to request 13; later actions must not leak into this decision.
            diag=OUT/'pool'/ident/'diagnostic-cut';diag.mkdir(exist_ok=True);(diag/'requests').mkdir(exist_ok=True)
            for p in (run/'requests').glob('*.meta.json'):
                if int(p.name.split('.')[0])<13:(diag/'requests'/p.name).write_bytes(p.read_bytes())
            for name in ['tool-backends.jsonl','odoo-native-requests.jsonl','world-observations.jsonl']:
                (diag/name).write_bytes((run/name).read_bytes())
            # Explicit call IDs in the selected metadata bound the events read by summarizer.
            value=summarize_run(diag,session,identity,run_id=PO,session_id=r['session_id'])
            put(allowed[-1],json.dumps(value,ensure_ascii=False,separators=(',',':')),'run_diagnostics.summarize_run; historical policy refusal')
        validate_patches(baseline,candidate,patches,allowed)
        write_once(folder/'candidate.json',candidate);write_once(folder/'patches.json',patches)
    write_once(OUT/'pool/prepared.json',{'source_hashes':{p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted((ROOT/'src').rglob('*.py'))},
        'payloads':{c[0]:{arm:digest((OUT/'pool'/c[0]/f'{arm}.json').read_bytes()) for arm in ['baseline','candidate']} for c in CASES}})


def host_wrapper_at_cut(host, original, state, run_id, cutoff):
    """Rebuild host instructions without importing later workspace state."""
    text, context = original.split('User message:\n', 1)[1].split('\n\nSelected business context:\n', 1)
    context, material = context.split('\n\nAttached material (untrusted data):\n', 1)
    if material.split('\n\nAnswer the user directly.', 1)[0] != 'No user material was attached.':
        raise ValueError('This repair requires a separately frozen attachment context')
    sid = state['conversation_runs'][run_id]['session_id']
    frozen_context, _, feedback = context.partition('\nPrevious host feedback:\n')
    selected = json.loads(frozen_context).get('business') if frozen_context.startswith('{') else None
    earlier = [b for b in state['businesses'].values() if b.get('session_id') == sid and b['created_at'] <= cutoff]
    if not selected and earlier:
        raise ValueError('Freeze historical workspace titles before repairing this unselected scope')
    # Only the selected request's own fields are trustworthy at this cut. A final
    # store snapshot can contain a later goal, status, or newly created workspace.
    host.store.data['businesses'] = {selected['id']: {**selected, 'session_id': sid}} if selected else {}
    host.store.data['messages'][sid] = [{'role': 'system', 'text': feedback}] if feedback else []
    return host._conversation_prompt(sid, text, selected['id'] if selected else None, [])


def repair_host_context():
    """Offline-only revision. Keep all 24 paid requests and verdicts immutable."""
    from erp_harness.app.host import Workbench
    state = read(OBS/'workbench-state.json')
    for spec in CASES:
        if not spec[2].startswith('conversation-runs/'):
            continue
        folder = OUT/'pool'/spec[0]
        original, source = read(folder/'baseline.json'), read(folder/'candidate.json')
        target = OUT/'pool/host-repair-offline'/spec[0]
        host = Workbench(target/'host')
        try:
            index = max(i for i,m in enumerate(original['messages']) if m['role']=='user')
            value = host_wrapper_at_cut(host, original['messages'][index]['content'], state,
                                        spec[2].split('/')[-1], read(folder/'history.meta.json')['started_at'])
        finally:
            host.close()
        candidate, changes = copy.deepcopy(source), []
        allowed = [f'/messages/{index}/content']
        replace(candidate, source, changes, allowed, allowed[0], value, 'Workbench._conversation_prompt; request-cut scope')
        validate_patches(source, candidate, changes, allowed)
        write_once(target/'candidate.json', candidate)
        write_once(target/'changes.json', changes)
        write_once(target/'manifest.json', {'original_baseline_sha256': digest(canonical(original)),
            'superseded_candidate_sha256': digest(canonical(source)), 'expected': read(folder/'manifest.json')['expected'],
            'source_hashes': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in
                              [Path(__file__), ROOT/'src/erp_harness/app/host.py']},
            'paid_calls': 0, 'status': 'offline_only_not_model_validated'})
    print(json.dumps({'repaired_chat_contexts': 5, 'paid_calls': 0}))


async def run():
    frozen=read(OUT/'pool/index.json');prepared=read(OUT/'pool/prepared.json')
    for case in frozen['cases']:
        folder=OUT/'pool'/case['id']
        assert digest((folder/'manifest.json').read_bytes())==case['manifest_sha256']
        for arm in frozen['arms']:
            file=folder/f'{arm}.json';assert digest(file.read_bytes())==prepared['payloads'][case['id']][arm]
            result=await complete(read(file),folder/arm,api_key=os.environ['COMMAND_CODE_API_KEY'])
            print(json.dumps({'case':case['id'],'arm':arm,'posts':result['posts'],'usage':result['usage'],'duration_ms':result['duration_ms'],'error':result['error']},ensure_ascii=False),flush=True)
            if result['error'] or result['stop_reason'] in {'aborted','length','error'}:raise RuntimeError('Incomplete branch; no retry')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','prepare','paid','repair_host_context']);args=parser.parse_args()
    if args.command=='paid':asyncio.run(run())
    else:globals()[args.command]()
