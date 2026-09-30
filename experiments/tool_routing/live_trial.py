"""Isolated businesses with opt-in OpenJev and V4.1 Flash. One attempt each."""
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
SELECTOR_CONFIG=None


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
        elif case == 'MAIL':
            spec=prepare_mail(db)
        elif case in {'E02','E03'}:
            spec=read(ROOT/f'.runtime/enterprise-validation-20260922/live/{case}/input.json')
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
    print(f'Prepared {len(DATABASES)} isolated database copies; no model calls.',flush=True)


def verify(case,label):
    folder=OUT/case;db=DATABASES[case];fixtures=read(folder/'fixtures.json')
    if case=='MAIL':
        return verify_mail(folder, db, label)
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
    elif case=='E02':
        body="""mo=env['mrp.production'].browse(1).exists()
assert mo and mo.state in ('draft','confirmed') and mo.product_qty==4
assert not env['mrp.production'].search([('product_id','=',961),('state','=','done')])
print('ENTERPRISE_RESULT='+json.dumps({'initial':True,'production':mo.read(['name','state','product_qty','bom_id'])},default=str))
"""
    elif case=='E03':
        body="""invoice=env['account.move'].browse(1).exists()
assert invoice.state=='posted' and abs(invoice.amount_residual-2339.10)<0.005
assert not env['account.payment'].search([('invoice_ids','in',[1]),('state','not in',['canceled','rejected'])])
print('ENTERPRISE_RESULT='+json.dumps({'initial':True,'invoice':invoice.read(['name','state','amount_total','amount_residual'])},default=str))
"""
    else:
        body="""bill=env['account.move'].browse(4).exists();p=env['stock.picking'].browse(6).exists()
assert bill.state=='posted' and bill.amount_residual==0 and p.state=='done'
assert not env['account.move'].search([('reversed_entry_id','=',4)])
print('ENTERPRISE_RESULT='+json.dumps({'initial':True,'bill':bill.read(['name','state','amount_total','amount_residual']),'picking':p.read(['name','state'])},default=str))
"""
    scenario=read(folder/'input.json')['case']
    source=f"import json\nenv.cr.execute('SET TRANSACTION READ ONLY')\nCASE={scenario!r}\nFIXTURES={fixtures!r}\ntry:\n"+''.join(' '+line+'\n' for line in body.splitlines())+'finally:\n env.cr.rollback()\n'
    result=shell(db,source)
    save(folder/(label+'.json'),{**result,'verifier_sha256':hashlib.sha256(source.encode()).hexdigest()})
    return result


def prepare_mail(db):
    info=shell(db,"""import json
invoice=env['account.move'].browse(1)
assert invoice.state=='posted' and invoice.move_type=='out_invoice'
assert invoice.partner_id.email.endswith('.example')
server=env['ir.mail_server'].search([('active','=',True)])
assert len(server)==1 and server.smtp_host=='127.0.0.1'
server.write({'smtp_host':'mailpit','smtp_port':1025})
if not invoice.company_id.email: invoice.company_id.write({'email':'billing@chengchuan.example'})
result={'invoice':invoice.name,'company':invoice.company_id.name,'company_id':invoice.company_id.id,
 'partner':invoice.partner_id.name,'partner_id':invoice.partner_id.id,'amount':invoice.amount_total}
env.cr.commit()
print('ENTERPRISE_RESULT='+json.dumps(result,ensure_ascii=False))
""")
    return {'case':'hitl_mail_revision','type':'invoice_delivery','completion_target':'sent','role':'manager',
        'title':'发票发送与审批改稿回归',
        'goal':f"把{info['company']}的{info['invoice']}发票 PDF 发给{info['partner']}的登记邮箱。邮件主题包含 cleanup-20260930。仅发送一次，最后回读 SMTP 状态，不要新开票或修改金额。",
        'references':[{'resource':'company','id':info['company_id'],'quote':info['company']},
            {'resource':'invoice','id':1,'quote':info['invoice']},
            {'resource':'contact','id':info['partner_id'],'quote':info['partner'],'purpose':'recipient'}]}


def verify_mail(folder, db, label):
    source="""import json,hashlib
from odoo.tools import html2plaintext
env.cr.execute('SET TRANSACTION READ ONLY')
i=env['account.move'].browse(1)
messages=env['mail.message'].search([('model','=','account.move'),('res_id','=',1),('subject','ilike','cleanup-20260930')])
mails=env['mail.mail'].search([('mail_message_id','in',messages.ids)])
result={'invoice':i.read(['name','state','amount_total','partner_id','company_id']),
 'messages':[{'id':m.id,'subject':m.subject,'body':html2plaintext(m.body),'recipients':m.partner_ids.ids,
 'attachments':[{'id':a.id,'name':a.name,'sha256':hashlib.sha256(a.raw).hexdigest()} for a in m.attachment_ids]} for m in messages],
 'mails':mails.read(['state','mail_message_id']), 'email':i.partner_id.email,
 'business_counts':{m:env[m].search_count([]) for m in ['sale.order','purchase.order','account.move']}}
print('ENTERPRISE_RESULT='+json.dumps(result,ensure_ascii=False,default=str))
env.cr.rollback()
"""
    result=shell(db,source)
    if label=='before':
        assert not result['messages']
    else:
        from urllib.request import urlopen
        before=read(folder/'before.json')
        captured=json.load(urlopen('http://127.0.0.1:18080/api/v1/messages'))['messages']
        matches=[m for m in captured if 'cleanup-20260930' in m['Subject']]
        result['checks']={'one_message':len(result['messages'])==1,
            'invoice_unchanged':result['invoice']==before['invoice'],
            'no_extra_documents':result['business_counts']==before['business_counts'],
            'smtp_accepted':len(result['mails'])==1 and result['mails'][0]['state']=='sent',
            'mailpit_once':len(matches)==1,'revision_applied':(folder/'revision-applied.json').exists()}
        if len(matches)==1 and len(result['messages'])==1:
            mail=json.load(urlopen('http://127.0.0.1:18080/api/v1/message/'+matches[0]['ID']))
            result['mailpit_id']=matches[0]['ID']
            result['checks']['recipient']=any(r['Address']==result['email'] for r in mail['To'])
            result['checks']['pdf_attached']=len(mail['Attachments'])==1 and mail['Attachments'][0]['ContentType']=='application/pdf'
            result['checks']['courteous_revision']=all(word in result['messages'][0]['body'] for word in ['您好','附件','谢谢'])
        result['passed']=all(result['checks'].values())
    save(folder/(label+'.json'),result)
    return result


def freeze():
    assert not (OUT/'frozen.json').exists()
    assert SELECTOR_CONFIG and SELECTOR_CONFIG.is_file(), 'Specify the validated selector config'
    files=[*sorted((ROOT/'src').rglob('*.py')),*sorted((ROOT/'experiments/tool_routing').glob('*.py')),ROOT/'experiments/enterprise_validation/evaluate.py']
    save(OUT/'frozen.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'source_hashes':{p.relative_to(ROOT).as_posix():sha(p) for p in files},
         'input_hashes':{case:sha(OUT/case/'input.json') for case in DATABASES},'model':MODEL,'reasoning':'high',
         'memory':'off','selector_config':str(SELECTOR_CONFIG.resolve()),'selector_config_sha256':sha(SELECTOR_CONFIG),
         'environment':read(OUT/'environment.json'),'environment_sha256':sha(OUT/'environment.json'),
         'attempts_per_case':1,'limits':{'run':None,'request':None,'output':None},'snapshot':read(OUT/'environment.json')['snapshot_sha256']})


def run(case):
    frozen=read(OUT/'frozen.json');folder=OUT/case;spec=read(folder/'input.json')
    assert all(sha(ROOT/p)==h for p,h in frozen['source_hashes'].items())
    assert sha(folder/'input.json')==frozen['input_hashes'][case]
    assert sha(OUT/'environment.json')==frozen['environment_sha256']
    assert (f'http://127.0.0.1:{PORT}',DATABASES,CONTAINER)==tuple(frozen['environment'][k] for k in ('url','databases','container'))
    assert SELECTOR_CONFIG and str(SELECTOR_CONFIG.resolve())==frozen['selector_config']
    assert sha(SELECTOR_CONFIG)==frozen['selector_config_sha256']
    account=read(SNAP/'accounts.json')[spec['role']]
    config={'ODOO_URL':f'http://127.0.0.1:{PORT}','ODOO_DB':DATABASES[case],
            'ODOO_USERNAME':account['login'],'ODOO_API_KEY':account['api_key']}
    Json2ReadClient(url=config['ODOO_URL'],db=config['ODOO_DB'],username=account['login'],api_key=account['api_key'],context={'allowed_company_ids':account['company_ids']})
    with (folder/'attempt.json').open('x',encoding='utf8') as stream:json.dump({'started':time.time(),'model':MODEL,'automatic_business_retries':0},stream)
    os.environ.update(config,LLM_API_KEY=os.environ['COMMAND_CODE_API_KEY'],LLM_BASE_URL='https://api.commandcode.ai/provider/v1',
        LLM_MODEL=MODEL,LLM_THINKING_TYPE='high',ERP_MEMORY_MODE='off',PYTHONUTF8='1',
        ERP_CAPABILITY_ROUTER_CONFIG=str(SELECTOR_CONFIG.resolve()))
    events=(folder/'host-events.jsonl').open('a',encoding='utf8');started=time.monotonic()
    host=Workbench(folder/'profile/data',ROOT,worker_timeout_seconds=None,event_sink=lambda e:(events.write(json.dumps(e,ensure_ascii=False,default=str)+'\n'),events.flush()))
    summary={'case':case,'business_passed':False}
    try:
        sid=host.create_session(spec['title'])['id']
        host.store.data['messages'][sid].append({'id':'frozen-proposal','role':'assistant','text':'固定实验业务输入',
             'proposal':{'id':'frozen','status':'pending',**{k:spec[k] for k in ['type','title','goal','completion_target','references'] if k in spec}}})
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
                        if approval.get('status')=='pending_approval' and isinstance(choice,dict) and choice.get('decision')=='revise':
                            host.request_approval_revision(sid,business['id'],rid,aid,choice['text'])
                            save(folder/'revision-applied.json',{'action_id':aid,'text':choice['text'],'run_id':rid,'business_id':business['id']})
                            break
                        if approval.get('status')=='pending_approval' and isinstance(choice,str) and choice in {'approve','reject'}:
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
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['setup','finish-setup','freeze','SALE','E01','E02','E03','E06','MAIL'])
    parser.add_argument('--trial',help='New isolated trial name; never reuse a paid attempt')
    parser.add_argument('--port',type=int,default=PORT)
    parser.add_argument('--selector-config',type=Path)
    parser.add_argument('--cases',nargs='+',choices=['SALE','E01','E02','E03','E06','MAIL'],default=list(DATABASES))
    args=parser.parse_args()
    if args.trial:
        assert re.fullmatch(r'[a-z][a-z0-9-]{0,31}',args.trial),'Invalid trial name'
        OUT=ROOT/'.runtime'/args.trial;CONTAINER='erp-'+args.trial;DATA='/tmp/'+args.trial
        suffixes={'SALE':'sale','E01':'stock','E02':'manufacturing','E03':'collection','E06':'refund','MAIL':'mail'}
        DATABASES={k:args.trial.replace('-','_')+'_'+suffixes[k] for k in args.cases}
        if (OUT/'environment.json').exists():
            DATABASES=read(OUT/'environment.json')['databases']
            assert all(re.fullmatch(re.escape(args.trial.replace('-','_'))+r'_[a-z0-9]+', db) for db in DATABASES.values())
    assert set(args.cases)==set(DATABASES), 'Custom cases require a new isolated --trial'
    PORT=args.port;SELECTOR_CONFIG=args.selector_config
    if args.command in {'setup','finish-setup'}:setup(resume=args.command=='finish-setup')
    elif args.command=='freeze':freeze()
    else:run(args.command)
