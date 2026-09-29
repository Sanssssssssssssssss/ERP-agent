import copy
import json

import pytest

from erp_harness.app.routing_context import assemble_context, selection_messages
from erp_harness.app.routing_state import build_routing_state, ledger_state


def test_batch_preserves_context_and_exact_candidate_schemas():
    from erp_harness.app.routing_context import selection_batch
    context = assemble_context(prefix())
    before = copy.deepcopy(context)
    packet = json.loads(selection_batch(context, ['actions', 'attachments'])[1]['content'])
    assert context == before == packet['context']
    for group, candidate in zip(['actions', 'attachments'], packet['candidates']):
        original = json.loads(selection_messages(context, group, [{'id': 'A'}, {'id': 'B'}])[1]['content'])
        assert candidate == original['candidate']
    assert packet['decisions'] == {'A': 'EXPOSE', 'B': 'HIDE'}
    with pytest.raises(ValueError):
        selection_batch(context, ['actions', 'actions'])


def test_live_ledger_preserves_verified_evidence_without_claiming_task_completion():
    from erp_harness.app.routing_context import host_ledger
    rows = [{'action_id': 'a', 'status': 'verified', 'finished_at': 123,
             'payload': {'model': 'sale.order'}, 'verification': {'status': 'satisfied', 'evidence': {'records': [{'id': 1}]}}}]
    result = host_ledger(rows, None)
    verification = result['recent_finished'][0]['verification']
    assert verification['result'] == rows[0]['verification']
    assert verification['task_completion'] == verification['current_validity'] == 'unknown'
    rows[0]['finished_at'] = float('nan')
    assert host_ledger(rows, None)['recent_finished'][0]['verification']['result'] is None


def prefix():
    return {'messages': [
        {'role': 'user', 'content': 'Confirm this order only; no shipping or invoices.'},
        {'role': 'assistant', 'tool_calls': [{'id': 'read', 'function': {'name': 'mcp_odoo_read_record', 'arguments': '{"model":"sale.order"}'}}]},
        {'role': 'tool', 'tool_call_id': 'read', 'content': '{"success":true,"result":{"id":1,"state":"draft"}}'},
        {'role': 'assistant', 'tool_calls': [{'id': 'confirm', 'function': {'name': 'mcp_odoo_execute_method', 'arguments': '{"model":"sale.order"}'}}]},
        {'role': 'tool', 'tool_call_id': 'confirm', 'content': json.dumps({'success': True, 'action_id': 'a', 'action_status': 'verified',
            'verification': {'status': 'satisfied', 'evidence': {'records': [{'id': 1, 'state': 'sale'}]}}})}],
        'tools': [{'function': {'name': 'mcp_odoo_read_record'}}]}


def test_live_and_replay_share_context_without_rewriting_facts_or_granting_approval():
    request = prefix()
    snapshot = build_routing_state(request)
    before = copy.deepcopy(snapshot)
    context = assemble_context(request, state=snapshot)
    assert context == assemble_context(request) and before == snapshot
    fact = next(f for f in context['observations'] if f.get('model') == 'sale.order')
    assert 'state' not in fact['values']
    assert before['business_facts'][0]['values']['state'] == 'draft'
    assert context['historical_evidence_refs'][0]['newer_verifications'] == [{'source_call_id': 'confirm', 'fields': ['state']}]
    assert context['verified_action_observations'][0]['verified_records'] == [{'id': 1, 'state': 'sale'}]
    assert context['action_ledger']['actions'] == []  # Tool receipts cannot grant host approval.
    assert context['verified_action_observations'][0]['current_database_validity'] == 'unknown'
    assert context['available_base_tools'] == [{'name': 'mcp_odoo_read_record', 'description': ''}]
    assert 'execution_permission' not in context


def test_no_supersession_from_future_duplicate_or_different_record_model():
    for mode in ('future', 'duplicate', 'different_model', 'different_instance'):
        request = prefix()
        if mode == 'future':
            request['messages'] = request['messages'][:3]
        elif mode == 'duplicate':
            request['messages'].append(copy.deepcopy(request['messages'][-1]))
        elif mode == 'different_model':
            request['messages'][3]['tool_calls'][0]['function']['arguments'] = '{"model":"purchase.order"}'
        else:
            request['messages'][3]['tool_calls'][0]['function']['arguments'] = '{"model":"sale.order","instance":"other"}'
        context = assemble_context(request)
        assert context['historical_evidence_refs'] == []


def test_real_approval_and_rejection_survive_fixed_policy_cleanup():
    request = prefix()
    ledger = ledger_state([{'action_id': 'a', 'status': 'rejected', 'payload': {'model': 'sale.order'}},
                           {'action_id': 'b', 'status': 'approved', 'payload': {'model': 'purchase.order'}}])
    state = build_routing_state(request, ledger=ledger)
    state['evidence_gaps'] = [{'reason': 'fact_budget', 'records': 20}]
    state['execution_permission'] = 'denied_by_acl'
    state['action_ledger']['unresolved'][0]['verification'] = {'scope': 'historical_action_only', 'execution_permission': 'not_granted'}
    before = copy.deepcopy(state)
    context = assemble_context(request, state=state)
    assert state == before and context['additional_authorization_evidence'] == 'denied_by_acl'
    assert {a['status'] for a in context['action_ledger']['actions']} == {'rejected', 'approved'}
    assert all(a.get('verification', {}).get('scope') != 'historical_action_only'
               for a in context['action_ledger']['actions'])
    assert context['input_coverage'] == [{'reason': 'observation_size_limit', 'records': 20},
        {'section': 'action_receipts', 'availability': 'all_host_receipts_present'}]
    assert 'coverage_complete' not in context['action_ledger']
    assert context['current_tool_events'][0]['as_of_ledger_status'] == 'rejected'


def test_choices_use_runtime_catalog_and_reject_ambiguous_input():
    request = prefix()
    context = assemble_context(request)
    messages = selection_messages(context, 'accounting', [{'id': 'B'}, {'id': 'A'}])
    payload = json.loads(messages[1]['content'])
    assert [(o['letter'], o['decision']) for o in payload['options']] == [('A', 'HIDE'), ('B', 'EXPOSE')]
    assert all('accounting tools' in o['meaning'] for o in payload['options'])
    from erp_harness.tools.router import native_tool_catalog
    catalog = {t.name.removeprefix('mcp_odoo_'): t.description for t in native_tool_catalog()}
    tools = {t['name']: t['description'] for t in payload['candidate']['tools']}
    assert 'receivable_payable_aging' in tools
    assert tools['accounting_health_summary'] == catalog['accounting_health_summary']
    with pytest.raises(ValueError):
        assemble_context(request, state=build_routing_state(request), goal='different scope')
    with pytest.raises(ValueError):
        selection_messages(context, 'actions', [{'id': 'A'}, {'id': 'A'}])


def test_world_merged_fields_are_not_dated_by_the_receipt_that_triggered_the_view():
    request = prefix()
    for stale in (False, True):
        state = build_routing_state(request)
        state['business_facts'][0].update(stale=stale, values={'id': 1, 'state': 'sale'})
        context = assemble_context(request, state=state)
        assert context['historical_evidence_refs'] == []


def test_current_errors_preserved_and_old_approval_errors_follow_host_ledger():
    request = prefix()
    request['messages'][-1]['content'] = json.dumps({'success': False, 'action_id': 'a',
        'action_status': 'pending_approval', 'error': 'trusted host approval is required'})
    ledger = ledger_state([{'action_id': 'a', 'status': 'approved', 'payload': {'model': 'sale.order'}}])
    context = assemble_context(request, ledger=ledger)
    assert not context['current_tool_events']
    assert context['historical_tool_event_refs'][0]['resolution'] == 'superseded_by_host_ledger'
    assert 'error_when_observed' not in context['historical_tool_event_refs'][0]
    # Without a newer host status, the current error remains actionable.
    context = assemble_context(request)
    assert context['current_tool_events'][0]['error'] == 'trusted host approval is required'
    assert context['action_ledger']['actions'] == []
    # An older unlinked failure is not silently labelled resolved.
    request['messages'].append({'role': 'assistant', 'tool_calls': []})
    context = assemble_context(request)
    assert context['historical_tool_event_refs'][0]['resolution'] == 'not_inferred'
    assert context['historical_tool_event_refs'][0]['error_when_observed'] == 'trusted host approval is required'


def test_selector_keeps_requested_outcome_without_executor_completion_instructions():
    request = prefix()
    stage = {'version': 1, 'business_type': 'invoice_delivery', 'completion_target': 'sent'}
    state = build_routing_state(request, stage=stage)
    context = assemble_context(request, state=state)
    assert 'acceptance' not in context['current_task']
    assert context['current_task']['requested_outcome'] == stage
    assert context['action_ledger']['actions'] == []
    state['task']['stage_known'] = False
    assert 'acceptance' not in assemble_context(request, state=state)['current_task']
    state['task'].update(stage_known=True, confirmed_stage={'business_type': 'purchase', 'completion_target': 'sent'})
    assert 'acceptance' not in assemble_context(request, state=state)['current_task']
    assert 'acceptance' not in assemble_context(request)['current_task']


def test_pdf_effect_keeps_invoice_identity_and_cannot_become_smtp_evidence():
    request = prefix()
    request['tools'][0]['function']['description'] = 'Read current records under the caller identity.'
    request['messages'][3]['tool_calls'][0]['function']['arguments'] = '{"model":"account.move.send.wizard"}'
    evidence = {'record_model': 'account.move', 'invoice_records': [{'id': 31, 'invoice_pdf_report_id': 580}]}
    request['messages'][-1]['content'] = json.dumps({'success': True, 'action_id': 'pdf', 'action_status': 'verified',
        'verification': {'status': 'satisfied', 'evidence': evidence}})
    context = assemble_context(request)
    effect = context['verified_action_observations'][0]
    assert effect['model'] == 'account.move' and effect['verified_records'] == evidence['invoice_records']
    assert 'delivery' not in effect['verified_effect']
    assert context['available_base_tools'][0]['description'] == request['tools'][0]['function']['description']
    evidence['delivery'] = 'smtp_accepted'
    request['messages'][-1]['content'] = json.dumps({'success': True, 'action_id': 'send', 'action_status': 'verified',
        'verification': {'status': 'satisfied', 'evidence': evidence}})
    assert assemble_context(request)['verified_action_observations'][0]['verified_effect']['delivery'] == 'smtp_accepted'
