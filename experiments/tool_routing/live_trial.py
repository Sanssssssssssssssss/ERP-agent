"""Three isolated businesses with opt-in Laya and V4.1 Flash. One attempt each."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'.runtime/laya-integrated-20260926'
sys.path.insert(0,str(ROOT/'experiments/enterprise_validation'))
import manage
from evaluate import CHECK
from erp_harness.app.host import Workbench
from erp_harness.erp.gateway import Json2ReadClient

SNAP=manage.RUN/'snapshots/baseline-10000'
CONTAINER='erp-laya-live-20260926'
PORT=18189
DATA='/tmp/laya-integrated-20260926'
DATABASES={k:f'erp_laya_{v}_20260926' for k,v in [('SALE','sale'),('E01','stock'),('E06','refund')]}
MODEL='deepseek/deepseek-v4.1-flash'
LAYA_MODEL=ROOT/'.runtime/capability-routing-phase-20260925/model-v14-intent'


def read(path): return json.loads(path.read_text(encoding='utf8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8')
def docker(*args,**kwargs): return subprocess.run(['wsl','-d','Ubuntu','--exec','docker',*args],check=True,**kwargs)
def compose(*args,**kwargs):
    return docker('compose','--env-file',manage.linux(manage.RUN/'.env'),'-f',manage.linux(manage.HERE/'compose.yml'),*args,**kwargs)


def shell(db,source):
    assert db in DATABASES.values()
    result=docker('exec','-i',CONTAINER,'/entrypoint.sh','odoo','shell','-d',db,'--no-http','--data-dir='+DATA,
                  input=f'assert env.cr.dbname == {db!r}\n'+source,capture_output=True,text=True,encoding='utf8')
    return json.loads(next(line.split('=',1)[1] for line in result.stdout.splitlines() if line.startswith('ENTERPRISE_RESULT=')))


def setup(resume=False):
    assert OUT.exists()==resume,'Use finish-setup only after the initial database restore'
    assert not list(OUT.glob('*/attempt.json')),'Never reset a paid attempt'
    with socket.socket() as port: port.bind(('127.0.0.1',PORT))
    existing=docker('ps','-a','--format','{{.Names}}',capture_output=True,text=True).stdout.splitlines()
    assert CONTAINER not in existing
    manifest=read(SNAP/'manifest.json')
    for name,digest in manifest['sha256'].items(): assert sha(SNAP/name)==digest
    for db in DATABASES.values():
        exists=compose('exec','-T','db','psql','-U','odoo','-d','postgres','-Atc',
                              f"SELECT 1 FROM pg_database WHERE datname='{db}'",capture_output=True,text=True)
        assert bool(exists.stdout.strip())==resume,db
        if resume:
            counts=compose('exec','-T','db','psql','-U','odoo','-d',db,'-Atc',
                'SELECT (SELECT count(*) FROM sale_order),(SELECT count(*) FROM purchase_order),(SELECT count(*) FROM mrp_production)',capture_output=True,text=True)
            assert counts.stdout.strip()=='5000|3000|2000'
    OUT.mkdir(exist_ok=resume)
    with (OUT/'setup.log').open('a' if resume else 'x',encoding='utf8') as log:
        if not resume:
            for db in DATABASES.values():
                compose('exec','-T','db','createdb','-U','odoo',db,stdout=log,stderr=log)
                with (SNAP/'database.dump').open('rb') as stream:
                    compose('exec','-T','db','pg_restore','--exit-on-error','-U','odoo','-d',db,stdin=stream,stdout=log,stderr=log)
        compose('run','-d','--no-deps','--name',CONTAINER,'-p',f'127.0.0.1:{PORT}:8069',
                       'odoo','odoo','-d',','.join(DATABASES.values()),'--db-filter=^('+ '|'.join(re.escape(db) for db in DATABASES.values()) +')$',
                       '--no-database-list','--max-cron-threads=0','--data-dir='+DATA,stdout=log,stderr=log)
        docker('cp',manage.linux(SNAP/'filestore.tar'),CONTAINER+':/tmp/baseline-filestore.tar',stdout=log,stderr=log)
        for db in DATABASES.values():
            target=DATA+'/filestore/'+db
            docker('exec',CONTAINER,'mkdir','-p',target,stdout=log,stderr=log)
            docker('exec',CONTAINER,'tar','-xf','/tmp/baseline-filestore.tar','--strip-components=2','-C',target,stdout=log,stderr=log)
    fixtures=read(SNAP/'fixtures.json')
    initialization=[]
    for case,db in DATABASES.items():
        initialization.append(shell(db,"""import json
env['ir.mail_server'].search([]).write({'active':False})
env['ir.mail_server'].create({'name':'Isolated regression, no outbound SMTP','smtp_host':'127.0.0.1','smtp_port':1,'smtp_encryption':'none'})
for user in env['res.users'].search([('login','like','@chengchuan.example')]): user.partner_id.write({'email':user.login})
env.cr.commit()
print('ENTERPRISE_RESULT='+json.dumps({'database':env.cr.dbname,'smtp':'localhost:1','cron':False}))
"""))
        folder=OUT/case;folder.mkdir()
        if case in {'SALE','E01'}:
            spec=read(ROOT/f'.runtime/backend-final-live-20260924/{case}/input.json')
        else:
            scenario=next(s for s in fixtures['scenarios'] if s['id']=='vendor_refund')
            company=next(c['name'] for c in fixtures['companies'] if c['id']==scenario['company_id'])
            spec={'case':scenario['id'],'type':'refund','completion_target':'reconciled','role':scenario['role'],
                  'title':'E06 vendor_refund','goal':f"在{company}处理以下业务。{scenario['goal']}\n原始单据及引用："+json.dumps(scenario['records'],ensure_ascii=False)}
        save(folder/'input.json',spec)
        save(folder/'fixtures.json',fixtures)
        verify(case,'before')
    after=docker('ps','-a','--format','{{.Names}}',capture_output=True,text=True).stdout.splitlines()
    assert set(existing)<=set(after)
    save(OUT/'environment.json',{'databases':DATABASES,'url':f'http://127.0.0.1:{PORT}','container':CONTAINER,
         'snapshot_sha256':manifest['sha256']['database.dump'],'initialization':initialization,'existing_containers_preserved':True})
    print('Prepared three isolated database copies; no model calls.',flush=True)


def verify(case,label):
    folder=OUT/case;db=DATABASES[case];fixtures=read(folder/'fixtures.json')
    if case=='SALE':
        body="""s=env['sale.order'].browse(6).exists()
result={'sale':s.read(['name','state','company_id','partner_id','amount_total','client_order_ref','invoice_ids']),
 'lines':s.order_line.read(['product_id','product_uom_qty','price_unit','discount','tax_ids']),
 'pickings':s.picking_ids.read(['name','state'])}
result=json.loads(json.dumps(result,ensure_ascii=False,default=str))
"""
        if label=='before':body+="assert s.state=='draft' and s.name=='S00006' and not s.invoice_ids\n"
        else:
            body+=f"""before={read(folder/'before.json')!r}
checks={{'confirmed':s.state=='sale','reference':s.client_order_ref=='Backend consolidation regression',
 'identity_amount':all(result['sale'][0][k]==before['sale'][0][k] for k in ['name','company_id','partner_id','amount_total']),
 'lines_unchanged':result['lines']==before['lines'],'no_invoice':not s.invoice_ids,
 'no_delivery':all(p.state not in ('done','cancel') for p in s.picking_ids)}}
result.update(checks=checks,passed=all(checks.values()))
"""
        body+="print('ENTERPRISE_RESULT='+json.dumps(result,ensure_ascii=False,default=str))\n"
    elif label=='after':
        body=CHECK.replace('assert env.cr.dbname == "erp_harness_enterprise_v1"',f'assert env.cr.dbname == {db!r}',1)
    elif case=='E01':
        body="""p=env['stock.picking'].browse([1,2]).exists()
assert len(p)==2 and all(x.state not in ('done','cancel') for x in p)
assert len(p.move_ids)==10 and all(m.product_uom_qty==10 for m in p.move_ids)
assert not env['sale.order'].browse(1).invoice_ids and not env['purchase.order'].browse(1).invoice_ids
print('ENTERPRISE_RESULT='+json.dumps({'initial':True,'pickings':p.read(['name','state'])}))
"""
    else:
        body="""bill=env['account.move'].browse(4).exists();p=env['stock.picking'].browse(6).exists()
assert bill.state=='posted' and bill.amount_residual==0 and p.state=='done'
assert not env['account.move'].search([('reversed_entry_id','=',4)])
print('ENTERPRISE_RESULT='+json.dumps({'initial':True,'bill':bill.read(['name','state','amount_total','amount_residual']),'picking':p.read(['name','state'])},default=str))
"""
    scenario='partial_transfer' if case=='E01' else 'vendor_refund'
    source=f"import json\nenv.cr.execute('SET TRANSACTION READ ONLY')\nCASE={scenario!r}\nFIXTURES={fixtures!r}\ntry:\n"+''.join(' '+line+'\n' for line in body.splitlines())+'finally:\n env.cr.rollback()\n'
    result=shell(db,source)
    save(folder/(label+'.json'),{**result,'verifier_sha256':hashlib.sha256(source.encode()).hexdigest()})
    return result


def freeze():
    assert not (OUT/'frozen.json').exists()
    files=[*sorted((ROOT/'src').rglob('*.py')),*sorted((ROOT/'experiments/tool_routing').glob('*.py')),ROOT/'experiments/enterprise_validation/evaluate.py']
    save(OUT/'frozen.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'source_hashes':{p.relative_to(ROOT).as_posix():sha(p) for p in files},
         'input_hashes':{case:sha(OUT/case/'input.json') for case in DATABASES},'model':MODEL,'reasoning':'high',
         'memory':'off','laya_model':str(LAYA_MODEL.resolve()),'laya_model_sha256':sha(LAYA_MODEL/'model.safetensors'),
         'laya_manifest_sha256':sha(LAYA_MODEL/'router.json'),
         'environment':read(OUT/'environment.json'),'environment_sha256':sha(OUT/'environment.json'),
         'attempts_per_case':1,'limits':{'run':None,'request':None,'output':None},'snapshot':read(OUT/'environment.json')['snapshot_sha256']})


def run(case):
    frozen=read(OUT/'frozen.json');folder=OUT/case;spec=read(folder/'input.json')
    assert all(sha(ROOT/p)==h for p,h in frozen['source_hashes'].items())
    assert sha(folder/'input.json')==frozen['input_hashes'][case]
    assert sha(OUT/'environment.json')==frozen['environment_sha256']
    assert (f'http://127.0.0.1:{PORT}',DATABASES,CONTAINER)==tuple(frozen['environment'][k] for k in ('url','databases','container'))
    assert str(LAYA_MODEL.resolve())==frozen['laya_model']
    assert sha(LAYA_MODEL/'router.json')==frozen['laya_manifest_sha256']
    assert sha(LAYA_MODEL/'model.safetensors')==frozen['laya_model_sha256']
    account=read(SNAP/'accounts.json')[spec['role']]
    config={'ODOO_URL':f'http://127.0.0.1:{PORT}','ODOO_DB':DATABASES[case],
            'ODOO_USERNAME':account['login'],'ODOO_API_KEY':account['api_key']}
    Json2ReadClient(url=config['ODOO_URL'],db=config['ODOO_DB'],username=account['login'],api_key=account['api_key'],context={'allowed_company_ids':account['company_ids']})
    with (folder/'attempt.json').open('x',encoding='utf8') as stream:json.dump({'started':time.time(),'model':MODEL,'automatic_business_retries':0},stream)
    os.environ.update(config,LLM_API_KEY=os.environ['COMMAND_CODE_API_KEY'],LLM_BASE_URL='https://api.commandcode.ai/provider/v1',
        LLM_MODEL=MODEL,LLM_THINKING_TYPE='high',ERP_MEMORY_MODE='off',PYTHONUTF8='1',
        ERP_LAYA_PYTHON=str(ROOT/'.runtime/laya-routing-20260924/venv/Scripts/python.exe'),
        ERP_LAYA_MODEL=str(LAYA_MODEL.resolve()))
    events=(folder/'host-events.jsonl').open('a',encoding='utf8');started=time.monotonic()
    host=Workbench(folder/'profile/data',ROOT,worker_timeout_seconds=None,event_sink=lambda e:(events.write(json.dumps(e,ensure_ascii=False,default=str)+'\n'),events.flush()))
    summary={'case':case,'business_passed':False}
    try:
        sid=host.create_session(spec['title'])['id']
        host.store.data['messages'][sid].append({'id':'frozen-proposal','role':'assistant','text':'固定实验业务输入',
             'proposal':{'id':'frozen','status':'pending',**{k:spec[k] for k in ['type','title','goal','completion_target']}}})
        business=host.confirm_business(sid,'frozen',True);rid=host.start_run(sid,business['id'])['id']
        previous=None
        while True:
            with host._lock:
                current=host.store.data['runs'][rid];state=current['status']
                if state!=previous:print(case,state,flush=True);previous=state
                if state=='awaiting_approval' and rid not in host._processes:
                    approvals=[host.store.data['approvals'][a] for a in current.get('pending_approval_action_ids',[])]
                    save(folder/'pending-approvals.json',{'session_id':sid,'business_id':business['id'],'run_id':rid,'approvals':approvals})
                    choices=read(folder/'operator-decisions.json') if (folder/'operator-decisions.json').exists() else {}
                    for approval in approvals:
                        aid=approval.get('action_id') or approval['id'];choice=choices.get(aid)
                        if approval.get('status')=='pending_approval' and choice in {'approve','reject'}:
                            decision=host.decide_approval(sid,business['id'],rid,aid,choice)
                            with (folder/'fixture-approvals.jsonl').open('a',encoding='utf8') as log:
                                log.write(json.dumps({'action_id':aid,'review':'Reviewed against frozen business scope','decision':decision},ensure_ascii=False)+'\n')
                if state not in {'running','awaiting_approval','cancel_requested'} and rid not in host._processes:break
            time.sleep(1)
        save(folder/'desktop-readback.json',host.refresh_business(sid,business['id']))
        summary.update(business_passed=verify(case,'after')['passed'])
    finally:
        host.close();events.close()
        if 'rid' in locals():
            current=host.store.data['runs'][rid]
            run_dir=folder/'profile/data/runs'/rid
            summary.update(run_id=rid,run_status=current['status'],usage=host._public_usage(current),
                           model_requests=len(list((run_dir/'requests').glob('*.request.json'))),tool_calls=len(current.get('tools',[])))
        summary['seconds']=time.monotonic()-started;save(folder/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['setup','finish-setup','freeze',*DATABASES])
    parser.add_argument('--trial',help='New isolated trial name; never reuse a paid attempt')
    parser.add_argument('--port',type=int,default=PORT)
    parser.add_argument('--laya-model',type=Path,default=LAYA_MODEL)
    args=parser.parse_args()
    if args.trial:
        assert re.fullmatch(r'[a-z][a-z0-9-]{0,31}',args.trial),'Invalid trial name'
        OUT=ROOT/'.runtime'/args.trial;CONTAINER='erp-'+args.trial;DATA='/tmp/'+args.trial
        DATABASES={k:args.trial.replace('-','_')+'_'+v for k,v in [('SALE','sale'),('E01','stock'),('E06','refund')]}
    PORT=args.port;LAYA_MODEL=args.laya_model
    if args.command in {'setup','finish-setup'}:setup(resume=args.command=='finish-setup')
    elif args.command=='freeze':freeze()
    else:run(args.command)
