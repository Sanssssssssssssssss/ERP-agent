"""The model and dispatcher must share one publication snapshot. No paid calls."""
import asyncio
import json
from itertools import count
from pathlib import Path
from unittest.mock import AsyncMock

from erp_harness.app.capability_routing import LayaProvider
from erp_harness.app.model_config import provider_config
from erp_harness.erp.store import ActionStore
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.config import ProviderSettings
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.runtime.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from erp_harness.runtime.provider_events import AssistantDoneEvent
from erp_harness.runtime.session import HarnessSession, SessionConfig
from erp_harness.runtime.storage import JsonlSessionStorage
from erp_harness.tools.dynamic_tools import DynamicToolController
from tests.test_dynamic_tools import fake_tools


def setup(tmp_path):
    calls=[]
    controller=DynamicToolController(fake_tools(set(),calls),tmp_path/'dynamic-tools.jsonl',count().__next__)
    controller.bind(lambda tools: None)
    store=ActionStore(tmp_path/'actions.sqlite3')
    provider=LayaProvider(OpenAICompatibleConfig(api_key='test',base_url='http://unused.invalid'))
    provider.bind_router(controller,store,tmp_path)
    return provider,controller,store,calls


def test_published_tools_are_dispatchable_in_the_same_turn(tmp_path,monkeypatch):
    provider,controller,store,calls=setup(tmp_path)
    provider._decide=AsyncMock(side_effect=[{'status':'ok','capabilities':['actions']},{'status':'ok','capabilities':[]}])
    seen=[]
    async def response(self,**kwargs):
        seen.append({t.name for t in kwargs['tools']})
        msg=AssistantMessage(content=[ToolCall(id='write',name='mcp_odoo_execute_approved_write',arguments={})],stop_reason='toolUse') if len(seen)==1 else AssistantMessage(content='done')
        yield AssistantDoneEvent(reason=msg.stop_reason,message=msg)
    monkeypatch.setattr(OpenAICompatibleProvider,'stream_response',response)
    monkeypatch.setenv('LLM_API_KEY','test')
    async def check():
        config=provider_config('http://unused.invalid','test','openai-compatible','high')
        session=await HarnessSession.load(SessionConfig(provider=provider,model='test',system='ERP',cwd=tmp_path,
            storage=JsonlSessionStorage(tmp_path/'session.jsonl'),tools=list(controller.tools),
            auto_compact_enabled=False,extensions_enabled=False,skills_enabled=False,owns_initial_provider=True,
            provider_name='openai-compatible',provider_settings=ProviderSettings(providers=(config,)),
            runtime_provider_config=config,thinking_level='high'))
        controller.bind(session.stage_tools_for_next_turn)
        events=[e async for e in session.prompt('Confirm')]
        assert any(e.type=='agent_end' for e in events)
        await session.aclose()
        await provider.aclose()
    asyncio.run(check())
    assert 'mcp_odoo_execute_approved_write' in seen[0] and 'mcp_odoo_execute_approved_write' not in seen[1]
    assert calls.count('execute_approved_write')==1
    assert all({'configure_odoo_tools','list_odoo_capabilities'}<=names for names in seen)
    store.close()


def test_model_selection_survives_restart_without_disabling_router(tmp_path):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Read')],'tools':list(controller.tools)}
    async def check():
        configure=next(t for t in controller.tools if t.name=='configure_odoo_tools')
        await configure.execute('main-model',{'capabilities':['actions']})
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['diagnostics']})
        kwargs['tools'][:]=controller.tools
        await provider._publish(kwargs)
        assert 'mcp_odoo_execute_approved_write' in {t.name for t in kwargs['tools']}
        assert set(controller._active)=={'actions','diagnostics'}
        # An explicit removal must not be undone by the next Laya decision.
        await configure.execute('main-remove',{'capabilities':['actions']})
        kwargs['tools'][:]=controller.tools
        resumed=LayaProvider(provider._config);resumed.bind_router(controller,store,tmp_path)
        resumed._decide=AsyncMock(return_value={'status':'ok','capabilities':['diagnostics']})
        await resumed._publish(kwargs)
        assert resumed._hold_reason() is None and controller._active==('actions',)
        resumed._decide.assert_awaited_once()
        await resumed.aclose();await provider.aclose()
    asyncio.run(check())
    store.close()


def test_redundant_model_selection_pins_tools_but_not_whole_router(tmp_path):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Continue')],'tools':list(controller.tools)}
    async def check():
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['actions']})
        await provider._publish(kwargs)
        await next(t for t in controller.tools if t.name=='configure_odoo_tools').execute('model-same',{'capabilities':['actions']})
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':[]})
        await provider._publish(kwargs)
        assert controller._active==('actions',) and provider._hold_reason() is None
        await provider.aclose()
    asyncio.run(check());store.close()


def test_pending_ledger_holds_and_router_failure_preserves_tools(tmp_path,monkeypatch):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Continue')],'tools':list(controller.tools)}
    initial=list(kwargs['tools'])
    async def check():
        for status in ['pending_approval','approved','executing','sending','needs_reconciliation','unknown']:
            monkeypatch.setattr(ActionStore,'read_receipts',staticmethod(lambda _p:[{'status':status}]))
            provider._decide=AsyncMock(side_effect=AssertionError('Unresolved write must retain tools'))
            await provider._publish(kwargs)
            assert kwargs['tools']==initial
        monkeypatch.setattr(ActionStore,'read_receipts',staticmethod(lambda _p:[]))
        provider._decide=AsyncMock(return_value={'status':'fallback','candidate_capabilities':[]})
        await provider._publish(kwargs)
        assert kwargs['tools']==initial and provider._hold_reason()=='host_takeover'
        await provider.aclose()
    asyncio.run(check())
    store.close()


def test_publication_receipt_failure_rolls_back_both_tool_views(tmp_path,monkeypatch):
    provider,controller,store,_=setup(tmp_path);staged=[]
    controller.bind(lambda tools:staged.append(tuple(tools)))
    original=controller._log
    def log(row):
        if row.get('event')=='end' and row.get('tool')=='configure_odoo_tools':
            raise OSError('simulated end receipt failure')
        original(row)
    monkeypatch.setattr(controller,'_log',log)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Confirm')],'tools':list(controller.tools)}
    initial=list(kwargs['tools'])
    async def check():
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['actions']})
        await provider._publish(kwargs)
        assert provider.failed and kwargs['tools']==initial and list(staged[-1])==initial
        assert controller._active==()
        await provider.aclose()
    asyncio.run(check());store.close()


def test_unwritable_router_receipts_keep_original_routing(tmp_path,monkeypatch):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Read')],'tools':list(controller.tools)}
    original=Path.write_text
    def fail(path,*args,**kw):
        if path.parent==provider.directory:raise OSError('simulated read-only directory')
        return original(path,*args,**kw)
    monkeypatch.setattr(Path,'write_text',fail)
    async def check():
        provider._decide=AsyncMock(side_effect=AssertionError('No selector after failed provenance'))
        await provider._publish(kwargs)
        assert provider.failed and kwargs['tools']==list(controller.tools)
        await provider.aclose()
    asyncio.run(check());store.close()


def test_diagnostic_receipt_failure_keeps_durable_publication(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from erp_harness.app.runner import _restore_dynamic_selection
    provider,controller,store,_=setup(tmp_path)
    original=provider._record
    def record(row):
        if row.get('status')=='applied':raise OSError('simulated diagnostic receipt failure')
        original(row)
    monkeypatch.setattr(provider,'_record',record)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Confirm')],'tools':list(controller.tools)}
    async def check():
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['actions']})
        await provider._publish(kwargs)
        assert provider.failed and controller._active==('actions',)
        assert kwargs['tools']==list(controller.tools)
        restored=DynamicToolController(fake_tools(set(),[]),tmp_path/'dynamic-tools.jsonl',count().__next__)
        _restore_dynamic_selection(restored,SimpleNamespace(messages=[],stage_tools_for_next_turn=lambda tools:None),tmp_path/'dynamic-tools.jsonl')
        assert restored._active==controller._active
        await provider.aclose()
    asyncio.run(check());store.close()


def test_publication_notice_and_request_correlation_are_request_local(tmp_path,monkeypatch):
    from erp_harness.app.request_receipts import RequestReceipts, routing_decision_id
    provider,controller,store,_=setup(tmp_path);receipts=RequestReceipts(tmp_path/'requests');seen=[]
    provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['actions']})
    async def response(self,**kw):
        seen.append(kw)
        for _ in range(2):
            row=receipts._allocate({'model':'test'})
            assert row['routing_decision_id']==routing_decision_id.get()
            yield AssistantDoneEvent(reason='stop',message=AssistantMessage(content='done'))
    monkeypatch.setattr(OpenAICompatibleProvider,'stream_response',response)
    async def check():
        from erp_harness.runtime.provider import provider_request_kind
        kind=provider_request_kind.set('normal')
        try:
            source=provider.stream_response(model='test',system='ERP',messages=[UserMessage(content='Confirm')],tools=list(controller.tools))
            while True:
                try:await asyncio.ensure_future(anext(source))
                except StopAsyncIteration:break
        finally:
            provider_request_kind.reset(kind)
        assert routing_decision_id.get() is None
        assert 'Current host publication: ["actions"]' in seen[0]['system']
        assert 'mcp_odoo_execute_approved_write' in {t.name for t in seen[0]['tools']}
        decision=json.loads((provider.directory/'decisions.jsonl').read_text().splitlines()[0])
        assert all(row['routing_decision_id']==decision['call_id'] for row in receipts._rows.values())
        publication=json.loads((provider.directory/'decisions.jsonl').read_text().splitlines()[-1])
        assert publication['event']=='publication' and publication['decision_id']==decision['call_id']
        assert publication['active']==['actions'] and publication['proposed']==['actions']
        assert len(publication['tool_contract_sha256'])==64
        await provider.aclose()
    asyncio.run(check());store.close()


def test_old_publication_is_not_current_state_and_errors_survive(tmp_path):
    provider,controller,store,_=setup(tmp_path)
    catalog=ToolResultMessage(tool_call_id='catalog',tool_name='list_odoo_capabilities',content=json.dumps({
        'success':True,'active':[], 'capabilities':[{'id':'actions','active':False,'status':'available'},
        {'id':'accounting','active':False,'status':'module_missing','missing_models':['account.move']}],
        'notice':'Configure next turn'}))
    configured=ToolResultMessage(tool_call_id='config',tool_name='configure_odoo_tools',content=json.dumps({
        'success':True,'active':['actions'],'published_tools':['old'],'available_next_turn':True}))
    failed=ToolResultMessage(tool_call_id='failure',tool_name='configure_odoo_tools',content=json.dumps({
        'success':False,'unknown':['invented'],'error':'Invalid selection'}))
    record=ToolResultMessage(tool_call_id='read',tool_name='mcp_odoo_read_record',content=json.dumps({
        'success':True,'result':{'active':False,'state':'draft'}}))
    messages=[UserMessage(content='Confirm'),catalog,configured,failed,record]
    before=[m.model_dump() for m in messages]
    projected,total=provider.project_model_context(messages)
    assert total==2 and [m.model_dump() for m in messages]==before
    assert [m.tool_call_id for m in projected[1:]]==['catalog','config','failure','read']
    cleaned=json.loads(projected[1].text)
    assert 'active' not in cleaned and all('active' not in row for row in cleaned['capabilities'])
    assert cleaned['capabilities'][1]['status']=='module_missing'
    assert cleaned['capabilities'][1]['missing_models']==['account.move']
    assert projected[3:] == [failed,record]
    assert provider.project_model_context(projected)[0]==projected
    from erp_harness.app.capability_routing import project_routing_result
    invalid='{"success":true,"capabilities":null}'
    assert project_routing_result('list_odoo_capabilities',invalid)==invalid
    diagnosis=ToolResultMessage(tool_call_id='diagnose',tool_name='diagnose_current_run',content=json.dumps({
        'success':True,'routing':{'status':'fallback','reason':'router_error'},'items':[]}))
    assert provider.project_model_context([AssistantMessage(content='Diagnose'),diagnosis])[0][-1]==diagnosis
    consumed=provider.project_model_context([diagnosis,AssistantMessage(content='Recovered')])[0][0]
    assert 'routing' not in json.loads(consumed.text)
    store.close()


def test_new_host_routed_run_does_not_inherit_previous_run_tools(tmp_path):
    from types import SimpleNamespace
    from erp_harness.app.runner import _restore_dynamic_selection
    provider,controller,store,_=setup(tmp_path)
    previous=ToolResultMessage(tool_call_id='old-run',tool_name='configure_odoo_tools',content=json.dumps({
        'success':True,'active':['actions']}))
    session=SimpleNamespace(messages=[previous],stage_tools_for_next_turn=lambda tools:None)
    log=tmp_path/'dynamic-tools.jsonl'
    _restore_dynamic_selection(controller,session,log,restore_history=False)
    assert controller._active==()
    _restore_dynamic_selection(controller,session,log)  # Existing model-owned sessions stay compatible.
    assert controller._active==('actions',)
    log.write_text(json.dumps({'event':'end','tool':'configure_odoo_tools','success':True,'active':['diagnostics']})+'\n')
    _restore_dynamic_selection(controller,session,log,restore_history=False)
    assert controller._active==('diagnostics',)  # An approval restart uses this run's durable receipt.
    store.close()


def test_routing_diagnosis_distinguishes_model_retention_and_failed_selection(tmp_path):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Continue')],'tools':list(controller.tools)}
    async def check():
        await next(t for t in controller.tools if t.name=='configure_odoo_tools').execute('recovery',{'capabilities':['actions']})
        kwargs['tools'][:]=controller.tools
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':[]})
        await provider._publish(kwargs,'decision-1')
        info=provider.routing_diagnostic()
        assert info['proposed']==[] and info['active']==['actions'] and info['required_by_model']==['actions']
        assert info['decision_id']=='decision-1' and not info['business_truth'] and not info['automatic_business_retry']
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['actions','actions']})
        await provider._publish(kwargs,'decision-2')
        info=provider.routing_diagnostic()
        assert info['status']=='fallback' and info['reason']=='router_error' and info['proposed'] is None
        assert info['active']==['actions'] and info['fallback']=='model_recovery'
        await provider.aclose()
    asyncio.run(check());store.close()
