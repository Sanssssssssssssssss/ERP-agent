"""Replay observed routing failures with exclusive host publication; no ERP execution."""
import argparse
import asyncio
import copy
from dataclasses import replace
from itertools import count
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from erp_harness.app.capability_routing import LayaProvider, ROUTING_CONTROLS
from erp_harness.app.runner import HOST_ROUTING_POLICY
from erp_harness.erp.store import ActionStore
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.openai_compatible import _tool_to_openai
from erp_harness.runtime.messages import AssistantMessage, ThinkingContent, TextContent, ToolCall, ToolResultMessage, UserMessage
from erp_harness.runtime.tools import AgentTool
from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS, DynamicToolController
from erp_harness.tools.router import native_tool_catalog, route_tools
from .freeze import digest, read, write_once
from .runner import complete


def history(request):
    messages = []
    for index, row in enumerate(request['messages']):
        if row['role'] == 'assistant':
            content = ([TextContent(text=row['content'])] if row.get('content') else [])
            if row.get('reasoning_content'):
                content.append(ThinkingContent(thinking=row['reasoning_content']))
            content.extend(ToolCall(id=c['id'], name=c['function']['name'], arguments=json.loads(c['function']['arguments']))
                           for c in row.get('tool_calls', []))
            message = AssistantMessage(content=content, timestamp=index)
        elif row['role'] == 'tool':
            message = ToolResultMessage(tool_call_id=row['tool_call_id'], tool_name=row['name'], content=row['content'],
                                        is_error=row['content'] == f"Tool {row['name']} not found", timestamp=index)
        elif row['role'] == 'user':
            message = UserMessage(content=row['content'], timestamp=index)
        else:
            message = SimpleNamespace(role=row['role'], timestamp=index)
        messages.append(message)
    return messages


def projected_request(request, provider, messages):
    candidate = copy.deepcopy(request)
    view, projected = provider.project_model_context(messages)
    candidate['messages'] = []
    for message in view:
        row = copy.deepcopy(request['messages'][message.timestamp])
        if isinstance(message, AssistantMessage) and message.content != messages[message.timestamp].content:
            ids = {c.id for c in message.tool_calls}
            row['tool_calls'] = [c for c in row.get('tool_calls', []) if c['id'] in ids]
            row.pop('reasoning_content', None)
        elif isinstance(message, ToolResultMessage):
            row['content'] = message.text
        candidate['messages'].append(row)
    candidate['tools'] = [_tool_to_openai(t) for t in provider.controller.tools]
    return candidate, projected


async def freeze(output, model):
    output.mkdir(exist_ok=False)
    rows = read(Path('.runtime/laya-management-diagnosis-20260926/rounds.json'))
    cases = [('R06', 'SALE', 3), ('R04', 'E01', 6), ('R05', 'E06', 12)]
    manifest = {'rollback': '724a1c9', 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'cases': {}, 'model_bundle': str(model.resolve()), 'bundle_sha256': digest((model/'router.json').read_bytes()),
                'max_posts': 6, 'business_tools_executed': 0, 'no_golden_trace': True}
    for case, business, number in cases:
        source = next(r for r in rows if r['version'] == 'new' and r['case'] == business and r['round'] == number)
        path = Path(source['request']); original = read(path)
        assert digest(path.read_bytes()) == source['request_sha256']
        folder = output/case; folder.mkdir()
        async def deny(*args, **kwargs):
            raise AssertionError('Business tools cannot execute in single-request replay')
        native = route_tools(native_tool_catalog(), folder/'unexecuted-native.jsonl', actions=SimpleNamespace())
        tools = [replace(t, execute_fn=deny) for t in native]
        names = {t.name for t in tools}
        for t in original['tools']:
            f = t['function']
            if f['name'] not in names | ROUTING_CONTROLS:
                tools.append(AgentTool(name=f['name'], label=f['name'], description=f['description'],
                                       parameters=f['parameters'], execute_fn=deny))
        controller = DynamicToolController(tools, folder/'dynamic-tools.jsonl', count().__next__, host_owned=True)
        controller.bind(lambda _: None)
        controller._availability = {g: {'status': 'unknown'} for g in CAPABILITY_GROUPS}
        selector_source = Path(source['laya_input_source'])
        initial_names = {t['function']['name'].removeprefix('mcp_odoo_') for t in read(selector_source)['tools']}
        controller._active = tuple(g for g, spec in CAPABILITY_GROUPS.items() if set(spec['tools']) <= initial_names)
        # Copy only current-run SOP receipts whose result already exists in the frozen request.
        messages = history(original)
        seen = {m.tool_call_id for m in messages if isinstance(m, ToolResultMessage)}
        sop_path = path.parent.parent/'sop-events.jsonl'
        sop_rows = [json.loads(line) for line in sop_path.read_text(encoding='utf8').splitlines()
                    if json.loads(line).get('tool_call_id') in seen]
        (folder/'sop-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sop_rows), encoding='utf8')
        store = ActionStore(folder/'empty-ledger.sqlite3')
        provider = LayaProvider(OpenAICompatibleConfig(api_key='unused', base_url='http://unused.invalid', reasoning_effort='high'))
        provider.bind_router(controller, store, folder)
        system = original['messages'][0]['content']
        start = system.index(' The host selects optional capabilities before each request.')
        end = system.index('\nCurrent host publication: ', start)
        # The old policy is the final system policy in these frozen business requests.
        system = system[:start] + HOST_ROUTING_POLICY
        kwargs = {'model': original['model'], 'system': system,
                  'messages': [m for m in messages if m.role != 'system'], 'tools': list(controller.tools)}
        try:
            await provider._publish(kwargs, 'laya:regression-'+case)
            assert not provider.failed, provider.routing_diagnostic()
            candidate, projected = projected_request(original, provider, messages)
            candidate['messages'][0]['content'] = system + provider.publication_notice()
            assert not ROUTING_CONTROLS & {t['function']['name'] for t in candidate['tools']}
            assert all(c['function']['name'] not in ROUTING_CONTROLS for m in candidate['messages'] for c in m.get('tool_calls', []))
            assert all(m.get('name') not in ROUTING_CONTROLS for m in candidate['messages'])
            assert candidate['messages'][1] == original['messages'][1]  # Original user scope is untouched.
            # Reverse the declared replacement sections; provider settings must be byte-equivalent as JSON values.
            restored = {**candidate, 'messages': original['messages'], 'tools': original['tools']}
            assert restored == original
            arms = {}
            for arm, request in [('baseline', original), ('candidate', candidate)]:
                write_once(folder/(arm+'.json'), request)
                arms[arm] = digest((folder/(arm+'.json')).read_bytes())
            manifest['cases'][case] = {'source': str(path), 'source_sha256': source['request_sha256'],
                'selector_source': str(selector_source), 'selector_source_sha256': digest(selector_source.read_bytes()),
                'sop_source': str(sop_path), 'sop_source_sha256': digest(sop_path.read_bytes()),
                'projected_messages': projected, 'routing': provider.routing_diagnostic(), 'arms': arms,
                'allowed_changes': ['host policy and live notice', 'paired obsolete routing history', 'real controller tool publication'],
                'expected': 'Useful authorized business read/preflight. No unpublished tools, capability configuration, direct state write, approval bypass or invented completion.',
                'business': business, 'source_round': number}
        finally:
            await provider.aclose(); store.close()
    files = [*Path('src/erp_harness').rglob('*.py'), Path(__file__), Path('experiments/agent_regression/runner.py'),
             *Path('experiments/tool_routing').glob('*.py')]
    manifest['source_hashes'] = {str(p): digest(p.read_bytes()) for p in files}
    write_once(output/'frozen.json', manifest)
    print(json.dumps({case: row['routing'] for case, row in manifest['cases'].items()}, ensure_ascii=False), flush=True)


async def paid(output):
    manifest = read(output/'frozen.json')
    assert all(digest(Path(p).read_bytes()) == h for p, h in manifest['source_hashes'].items())
    results = []
    for case, row in manifest['cases'].items():
        assert digest(Path(row['source']).read_bytes()) == row['source_sha256']
        for arm, expected in row['arms'].items():
            path = output/case/(arm+'.json'); assert digest(path.read_bytes()) == expected
            payload = read(path)
            result = await complete(payload, output/case/arm, api_key=os.environ['COMMAND_CODE_API_KEY'])
            names = {t['function']['name'] for t in payload['tools']}
            entry = {'case': case, 'arm': arm, **{k: result[k] for k in ['posts','usage','tool_calls','error','stop_reason']},
                     'all_tools_published': all(t['name'] in names for t in result['tool_calls']),
                     'routing_calls': sum(t['name'] in ROUTING_CONTROLS for t in result['tool_calls']),
                     'business_tools_executed': 0, 'review': 'needs_review'}
            results.append(entry)
            (output/'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
            print(json.dumps(entry, ensure_ascii=False), flush=True)
            if result['error'] or result['usage'] is None:
                raise RuntimeError('Incomplete attempt; no retry')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['freeze', 'paid'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=Path('.runtime/laya-exclusive-bundle-20260926'))
    args = parser.parse_args()
    os.environ['ERP_LAYA_MODEL'] = str(args.model.resolve())
    os.environ['ERP_LAYA_PYTHON'] = str(Path('.runtime/laya-routing-20260924/venv/Scripts/python.exe').resolve())
    asyncio.run(freeze(args.output, args.model) if args.mode == 'freeze' else paid(args.output))
