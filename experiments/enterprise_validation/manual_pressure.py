"""Read-only concurrency and local attachment queue. No model or business writes."""
import base64
import io
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
from erp_harness.app.host import Workbench
from erp_harness.erp.gateway import Json2ReadClient
from erp_harness.erp.reads import NativeReads
from tests.test_material_extract import image_document, samples

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'.runtime/manual-business-20260930'


def main():
    account=json.loads((ROOT/'.runtime/enterprise-validation-20260922/snapshots/baseline-10000/accounts.json').read_text('utf8'))['manager']
    result={'reads':[], 'business_writes':0, 'model_calls':0, 'scope':'Tool concurrency only; desktop remains one active business'}
    process=psutil.Process();peak=process.memory_info().rss
    for concurrency in [1,5,20]:
        def worker(index):
            client=Json2ReadClient(url='http://127.0.0.1:18219',db='erp_manual_20260930_review',username=account['login'],api_key=account['api_key'])
            count=0; original=client._json2_call_once
            def counted(*args,**kwargs):
                nonlocal count
                count+=1;return original(*args,**kwargs)
            client._json2_call_once=counted
            reads=NativeReads(client);rows=[]
            for attempt in range(5):
                start=time.monotonic();before=count
                value=reads.call('read_record',{'model':'sale.order','record_id':12,'fields':['id','name','state','amount_total','company_id']})
                rows.append({'phase':'cold' if attempt==0 else 'warm','ms':1000*(time.monotonic()-start),'success':value.get('success') is True,'queries':count-before})
            return rows
        start=time.monotonic()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            rows=[r for group in pool.map(worker,range(concurrency)) for r in group]
        peak=max(peak,process.memory_info().rss)
        for phase in ['cold','warm']:
            values=[r for r in rows if r['phase']==phase];latency=sorted(r['ms'] for r in values)
            result['reads'].append({'concurrency':concurrency,'phase':phase,'n':len(values),'p50_ms':statistics.median(latency),'p95_ms':latency[math.ceil(len(latency)*.95)-1],
                'error_rate':sum(not r['success'] for r in values)/len(values),'queries':sum(r['queries'] for r in values),'batch_seconds':time.monotonic()-start})
    fixture=OUT/'materials';fixture.mkdir(exist_ok=True)
    for name,data in samples(): (fixture/name).write_bytes(data)
    host=Workbench(OUT/'attachment-pressure')
    try:
        sid=host.create_session()['id'];start=time.monotonic();uploads=[]
        for index in range(10):
            stream=io.BytesIO();image_document(f'采购订单 P{index+1:05}').save(stream,format='PNG')
            stamp=time.monotonic();row=host._import_material(sid,f'{index}.png',base64.b64encode(stream.getvalue()).decode())
            uploads.append({'id':row['id'],'enqueue_ms':1000*(time.monotonic()-stamp)})
        responsiveness=[]
        while any(host.store.data['materials'][r['id']]['status']=='parsing' for r in uploads):
            stamp=time.monotonic();host.get_session(sid);responsiveness.append(1000*(time.monotonic()-stamp))
            peak=max(peak,process.memory_info().rss);time.sleep(.05)
        rows=[host.store.data['materials'][r['id']] for r in uploads]
        result['attachment_queue']={'count':len(rows),'ready':sum(r['status']=='ready' for r in rows),'failed':sum(r['status']=='failed' for r in rows),
            'seconds':time.monotonic()-start,'max_enqueue_ms':max(r['enqueue_ms'] for r in uploads),'max_session_read_ms':max(responsiveness,default=0),
            'errors':[r.get('error') for r in rows if r['status']=='failed'],'peak_process_mib':peak/1024**2}
    finally:host.close()
    (OUT/'pressure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
