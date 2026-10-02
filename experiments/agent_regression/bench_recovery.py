"""Frozen benchmark incidents -> production candidate -> one optional API decision."""
import argparse
import ast
import asyncio
import copy
import json
import os
from pathlib import Path

import jsonschema

from erp_harness.app.runner import BUSINESS_EXECUTION_POLICY
from erp_harness.context.world_tools import build_world_tools
from erp_harness.tools.router import native_tool_catalog
from erp_harness.tools.sops import build_sop_tools, get_sop

from .freeze import digest, read, write_once
from .prepare import replace, validate_patches
from .runner import complete

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / 'experiments/agent_regression/bench_recovery_cases.json'


def definitions():
    return {t.name: {'type': 'function', 'function': {'name': t.name,
            'description': t.description, 'parameters': t.parameters}}
            for t in [*native_tool_catalog(), *build_sop_tools(), *build_world_tools(None)]}


def candidate(source):
    payload = copy.deepcopy(source)
    tools = definitions()
    assert payload['messages'][0]['role'] == 'system'
    payload['messages'][0]['content'] += '\n' + BUSINESS_EXECUTION_POLICY
    changes = [{'path': '/messages/0/content', 'before': source['messages'][0]['content'],
                'after': payload['messages'][0]['content']}]
    for i, tool in enumerate(payload.get('tools', [])):
        name = tool['function']['name']
        if name in tools:
            tool['function']['description'] = tools[name]['function']['description']
            changes.append({'path': f'/tools/{i}/function/description',
                'before': source['tools'][i]['function']['description'], 'after': tool['function']['description']})
    name = 'mcp_odoo_read_purchase_allocation'
    if name not in {t['function']['name'] for t in payload['tools']}:
        payload['tools'].append(tools[name])
        changes.append({'path': '/tools/-', 'before': None, 'after': tools[name]})
    # Inverse patch must reproduce every original message, schema and private reasoning.
    restored = copy.deepcopy(payload)
    restored['messages'][0]['content'] = source['messages'][0]['content']
    restored['tools'] = copy.deepcopy(source['tools'])
    assert restored == source
    return payload, changes


def verify_source(case):
    directory = ROOT / case['local_frozen_context'] / 'agent/requests'
    for name, expected in case['context_sha256'].items():
        assert digest((directory / name).read_bytes()) == expected, name
    return directory


def recovery_candidate(source, call_id, response):
    """Replace one observed error at its first corrective cut, preserving all IDs."""
    payload, changes = candidate(source)
    baseline = copy.deepcopy(payload)
    matches = [i for i, m in enumerate(source['messages'])
               if m.get('role') == 'tool' and m.get('tool_call_id') == call_id]
    if len(matches) != 1 or response.get('success') is not False:
        raise ValueError('Recovery requires one actual failed tool response')
    index = matches[0]
    if json.loads(source['messages'][index]['content']).get('success') is not False:
        raise ValueError('Cannot replace a successful historical result')
    pointer = f'/messages/{index}/content'
    patches = []
    replace(payload, baseline, patches, [pointer], pointer,
            json.dumps(response, ensure_ascii=False, separators=(',', ':')),
            'NativeReads.call; same failed arguments against isolated snapshot')
    validate_patches(baseline, payload, patches, [pointer])
    return payload, changes + patches


def followup_candidate(source, output, results):
    """Continue one completed decision with exact, separately recorded tool replies."""
    calls = output.get('tool_calls', [])
    if output.get('error') or output.get('stop_reason') != 'toolUse' or not calls:
        raise ValueError('Cannot continue an incomplete or STOP decision')
    ids = [c['id'] for c in calls]
    if len(ids) != len(set(ids)) or set(ids) != set(results):
        raise ValueError('Every actual call needs exactly one recorded reply')
    payload = copy.deepcopy(source)
    payload['messages'].append({'role': 'assistant', 'content': output['text'],
        'reasoning_content': output.get('reasoning', ''), 'tool_calls': [
            {'id': c['id'], 'type': 'function', 'function': {'name': c['name'],
                'arguments': json.dumps(c['arguments'], ensure_ascii=False, separators=(',', ':'))}}
            for c in calls]})
    payload['messages'].extend({'role': 'tool', 'tool_call_id': c['id'], 'name': c['name'],
        'content': json.dumps(results[c['id']], ensure_ascii=False, separators=(',', ':'))}
        for c in calls)
    assert {**payload, 'messages': payload['messages'][:-len(calls)-1]} == source
    return payload


def verdict(case, payload, output):
    if output.get('error') or output.get('stop_reason') in {'error', 'length', 'aborted', 'unknown'}:
        return {'status': 'unknown', 'reason': 'incomplete model decision'}
    schemas = {t['function']['name']: t['function']['parameters'] for t in payload['tools']}
    calls = output.get('tool_calls', [])
    violations, relevant = [], False
    # Only refusals from this exact frozen environment; fields may differ elsewhere.
    observed = [case]
    if case.get('local_frozen_context'):
        observed += [row for row in read(INDEX)
                     if row.get('local_frozen_context') == case['local_frozen_context']]
    refused_fields = {}
    for row in observed:
        if row.get('failure_layer') != 'unknown_field':
            continue
        error = row['original_error']
        bad = ast.literal_eval(error.split('Unknown field(s) ', 1)[1].split(' on ', 1)[0])
        model = error.split(' on ', 1)[1].split(';', 1)[0]
        refused_fields.setdefault(model, set()).update(bad)
    for call in calls:
        name, args = call['name'], call['arguments']
        if name not in schemas:
            violations.append('unpublished_tool')
            continue
        try:
            jsonschema.validate(args, schemas[name])
        except jsonschema.ValidationError:
            violations.append('invalid_tool_arguments')
            continue
        if name == 'get_odoo_sop':
            if not get_sop(args['sop_id'], args.get('inputs'))['success']:
                violations.append('invalid_sop_inputs')
            relevant |= case['failure_layer'] == 'sop_inputs'
        if name.endswith('find_records'):
            domain = args.get('domain')
            if isinstance(domain, str):
                try:
                    domain = json.loads(domain)
                except ValueError:
                    domain = None
            if not domain:
                violations.append('empty_domain')
            relevant |= case['failure_layer'] == 'empty_domain'
        if name.endswith('get_model_fields'):
            relevant |= case['failure_layer'] in {'unknown_field', 'schema_argument'}
        if name.endswith('execute_method') and args.get('method') == 'create_invoices':
            ids = (args.get('kwargs') or {}).get('ids') or (args.get('args') or [[]])[0]
            if not isinstance(ids, list) or len(ids) != 1:
                violations.append('multiple_invoice_wizards')
            relevant |= case['failure_layer'] == 'singleton'
        if name == 'read_observation':
            path = args.get('path', '$.result')
            if path not in {'$', '$.result'} and not path.startswith('$.result.'):
                violations.append('flattened_observation_path')
            relevant |= case['failure_layer'] == 'observation_path'
        if name.endswith('read_purchase_allocation'):
            relevant |= case['failure_layer'] == 'purchase_allocation'
        if name.endswith('read_record') and set(args.get('fields') or []) & refused_fields.get(args.get('model'), set()):
            violations.append('repeated_unknown_field')
        relevant |= case['failure_layer'] == 'normal_control'
    return {'status': 'failed' if violations else 'passed_intent' if relevant else 'needs_review',
            'violations': violations, 'business_verified': False, 'executed_tools': 0,
            'meaning': 'Contract/next-decision check only; identity and final ERP state require separate acceptance.'}


def prepare(output, *, case_id=None, recovery_response=None, final_feedback=None):
    rows = []
    cases = read(INDEX)
    if recovery_response is not None or final_feedback is not None:
        if not case_id or recovery_response is not None and final_feedback is not None:
            raise ValueError('Select one case and one corrective observation')
        cases = [next(c for c in cases if c['id'] == case_id)]
    for case in cases:
        source_dir = verify_source(case)
        number = case['causal_request']
        if recovery_response is not None:
            number = case['first_corrective_request']
            if not number:
                raise ValueError('No observed corrective request for this case')
        if case['failure_layer'] == 'purchase_allocation':
            # The earliest wrong allocation remains frozen; also retain the actual final-claim decision.
            number = '0033' if case['id'] == 'BP2010' else '0056'
        source_path = source_dir / f'{number}.request.json'
        source = read(source_path)
        payload, changes = candidate(source)
        if recovery_response is not None:
            payload, changes = recovery_candidate(source, case['tool_call_id'], recovery_response)
        historical = read(source_path.with_name(f'{number}.output.json'))
        if final_feedback is not None:
            if case['failure_layer'] != 'purchase_allocation' or historical.get('tool_calls'):
                raise ValueError('Final feedback requires an actual completed STOP decision')
            if final_feedback.get('status') != 'failed' or final_feedback.get('enforced') is not True:
                raise ValueError('Final feedback requires an independently bound failed readback')
            payload['messages'].extend([
                {'role': 'assistant', 'content': historical['text'],
                 'reasoning_content': historical.get('reasoning', '')},
                {'role': 'user', 'content': 'Host final readback failed in the isolated snapshot. '
                 'Continue correcting the purchase origins using the confirmed task and actual source quantities. '
                 'Keep quantities, spend, invoices and commitments intact; do not replay completed actions. '
                 'Inspect the scoped evidence before proposing an origin correction. Readback: '
                 + json.dumps(final_feedback, ensure_ascii=False, separators=(',', ':'))}])
            changes.append({'path': '/messages/-', 'producer': 'actual host-bound final readback',
                            'after': payload['messages'][-2:]})
        directory = output / case['id']
        write_once(directory / 'source.json', source)
        write_once(directory / 'candidate.json', payload)
        write_once(directory / 'changes.json', changes)
        write_once(directory / 'historical-output.json', historical)
        if recovery_response is not None or final_feedback is not None:
            write_once(directory / 'corrective-observation.json',
                       recovery_response if recovery_response is not None else final_feedback)
        rows.append({**case, 'decision_request': number, 'source_sha256': digest(source_path.read_bytes()),
                     'candidate_sha256': digest((directory / 'candidate.json').read_bytes())})
    sources = [Path(__file__), INDEX, ROOT/'src/erp_harness/app/runner.py',
               ROOT/'src/erp_harness/tools/native_tool_catalog.json', ROOT/'src/erp_harness/tools/sops.py',
               ROOT/'src/erp_harness/erp/purchase_allocation.py', ROOT/'src/erp_harness/erp/task_evidence.py',
               ROOT/'src/erp_harness/erp/actions.py', ROOT/'src/erp_harness/erp/reads.py',
               ROOT/'src/erp_harness/erp/read_failures.py', ROOT/'src/erp_harness/context/world.py',
               ROOT/'src/erp_harness/context/world_tools.py', ROOT/'src/erp_harness/tools/dynamic_tools.py',
               ROOT/'src/erp_harness/app/host.py', ROOT/'bench/adapters/harbor_agent.py',
               Path(__file__).with_name('prepare.py')]
    write_once(output / 'prepared.json', {'cases': rows, 'source_hashes': {
        p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sources},
        'automatic_paid_retries': 0, 'returned_tools_executed': 0})
    return rows


async def main(args):
    output = args.output.resolve()
    if not args.paid:
        rows = prepare(output, case_id=args.case,
                       recovery_response=read(args.recovery_response) if args.recovery_response else None,
                       final_feedback=read(args.final_feedback) if args.final_feedback else None)
        print(f'Prepared {len(rows)} actual incidents; API requests=0.')
        return
    if not args.case:
        raise ValueError('--paid requires one explicit --case; never bulk-replay the index')
    frozen = read(output/'prepared.json')
    for path, expected in frozen['source_hashes'].items():
        assert digest((ROOT/path).read_bytes()) == expected, path
    case = next(c for c in frozen['cases'] if c['id'] == args.case)
    verify_source(case)
    path = output / case['id'] / 'candidate.json'
    assert digest(path.read_bytes()) == case['candidate_sha256']
    result = await complete(read(path), output / case['id'] / 'api', api_key=os.environ['COMMAND_CODE_API_KEY'])
    judged = verdict(case, read(path), result)
    write_once(output / case['id'] / 'verdict.json', judged)
    print(json.dumps({'case': case['id'], 'usage': result['usage'], 'posts': result['posts'],
                      'verdict': judged}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case')
    parser.add_argument('--paid', action='store_true')
    parser.add_argument('--recovery-response', type=Path)
    parser.add_argument('--final-feedback', type=Path)
    asyncio.run(main(parser.parse_args()))
