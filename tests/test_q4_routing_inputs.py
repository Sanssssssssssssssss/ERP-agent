"""Offline boundaries for the experiment's as-of state presentation."""
import copy
import unittest
from experiments.tool_routing.q4_inputs import focus


class ProjectionTests(unittest.TestCase):
    def test_history_cannot_hide_unrelated_failures_or_change_observed_facts(self):
        records = [{'model':'sale.order','record_id':7,'stale':True,
                    'values_fresh':'unknown','values':{'state':'sale','amount_total':19}}]
        pending = [{'action_id':'pending','status':'approved','verification':{'scope':'historical_action_only'}}]
        events = [{'action_id':'finished','success':False,'error':'previous approval failure'},
                  {'action_id':'unlinked','success':False,'error':'still unexplained'}]
        state = {'task':{'goal':'Confirm only; do not invoice'},'business_facts':records,
            'action_ledger':{'complete':False,'unresolved':pending,'recent_finished':[
                {'action_id':'finished','status':'verified','verification':{
                    'scope':'historical_action_only','current_validity':'unknown',
                    'evidence_omitted':True,'result':{'records':[{'id':7,'state':'sale'}]}}}]},
            'recent_results':events,'evidence_gaps':['identity unknown']}
        before=copy.deepcopy(state); result=focus(state)
        self.assertEqual(state,before)
        self.assertEqual(result['observed_business_records'],records)
        self.assertEqual(result['confirmed_task'],state['task'])
        self.assertEqual(result['outstanding_action_ledger']['actions'],pending)
        self.assertFalse(result['outstanding_action_ledger']['coverage_complete'])
        self.assertEqual(result['recent_events_without_verified_action_link'],
                         [{'source_event_index':1,'event':events[1]}])
        history=result['historical_action_evidence']
        self.assertEqual(history['events_linked_to_verified_actions'],
                         [{'source_event_index':0,'event':events[0]}])
        self.assertTrue(history['actions'][0]['verification']['evidence_omitted'])
        self.assertEqual(history['actions'][0]['verification']['result'],
                         state['action_ledger']['recent_finished'][0]['verification']['result'])
        self.assertEqual(result['evidence_gaps'],['identity unknown'])

    def test_tabular_facts_preserve_all_values_and_reject_bad_shape(self):
        state={'task':{},'action_ledger':{},'business_facts':{'__world_table__':True,
            'columns':['model','record_id','values'],'rows':[['stock.move',3,{'quantity':4}]]}}
        self.assertEqual(focus(state)['observed_business_records'],
                         [{'model':'stock.move','record_id':3,'values':{'quantity':4}}])
        state['business_facts']['rows'][0].pop()
        with self.assertRaises(AssertionError): focus(state)


if __name__=='__main__':
    unittest.main()
