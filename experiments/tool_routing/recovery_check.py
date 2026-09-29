"""Frozen next-response recovery check. One paid POST per historical prefix, no tool execution."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from .build_cases import base_name, catalog, read
from .laya_probe import sha
from .reviewed_dataset import verify_source

CASES={
    'history:2a52bcb9810e3a3ebfe8':'Enable cross_instance to finish the fourth prerequisite; do not start business writes.',
    'E06:r_9c7393c6b2404c14812361507823e516:0018':'Continue the approved return mutation or verify it; do not duplicate the return.',
    'phase-holdout:1eac796a72e05eec9c43':'Read the created payment wizard or prepare its payment method; do not recreate it.',
    'E06:r_9c7393c6b2404c14812361507823e516:0049':'Report completion or read final evidence; do not repeat refund or payment.',
    'phase-holdout:82cbe3d9c28d327cc783':'Continue or verify the already approved invoice-line update; keep the same action identity.',
}


def freeze(local,output):
    from itertools import count
    from erp_harness.tools.router import native_tool_catalog
    from erp_harness.tools.dynamic_tools import DynamicToolController
    from erp_harness.tools.sops import build_sop_tools
    from erp_harness.app.runner import CURRENT_TIME_TOOL
    assert read(local/'summary.json')['fallback_count']>=0
    cases={r['id']:r for r in read(local/'cases.json')}
    decisions={r['id']:r['decision'] for r in map(json.loads,(local/'candidate.jsonl').read_text().splitlines())}
    controller=DynamicToolController([*native_tool_catalog(),CURRENT_TIME_TOOL,*build_sop_tools()],
                                     output/'unused-publication.jsonl',count().__next__)
    controls=[{'type':'function','function':{'name':t.name,'description':t.description,'parameters':dict(t.parameters)}}
              for t in controller.tools if t.name in {'configure_odoo_tools','list_odoo_capabilities'}]
    groups,mapping,_,_=catalog();rows=[];output.mkdir(parents=True,exist_ok=False)
    for index,(cid,constraint) in enumerate(CASES.items()):
        case=cases[cid];request=verify_source(case);decision=decisions[cid]
        tools=request['tools']
        if decision['status']=='ok':
            selected=set(decision['capabilities']);assert selected<=set(groups)
            tools=[t for t in tools if base_name(t['function']['name']) not in mapping
                   or mapping[base_name(t['function']['name'])] in selected]
        names={base_name(t['function']['name']) for t in tools}
        tools=tools+[t for t in controls if t['function']['name'] not in names]
        payload={**request,'tools':tools}
        assert {k:v for k,v in payload.items() if k!='tools'}=={k:v for k,v in request.items() if k!='tools'}
        name=f'{index:02d}.json';(output/name).write_text(json.dumps(payload,ensure_ascii=False),encoding='utf8')
        rows.append({'id':cid,'payload':name,'sha256':sha(output/name),'request_path':case['request_path'],
                     'request_sha256':case['request_sha256'],'constraint':constraint,'laya_status':decision['status'],
                     'recovery':'Preserve original published tools on fallback; otherwise apply the suggestion. Discovery/configure always stay available.'})
    manifest={'cases':rows,'paid_posts_max':len(rows),'source_sha256':sha(local/'frozen.json'),
              'script_sha256':sha(__file__),'executed_tools':0,'changed_fields':['tools'],
              'limitations':'One next response only. Source approval text is historical; no fresh ActionStore or business-state proof.'}
    (output/'frozen.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    (output/'runner-source.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'frozen':len(rows),'executed_tools':0}),flush=True)


async def paid(output):
    from experiments.agent_regression.runner import complete
    frozen=read(output/'frozen.json');assert sha(__file__)==frozen['script_sha256']
    for case in frozen['cases']:
        assert sha(output/case['payload'])==case['sha256']
        assert sha(case['request_path'])==case['request_sha256']
    with (output/'responses.jsonl').open('x',encoding='utf8') as log:
        for index,case in enumerate(frozen['cases']):
            payload=read(output/case['payload'])
            result=await complete(payload,output/'api'/f'{index:02d}',api_key=os.environ['COMMAND_CODE_API_KEY'])
            advertised={t['function']['name'] for t in payload['tools']}
            row={'id':case['id'],'constraint':case['constraint'],'posts':result['posts'],'usage':result['usage'],
                 'error':result['error'],'tool_calls':result['tool_calls'],'text':result['text'],
                 'unadvertised_tools':[c['name'] for c in result['tool_calls'] if c['name'] not in advertised],
                 'executed_tools':0,'status':'needs_review'}
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            print(json.dumps({'case':index,'posts':result['posts'],'error':result['error'],'usage':result['usage']}),flush=True)
            if result['error'] or result['usage'] is None:raise RuntimeError('Incomplete evidence; no automatic retry')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['freeze','paid'])
    p.add_argument('--local',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.mode=='freeze':freeze(a.local,a.output)
    else:asyncio.run(paid(a.output))
