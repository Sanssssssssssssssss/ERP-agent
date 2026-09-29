"""The model and dispatcher must share one publication snapshot. No paid calls."""
import asyncio
import json
from itertools import count
from pathlib import Path
from unittest.mock import AsyncMock

from erp_harness.app.capability_routing import OpenJevProvider
from erp_harness.app.model_config import provider_config
from erp_harness.erp.store import ActionStore
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.config import ProviderSettings
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.runtime.messages import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from erp_harness.runtime.provider_events import AssistantDoneEvent
from erp_harness.runtime.session import HarnessSession, SessionConfig
from erp_harness.runtime.storage import JsonlSessionStorage
from erp_harness.tools.dynamic_tools import DynamicToolController
from tests.test_dynamic_tools import fake_tools


def setup(tmp_path):
    calls=[]
    controller=DynamicToolController(fake_tools(set(),calls),tmp_path/'dynamic-tools.jsonl',count().__next__,host_owned=True)
    controller.bind(lambda tools: None)
    store=ActionStore(tmp_path/'actions.sqlite3')
    provider=OpenJevProvider(OpenAICompatibleConfig(api_key='test',base_url='http://unused.invalid'))
    provider.bind_router(controller,store,tmp_path)
    return provider,controller,store,calls


def test_explicit_missing_tool_recovery_persists_without_business_execution(tmp_path):
    provider, controller, store, calls = setup(tmp_path)
    async def check():
        recover = next(t for t in controller.tools if t.name == 'recover_capabilities')
        assert not (await recover.execute('invalid', {'capabilities': ['not_registered']})).details['success']
        assert controller._active == ()
        result = await recover.execute('recovery', {'capabilities': ['attachments']})
        assert result.details['success'] and controller._active == ('attachments',)
        from erp_harness.app.runner import _receipt_dynamic_selection
        assert _receipt_dynamic_selection(tmp_path / 'dynamic-tools.jsonl') == (True, ('attachments',))
        assert set(calls) <= {'find_records'}
        # A new provider sees the receipt even if the selector again omits the group.
        resumed = OpenJevProvider(provider._config)
        resumed.bind_router(controller, store, tmp_path)
        resumed._decide = AsyncMock(return_value={'status': 'ok', 'capabilities': []})
        kwargs = {'model': 'test', 'system': 'ERP', 'messages': [UserMessage(content='Read attachment')],
                  'tools': list(controller.tools)}
        await resumed._publish(kwargs)
        assert controller._active == ('attachments',)
        assert resumed.routing_diagnostic()['dependencies'][0]['source'] == 'executor_recovery'
        assert set(calls) <= {'find_records'}
        await resumed.aclose(); await provider.aclose()
    asyncio.run(check()); store.close()


def test_recovery_receipt_failure_rolls_back_publication(tmp_path, monkeypatch):
    provider, controller, store, _ = setup(tmp_path)
    published = []
    controller.bind(lambda tools: published.append({t.name for t in tools}))
    original = controller._log
    def fail_end(row):
        if row['event'] == 'end' and row.get('tool') == 'recover_capabilities':
            raise OSError('receipt disk unavailable')
        original(row)
    monkeypatch.setattr(controller, '_log', fail_end)
    async def check():
        import pytest
        with pytest.raises(OSError):
            await next(t for t in controller.tools if t.name == 'recover_capabilities').execute(
                'recover', {'capabilities': ['attachments']})
        assert controller._active == ()
        assert published[-1] == {t.name for t in controller.tools}
        await provider.aclose()
    asyncio.run(check()); store.close()


def test_selector_protocol_rejects_missing_mismatched_and_undeclared_decisions(tmp_path):
    from types import SimpleNamespace
    import pytest
    provider, _, store, _ = setup(tmp_path)
    packet = {'id': 'one', 'groups': ['attachments']}
    good = {'id': 'one', 'scope': 'capability_publication_only', 'status': 'ok',
            'capabilities': ['attachments'], 'result': {'decisions': {'attachments': {'answer': 'A'}}}}
    async def check():
        for update in ({}, {'id': 'other'}, {'capabilities': []}, {'result': []}, {'result': {'decisions': []}}, {'result': {'decisions': {}}},
                       {'result': {'decisions': {'attachments': {'answer': 'yes'}}}}):
            response = {**good, **update}
            provider.process = SimpleNamespace(stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
                stdout=SimpleNamespace(readline=AsyncMock(return_value=json.dumps(response).encode())))
            if update:
                with pytest.raises(ValueError):
                    await provider._decide(packet)
            else:
                assert await provider._decide(packet) == good
        provider.process = None
        await provider.aclose()
    asyncio.run(check()); store.close()


def test_worker_launch_avoids_stdlib_shadowing_and_business_credentials(tmp_path, monkeypatch):
    from types import SimpleNamespace
    provider, _, store, _ = setup(tmp_path)
    provider.selector = {'python': 'isolated-python'}
    provider.selector_config = 'pinned-config.json'
    response = {'id': 'x', 'scope': 'capability_publication_only', 'status': 'fallback'}
    process = SimpleNamespace(stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
        stdout=SimpleNamespace(readline=AsyncMock(return_value=json.dumps(response).encode())))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    for name in ('ODOO_API_KEY', 'LLM_API_KEY', 'COMMAND_CODE_API_KEY'):
        monkeypatch.setenv(name, 'must-not-reach-selector')
    async def check():
        assert await provider._decide({'id': 'x', 'groups': ['actions']}) == response
        assert launch.call_args.args[:2] == ('isolated-python', '-P')
        assert not any(k.startswith(('ODOO_', 'LLM_', 'COMMAND_CODE_')) for k in launch.call_args.kwargs['env'])
        provider.process = None
        await provider.aclose()
    asyncio.run(check()); store.close()


def test_unknown_modules_do_not_expand_the_declared_selector_candidates(tmp_path):
    from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS
    provider, controller, store, _ = setup(tmp_path)
    provider.candidate_groups = [g for g in CAPABILITY_GROUPS if g not in ('employee', 'time_off')]
    controller._availability = {g: {'status': 'unknown'} for g in CAPABILITY_GROUPS}
    provider._decide = AsyncMock(return_value={'status': 'ok', 'capabilities': []})
    async def check():
        await provider._publish({'model': 'test', 'system': 'ERP', 'messages': [UserMessage(content='Read')],
                                 'tools': list(controller.tools)})
        assert provider._decide.await_args.args[0]['groups'] == provider.candidate_groups
        # Excluded experiment groups remain accessible through explicit recovery.
        result = await next(t for t in controller.tools if t.name == 'recover_capabilities').execute(
            'explicit', {'capabilities': ['employee']})
        assert result.details['success'] and controller._active == ('employee',)
        await provider.aclose()
    asyncio.run(check()); store.close()


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
    assert all(not {'configure_odoo_tools','list_odoo_capabilities'} & names for names in seen)
    store.close()


def test_sop_dependencies_survive_restart_without_model_routing(tmp_path):
    from erp_harness.tools.sops import build_sop_tools
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Confirm')],'tools':list(controller.tools)}
    async def check():
        sop=build_sop_tools(tmp_path/'sop-events.jsonl',count().__next__)[1]
        result=await sop.execute('sop',{'sop_id':'safe_write_review','inputs':{'model':'sale.order','operation':'write'}})
        assert result.details['success']
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':[]})
        await provider._publish(kwargs)
        assert controller._active==('actions',)
        resumed=OpenJevProvider(provider._config);resumed.bind_router(controller,store,tmp_path)
        resumed._decide=AsyncMock(return_value={'status':'ok','capabilities':['diagnostics']})
        await resumed._publish(kwargs)
        assert set(controller._active)=={'actions','diagnostics'}
        assert resumed.routing_diagnostic()['dependencies'][0]['tool_call_id']=='sop'
        assert not {'configure_odoo_tools','list_odoo_capabilities'} & {t.name for t in kwargs['tools']}
        await resumed.aclose();await provider.aclose()
    asyncio.run(check());store.close()


def test_dispatch_recovery_requires_exact_failed_call_and_never_executes_it(tmp_path):
    provider,controller,store,calls=setup(tmp_path)
    name='mcp_odoo_search_across_instances'
    attempt=AssistantMessage(content=[ToolCall(id='missing',name=name,arguments={})])
    failure=ToolResultMessage(tool_call_id='missing',tool_name=name,content=f'Tool {name} not found',is_error=True)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Read'),attempt,failure],'tools':list(controller.tools)}
    async def check():
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':[]})
        for invalid in [failure.model_copy(update={'tool_call_id':'other'}),failure.model_copy(update={'is_error':False}),
                        failure.model_copy(update={'content':[TextContent(text='Tool error')]})]:
            kwargs['messages'][-1]=invalid
            await provider._publish(kwargs)
            assert not controller._active
        kwargs['messages'][-1]=failure
        await provider._publish(kwargs)
        assert controller._active==('cross_instance',) and 'search_across_instances' not in calls
        assert provider.routing_diagnostic()['recovery'][0]['tool_call_id']=='missing'
        kwargs['messages'].append(AssistantMessage(content='Read completed'))
        await provider._publish(kwargs)
        assert not controller._active
        await provider.aclose()
    asyncio.run(check());store.close()


def test_pending_ledger_retains_actions_and_worker_failure_uses_host_fallback(tmp_path,monkeypatch):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Continue')],'tools':list(controller.tools)}
    async def check():
        base = controller.tools
        await controller.publish('prior-turn', ['actions', 'migration', 'diagnostics'])
        kwargs['tools'][:] = controller.tools
        assert controller.tools[:len(base)] == base
        for status in ['pending_approval','approved','executing','sending','needs_reconciliation','unknown']:
            monkeypatch.setattr(ActionStore,'read_receipts',staticmethod(lambda _p:[{'status':status}]))
            provider._decide=AsyncMock(return_value={'status':'ok','capabilities':[]})
            await provider._publish(kwargs)
            assert controller._active==('actions',)
            provider._decide.assert_awaited_once()
            packet=provider._decide.await_args.args[0]
            state=json.loads(packet['messages'][1]['content'])['context']
            assert state['version']=='capability_context_v12'
            assert state['action_ledger']['actions'][0]['status']==status
            assert 'actions' in state['runtime_retained_capabilities']
        monkeypatch.setattr(ActionStore,'read_receipts',staticmethod(lambda _p:[]))
        provider._decide=AsyncMock(side_effect=RuntimeError('Worker failed'))
        await provider._publish(kwargs)
        assert provider._hold_reason()=='host_takeover' and 'actions' in controller._active
        assert 'accounting' not in controller._active  # Missing installed module remains blocked.
        assert not {'configure_odoo_tools','list_odoo_capabilities'} & {t.name for t in kwargs['tools']}
        provider._decide=AsyncMock(side_effect=AssertionError('No automatic worker retry'))
        await provider._publish(kwargs)
        assert provider.routing_diagnostic()['fallback']=='registered_catalog'
        await provider.aclose()
    asyncio.run(check());store.close()


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
        assert 'Only the tools in this request' in seen[0]['system']
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
    assert total==3 and [m.model_dump() for m in messages]==before
    assert projected == [messages[0],record]
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


def test_invalid_selection_is_diagnosable_without_transferring_ownership(tmp_path):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Continue')],'tools':list(controller.tools)}
    async def check():
        await controller.publish('host', ['actions'])
        kwargs['tools'][:]=controller.tools
        provider._decide=AsyncMock(return_value={'status':'ok','capabilities':['actions','actions']})
        await provider._publish(kwargs,'decision-2')
        info=provider.routing_diagnostic()
        assert info['status']=='fallback' and info['reason']=='router_error' and info['proposed'] is None
        assert info['active']==['actions'] and info['owner']=='runtime'
        assert not info['business_truth'] and not info['automatic_business_retry']
        await provider.aclose()
    asyncio.run(check());store.close()


def test_mixed_history_removes_routing_pairs_preserving_business_calls(tmp_path):
    from erp_harness.runtime.messages import ThinkingContent
    provider,controller,store,_=setup(tmp_path)
    call=ToolCall(id='read',name='mcp_odoo_read_record',arguments={'model':'sale.order','record_id':6})
    messages=[UserMessage(content='Confirm'), AssistantMessage(content=[ThinkingContent(thinking='configure first'),
        ToolCall(id='routing',name='configure_odoo_tools',arguments={'capabilities':['actions']}),call]),
        ToolResultMessage(tool_call_id='routing',tool_name='configure_odoo_tools',content='{}'),
        ToolResultMessage(tool_call_id='read',tool_name=call.name,content='{"state":"draft"}')]
    view,_=provider.project_model_context(messages)
    assert view[1].content==[call] and view[2]==messages[3]
    assert len(messages)==4 and len(messages[1].content)==3
    store.close()


def test_v7_uses_trained_state_and_same_publication_boundary(tmp_path, monkeypatch):
    config = tmp_path / 'selector.json'
    config.write_text(json.dumps({'backend': 'laya', 'contract': 'host_facts_v1',
                                 'candidate_groups': ['actions']}), encoding='utf8')
    monkeypatch.setenv('ERP_CAPABILITY_ROUTER_CONFIG', str(config))
    provider, controller, store, _ = setup(tmp_path)
    provider._decide = AsyncMock(return_value={'status': 'ok', 'capabilities': ['actions']})
    async def check():
        kwargs = {'model': 'test', 'system': 'ERP', 'messages': [UserMessage(content='Confirm S00006')],
                  'tools': list(controller.tools)}
        await provider._publish(kwargs)
        packet = provider._decide.call_args.args[0]
        assert packet['context_version'] == 'host_facts_v1' and 'messages' not in packet
        assert 'available_base_tools' in packet['state']
        assert packet['state']['task']['goal'] == 'Confirm S00006'
        assert controller._active == ('actions',)
        assert kwargs['tools'] == list(controller.tools)
        await provider.aclose()
    asyncio.run(check()); store.close()


def test_laya_packet_filters_unavailable_groups_without_reinterpreting_scores():
    from types import SimpleNamespace
    import pytest
    from erp_harness.providers.laya_worker import select_packet
    router = SimpleNamespace(questions={'actions': {}, 'attachments': {}},
        route=lambda state, *, groups: {'status': 'ok', 'capabilities': groups,
            'probabilities': {'actions': .6, 'attachments': .9}, 'scope': 'capability_publication_only'})
    packet = {'id': 'call', 'groups': ['actions'], 'state': {}, 'context_version': 'host_facts_v1'}
    receipt = select_packet(router, packet)
    assert receipt['id'] == 'call' and receipt['capabilities'] == ['actions']
    assert receipt['result']['decisions'] == {'actions': {'answer': 'A', 'scores': [.6, .4]}}
    with pytest.raises(ValueError):
        select_packet(router, {**packet, 'context_version': 'capability_context_v12'})
    with pytest.raises(ValueError):
        select_packet(router, {**packet, 'groups': ['unknown']})


def test_laya_additions_preserve_full_prefix_and_resume_order(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from erp_harness.app.runner import _restore_dynamic_selection
    from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS
    config = tmp_path / 'additive.json'
    config.write_text(json.dumps({'backend': 'laya', 'contract': 'host_facts_v1',
        'candidate_groups': ['actions', 'attachments', 'diagnostics']}))
    monkeypatch.setenv('ERP_CAPABILITY_ROUTER_CONFIG', str(config))
    provider, controller, store, calls = setup(tmp_path)
    controller._availability = {g: {'status': 'unknown'} for g in CAPABILITY_GROUPS}
    kwargs = {'model': 'test', 'system': 'ERP', 'messages': [UserMessage(content='Continue')],
              'tools': list(controller.tools)}

    async def check():
        for additions, candidates in [(['diagnostics'], ['actions', 'attachments', 'diagnostics']),
                                       ([], ['actions', 'attachments']),
                                       (['actions'], ['actions', 'attachments'])]:
            previous = list(controller.tools)
            retained = list(controller._active)
            provider._decide = AsyncMock(return_value={'status': 'ok', 'capabilities': additions})
            await provider._publish(kwargs)
            packet = provider._decide.await_args.args[0]
            assert packet['groups'] == candidates
            assert packet['state']['runtime_retained_capabilities'] == sorted(retained)
            assert list(controller.tools[:len(previous)]) == previous
            assert kwargs['tools'] == list(controller.tools)
            assert kwargs['system'] == 'ERP'
        assert controller._active == ('diagnostics', 'actions')
        monkeypatch.setattr(ActionStore, 'read_receipts', staticmethod(lambda _: [{'status': 'unknown'}]))
        provider._decide = AsyncMock(return_value={'status': 'ok', 'capabilities': []})
        await provider._publish(kwargs)
        assert controller._active == ('diagnostics', 'actions')
        assert provider._decide.await_args.args[0]['state']['action_ledger']['unresolved'][0]['status'] == 'unknown'
        monkeypatch.setattr(ActionStore, 'read_receipts', staticmethod(lambda _: []))
        # Same-run approval restart restores the append order, not alphabetical order.
        resumed = DynamicToolController(fake_tools(set(), calls), tmp_path/'dynamic-tools.jsonl',
                                        count().__next__, host_owned=True)
        resumed.bind(lambda _: None)
        _restore_dynamic_selection(resumed, SimpleNamespace(messages=[], stage_tools_for_next_turn=lambda _: None),
                                   tmp_path/'dynamic-tools.jsonl', restore_history=False)
        assert [t.name for t in resumed.tools] == [t.name for t in controller.tools]
        previous = list(controller.tools)
        await next(t for t in controller.tools if t.name == 'recover_capabilities').execute(
            'recover', {'capabilities': ['attachments']})
        kwargs['tools'][:] = controller.tools
        assert list(controller.tools[:len(previous)]) == previous
        provider._decide = AsyncMock(side_effect=AssertionError('Already available; do not infer again'))
        await provider._publish(kwargs)
        provider._decide.assert_not_awaited()
        assert controller._active == ('diagnostics', 'actions', 'attachments')
        assert set(calls) <= {'find_records'}  # Selection/recovery never executes a business write.
        await provider.aclose()
    asyncio.run(check())
    store.close()


def test_laya_question_batches_preserve_complete_answers():
    from types import SimpleNamespace
    from erp_harness.providers.laya_worker import CapabilityRouter
    router = object.__new__(CapabilityRouter)
    router.device_type = 'cuda'; router.question_batch_size = 2
    batches = []
    def infer(state, questions):
        batches.append(list(questions))
        return {'answers': {g: {'choice': 'A', 'probabilities': {'A': .9, 'B': .1}}
                            for g in questions}, 'usage': {'input_tokens': len(questions)*10, 'output_tokens': 0}}
    router.agent = SimpleNamespace(device=SimpleNamespace(type='cuda'), system_one=infer)
    result, selected = router._predict('complete input', dict.fromkeys(['a','b','c','d','e']), 'A')
    assert batches == [['a','b'],['c','d'],['e']]
    assert selected == ['a','b','c','d','e'] and result['usage']['input_tokens'] == 50


def test_module_probe_cache_is_run_and_identity_scoped(tmp_path):
    calls = []
    async def check():
        for identity in ('same', 'same', 'changed'):
            controller = DynamicToolController(fake_tools({'account.move'}, calls), tmp_path/'dynamic-tools.jsonl',
                                               count().__next__, host_owned=True)
            controller.bind_probe_scope(['actions', 'accounting'], {'credential_scope_sha256': identity})
            result = await controller._list('probe')
            assert controller._availability['accounting']['status'] == 'available'
            assert controller._availability['employee']['status'] == 'unknown'
        assert calls == ['find_records', 'find_records']
    asyncio.run(check())
