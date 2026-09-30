"""Four isolated manual workflows. Reuse the accepted worker and operator gate."""
import argparse
import json
from pathlib import Path

from experiments.tool_routing import live_trial as live
from .manual_business import ROOT, OUT, OBS

CASE_BUSINESS = dict(PO='b_c620eb84b71e479f98aeedabce9e2027', SALE='b_e96a0933c4b74f75901963c7aada6bf8',
                     DEPOSIT='b_77d28ec38eff4c59953ce38d19b68169', MO='b_bb11a048daad434a9de1160e48f0e2ba')
live.OUT=OUT/'live';live.CONTAINER='erp-manual-business-20260930';live.PORT=18219;live.DATA='/tmp/manual-business-20260930'
live.DATABASES={k:'erp_manual_20260930_'+k.lower() for k in CASE_BUSINESS}
live.SELECTOR_CONFIG=Path(live.read(ROOT/'.runtime/backend-cleanup-live-20260930/frozen.json')['selector_config'])


def verify(case,label):
    folder=live.OUT/case;before=live.read(folder/'before.json') if label!='before' else None
    source="""import json
env.cr.execute('SET TRANSACTION READ ONLY')
s=env['sale.order'].browse([9,12,15]);p=env['purchase.order'].browse(1);mo=env['mrp.production'].browse(1)
result={'sales':s.read(['name','state','company_id','partner_id','amount_total','amount_untaxed','invoice_ids']),
 'sale_lines':s.order_line.read(['order_id','product_id','product_uom_qty','price_unit','qty_delivered','qty_invoiced']),
 'sale_pickings':s.picking_ids.read(['name','state','sale_id']),
 'purchase':p.read(['name','state','partner_id','company_id','amount_total','picking_ids','invoice_ids']),
 'purchase_lines':p.order_line.read(['product_id','product_qty','price_unit','qty_received','qty_invoiced']),
 'purchase_pickings':p.picking_ids.read(['name','state']),
 'original_mo':mo.read(['name','state','product_id','product_qty','qty_produced','date_start','date_finished']),
 'counts':{m:env[m].search_count([]) for m in ['sale.order','purchase.order','mrp.production','account.move','account.payment']},
 'max_po':env['purchase.order'].search([],order='id desc',limit=1).id,
 'max_mo':env['mrp.production'].search([],order='id desc',limit=1).id}
"""
    if before:
        source+=f'BEFORE={before!r}\nCASE={case!r}\n'
        source+='''checks={}
if CASE=='PO':
 new=env['purchase.order'].search([('id','>',BEFORE['max_po'])]);old=BEFORE['purchase_lines']
 checks={'old_cancelled':p.state=='cancel','one_replacement':len(new)==1,'replacement_draft':len(new)==1 and new.state=='draft',
  'supplier':len(new)==1 and new.partner_id.name=='华东工业供应商003（杭州）','source':len(new)==1 and new.origin==p.name,
  'same_lines':len(new)==1 and sorted((l.product_id.id,l.product_qty) for l in new.order_line)==sorted((l['product_id'][0],l['product_qty']) for l in old),
  'date_not_copied_past':len(new)==1 and all(l.date_planned.date().isoformat()>='2026-09-30' for l in new.order_line),
  'no_receipt':not any(p.picking_ids.filtered(lambda x:x.state=='done')) and all(l.qty_received==0 for l in p.order_line),
  'no_bills':not p.invoice_ids and not new.invoice_ids}
 result['replacement']=new.read(['name','state','partner_id','origin','amount_total','date_planned'])
elif CASE=='SALE':
 a=env['sale.order'].browse(12);b=env['sale.order'].browse(9)
 checks={'qualifying_confirmed':a.state=='sale','excluded_unchanged':b.state==BEFORE['sales'][0]['state'],
  'no_invoice':not a.invoice_ids and not b.invoice_ids,'no_delivery':not any(x.state=='done' for x in (a|b).picking_ids),
  'sales_count':result['counts']['sale.order']==BEFORE['counts']['sale.order'],
  'amounts_unchanged':all(x['amount_total']==y['amount_total'] for x,y in zip(result['sales'],BEFORE['sales']))}
elif CASE=='DEPOSIT':
 a=env['sale.order'].browse(15);invoices=a.invoice_ids
 checks={'confirmed':a.state=='sale','one_invoice':len(invoices)==1,'posted':len(invoices)==1 and invoices.state=='posted',
  'amount':len(invoices)==1 and abs(invoices.amount_total-500)<.005,'unpaid':len(invoices)==1 and abs(invoices.amount_residual-500)<.005,
  'customer_company':len(invoices)==1 and invoices.partner_id==a.partner_id and invoices.company_id==a.company_id,
  'no_payment':result['counts']['account.payment']==BEFORE['counts']['account.payment'],
  'no_send':not env['mail.message'].search_count([('model','=','account.move'),('res_id','in',invoices.ids),('message_type','=','comment')])}
 result['invoices']=invoices.read(['name','state','amount_total','amount_residual','payment_state','invoice_origin'])
else:
 new=env['mrp.production'].search([('id','>',BEFORE['max_mo'])]);finished=new.filtered(lambda r:r.product_id.default_code=='CC-0980');sub=new.filtered(lambda r:r.product_id.default_code=='CC-0960')
 purchases=env['purchase.order'].search([('id','>',BEFORE['max_po'])])
 checks={'original_preserved':mo.product_qty==4 and mo.state=='done','new_16':len(finished)==1 and finished.product_qty==16 and finished.state=='done',
  'subassembly_40':sum(sub.mapped('product_qty'))==40 and all(m.state=='done' for m in sub),'produced_20':sum((mo|finished).mapped('qty_produced'))==20,
  'no_substitute':all(m.product_id.default_code in ['CC-0960','CC-0980'] for m in new),
  'one_material_purchase':len(purchases)==1 and len(purchases.order_line)==1 and purchases.order_line.product_id.default_code=='CC-0800' and purchases.order_line.product_qty==120,
  'received':len(purchases)==1 and purchases.order_line.qty_received==120,
  'no_extra_invoice':result['counts']['account.move']==BEFORE['counts']['account.move']}
 result['production']=(mo|new).read(['name','state','product_id','product_qty','qty_produced','bom_id','date_start','date_finished','origin'])
 result['material_purchase']=purchases.read(['name','state','date_planned'])
result.update(checks=checks,passed=all(checks.values()))
'''
    else:
        source+="assert all(x['state']=='draft' for x in result['sales'])\nassert mo.state=='draft' and mo.product_qty==4\nassert p.state=='purchase' and not p.invoice_ids and all(l.qty_received==0 for l in p.order_line)\n"
    source+="print('ENTERPRISE_RESULT='+json.dumps(result,ensure_ascii=False,default=str))\nenv.cr.rollback()\n"
    result=live.shell(live.DATABASES[case],source)
    live.save(folder/(label+'.json'),result)
    return result


def prepare():
    state=live.read(OBS/'workbench-state.json')
    for case,bid in CASE_BUSINESS.items():
        b=state['businesses'][bid];folder=live.OUT/case;folder.mkdir(exist_ok=True)
        refs=[{k:r[k] for k in ('resource','id','quote','purpose') if k in r} for r in b['references']]
        goal=b['goal']
        if case=='PO':
            goal='原采购单 P00001 的供应商无法供货。改由华东工业供应商003（杭州）承接。创建一张替代采购草稿，保留原商品和各行数量、origin=P00001；取消旧采购单及未收货的关联收货，不做实际收货或账单。替代单保持草稿。历史价格与交期只作参考，核对当前 ERP 供应商条件；交期不可复制已经过去的日期。'
            refs=[{'resource':'purchase_order','id':1,'quote':'P00001','purpose':'source','expected_state':'cancel'},
                  {'resource':'contact','id':525,'quote':'华东工业供应商003（杭州）','purpose':'target'}]
        if case=='SALE':
            goal=goal.replace('不发货（不生成或完成交货单）','不登记实际发货（确认销售单时 Odoo 自动生成的待交货单可保留）')
        spec={'case':case,'type':b['type'],'completion_target':'draft' if case=='PO' else b['completion_target'],
              'title':{'PO':'P00001 换供应商并取消旧单','SALE':'S00012 条件确认','DEPOSIT':'S00015 500元预付款','MO':'CC-0980 制造20套'}[case],
              'role':'manager','goal':goal,'references':refs}
        live.save(folder/'input.json',spec)
        live.save(folder/'fixtures.json',{})
        verify(case,'before')
    env=live.read(live.OUT/'environment.json');env['databases']=live.DATABASES
    live.save(live.OUT/'environment.json',env)
    live.save(live.OUT/'approval-script.json',{'source':'User approved manual four-workflow plan; operator reviews each exact action',
       'PO':'Only vendor003 replacement draft, current delivery terms, original PO cancellation; no receipt or invoice',
       'SALE':'Only S00012 confirmation; no edits to S00009 or actual delivery/invoicing',
       'DEPOSIT':'Confirm S00015, fixed 500 CNY deposit, post only; no payment/email',
       'MO':'Original4 plus new16 CC0980, net requirements through exact BOM, no duplicate completed work; actual receipt/completion explicitly confirmed in this simulated fixture at each action approval',
       'date_choice':'If required, use current ERP supplier terms and report resulting deadline; do not invent a supplier promise',
       'snapshot_note':'Baseline-10000 reconstruction, not an exact replay of the original database. Sales confirmation may automatically generate a pending delivery as normal Odoo behavior.'})
    live.freeze()


live.verify=verify
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare',*CASE_BUSINESS]);args=p.parse_args()
    if args.command=='prepare':prepare()
    else:live.run(args.command)
