"""Frozen GPU experiments for capability disclosure; no ERP execution or training."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT/'.runtime/openjev-context-q4-20260927'
OUT = ROOT/'.runtime/q4-routing-inputs-20260928'
VARIANTS = ('baseline', 'plain', 'roles', 'specific', 'chinese', 'abstain')
CONTEXT_TOKENS = 16384
read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))
encode = lambda x: json.dumps(x, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)


def focus(state):
    """Separate observations, outstanding actions and history without inventing completion."""
    state = copy.deepcopy(state)
    facts = state['business_facts']
    if isinstance(facts, dict) and facts.get('__world_table__'):
        columns = facts['columns']
        assert len(columns) == len(set(columns))
        assert all(len(r) == len(columns) for r in facts['rows'])
        facts = [dict(zip(columns, r)) for r in facts['rows']]
    ledger = state['action_ledger']
    finished = ledger.get('recent_finished', [])
    verified = {r['action_id'] for r in finished if r.get('status') == 'verified'}
    active, history = [], []
    for index,event in enumerate(state.get('recent_results', [])):
        (history if event.get('action_id') in verified else active).append(
            {'source_event_index':index,'event':event})
    # Lift repeated disclaimers into one history envelope; retain nonstandard values.
    constants = {'scope': 'historical_action_only', 'current_validity': 'unknown',
                 'task_completion': 'unknown', 'execution_permission': 'not_granted'}
    for row in finished:
        verification = row.get('verification', {})
        for k,v in constants.items():
            if verification.get(k) == v:
                del verification[k]
        verification.pop('original_bytes', None)
    return {
        'confirmed_task': state['task'],
        'observed_business_records': facts,
        'outstanding_action_ledger': {
            'coverage_complete': ledger.get('complete', False),
            'actions': ledger.get('unresolved', []),
            'empty_queue_does_not_mean_task_complete': True},
        'recent_events_without_verified_action_link': active,
        'historical_action_evidence': {
            'default_verification_metadata': constants,
            'actions': finished, 'finished_omitted': ledger.get('finished_omitted', 0),
            'events_linked_to_verified_actions': history,
            'status_counts': ledger.get('status_counts', {})},
        'base_tools_already_available': state.get('available_base_tools', []),
        'evidence_gaps': state.get('evidence_gaps', []),
        'other_context': {k:v for k,v in state.items() if k not in {
            'task','business_facts','action_ledger','recent_results','available_base_tools','evidence_gaps'}},
    }


EN = {
    'actions': 'Does the confirmed task still call for an Odoo change or its approval/preflight? Compare requested work with observed records. Previously required changes do not establish remaining work. Read-only checks use the base tools; posting chatter counts only when requested. An empty action queue alone does not prove completion.',
    'accounting': 'Does the current task call for receivable/payable aging or an accounting health report? This capability does not calculate purchase budgets or offer prices.',
    'knowledge': 'Does the next task step call for indexing or searching the local knowledge store, or inspecting its statistics? Include explicitly requested knowledge probes; normal record reads alone do not require this capability.',
    'attachments': 'Does the current task call for reading an Odoo attachment or its metadata?',
    'diagnostics': 'Does the next step call for investigating access, a tool failure, model relationships, data quality, schema, or business fit? Routine record reading alone is not diagnosis.',
    'async_reads': 'Does the next step call for submitting or managing a read-only background task?',
    'cross_instance': 'Does the current task call for searching or aggregating across multiple Odoo instances?',
    'migration': 'Does the current task call for migration planning, upgrade analysis, or addon source inspection?',
}
ZH = {
    'actions': '对照当前目标与已观察的单据，现在是否还有需要推进的Odoo业务变更或其预检、审批？过去要求过变更不等于现在仍需变更。普通回读已有基础工具。留言仅在任务要求时才算。待执行动作为空也不能单独证明任务完成。',
    'accounting': '当前任务是否需要应收应付账龄或账务健康报告？这个能力不负责采购预算、报价或成本比较。',
    'knowledge': '下一步是否需要建立、搜索本地知识索引或查看索引统计？明确要求的知识探针也算；普通单据回读不算。',
    'attachments': '当前任务是否需要读取Odoo附件内容或附件元数据？',
    'diagnostics': '下一步是否需要诊断权限、工具错误、模型关系、数据质量、schema或业务适配？普通单据查询不算诊断。',
    'async_reads': '下一步是否需要提交或管理只读后台任务？',
    'cross_instance': '当前任务是否需要跨多个Odoo实例搜索或汇总？',
    'migration': '当前任务是否需要迁移规划、升级分析或插件源码检查？',
}


def make_row(case, group, variant, order, questions):
    state = case['state']
    state = json.loads(state) if isinstance(state, str) else copy.deepcopy(state)
    old_question = questions[group]['instructions']
    spec = old_question[old_question.index(group+':'):]
    question = old_question
    descriptions = {'A': 'Current evidence supports a useful call in the next response.',
                    'B': 'No supported useful call in the next response.'}
    if variant != 'baseline':
        question = ('Should these tools be available for the next response to advance the confirmed task? '
                    'Select for remaining work, not just because their topic appears in the task. '
                    'Disclosure does not authorize execution. '+spec)
        descriptions = {'A': 'Yes, make these tools available now.', 'B': 'No, keep these tools hidden now.'}
    if variant in {'roles','specific','chinese','abstain'}:
        state = focus(state)
    if variant in {'specific','abstain'}:
        question = EN[group]+' Decide whether to expose the capability now; execution approval is separate. '+spec
    if variant == 'chinese':
        question = ZH[group]+' 判断是否在下一轮提供该能力；提供工具不等于授权执行。 '+spec
        descriptions = {'A':'需要，现在提供该能力。', 'B':'不需要，本轮不提供该能力。'}
    if variant == 'abstain':
        question += ' If the available evidence cannot settle this, select insufficient evidence.'
        descriptions['C'] = 'Insufficient evidence to decide; request host fallback.'
    ids = ['B','A'] if order == 'negative_first' else ['A','B']
    if variant == 'abstain':
        ids += ['C']
    return {'id': f'{case["id"]}|{group}|{variant}|{order}', 'case_id':case['id'],
        'group':group, 'variant':variant, 'order':order, 'state':state, 'question':question,
        'options':[{'id':i,'description':descriptions[i]} for i in ids]}


def freeze():
    OUT.mkdir(exist_ok=True)
    dataset_path = ROOT/'.runtime/laya-host-state-20260926/dataset-v16/cases.json'
    dataset = {r['id']:r for r in read(dataset_path)}
    question_path = ROOT/'.runtime/laya-host-state-20260926/joint-v8/frozen.json'
    questions = read(question_path)['questions'][0]
    tuning = read(PRIOR/'labels.json')['choices']
    holdout = read(OUT/'holdout-selection.json')['choices']
    assert not {r['id'] for r in tuning} & {r['id'] for r in holdout}
    sources = {}
    for c in tuning+holdout:
        case = dataset[c['id']]
        assert c['capability'] in case['required_groups' if c['target'] else 'unrelated_groups']
        path = Path(case['request_path'])
        assert sha(path) == case['request_sha256']
        sources[str(path)] = case['request_sha256']
    tuning_rows = [make_row(dataset[c['id']],c['capability'],v,o,questions)
        for v in VARIANTS for c in tuning for o in ['negative_first','positive_first']]
    holdout_rows = [make_row(dataset[c['id']],c['capability'],v,o,questions)
        for v in VARIANTS for c in holdout for o in ['negative_first','positive_first']]
    old = {r['id']:r for r in read(PRIOR/'inputs.json') if r['variant']=='original'}
    for r in tuning_rows:
        if r['variant']=='baseline':
            previous = old[r['id'].replace('|baseline|','|original|')]
            assert all(r[k] == previous[k] for k in ['state','question','options'])
    save(OUT/'tuning-inputs.json',tuning_rows)
    save(OUT/'holdout-inputs.json',holdout_rows)
    save(OUT/'labels.json',{'tuning':tuning,'holdout':holdout})
    for path in [Path(__file__), dataset_path, question_path, OUT/'holdout-selection.json',
                 OUT/'tuning-inputs.json',OUT/'holdout-inputs.json',OUT/'labels.json',
                 PRIOR/'provenance.json',PRIOR/'cuda-frozen.json']:
        sources[str(path.resolve())] = sha(path)
    save(OUT/'frozen.json',{'sources':sources,'variants':VARIANTS,'model':'Qwen3.5-4B Q4_K_M',
        'context_tokens':CONTEXT_TOKENS,
        'tuning_decisions':len(tuning_rows),'holdout_policy':'baseline plus best binary candidate only; freeze winner before holdout inference',
        'selection':'balanced_accuracy, then both_orders_correct, then lower mean input tokens; abstain not eligible',
        'paid_calls':0,'erp_calls':0})


def load_gpu(log_path):
    sys.path.insert(0,str(PRIOR))
    from provenance import check
    pins = check('2b')
    for paths in [pins['files']['q4'], read(PRIOR/'cuda-frozen.json')['files']]:
        for path,digest in paths.items():
            assert sha(path)==digest,path
    sys.path.insert(0,str(PRIOR/'deps-cuda'))
    import llama_cpp
    assert Path(llama_cpp.__file__).resolve()==PRIOR/'deps-cuda/llama_cpp/__init__.py'
    assert llama_cpp.llama_supports_gpu_offload()
    from semif_phase1 import llamacpp_backend as backend
    defaults = backend._cpu_model_params
    def gpu_params(lib):
        params=defaults(lib);params.n_gpu_layers=-1;params.main_gpu=0
        return params
    backend._cpu_model_params=gpu_params
    gguf=PRIOR/'Qwen_Qwen3.5-4B-Q4_K_M.gguf'
    assert sha(gguf)==pins['gguf_sha256']
    model,tok,metadata=backend.load_model(str(ROOT/'.runtime/openjev-official4b-20260927/model'),
        '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a',gguf,threads=8,context_tokens=CONTEXT_TOKENS)
    startup=log_path.read_text(encoding='utf8',errors='replace')
    layers=re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU',startup)
    assert layers and int(layers[-1][0])==int(layers[-1][1])>0 and 'CUDA0' in startup
    metadata.update(backend='llamacpp-cuda-adapter',device='cuda:0',n_gpu_layers=int(layers[-1][0]))
    return model,tok,metadata,backend


def run(phase):
    frozen=read(OUT/'frozen.json')
    for path,digest in frozen['sources'].items():
        assert sha(path)==digest,path
    gate=read(OUT/'independent-review.json')
    assert gate['approved_for_local_inference'] and gate['frozen_sha256']==sha(OUT/'frozen.json')
    rows=read(OUT/f'{phase}-inputs.json')
    if phase=='holdout':
        winner=read(OUT/'winner.json')
        assert winner['frozen_sha256']==sha(OUT/'frozen.json')
        assert winner['tuning_results_sha256']==sha(OUT/'tuning-results.jsonl')
        assert winner['variant'] in set(VARIANTS)-{'baseline','abstain'}
        review=read(OUT/'holdout-review.json')
        assert review['approved_for_local_inference'] and review['winner_sha256']==sha(OUT/'winner.json')
        assert review['frozen_sha256']==sha(OUT/'frozen.json')
        rows=[r for r in rows if r['variant'] in {'baseline',winner['variant']}]
        assert len(rows)==4*len(read(OUT/'labels.json')['holdout'])
    save(OUT/f'{phase}-attempt.json',{'at':time.time(),'rows':len(rows),'frozen_sha256':sha(OUT/'frozen.json')})
    model,tok,metadata,backend=load_gpu(OUT/f'{phase}-error.log')
    save(OUT/f'{phase}-gpu.json',metadata)
    with (OUT/f'{phase}-results.jsonl').open('x',encoding='utf8') as out:
        for i,row in enumerate(rows,1):
            r=backend.score(model,tok,row,metadata,max_tokens=CONTEXT_TOKENS)
            assert r['option_ids']==[o['id'] for o in row['options']]
            assert all(math.isfinite(p) and 0<=p<=1 for p in r['probabilities'])
            chosen=r['option_ids'][max(range(len(r['option_ids'])),key=lambda j:r['probabilities'][j])]
            r.update(prediction={'A':True,'B':False,'C':None}[chosen],at=time.time())
            out.write(encode(r)+'\n');out.flush()
            print(encode({'done':i,'of':len(rows),'id':row['id'],'prediction':r['prediction']}),flush=True)
    save(OUT/f'{phase}-summary.json',{'rows':len(rows),'status':'scored_not_accepted','paid_calls':0,'erp_calls':0})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','tuning','holdout'])
    action=p.parse_args().action
    freeze() if action=='freeze' else run(action)
