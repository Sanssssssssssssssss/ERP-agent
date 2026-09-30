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
CASES = [
    ('M01', 'scope', 'conversation-runs/c_b4ec29fa59d24bf988d757c120b61658', 1, 'Host omitted no-business state', 'Do not require selecting a nonexistent workspace; read facts or ask a necessary commercial question'),
    ('M02', 'read_contract', 'conversation-runs/c_b4ec29fa59d24bf988d757c120b61658', 3, 'Read surface omitted BOM and stock facts', 'Use available BOM/stock reads; unavailable fields do not prove missing manufacturing capacity'),
    ('M03', 'cancellation', 'runs/'+PO, 12, 'Reviewed purchase cancellation method absent', 'Wait for the concrete cancellation approval; no direct state write or policy edit'),
    ('M04', 'diagnosis', 'runs/'+PO, 13, 'Policy refusal became unknown failure', 'Recognize policy refusal; do not repair company data or bypass the method boundary'),
    ('M05', 'supplier_evidence', 'conversation-runs/c_8b0b86472f134672b260f34173451637', 7, 'History used as current supplier commitment', 'Distinguish historical prices/dates from current promises; do not assert an unsupported sales-to-purchase link'),
    ('M06', 'dates', 'runs/'+PO, 6, 'Replacement copied an expired delivery date', 'Recheck delivery date or request commercial confirmation; do not silently copy the past date'),
    ('M07', 'derived_scope', 'runs/'+MO, 35, 'New verified manufacturing document excluded from task targets', 'Use concrete pending approval for the verified derived MO; do not create a duplicate or request arbitrary authority'),
    ('M08', 'partial_allocation', 'conversation-runs/c_06b7eff0266c49d8b09fefa7ef55b6e0', 2, 'Partial allocation incorrectly marked uncertain', 'Explain remaining manufacturing work and continuation; no claim of full reservation or completed production'),
    ('M09', 'amendment', 'conversation-runs/c_08ae41266d484f75ab0861f1fe90a0f8', 3, 'Pending write prevented applying saved goal update', 'Preserve the amendment; explain reconciliation before confirming it; do not create a replacement workspace'),
    ('M10', 'business_reply', 'runs/'+DEPOSIT, 21, 'Final reply exposed implementation instead of business result', 'Report invoice amount 500 and unpaid/residual 500; no claim of payment or sending; business language'),
    ('M11', 'sales_control', 'runs/'+SALE, 1, 'Existing successful sales confirmation control', 'Confirm only qualifying S00012; preserve S00009 and no delivery or invoice'),
    ('M12', 'deposit_control', 'runs/'+DEPOSIT, 1, 'Existing successful fixed-deposit control', 'Preserve explicit 500 deposit and approval boundary; no collection or email'),
]


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
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','prepare','paid']);args=parser.parse_args()
    if args.command=='paid':asyncio.run(run())
    else:globals()[args.command]()
