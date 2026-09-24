"""Compare a tool publication, approval pause and resume against the frozen runtime."""
import asyncio
import importlib
import json
from pathlib import Path
import tempfile


async def replay(directory: Path, *, legacy: bool = False):
    core = 'pi_agent' if legacy else 'erp_harness.runtime'
    storage = importlib.import_module(core + ('.session' if legacy else '.storage'))
    messages = importlib.import_module(core + '.messages')
    tools = importlib.import_module(core + '.tools')
    events = importlib.import_module(core + '.provider_events')
    sessions = importlib.import_module('pi_coding.session' if legacy else core + '.session')
    resources = importlib.import_module('pi_coding.resources' if legacy else 'erp_harness.context.resources')
    fake = importlib.import_module('pi_ai.fake' if legacy else 'erp_harness.providers.fake')
    Session = getattr(sessions, 'CodingSession' if legacy else 'HarnessSession')
    Config = getattr(sessions, 'CodingSessionConfig' if legacy else 'SessionConfig')
    ResourcePaths = getattr(resources, 'PiResourcePaths' if legacy else 'ResourcePaths')
    path = directory / 'session.jsonl'
    trace = []

    def response(name=None):
        content = [messages.ToolCall(id=name, name=name, arguments={})] if name else 'done'
        message = messages.AssistantMessage(content=content, model='fake', stop_reason='toolUse' if name else 'stop')
        return [events.AssistantDoneEvent(reason=message.stop_reason, message=message)]

    async def approve(_id, _args, _signal, _update):
        return tools.AgentToolResult(content='approval_required')

    approval = tools.AgentTool(name='approve', label='Approve', description='Request approval', parameters={'type': 'object'}, execute_fn=approve)

    async def discover(_id, _args, _signal, _update):
        session.stage_tools_for_next_turn([approval])
        return tools.AgentToolResult(content='raw observation')

    discovery = tools.AgentTool(name='discover', label='Discover', description='Find the business action', parameters={'type': 'object'}, execute_fn=discover)

    def project(history, _signal):
        trace.append('project')
        return [m.model_copy(update={'content': [messages.TextContent(text='consumed observation')]})
                if isinstance(m, messages.ToolResultMessage) and m.tool_name == 'discover' else m for m in history]

    async def stop(turn):
        # The action result must already be on disk before any approval pause.
        saved = await storage.JsonlSessionStorage(path).read_all()
        assert any(isinstance(e, storage.MessageEntry) and isinstance(e.message, messages.ToolResultMessage) for e in saved)
        trace.append('persisted_then_stop_check')
        return any(result.tool_name == 'approve' for result in turn.tool_results)

    provider = fake.FakeProvider([response('discover'), response('approve')])
    kwargs = dict(provider=provider, model='fake', system='Frozen ERP execution test',
                  storage=storage.JsonlSessionStorage(path), cwd=directory, tools=[discovery],
                  skills_enabled=False, extensions_enabled=False, auto_compact_enabled=False,
                  resource_paths=ResourcePaths(root=directory / '.pi-agent', project_resources_enabled=False))
    if not legacy:
        kwargs.update(transform_context=project, should_stop_after_turn=stop)
    session = await Session.load(Config(**kwargs))
    if legacy:
        original = session._harness.config.transform_context
        async def transform(history, signal):
            return await original(project(history, signal), signal)
        session._harness.config.transform_context = transform
        session._harness.config.should_stop_after_turn = stop
    first_events = [e.type async for e in session.prompt('Create the requested purchase.')]
    assert len(provider.calls) == 2
    await session.aclose()
    resumed = fake.FakeProvider([response()])
    kwargs.update(provider=resumed, tools=[approval])
    session = await Session.load(Config(**kwargs))
    if legacy:
        original = session._harness.config.transform_context
        session._harness.config.transform_context = transform
        session._harness.config.should_stop_after_turn = stop
    final_events = [e.type async for e in session.prompt('The user approved; finish.')]
    await session.aclose()

    def dump(message):
        return message.model_dump(mode='json', exclude={'timestamp', 'timing'})

    return {
        'requests': [{'system': system, 'messages': [dump(m) for m in history],
                      'tools': [{'name': t.name, 'description': t.description, 'parameters': t.parameters} for t in catalog]}
                     for _, system, history, catalog in provider.calls + resumed.calls],
        'events': [first_events, final_events],
        'hooks': trace,
        'saved_messages': [dump(e.message) for e in await storage.JsonlSessionStorage(path).read_all() if isinstance(e, storage.MessageEntry)],
    }


def test_runtime_matches_frozen_pause_and_resume(tmp_path):
    expected = json.loads(Path(__file__).with_name('fixtures').joinpath('runtime-baseline.json').read_text(encoding='utf-8'))
    assert asyncio.run(replay(tmp_path)) == expected


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        result = asyncio.run(replay(Path(directory), legacy=True))
    target = Path(__file__).with_name('fixtures') / 'runtime-baseline.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
