"""The model and dispatcher must share one publication snapshot. No paid calls."""
import asyncio
from itertools import count
from unittest.mock import AsyncMock

from erp_harness.app.capability_routing import LayaProvider
from erp_harness.app.model_config import provider_config
from erp_harness.erp.store import ActionStore
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.config import ProviderSettings
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.runtime.messages import AssistantMessage, ToolCall, UserMessage
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


def test_fallback_and_manual_selection_survive_worker_restart(tmp_path):
    provider,controller,store,_=setup(tmp_path)
    kwargs={'model':'test','system':'ERP','messages':[UserMessage(content='Read')],'tools':list(controller.tools)}
    async def check():
        configure=next(t for t in controller.tools if t.name=='configure_odoo_tools')
        await configure.execute('main-model',{'capabilities':['actions']})
        provider._decide=AsyncMock(side_effect=AssertionError('Host selection must win'))
        kwargs['tools'][:]=controller.tools
        await provider._publish(kwargs)
        assert 'mcp_odoo_execute_approved_write' in {t.name for t in kwargs['tools']}
        resumed=LayaProvider(provider._config);resumed.bind_router(controller,store,tmp_path)
        assert resumed._hold_reason()=='host_takeover'
        await resumed.aclose();await provider.aclose()
    asyncio.run(check())
    store.close()


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
