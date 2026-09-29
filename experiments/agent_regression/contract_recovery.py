"""Observed R07/R09/R03 prefixes; one candidate request each, never execute model calls."""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path

from erp_harness.erp.actions import NativeActions
from erp_harness.erp.gateway import Json2ReadClient
from erp_harness.erp.reads import NativeReads
from erp_harness.erp.store import ActionStore
from erp_harness.tools.router import native_tool_catalog, route_tools
from erp_harness.tools.sops import build_sop_tools
from .freeze import digest, read, write_once
from .runner import complete

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.runtime/laya-exclusive-20260926'
CASES = [('R07', 'SALE', 2), ('R09', 'E01', 3), ('R03', 'E06', 29)]


async def prepare(output, trial):
    environment = read(trial / 'environment.json')
    accounts = read(ROOT / '.runtime/enterprise-validation-20260922/snapshots/baseline-10000/accounts.json')
    account = accounts['warehouse']
    client = Json2ReadClient(url=environment['url'], db=environment['databases']['E01'],
                            username=account['login'], api_key=account['api_key'],
                            context={'allowed_company_ids': account['company_ids']})
    native = NativeReads(client)
    sops = {tool.name: tool for tool in build_sop_tools(read_fields=lambda **kw: native.call('get_model_fields', kw))}
    actions = NativeActions(native, store=ActionStore(output / 'preflight.sqlite3'))
    try:
        tools = {tool.name: tool for tool in route_tools(native_tool_catalog(), output / 'tools.jsonl', native=native, actions=actions, native_health=True)}
        rows = []
        for case, business, number in CASES:
            source = next((SOURCE / business / 'profile/data/runs').glob(f'*/requests/{number:04}.request.json'))
            original = read(source)
            candidate = copy.deepcopy(original)
            calls = {c['id']: c['function'] for m in original['messages'] for c in m.get('tool_calls', [])}
            changed = []
            for index, message in enumerate(candidate['messages']):
                if message['role'] != 'tool':
                    continue
                call = calls.get(message.get('tool_call_id'), {})
                name = call.get('name')
                if name == 'list_odoo_sops' and case == 'R07' or name == 'get_odoo_sop' and case == 'R09':
                    result = await sops[name].execute(message['tool_call_id'], json.loads(call['arguments']))
                    message['content'] = result.text
                    changed.append(index)
            for tool in candidate['tools']:
                if tool['function']['name'] == 'mcp_odoo_validate_write':
                    tool['function']['description'] = tools[tool['function']['name']].description
            restored = copy.deepcopy(candidate)
            for index in changed:
                restored['messages'][index] = original['messages'][index]
            restored['tools'] = original['tools']
            assert restored == original
            write_once(output / case / 'original.json', original)
            write_once(output / case / 'candidate.json', candidate)
            write_once(output / case / 'historical-response.json', read(source.with_name(f'{number:04}.output.json')))
            rows.append({'id': case, 'source': str(source), 'source_sha256': digest(source.read_bytes()),
                         'changed_messages': changed, 'candidate_sha256': digest((output / case / 'candidate.json').read_bytes()),
                         'constraints': ['Preserve original company, record identities and requested scope.',
                                         'No unapproved writes or claims of completion; safe alternative reads are allowed.']})
        # Real R08 omitted a mandatory child product. Exercise production validation without any ERP write.
        manager = accounts['manager']
        manager_reads = NativeReads(Json2ReadClient(url=environment['url'], db=environment['databases']['E01'],
            username=manager['login'], api_key=manager['api_key'], context={'allowed_company_ids': manager['company_ids']}))
        guarded = NativeActions(manager_reads, store=actions.store)
        bad = {'picking_id': 6, 'product_return_moves': [[0, 0, {'move_id': 1210, 'quantity': 2, 'to_refund': True}]]}
        result = guarded.validate_write('stock.return.picking', 'create', values=bad)
        assert result['success'] is False and 'product_id' in result.get('error', ''), result
        assert actions.store.summary()['actions'] == 0
        good = copy.deepcopy(bad); good['product_return_moves'][0][2]['product_id'] = 15
        control = guarded.validate_write('stock.return.picking', 'create', values=good)
        assert control['success'], control
        write_once(output / 'preflight.json', {'missing_product': result, 'valid_control': control['success'], 'erp_writes': 0})
        write_once(output / 'frozen.json', {'cases': rows, 'posts_max': 3, 'tools_executed': 0,
            'sources': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in [Path(__file__),
                ROOT/'src/erp_harness/tools/sops.py', ROOT/'src/erp_harness/tools/router.py', ROOT/'src/erp_harness/erp/write_guards.py']}})
    finally:
        actions.store.close()


async def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.paid:
        await prepare(output, args.trial)
        return
    frozen = read(output / 'frozen.json')
    for path, expected in frozen['sources'].items():
        assert digest((ROOT / path).read_bytes()) == expected
    for row in frozen['cases']:
        payload = output / row['id'] / 'candidate.json'
        assert digest(payload.read_bytes()) == row['candidate_sha256']
        result = await complete(read(payload), output / row['id'] / 'api', api_key=os.environ['COMMAND_CODE_API_KEY'])
        print(json.dumps({'id': row['id'], 'posts': result['posts'], 'error': result['error'],
                          'calls': result['tool_calls'], 'usage': result['usage']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trial', type=Path, default=Path('.runtime/laya-contract-live-20260926'))
    parser.add_argument('--paid', action='store_true')
    asyncio.run(main(parser.parse_args()))
