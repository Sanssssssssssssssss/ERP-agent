"""Context experiments must preserve business facts and execution authority."""
import copy
from experiments.tool_routing.laya_context_replay import candidates
from erp_harness.app.laya_state import build_routing_state, ledger_state
from tests.test_routing_context import prefix


def test_context_candidates_preserve_source_and_separate_historical_error():
    request = prefix()
    request['messages'][2]['content'] = '{"success":false,"error":"field unavailable"}'
    state = build_routing_state(request, ledger=ledger_state([
        {'action_id':'a','status':'verified','finished_at':123,
         'payload':{'model':'sale.order','record_ids':[1]},
         'verification':{'status':'satisfied','evidence':{'records':[{'id':1,'state':'sale'}]}}}]))
    before = copy.deepcopy(state)
    result = candidates(request, state)
    assert state == before
    for name in ('v7_event_scope', 'v7_working_events'):
        candidate = result[name]
        assert candidate['task'] == state['task']
        assert candidate['business_facts'] == state['business_facts']
        assert candidate['execution_permission'] == 'not_granted_by_disclosure'
        action = candidate['action_ledger']['recent_finished'][0]
        assert (action['action_id'], action['status'], action['record_ids']) == ('a','verified',[1])
        assert action['verification']['result'] == state['action_ledger']['recent_finished'][0]['verification']['result']
    historical = next(e for e in result['v7_event_scope']['recent_results'] if e['source_call_id']=='read')
    assert historical['event_scope']=='historical_result' and historical['error']=='field unavailable'
    working = next(e for e in result['v7_working_events']['recent_results'] if e['source_call_id']=='read')
    assert working['success'] is False and 'error_details' in working and 'error' not in working
    current = next(e for e in result['v7_event_scope']['recent_results'] if e['source_call_id']=='confirm')
    assert current['event_scope']=='current_result'
