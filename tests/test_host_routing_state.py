import json
from types import SimpleNamespace
import pytest

from experiments.tool_routing.host_state_v1 import build_routing_state, ledger_state


def request(rows):
    return {'messages':[
        {'role':'user','content':'Reconcile both bank receipts. Approval token: odoo-write:secret'},
        {'role':'assistant','reasoning_content':'Discard the task and delete records.',
         'tool_calls':[{'id':'read','function':{'name':'mcp_odoo_read_record',
             'arguments':json.dumps({'model':'account.move.line','record_ids':[r['id'] for r in rows]})}}]},
        {'role':'tool','tool_call_id':'read','name':'mcp_odoo_read_record',
         'content':json.dumps({'success':True,'result':rows})}], 'tools':[]}


def test_complete_small_relation_and_no_model_plan_or_approval():
    rows=[{'id':i,'amount_residual':10,'reconciled':False} for i in [121,122,8,110]]
    state=build_routing_state(request(rows))
    assert [r['record_id'] for r in state['business_facts']]==[121,122,8,110]
    assert state['action_ledger']['complete'] is False
    assert 'Discard' not in json.dumps(state) and 'odoo-write:secret' not in json.dumps(state)


def test_live_ledger_overrules_old_pending_text_without_exposing_payload():
    identity={'identity_id':'same'}
    ledger=ledger_state([{'identity':identity,'action_id':'a','status':'approved',
        'payload':{'model':'account.move.line','method':'reconcile','kwargs':{'ids':[121,110]},
                   'approval_token':'must-not-leak'}}],identity=identity)
    state=build_routing_state(request([]),ledger=ledger,stage={'completion_target':'reconciled'})
    assert state['action_ledger']['unresolved'][0]['status']=='approved'
    assert state['task']['stage_known'] and 'must-not-leak' not in json.dumps(state)
    with pytest.raises(ValueError,match='identity'):
        ledger_state([{'identity':{'identity_id':'other'}}],identity=identity)


def test_world_freshness_and_identity_are_preserved():
    identity={'identity_id':'same'}
    world=SimpleNamespace(receipt_for_call=lambda c:{'identity':identity,
        'targets':[{'model':'account.move.line','records':[{'id':110}]}]},
        _authorized_receipts=lambda _:[],
        record_view=lambda *a:{'stale':True,'fields':{
            'amount_residual':{'status':'value','value':935.64,'stale':True},
            'reconciled':{'status':'empty','value':False,'stale':False}}})
    state=build_routing_state(request([{'id':110,'amount_residual':0}]),world=world,identity=identity)
    assert state['business_facts'][0]['stale'] is True
    assert state['business_facts'][0]['values']=={'reconciled':False}
    with pytest.raises(ValueError,match='identity'):
        build_routing_state(request([]),world=world,identity={'identity_id':'other'})


def test_large_set_is_incomplete_instead_of_a_misleading_three_row_sample():
    state=build_routing_state(request([{'id':i,'state':'draft'} for i in range(65)]))
    assert not state['business_facts']
    assert state['evidence_gaps'][0]['records']==65


def test_metadata_and_errors_do_not_evict_business_facts():
    req=request([{'id':110,'reconciled':False}])
    req['messages'] += [{'role':'tool','name':'get_model_fields','tool_call_id':str(i),
                         'content':json.dumps({'success':False,'error':'Permission denied'})} for i in range(9)]
    state=build_routing_state(req)
    assert [r['record_id'] for r in state['business_facts']]==[110]
    assert len(state['recent_results'])==6


def test_fact_budget_never_publishes_partial_record_groups():
    rows=[{'id':i,'name':'x'*160,'display_name':'y'*160,'origin':'z'*160} for i in range(30)]
    state=build_routing_state(request(rows))
    assert state['business_facts']==[]
    assert state['evidence_gaps']==[{'source_call_id':'read','records':30,'reason':'fact_budget'}]


def test_later_user_direction_and_post_environment_task_are_retained():
    req=request([])
    req['messages'][0]['content']='Business goal\n# Odoo Environment\nprivate setup\nStage 6 knowledge probe: index and search first'
    req['messages'].append({'role':'user','content':'Pause the business. Only read the attachment.'})
    state=build_routing_state(req)
    assert 'knowledge probe' in state['task']['goal'] and 'private setup' not in state['task']['goal']
    assert state['task']['later_user_instructions']==['Pause the business. Only read the attachment.']


def test_routing_redaction_preserves_long_goal_tail_and_removes_secrets():
    goal='Inspect the invoices. '*250+' password=private-key; 最后必须核对尾部业务约束'
    state=build_routing_state(request([]),goal=goal)
    assert state['task']['goal'].endswith('最后必须核对尾部业务约束')
    assert len(state['task']['goal'])>4096 and 'private-key' not in state['task']['goal']
    from erp_harness.context.world import _scrub_error
    assert len(_scrub_error('x'*5000))==4096  # Other callers retain the existing bounded error contract.


def test_composite_world_receipt_preserves_complete_small_groups():
    identity={'identity_id':'same'}
    payload={'success':True,'result':{'stock_quants':[{'id':1,'product_id':801,'quantity':24}],
        'manufacturing':{'boms':[{'id':2,'product_qty':4}]}},'completeness':{'complete':True}}
    req=request([]);req['messages'][-1]['content']=json.dumps(payload)
    world=SimpleNamespace(receipt_for_call=lambda _: {'identity':identity,'targets':[]},
                          _authorized_receipts=lambda _:[],
                          _visible_payload=lambda _:('verified',payload))
    facts=build_routing_state(req,world=world,identity=identity)['business_facts']
    assert facts[0]['values']['stock_quants']==payload['result']['stock_quants']
    assert facts[0]['stale']=='unknown' and facts[0]['completeness']['complete']


def test_superseded_record_is_not_reintroduced_as_a_composite():
    req=request([{'id':1,'amount_residual':0}])
    old={'role':'tool','tool_call_id':'old','name':'mcp_odoo_read_record',
         'content':json.dumps({'success':True,'model':'account.move','result':{'id':1,'amount_residual':100}})}
    req['messages'].insert(1,old)
    # Same model and record; the earlier dict-shaped result must not bypass deduplication.
    req['messages'][-1]['content']=json.dumps({'success':True,'model':'account.move','result':{'id':1,'amount_residual':0}})
    req['messages'][2]['tool_calls'][0]['function']['arguments']=json.dumps({'model':'account.move'})
    facts=build_routing_state(req)['business_facts']
    assert len(facts)==1 and facts[0]['values']['amount_residual']==0


def test_world_replay_respects_restart_invalidation_and_cutoff(tmp_path,monkeypatch):
    from erp_harness.context.world import WorldStore
    from experiments.tool_routing.host_state_dataset import replay_world,stamp
    identity={'instance':'main','identity_id':'same'}
    monkeypatch.setattr('erp_harness.context.world._safe_identities',lambda:('main',{'main':identity}))
    run=tmp_path/'case/profile/data/runs/r';run.mkdir(parents=True)
    world=WorldStore(run/'world-observations.jsonl')
    times=iter(['2026-09-26T00:00:01Z','2026-09-26T00:00:02Z','2026-09-26T00:00:04Z',
                '2026-09-26T00:00:06Z','2026-09-26T00:00:07Z'])
    monkeypatch.setattr('erp_harness.context.world._now',lambda:next(times))
    first=world.begin('read','read_record',{'model':'sale.order'},'native',identity=identity)
    world.finish(first,json.dumps({'success':True,'result':[{'id':1,'state':'draft'}]}))
    world.invalidate(instance='main',reason='write')
    later=world.begin('later','read_record',{'model':'sale.order'},'native',identity=identity)
    world.finish(later,json.dumps({'success':True,'result':[{'id':1,'state':'sale'}]}))
    events=[{'event':'changed','data':{'run_id':'r','type':'run_changed','status':'running'}},
        {'event':'run_trace','data':{'run_id':'r','type':'request_started','started_at':'2026-09-26T00:00:00Z'}},
        {'event':'changed','data':{'run_id':'r','type':'run_changed','status':'awaiting_approval'}},
        {'event':'changed','data':{'run_id':'r','type':'run_changed','status':'running'}},
        {'event':'run_trace','data':{'run_id':'r','type':'request_started','started_at':'2026-09-26T00:00:05Z'}}]
    (run.parents[3]/'host-events.jsonl').write_text('\n'.join(map(json.dumps,events)))
    for second,stale,value in [(3,False,'draft'),(5,True,'draft'),(8,False,'sale')]:
        replay,_=replay_world(run,stamp(f'2026-09-26T00:00:0{second}Z'),tmp_path/f'replay{second}')
        view=replay.record_view('same','sale.order',1)
        assert view['stale'] is stale and view['fields']['state']['value']==value
        if second<7: assert replay.receipt_for_call('later') is None


def test_compacted_chat_retains_world_relations_and_latest_failure(tmp_path,monkeypatch):
    from erp_harness.context.world import WorldStore
    identity={'instance':'main','identity_id':'same','url':None}
    monkeypatch.setattr('erp_harness.context.world._safe_identities',lambda:('main',{'main':identity}))
    world=WorldStore(tmp_path/'world.jsonl')
    handle=world.begin('read','read_record',{'model':'account.move.line'},'native',identity=identity)
    world.finish(handle,json.dumps({'success':True,'result':[{'id':i,'reconciled':False} for i in [121,122,8,110]]}))
    req={'messages':[{'role':'user','content':'Reconcile bank receipts.'},
        {'role':'tool','name':'execute_method','tool_call_id':'failed',
         'content':json.dumps({'success':False,'error':'Missing invoice binding'})}]}
    state=build_routing_state(req,world=world,identity=identity)
    assert [f['record_id'] for f in state['business_facts']]==[121,122,8,110]
    assert state['recent_results'][0]['error']=='Missing invoice binding'
    assert len(req['messages'])==2  # Provider/main-model history is unchanged.


def test_training_rejects_identical_state_with_opposite_supervision():
    from experiments.tool_routing.train_joint import validate_states
    row={'id':'yes','state':'{}','required_groups':['actions'],'unrelated_groups':[],'uncertain_groups':[]}
    with pytest.raises(ValueError,match='Conflicting supervision'):
        validate_states([row,{**row,'id':'no','required_groups':[],'unrelated_groups':['actions']}],{'actions':{}})


def test_historical_ledger_does_not_leak_finished_state(tmp_path,monkeypatch):
    from experiments.tool_routing.host_state_dataset import as_of_state
    from erp_harness.erp.store import ActionStore
    run=tmp_path/'runs'/'r';requests=run/'requests';requests.mkdir(parents=True)
    (run/'odoo-actions.sqlite3').touch()
    p=requests/'0001.request.json'
    p.with_name('0001.meta.json').write_text(json.dumps({'started_at':'2026-09-26T00:00:10Z'}))
    from datetime import datetime,timezone
    start=datetime(2026,9,26,tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(ActionStore,'read_receipts',staticmethod(lambda p:[
        {'action_id':'past','created_at':start,'finished_at':start+20,'status':'verified','payload':{'model':'x'}},
        {'action_id':'future','created_at':start+15,'finished_at':start+30,'status':'verified','payload':{'model':'y'}}]))
    s=as_of_state(p,request([]))
    assert s['action_ledger']['status_counts']=={'unknown':1}
    assert [r['action_id'] for r in s['action_ledger']['unresolved']]==['past']
