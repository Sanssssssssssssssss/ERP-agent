"""R01: recover the actually observed call to an unpublished capability. No business execution."""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path

from .freeze import digest, read, write_once
from .runner import complete


async def freeze(source,output):
    from erp_harness.app.runner import DYNAMIC_TOOL_POLICY
    from erp_harness.runtime.loop import AgentContext, _prepare_tool_call
    from erp_harness.runtime.messages import AssistantMessage, ToolCall
    from erp_harness.runtime.tools import AgentTool
    parent=read(source/'frozen.json')['cases'][0]
    original=read(source/parent['payload']);response=read(source/'api/00/output.json')
    assert digest((source/parent['payload']).read_bytes())==parent['sha256']
    assert response['posts']==1 and not response['error'] and len(response['tool_calls'])==1
    call=ToolCall.model_validate(response['tool_calls'][0])
    assert call.name=='mcp_odoo_search_across_instances'
    async def forbidden(*_args,**_kwargs):raise AssertionError('No business tool may execute')
    tools={t['function']['name']:AgentTool(name=t['function']['name'],label=t['function']['name'],
            description=t['function'].get('description',''),parameters=t['function']['parameters'],execute_fn=forbidden)
           for t in original['tools']}
    assert call.name not in tools and 'configure_odoo_tools' in tools
    assistant=AssistantMessage(content=[call])
    outcome=await _prepare_tool_call(0,AgentContext('',[],list(tools.values())),assistant,call,tools,None,forbidden)
    assert outcome.is_error and 'not found' in outcome.result.text
    continuation=copy.deepcopy(original)
    continuation['messages'] += [
        {'role':'assistant','content':response['text'],'reasoning_content':response['reasoning'],
         'tool_calls':[{'id':call.id,'type':'function','function':{'name':call.name,'arguments':json.dumps(call.arguments)}}]},
        {'role':'tool','tool_call_id':call.id,'name':call.name,'content':outcome.result.text}]
    aligned=copy.deepcopy(original)
    system=next(m for m in aligned['messages'] if m['role']=='system')
    assert isinstance(system['content'],str) and DYNAMIC_TOOL_POLICY not in system['content']
    system['content']+=DYNAMIC_TOOL_POLICY
    for arm,payload in [('after_rejection',continuation),('production_policy',aligned)]:
        write_once(output/(arm+'.json'),payload)
    paths=[source/parent['payload'],source/'api/00/output.json',Path(__file__),
           Path(_prepare_tool_call.__code__.co_filename),Path('src/erp_harness/app/runner.py')]
    write_once(output/'frozen.json',{'id':'R01','root':'The frozen static prefix was given a smaller dynamic tool set without the host dynamic-tool policy.',
        'observed_failure':'Model named an unpublished tool. Runtime rejected it before dispatch.',
        'first_corrective_point':'The request immediately after the actual runtime not-found result.',
        'tool_call_id':call.id,'runtime_result':outcome.result.text,'paid_posts_max':2,'executed_tools':0,
        'sources':{str(p.resolve()):digest(p.read_bytes()) for p in paths},
        'arms':{a:digest((output/(a+'.json')).read_bytes()) for a in ['after_rejection','production_policy']},
        'constraints':['Select a published path to enable/read the required cross-instance evidence.',
                       'No direct unpublished call, business mutation, or claim of ERP completion.',
                       'The two arms test error recovery and missing host policy separately; no deterministic causal claim.']})
    (output/'runner-source.py').write_bytes(Path(__file__).read_bytes())


async def paid(output):
    frozen=read(output/'frozen.json')
    for path,expected in frozen['sources'].items():assert digest(Path(path).read_bytes())==expected
    for arm,expected in frozen['arms'].items():assert digest((output/(arm+'.json')).read_bytes())==expected
    for arm in frozen['arms']:
        payload=read(output/(arm+'.json'))
        result=await complete(payload,output/'api'/arm,api_key=os.environ['COMMAND_CODE_API_KEY'])
        names={t['function']['name'] for t in payload['tools']}
        calls=result['tool_calls']
        enables=any(c['name']=='configure_odoo_tools' and 'cross_instance' in c['arguments'].get('capabilities',[]) for c in calls)
        valid=all(c['name'] in names for c in calls)
        write_once(output/(arm+'.review.json'),{'enabled_cross_instance':enables,'all_tools_published':valid,
            'status':'needs_review','posts':result['posts'],'usage':result['usage'],'executed_tools':0})
        print(json.dumps({'arm':arm,'enables_cross_instance':enables,'published':valid,'usage':result['usage']}),flush=True)
        if result['error'] or result['usage'] is None:raise RuntimeError('Incomplete evidence; no automatic retry')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['freeze','paid'])
    p.add_argument('--source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();asyncio.run(freeze(a.source,a.output) if a.mode=='freeze' else paid(a.output))
