"""Actual incident coverage and the production API/parser boundary, without billing."""
import ast
import copy
import json
from pathlib import Path

import httpx
import pytest

from erp_harness.tools.sops import get_sop
from experiments.agent_regression.bench_recovery import (
    INDEX,
    candidate,
    definitions,
    followup_candidate,
    recovery_candidate,
    verdict,
)
from experiments.agent_regression.freeze import read
from experiments.agent_regression.runner import complete
from tests.test_agent_regression import response


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_all_actual_incidents_have_root_evidence_and_test_routes():
    cases = read(INDEX)
    assert len([c for c in cases if c['failure_layer'] != 'normal_control']) == 43
    layers = {c['failure_layer'] for c in cases}
    assert layers >= {'unknown_field', 'sop_inputs', 'empty_domain', 'singleton',
                      'observation_path', 'schema_argument', 'diagnostic_scope', 'purchase_allocation'}
    for c in cases:
        assert c['context_sha256'] and c['causal_request'] and c['tool_call_id']
        path, *names = c['offline_test'].split('::')
        tree = ast.parse((Path(__file__).resolve().parents[1] / path).read_text(encoding='utf-8'))
        for name in names:
            tree = next(n for n in tree.body if getattr(n, 'name', None) == name)
    # Synthetic contract boundaries remain offline; no invented paid cases.
    assert not get_sop('safe_write_review')['success']
    result = get_sop('safe_write_review', {'model': 'purchase.order', 'operation': 'create'})
    assert result['success']


def test_recovery_verdict_checks_sibling_fields_from_the_same_frozen_environment():
    case = next(row for row in read(INDEX) if row['id'] == 'BT2066-10')
    tools = definitions()
    payload = {'tools': [tools['mcp_odoo_get_model_fields'], tools['mcp_odoo_read_record']]}
    output = {'stop_reason': 'toolUse', 'tool_calls': [
        {'name': 'mcp_odoo_get_model_fields', 'arguments': {'model': 'mrp.workcenter', 'query': 'capacity'}},
        {'name': 'mcp_odoo_read_record', 'arguments': {'model': 'mrp.routing.workcenter', 'record_id': 1, 'fields': ['batch']}},
    ]}
    assert verdict(case, payload, output)['status'] == 'failed'
    assert verdict(case, payload, output)['violations'] == ['repeated_unknown_field']
    output['tool_calls'][1]['arguments']['fields'] = ['time_mode_batch']
    assert verdict(case, payload, output)['status'] == 'passed_intent'
    # A refusal in one installed schema does not establish absence in another.
    elsewhere = {**case, 'local_frozen_context': 'different-frozen-environment'}
    output['tool_calls'][1]['arguments']['fields'] = ['batch']
    assert verdict(elsewhere, payload, output)['violations'] == []


def test_all_observed_unknown_fields_refuse_before_data_rpc_and_offer_discovery():
    from erp_harness.erp.reads import NativeReads
    from tests.test_supply_context import SupplyClient
    fresh = read(INDEX.parent / 'self-debug-fresh-20261003/cases.json')['cases']
    fresh += [{'id': row['case_id'], 'failure_kind': 'unknown_field',
               'unknown_fields': row['public_failure']['recovery_request']['unknown_fields'],
               'model': row['failed_arguments']['model']}
              for row in read(INDEX.parent / 'tool-self-debug-20261003/fresh-fixed-cases.json')['cases']
              if row['tool'] == 'mcp_odoo_read_record']
    for case in [*read(INDEX), *fresh]:
        if case.get('failure_layer', case.get('failure_kind')) not in {'unknown_field', 'invalid_query_field'}:
            continue
        if 'unknown_fields' in case:
            fields, model = case['unknown_fields'], case['model']
        else:
            error = case['original_error']
            fields = ast.literal_eval(error.split('Unknown field(s) ', 1)[1].split(' on ', 1)[0])
            model = error.split(' on ', 1)[1].split(';', 1)[0]
        client = SupplyClient()
        client._add(model, [field for field in ['id', 'name', 'display_name'] if field not in fields], [{'id': 1, 'name': 'Known'}])
        client.read_records = lambda *a, **k: (_ for _ in ()).throw(AssertionError('invalid field reached data RPC'))
        runtime = NativeReads(client)
        result = runtime.call('read_record', {'model': model, 'record_id': 1, 'fields': fields})
        assert not result['success'] and result['reason_code'] == 'query_invalid', case['id']
        assert result['recovery_request']['unknown_fields'] == fields
        assert result['recovery_request']['arguments']['model'] == model
        assert result['recovery_request']['arguments']['instance'] == runtime.instance
        recovery = result['recovery_request']
        tool = definitions()[recovery['tool']]
        import jsonschema
        jsonschema.validate(recovery['arguments'], tool['function']['parameters'])


def test_actual_sop_and_empty_domain_arguments():
    from erp_harness.erp.reads import NativeReads
    from tests.test_supply_context import SupplyClient
    runtime = NativeReads(SupplyClient())
    for case in read(INDEX):
        args = case.get('failed_arguments') or {}
        if case['failure_layer'] == 'sop_inputs':
            result = get_sop(args['sop_id'], args.get('inputs'))
            assert not result['success'], case['id']
            assert result['reason_code'] == 'sop_inputs_invalid'
        elif case['failure_layer'] == 'empty_domain':
            result = runtime.call('find_records', args)
            assert not result['success'], case['id']
            assert result['reason_code'] == 'query_invalid'
        else:
            continue
        assert result.get('next_action') or result.get('recommended_next_action'), case['id']


def test_actual_schema_argument_calls_are_invalid():
    fresh = read(INDEX.parent / 'self-debug-fresh-20261003/cases.json')['cases']
    for case in [*read(INDEX), *fresh]:
        if case.get('failure_layer', case.get('failure_kind')) not in {'schema_argument', 'schema_argument_limit'}:
            continue
        payload = {'tools': [definitions()[case['tool']]]}
        output = {'stop_reason': 'toolUse', 'tool_calls': [
            {'name': case['tool'], 'arguments': case['failed_arguments']}]}
        assert verdict({**case, 'failure_layer': 'schema_argument'}, payload, output)['status'] == 'failed', case['id']


def test_corrective_replay_preserves_every_other_message_schema_and_id():
    source = {'messages': [{'role': 'system', 'content': 'contract'},
        {'role': 'assistant', 'reasoning_content': 'private', 'tool_calls': [
            {'id': 'observed', 'function': {'name': 'mcp_odoo_read_record', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'observed', 'content': '{"success":false,"error":"unknown field"}'}],
        'tools': []}
    original = copy.deepcopy(source)
    response = {'success': False, 'recovery_request': {'tool': 'mcp_odoo_get_model_fields'}}
    corrected, changes = recovery_candidate(source, 'observed', response)
    baseline, _ = candidate(source)
    assert source == original
    assert corrected['messages'][1] == baseline['messages'][1]
    assert corrected['messages'][2]['tool_call_id'] == 'observed'
    assert corrected['tools'] == baseline['tools']
    assert json.loads(corrected['messages'][2]['content']) == response
    assert changes[-1]['path'] == '/messages/2/content'
    with pytest.raises(ValueError, match='one actual failed'):
        recovery_candidate(source, 'invented-id', response)


def test_followup_requires_exact_call_receipts_and_preserves_usage_configuration():
    source = {'model': 'frozen', 'messages': [{'role': 'system', 'content': 'contract'}],
              'tools': [], 'stream': True}
    output = {'stop_reason': 'toolUse', 'text': 'Inspect.', 'reasoning': 'private',
              'tool_calls': [{'id': 'actual', 'name': 'mcp_odoo_read_purchase_allocation',
                              'arguments': {'purchase_ids': [1], 'order_ids': [2]}}]}
    result = {'actual': {'success': True, 'result': {'status': 'failed'}}}
    payload = followup_candidate(source, output, result)
    assert payload['messages'][:-2] == source['messages']
    assert payload['model'] == source['model'] and payload['tools'] == source['tools']
    assert payload['messages'][-1]['tool_call_id'] == 'actual'
    assert payload['messages'][-2]['reasoning_content'] == 'private'
    with pytest.raises(ValueError, match='exactly one'):
        followup_candidate(source, output, {'invented': result['actual']})
    with pytest.raises(ValueError, match='incomplete or STOP'):
        followup_candidate(source, {**output, 'stop_reason': 'unknown'}, result)


@pytest.mark.anyio
async def test_actual_observation_paths_and_new_read_replay(tmp_path):
    from unittest.mock import patch

    from erp_harness.context.world import WorldStore
    from erp_harness.context.world_tools import build_world_tools
    from tests.test_world import ENV
    with patch.dict('os.environ', ENV):
        world = WorldStore(tmp_path / 'world.jsonl')
        receipt = world.finish(world.begin('allocation-read', 'read_purchase_allocation',
            {'purchase_ids': [8], 'order_ids': [7]}, 'native'),
            '{"success":true,"result":{"products":[],"supplierinfo":[],"manufacturing":{}}}')
        assert world.search_observations(receipt['identity'])['items']
        reader = build_world_tools(world)[1]
        for case in read(INDEX):
            if case['failure_layer'] != 'observation_path':
                continue
            args = {**case['failed_arguments'], 'observation_ref': receipt['receipt_id']}
            rejected = await reader.execute('bad-path', args)
            assert rejected.details['error_class'] == 'invalid_path', case['id']
            args['path'] = '$.result.' + args['path'][2:]
            assert (await reader.execute('actual-path', args)).details['success'], case['id']


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['passed', 'failed'])
async def test_model_stop_cannot_skip_host_final_verification(tmp_path, status):
    from types import SimpleNamespace
    from unittest.mock import patch

    from erp_harness.app import runner
    from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
    from tests.test_purchase_allocation import seed

    actions, writer, _ = seed(tmp_path)
    actions.store.close()
    instruction = tmp_path / 'instruction.txt'
    instruction.write_text('核对采购后结束。', encoding='utf-8')
    args = SimpleNamespace(instruction_file=instruction, session_file=tmp_path / 'session.jsonl',
                           usage_file=tmp_path / 'usage.json', receipt_dir=tmp_path / 'run', max_turns=1)
    requests = []
    def handler(request):
        requests.append(request)
        body = {'choices': [{'delta': {'content': '已完成。'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
        return httpx.Response(200, text='data: ' + json.dumps(body) + '\n\ndata: [DONE]\n\n',
                              headers={'content-type': 'text/event-stream'})
    verification = {'status': status, 'enforced': True, 'retry_safe': False}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with (patch.object(runner.NativeReads, 'from_environment', return_value=actions.reads),
              patch('erp_harness.erp.actions._writer_from_reader', return_value=writer),
              patch('erp_harness.memory.attach', return_value=None),
              patch('erp_harness.erp.purchase_allocation.final_purchase_verification', return_value=verification) as check,
              patch.object(runner, 'OpenAICompatibleProvider', side_effect=lambda config: OpenAICompatibleProvider(config, client=client)),
              patch.dict('os.environ', {'LLM_API_KEY': 'test-only', 'LLM_BASE_URL': 'https://unused.invalid/v1',
                                       'LLM_MODEL': 'deepseek/test', 'LLM_PROVIDER': 'openai-compatible',
                                       'ODOO_TASK_EVIDENCE_FILE': ''})):
            if status == 'failed':
                with pytest.raises(RuntimeError, match='final purchase allocation'):
                    await runner.run(args)
            else:
                await runner.run(args)
            check.assert_called_once()
    assert len(requests) == 1 and not writer.calls
    assert read(args.receipt_dir / 'purchase-final-verification.json') == verification
    assert read(args.usage_file)['modelCalls'] == 1


@pytest.mark.anyio
async def test_frozen_candidate_preserves_context_and_api_rejects_invalid_calls(tmp_path):
    source = {'model': 'test', 'stream': True, 'messages': [
        {'role': 'system', 'content': 'contract'},
        {'role': 'assistant', 'content': 'history', 'reasoning_content': 'private'}],
        'tools': []}
    original = copy.deepcopy(source)
    payload, changes = candidate(source)
    assert source == original and payload['messages'][1:] == original['messages'][1:]
    assert changes
    seen = []
    result = await complete(payload, tmp_path, api_key='test-only', transport=httpx.MockTransport(
        lambda request: seen.append(request) or response()))
    case = {'failure_layer': 'singleton'}
    assert verdict(case, payload, result)['status'] == 'failed'  # Unpublished write from API.
    assert len(seen) == 1 and result['executed_tools'] == 0
    assert result['usage']['reasoning'] <= result['usage']['output']
    with pytest.raises(FileExistsError):
        await complete(payload, tmp_path, api_key='test-only', transport=httpx.MockTransport(lambda r: response()))
    assert len(seen) == 1


@pytest.mark.parametrize('name,args,layer', [
    ('mcp_odoo_find_records', {'model': 'sale.order', 'domain': []}, 'empty_domain'),
    ('get_odoo_sop', {'sop_id': 'safe_write_review'}, 'sop_inputs'),
    ('mcp_odoo_get_model_fields', {'model': 'sale.order', 'relevance': 'null'}, 'schema_argument'),
    ('mcp_odoo_execute_method', {'model': 'sale.advance.payment.inv', 'method': 'create_invoices',
                              'kwargs': {'ids': [1, 2]}}, 'singleton'),
    ('read_observation', {'observation_ref': 'observed', 'path': '$.products'}, 'observation_path'),
])
def test_same_error_is_rejected_in_any_business(name, args, layer):
    payload, _ = candidate({'messages': [{'role': 'system', 'content': 'contract'}], 'tools': [
        definitions()[name]]})
    output = {'stop_reason': 'toolUse', 'tool_calls': [{'name': name, 'arguments': args}]}
    assert verdict({'failure_layer': layer}, payload, output)['status'] == 'failed'
