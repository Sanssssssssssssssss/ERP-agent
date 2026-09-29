"""Lossless historical-record deduplication, evaluated before the reserved holdout."""
import copy
import json
import sys
import time
from . import q4_inputs as base

OUT=base.OUT/'dedup'
read,save,sha,encode=base.read,base.save,base.sha,base.encode


def project(row):
    row=copy.deepcopy(row);state=row['state'];observed=state['observed_business_records']
    lookup={}
    for i,r in enumerate(observed):
        if r.get('values_fresh') is True:
            key=(r['model'],r['record_id']);assert key not in lookup
            lookup[key]=(i,r.get('values',{}))
    def walk(node,model,record=False,restore=False):
        if isinstance(node,list):return [walk(v,model,record,restore) for v in node]
        if not isinstance(node,dict):return node
        model=node.get('record_model',node.get('model',model))
        result={k:walk(v,model,k=='records',restore) for k,v in node.items()}
        if restore and '_equal_observed_fields' in result:
            ref=result.pop('_equal_observed_fields')
            values=observed[ref['index']]['values']
            result.update({k:values[k] for k in ref['fields']})
        elif record and (model,node.get('id')) in lookup:
            index,values=lookup[model,node['id']]
            fields=[k for k,v in result.items() if k!='id' and k in values and encode(v)==encode(values[k])]
            if fields:
                assert '_equal_observed_fields' not in result
                result={k:v for k,v in result.items() if k not in fields}
                result['_equal_observed_fields']={'index':index,'fields':fields}
        return result
    for action in state['historical_action_evidence']['actions']:
        v=action.get('verification',{})
        if v.get('result') is not None:
            before=copy.deepcopy(v['result']);v['result']=walk(before,action.get('model'))
            assert walk(v['result'],action.get('model'),restore=True)==before
    return row


def prepare():
    OUT.mkdir(exist_ok=True)
    for phase in ['tuning','holdout']:
        rows=[]
        for row in read(base.OUT/f'{phase}-inputs.json'):
            if row['variant'] not in {'roles','specific'}:continue
            new=project(row);variant='dedup_'+row['variant']
            new['variant']=variant;new['id']=row['id'].replace('|'+row['variant']+'|','|'+variant+'|')
            rows.append(new)
        save(OUT/f'{phase}-inputs.json',rows)
    sources={str(p):sha(p) for p in [base.OUT/'frozen.json',base.OUT/'labels.json',
        OUT/'tuning-inputs.json',OUT/'holdout-inputs.json',__file__]}
    save(OUT/'frozen.json',{'sources':sources,'tuning_decisions':36,'holdout_unrun':True,
        'meaning':'Only equal historical fields reference fresh observations. Conflicting values retained. Exact roundtrip asserted.'})


def run():
    for path,digest in read(base.OUT/'frozen.json')['sources'].items():assert sha(path)==digest,path
    for path,digest in read(OUT/'frozen.json')['sources'].items():assert sha(path)==digest,path
    review=read(OUT/'independent-review.json')
    assert review['approved_for_local_inference'] and review['frozen_sha256']==sha(OUT/'frozen.json')
    save(OUT/'attempt.json',{'at':time.time(),'rows':36,'frozen_sha256':sha(OUT/'frozen.json')})
    model,tok,metadata,backend=base.load_gpu(OUT/'error.log');save(OUT/'gpu.json',metadata)
    rows=read(OUT/'tuning-inputs.json');assert len(rows)==36
    with (OUT/'results.jsonl').open('x',encoding='utf8') as stream:
        for i,row in enumerate(rows,1):
            r=backend.score(model,tok,row,metadata,max_tokens=base.CONTEXT_TOKENS)
            assert r['option_ids']==[o['id'] for o in row['options']]
            assert all(base.math.isfinite(p) and 0<=p<=1 for p in r['probabilities'])
            choice=r['option_ids'][max(range(2),key=lambda j:r['probabilities'][j])]
            r.update(prediction=choice=='A',at=time.time());stream.write(encode(r)+'\n');stream.flush()
            print(encode({'done':i,'of':36,'id':row['id'],'prediction':r['prediction']}),flush=True)
    save(OUT/'summary.json',{'rows':36,'paid_calls':0,'erp_calls':0,'status':'scored_not_accepted'})


if __name__=='__main__':
    {'prepare':prepare,'run':run}[sys.argv[1]]()
