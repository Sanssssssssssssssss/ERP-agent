"""Offline historical-index and publication checks. No model, network, or business writes."""
import asyncio
import copy
from itertools import count
import json

from experiments.tool_routing.decision_dataset import FIXTURES, split_family, state_from_request
from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS, DynamicToolController
from tests.test_dynamic_tools import fake_tools


def test_state_uses_only_prefix_and_keeps_parallel_observations():
    request = {'messages': [
        {'role': 'user', 'content': 'Confirmed current phase:\nConfirm S01499. Do not invoice.\nCompletion target: confirmed.'},
        {'role': 'assistant', 'tool_calls': [{'id': 'old-call', 'function': {'name': 'read_record'}}]},
        {'role': 'tool', 'name': 'read_record', 'content': '{"result":{"state":"draft"}}'},
        {'role': 'tool', 'name': 'diagnose_current_run', 'content': '{"success":false,"error":"missing binding"}'},
    ]}
    original = state_from_request(request)
    altered = copy.deepcopy(request)
    altered['oracle'] = {'expected_groups': ['FORBIDDEN_FUTURE_SENTINEL']}
    altered['response'] = {'tool_calls': ['FORBIDDEN_FUTURE_SENTINEL']}
    assert state_from_request(altered) == original
    state = json.loads(original)
    assert state['goal'] == 'Confirm S01499. Do not invoice.'
    assert len(state['recent_observations']) == 2
    assert state['recent_observations'][0]['observation']['error'] == 'missing binding'


def test_routing_view_preserves_nested_business_state_and_call_target():
    goal = 'Confirm the order. ' + 'long order list ' * 100 + 'Post the invoice. ' + 'constraints ' * 100
    request = {'messages': [
        {'role': 'user', 'content': goal},
        {'role': 'assistant', 'tool_calls': [{'id': 'read', 'function': {
            'name': 'mcp_odoo_read_record', 'arguments': '{"model":"sale.order","record_id":42}'}}]},
        {'role': 'tool', 'tool_call_id': 'read', 'content':
            '{"success":true,"verification":{"evidence":{"records":[{"state":"sale","id":42}]}}}'},
    ]}
    view = json.loads(state_from_request(request))
    assert view['goal'] == goal  # Mid-task obligations must not disappear behind a character cap.
    observation = view['recent_observations'][0]
    assert observation['tool'] == 'mcp_odoo_read_record'
    assert observation['target'] == {'model': 'sale.order', 'record_id': 42}
    record = observation['observation']['verification']['evidence']['records'][0]
    assert record['state'] == 'sale' and record['id'] == 42


def test_task_splits_preserve_reserved_families():
    assert split_family('erpbench:2262') == 'test'
    assert split_family('erpbench:2205') == 'dev'
    for family in ['enterprise:E01', 'enterprise:E04', 'enterprise:E06', 'erpbench:2278']:
        assert split_family(family) == 'test'


def test_real_history_index_has_provenance_and_honest_gaps():
    rows = [json.loads(s) for s in (FIXTURES/'history_cases.jsonl').read_text(encoding='utf8').splitlines()]
    assert rows and len({r['id'] for r in rows}) == len(rows)
    for row in rows:
        assert row['response_path'] and len(row['response_sha256']) == 64
        assert set(row['actual_groups']) <= set(CAPABILITY_GROUPS)
        if row['decision_eligible']:
            assert row['request_path'] and len(row['request_sha256']) == 64
        if row['training_eligible']:
            assert row['decision_eligible'] and not row['needs_review']
        for call in row['calls']:
            assert call['tool_call_id']
    inventory = json.loads((FIXTURES/'history_inventory.json').read_text(encoding='utf8'))
    assert set(inventory['counts']) == set(CAPABILITY_GROUPS)
    for group, stats in inventory['counts'].items():
        actual = sum(call.get('capability') == group for row in rows for call in row['calls'])
        assert stats['actual_model_calls'] == actual


def test_historical_group_sets_use_existing_complete_replacement(tmp_path):
    rows = [json.loads(s) for s in (FIXTURES/'history_cases.jsonl').read_text(encoding='utf8').splitlines()]
    selections = sorted({tuple(sorted(row['actual_groups'])) for row in rows if row['decision_eligible']})
    calls = []
    controller = DynamicToolController(fake_tools({'account.move', 'hr.employee', 'hr.leave.report.calendar'}, calls),
                                       tmp_path/'publication.jsonl', count(1).__next__)
    snapshots = []
    controller.bind(lambda tools: snapshots.append(tuple(tools)))
    base = {t.name for t in controller.tools}
    configure = controller.tools[-1]

    async def replay():
        for selected in [*selections, ()]:
            result = (await configure.execute('offline', {'capabilities': list(selected)})).details
            assert result['success'] and result['available_next_turn']
            expected = base | {'mcp_odoo_'+t for g in selected for t in CAPABILITY_GROUPS[g]['tools']}
            assert {t.name for t in snapshots[-1]} == expected
            assert set(result['published_tools']) == expected
    asyncio.run(replay())
    assert calls == ['find_records']  # Only the mocked installation probe; no business tool executes.
