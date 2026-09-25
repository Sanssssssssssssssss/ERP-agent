"""Offline historical-index and publication checks. No model, network, or business writes."""
import asyncio
import copy
from itertools import count
import json
import pytest
from types import SimpleNamespace

from experiments.tool_routing.decision_dataset import FIXTURES, split_family, state_from_request
from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS, DynamicToolController
from tests.test_dynamic_tools import fake_tools
from experiments.tool_routing.train_head import chosen, main as train_head
from experiments.tool_routing.train_decider import main as train_scorer
from experiments.tool_routing.intent_probe import with_prior_intent
from experiments.tool_routing.competitive_probe import selected
from experiments.tool_routing.reviewed_dataset import inclusion_labels, load_reviewed, validate
from experiments.tool_routing.reviewed_dataset import expanded_reviewed, training_labels
from experiments.tool_routing.routing_state import routing_state
from experiments.tool_routing.model_comparison import parse_selector, verdict
from experiments.tool_routing.train_head import concrete_questions
from experiments.tool_routing.train_reviewed import training_items, decision_metrics, selection_rank


def test_reviewed_training_covers_every_judged_format_and_masks_unknowns():
    train, _, _, groups = expanded_reviewed()
    specs = [concrete_questions(groups,label,first) for label in ['A','B'] for first in [False,True]]
    encoded = {r['id']:[[{'ids':[gi,v],'markers':[0,1]} for gi in range(len(groups))] for v in range(4)] for r in train}
    items = training_items(train,list(groups),specs,encoded)
    assert all('target' not in item for item in items)  # SDK reserves it for soft-label vectors.
    class_mass={}
    for item in items:
        key=item['group'],item['semantic_target']
        class_mass[key]=class_mass.get(key,0)+item['weight']
    assert class_mass['knowledge',1]==pytest.approx(class_mass['actions',1])
    for r in train:
        for variant in range(4):
            masses={t:sum(i['weight'] for i in items if i['case_id']==r['id'] and i['variant']==variant and i['semantic_target']==t) for t in [0,1]}
            if masses[1]:assert masses[0]==pytest.approx(masses[1])
    assert sum(i['weight'] for i in items)==pytest.approx(len(items))
    assert len({(i['case_id'],i['group'],i['variant']) for i in items})==4*sum(v is not None for r in train for v in training_labels(r,groups).values())
    for r in train:
        for g,target in training_labels(r,groups).items():
            matched = [i for i in items if i['case_id']==r['id'] and i['group']==g]
            if target is None:
                assert matched==[]
            else:
                assert {i['variant'] for i in matched}=={0,1,2,3}
                assert all(i['label']==([1,0,1,0] if target else [0,1,0,1])[i['variant']] for i in matched)
                assert max(i['weight'] for i in matched)==pytest.approx(min(i['weight'] for i in matched))
    # Duplicating common negatives must not hide failure on the scarce positive class.
    positive={'group':'actions','target':1,'selected':False,'ce':2.}
    negative={'group':'actions','target':0,'selected':False,'ce':0.}
    assert decision_metrics([positive,negative])['balanced_ce']==1.
    assert decision_metrics([positive,*([negative]*100)])['balanced_ce']==1.
    assert selection_rank({'missing_required':0,'unrelated':1,'balanced_ce':.5}) < selection_rank(
        {'missing_required':1,'unrelated':0,'balanced_ce':.01})


def test_expanded_labels_do_not_force_optional_preloads_or_leak_families():
    train, dev, evaluation, groups = expanded_reviewed()
    assert len(train)>6 and dev and len(evaluation)>9
    optional = next(r for r in train if r['id']=='2003:agent:0003')
    assert training_labels(optional,groups)['actions'] is None
    assert training_labels(optional,groups)['migration']==0
    with pytest.raises(ValueError,match='business family'):
        validate(train,evaluation,groups,[{**dev[0],'business_group':train[0]['business_group']}])
    assert parse_selector({'tool_calls':[{'name':'select_capabilities','arguments':{'capabilities':['missing']}}]},groups) is None
    assert verdict([],{'required_groups':[],'unrelated_groups':['migration'],'allowed_injection_sets':[[],['actions']]})['status']=='pass'


def test_reviewed_preference_is_supervision_but_not_business_requirement():
    train, _, _, groups = expanded_reviewed()
    row = copy.deepcopy(next(r for r in train if r['id']=='2003:agent:0003'))
    row.update(preferred_groups=['actions'],preference_rationale='Reviewed pending write supports preloading.')
    validate([row],[],groups)
    assert training_labels(row,groups)['actions']==1
    assert verdict([],row)['status']=='pass'
    with pytest.raises(ValueError,match='Preferred'):
        validate([{**row,'preference_rationale':''}],[],groups)
    with pytest.raises(ValueError,match='Preferred'):
        validate([{**row,'preferred_groups':['migration']}],[],groups)
    from experiments.tool_routing.collect_dataset import coverage
    report=coverage([row],groups)
    assert report['train']['groups']['actions']['1']=={'nodes':1,'families':1}


def test_projection_keeps_pre_approval_observations_and_publication_without_future():
    request={'messages':[
        {'role':'user','content':'Confirm PO\n# Odoo Environment\npassword=SECRET\nStage 6 knowledge probe: search first'},
        {'role':'assistant','tool_calls':[{'id':'a','function':{'name':'mcp_odoo_validate_write','arguments':'{"model":"purchase.order"}'}}]},
        {'role':'tool','tool_call_id':'a','content':'{"action_status":"pending_approval"}'},
        {'role':'assistant','content':'Waiting for approval.'},
        {'role':'user','content':'The host approved action a.'}],
        'tools':[{'function':{'name':'mcp_odoo_execute_approved_write'}},
                 {'function':{'name':'mcp_odoo_get_model_fields','description':'Read field definitions without extra capability.'}},
                 {'function':{'name':'diagnose_current_run','description':'Read ActionStore execution diagnostics.'}}],
        'response':{'secret_future':'NEVER'}}
    text=routing_state(request);state=json.loads(text)
    assert 'SECRET' not in text and 'NEVER' not in text and 'Stage 6' in state['goal']
    assert state['published_capabilities']==['actions']
    assert state['recent_observations'][0]['tool_call_id']=='a'
    assert state['recent_observations'][0]['observation']['action_status']=='pending_approval'
    assert state['recent_observations'][0]['arguments']['model']=='purchase.order'
    assert {t['name'] for t in state['available_base_tools']}=={'mcp_odoo_get_model_fields','diagnose_current_run'}
    assert 'ActionStore' in str(state['available_base_tools'])
    request['messages'] += [
        {'role':'assistant','content':None,'reasoning_content':'Execute the approved action, then read back.',
         'tool_calls':[{'id':'b','function':{'name':'mcp_odoo_execute_approved_write','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'b','content':'{"action_status":"verified"}'}]
    completed=json.loads(routing_state(request))
    assert completed['latest_update_source_message']==4
    assert completed['recent_observations'][0]['source_message']==6
    assert completed['recent_observations'][0]['observation']['action_status']=='verified'
    assert completed['recent_observations'][1]['observation']['action_status']=='pending_approval'
    assert completed['observation_order'].startswith('newest_first')
    assert completed['prior_assistant_intent_unverified']=='Execute the approved action, then read back.'
    request['messages'][-2]['content']='The action is submitted; read its status.'
    assert json.loads(routing_state(request))['prior_assistant_intent_unverified']==request['messages'][-2]['content']


def test_competitive_choice_maps_labels_without_probability_threshold():
    assert selected({'choice':'K'}, {'K':'base_only', 'A':'actions'}) == []
    assert selected({'choice':'A'}, {'K':'base_only', 'A':'actions'}) == ['actions']
    with pytest.raises(KeyError):
        selected({'choice':'missing'}, {'A':'actions'})


def test_reviewed_labels_preserve_preloads_unknowns_and_evaluation_boundary():
    train, evaluation, groups = load_reviewed()
    example = next(r for r in train if r['id'] == '2003:agent:0003')
    assert inclusion_labels(example, groups)['actions'] == 1
    assert inclusion_labels(example, groups)['diagnostics'] is None
    assert inclusion_labels(example, groups)['migration'] == 0
    with pytest.raises(ValueError, match='business family'):
        validate([{**train[0], 'business_group':evaluation[0]['business_group']}], evaluation, groups)
    with pytest.raises(ValueError, match='alias'):
        validate([{**train[0], 'request_sha256':evaluation[0]['request_sha256']}], evaluation, groups)
    bad = copy.deepcopy(train)
    bad[0]['unrelated_groups'].append('actions')
    with pytest.raises(ValueError, match='Contradictory'):
        validate(bad, evaluation, groups)
    with pytest.raises(ValueError, match='not complete'):
        validate([{**train[0], 'review_status':'pending'}], evaluation, groups)
    for entrypoint in [train_head, train_scorer]:
        with pytest.raises(ValueError, match='not reviewed'):
            entrypoint(SimpleNamespace(allow_reference_labels=False))


def test_router_reads_choice_before_rounded_probability():
    answer = {'type':'choice','choice':'B','probabilities':{'A':.5,'B':.5}}
    assert not chosen(answer,'A') and chosen(answer,'B')


def test_prior_intent_cannot_read_future_response_or_replace_observations():
    state=json.dumps({'goal':'Confirm the order','recent_observations':[{'state':'sale'}]})
    request={'messages':[{'role':'assistant','content':'First read the order.'}],
             'response':{'content':'FORBIDDEN_FUTURE'}}
    value=json.loads(with_prior_intent(state,request))
    hint=value.pop('previous_assistant_intent')
    assert value==json.loads(state) and hint['content']=='First read the order.'
    assert 'FORBIDDEN_FUTURE' not in json.dumps(hint)


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


def test_joint_training_covers_all_formats_without_repeating_pairs():
    from experiments.tool_routing.train_joint import unique_items, epoch_items, rank
    items = [{'case_id': c, 'group': 'actions', 'variant': v, 'label': v % 2, 'weight': w}
             for c in ['case-1', 'case-2'] for v in range(4) for w in [1., 2.]]
    unique = unique_items(items)
    assert len(unique) == 8 and sum(i['weight'] for i in unique) == 24
    seen = []
    for epoch in range(1, 5):
        rows = epoch_items(unique, epoch)
        assert len(rows) == 2 and sum(i['weight'] for i in rows) == 2
        assert len({(i['case_id'], i['group']) for i in rows}) == 2
        seen.extend((i['case_id'], i['variant']) for i in rows)
    assert len(set(seen)) == 8
    # Publishing everything must not win merely because it cannot miss a group.
    assert rank({'pass': 30, 'missing_required': 2, 'unrelated': 3}) < rank({'pass': 0, 'missing_required': 0, 'unrelated': 45})


def test_training_expansion_adds_intents_without_changing_holdout():
    import csv
    rows = list(csv.DictReader((FIXTURES/'collection_expansion.tsv').open(encoding='utf8'), delimiter='\t'))
    original = list(csv.DictReader((FIXTURES/'collection_scenarios.tsv').open(encoding='utf8'), delimiter='\t'))
    assert len({r['id'] for r in rows}) == len(rows)
    assert {r['id'] for r in rows}.isdisjoint({r['id'] for r in original})
    assert all(r['split'] == 'train' and r['variants'] in {'0', '1', '2'} for r in rows)
    groups = {g for r in rows for g in r['preferred'].split(',')} - {'-'}
    assert groups == set(CAPABILITY_GROUPS) - {'employee', 'time_off'}


def test_joint_sampling_repeats_rare_pairs_at_unit_weight_and_rotates_formats():
    from experiments.tool_routing.train_joint import epoch_items
    items=[{'case_id':c,'group':'knowledge','variant':v,'weight':w}
           for c,w in [('rare',100.),('common',.00001)] for v in range(4)]
    epochs=[epoch_items(items,e,True) for e in range(1,5)]
    assert all(len(rows)==2 and all(r['weight']==1. for r in rows) for rows in epochs)
    assert {r['case_id'] for rows in epochs for r in rows}=={'rare'}
    assert {r['variant'] for rows in epochs for r in rows}==set(range(4))
    assert epochs[0]==epoch_items(items,1,True) and items[0]['weight']==100.


def test_joint_source_balance_preserves_class_mass_and_historical_negatives():
    from experiments.tool_routing.train_joint import balance_sources
    cases = [{'id':'history','source_kind':'historical_business'},
             {'id':'probe','source_kind':'new_scripted_contract_probe'}]
    rows = [{'case_id':c,'group':'actions','variant':0,'semantic_target':t,'weight':w}
            for c,t,w in [('history',0,1.),('probe',0,99.),('history',1,20.)]]
    result = balance_sources(rows,cases)
    assert [r['weight'] for r in result] == [50.,50.,20.]
    assert sum(r['weight'] for r in result) == sum(r['weight'] for r in rows)
    assert rows[0]['weight'] == 1.  # Frozen inputs are not mutated.


def test_covered_exposures_keep_both_phases_and_exact_weight_mass():
    from collections import defaultdict
    from experiments.tool_routing.train_joint import balance_phases, epoch_items
    cases=[{'id':'pending','business_group':'same'}, {'id':'done','business_group':'same'},
           {'id':'rare','business_group':'other'}]
    items=[{'case_id':c,'group':'actions','variant':v,'semantic_target':t,'weight':w}
           for c,t,w in [('pending',1,.1),('done',0,9.9),('rare',1,100.)] for v in range(4)]
    balanced=balance_phases(items,cases)
    assert [r['weight'] for r in balanced]==[5.]*8+[100.]*4
    seen=set()
    for epoch in range(1,5):
        original=epoch_items(balanced,epoch)
        covered=epoch_items(balanced,epoch,cover_weighted_pairs=True)
        mass=defaultdict(float)
        for r in covered:
            assert 0 < r['weight'] <= 1
            mass[r['case_id']]+=r['weight'];seen.add((r['case_id'],r['variant']))
        assert dict(mass)==pytest.approx({r['case_id']:r['weight'] for r in original})
        assert len(mass)==3
    assert len(seen)==12


def test_training_disagreement_mask_preserves_required_labels_and_review():
    from experiments.tool_routing.reviewed_dataset import training_labels, validate
    import copy
    rows=json.loads((FIXTURES/'phase_training.json').read_text(encoding='utf8'))['historical']
    row=copy.deepcopy(next(r for r in rows if r['required_groups'] and r['unrelated_groups']))
    positive=next(iter(set(row['required_groups'])|set(row.get('preferred_groups',[]))))
    negative=next(iter(row['unrelated_groups']))
    groups={positive:{},negative:{}}
    row['training_mask_groups']=[negative]
    assert training_labels(row,groups)=={positive:1,negative:None}
    assert negative in row['unrelated_groups']
    row['training_mask_groups']=[positive]
    with pytest.raises(ValueError,match='disagreement masks'):
        validate([row],[],{g:{} for g in CAPABILITY_GROUPS})


def test_local_router_rejects_invalid_or_oversized_context_before_inference():
    from unittest.mock import Mock
    from experiments.tool_routing.router import CapabilityRouter
    router=object.__new__(CapabilityRouter)
    tokenizer=Mock(return_value={'input_ids':list(range(8000))})
    tokenizer.mask_token='<mask>'
    router.device_type='cuda'
    router.agent=SimpleNamespace(tok=tokenizer,cfg={'max_len':8192,'head_max_len':256},
                                 device=SimpleNamespace(type='cuda'),system_one=Mock())
    with pytest.raises(ValueError,match='complete model request'):
        router.route({'messages':[]})
    with pytest.raises(ValueError,match='provider message roles'):
        router.route({'messages':[{'role':'user','content':'Confirm the order.'},
                                  {'role':'toolResult','content':'Pi session data'}],'tools':[]})
    with pytest.raises(ValueError,match='exceeds model context'):
        router.route({'messages':[{'role':'user','content':'Confirm the requested order.'}],'tools':[]})
    router.agent.device.type='cpu'
    with pytest.raises(RuntimeError,match='Inference device changed'):
        router.route({'messages':[{'role':'user','content':'Confirm the requested order.'}],'tools':[]})
    router.agent.system_one.assert_not_called()


def test_router_label_review_uses_semantics_and_withholds_unstable_selection():
    from unittest.mock import Mock
    from experiments.tool_routing.router import CapabilityRouter
    router=object.__new__(CapabilityRouter)
    router.questions={'actions':{'type':'choice','criteria':{'B':'Leave unpublished','A':'Publish actions'}}}
    router.device_type='cuda';router.model_sha256='test'
    tokenizer=Mock(return_value={'input_ids':[1,2]});tokenizer.mask_token='<mask>'
    def answer(choice):
        return {'answers':{'actions':{'choice':choice,'probabilities':{'A':.99,'B':.01}}},
                'usage':{'input_tokens':10,'output_tokens':0}}
    predict=Mock(side_effect=[answer('A'),answer('B')])
    router.agent=SimpleNamespace(tok=tokenizer,cfg={'max_len':8192,'head_max_len':256},
                                 device=SimpleNamespace(type='cuda'),system_one=predict)
    request={'messages':[{'role':'user','content':'Confirm the order.'}],'tools':[]}
    result=router.route(request,verify_labels=True)
    assert result['status']=='ok' and result['capabilities']==['actions']
    assert result['inference_calls']==2 and result['usage']['input_tokens']==20
    assert predict.call_args_list[1].args[1]['actions']['criteria']=={'A':'Leave unpublished','B':'Publish actions'}
    predict.side_effect=[answer('A'),answer('A')]
    result=router.route(request,verify_labels=True)
    assert result['status']=='fallback' and result['use_existing_router']
    assert 'capabilities' not in result  # A host must not publish a rejected proposal.


def test_publication_fallback_preserves_host_selection_and_module_checks(tmp_path):
    from experiments.tool_routing.router import publish_next_turn
    calls=[];snapshots=[]
    controller=DynamicToolController(fake_tools(set(),calls),tmp_path/'publication.jsonl',count(1).__next__)
    controller.bind(lambda tools:snapshots.append(tuple(tools)))
    async def check():
        assert (await publish_next_turn(controller,{'status':'ok','capabilities':['actions']},'initial'))['applied']
        original=controller.tools;count_before=len(snapshots)
        for decision,flags in [({'status':'fallback'},{}),({'status':'ok','capabilities':[]},{'host_owns_selection':True}),
                               ({'status':'ok','capabilities':[]},{'unresolved_write':True})]:
            assert not (await publish_next_turn(controller,decision,'hold',**flags))['applied']
            assert controller.tools==original and len(snapshots)==count_before
        for groups in [['accounting'],['unknown'],['actions','actions'],None]:
            assert not (await publish_next_turn(controller,{'status':'ok','capabilities':groups},'invalid'))['applied']
            assert controller.tools==original
        assert (await publish_next_turn(controller,{'status':'ok','capabilities':[]},'read-only'))['applied']
        assert {'configure_odoo_tools','list_odoo_capabilities'}<={t.name for t in controller.tools}
        assert 'mcp_odoo_execute_approved_write' not in {t.name for t in controller.tools}
    asyncio.run(check())
    assert calls==['find_records']  # Publication probes availability; it never executes a business write.


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
