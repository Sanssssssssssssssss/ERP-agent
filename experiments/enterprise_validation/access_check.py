"""Exercise employee denials and real business methods in a rolled-back Odoo transaction."""
import json
import xmlrpc.client

from manage import RUN, shell
from access import POLICY

SOURCE = r'''
import json
from odoo.exceptions import AccessError
assert env.cr.dbname == 'erp_harness_enterprise_v1'
results = []
manager = env['res.users'].search([('login','=','manager@chengchuan.example')])
context = {'mail_notify_force_send':False,'mail_notify_noemail':True,'tracking_disable':True}

def as_user(record, user):
    return record.with_user(user).with_context(**context, allowed_company_ids=user.company_ids.ids)

def denied(label, operation):
    try:
        with env.cr.savepoint():
            operation()
            raise AssertionError('Unexpected permission: '+label)
    except AccessError:
        results.append(label)

fixtures = {}
for company_name, prefix in [('澄川工业部件有限公司',''),('澄川机电贸易有限公司','trade_')]:
    co = env['res.company'].search([('name','=',company_name)])
    roles = {r: env['res.users'].search([('login','=',prefix+r+'@chengchuan.example')])
             for r in ('sales','purchase','warehouse','production','finance')}
    wh = env['stock.warehouse'].search([('company_id','=',co.id)],limit=1)
    product = env['product.product'].create({'name':'岗位回归临时商品','type':'consu',
        'is_storable':True,'list_price':10,'taxes_id':[(5,0,0)],'supplier_taxes_id':[(5,0,0)]})
    partner = env['res.partner'].create({'name':'岗位回归临时客户','company_id':co.id})
    sale = as_user(env['sale.order'],roles['sales']).create({'partner_id':partner.id,'company_id':co.id,
        'warehouse_id':wh.id,'user_id':roles['sales'].id,'order_line':[(0,0,{
            'product_id':product.id,'product_uom_qty':1,'price_unit':10})]})
    sale.action_confirm()
    assert sale.state == 'sale' and sale.picking_ids
    purchase = as_user(env['purchase.order'],roles['purchase']).create({'partner_id':partner.id,
        'company_id':co.id,'picking_type_id':wh.in_type_id.id,'order_line':[(0,0,{
            'product_id':product.id,'product_qty':2,'price_unit':5,'date_planned':'2026-09-30 09:00:00'})]})
    purchase.button_confirm()
    assert purchase.state == 'purchase' and purchase.picking_ids
    for picking in (purchase.picking_ids | sale.picking_ids):
        picking = as_user(picking, roles['warehouse'])
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        assert picking.state == 'done', picking.name
    sale_finance = as_user(sale, roles['finance'])
    invoice = sale_finance._create_invoices()
    invoice.action_post()
    assert invoice.state == 'posted'
    draft = invoice.sudo().copy({'company_id':co.id})
    payment = env['account.payment'].with_company(co).create({'partner_id':partner.id,
        'amount':10,'payment_type':'inbound','partner_type':'customer',
        'journal_id':env['account.journal'].search([('company_id','=',co.id),('type','=','bank')],limit=1).id})
    mo = as_user(env['mrp.production'],roles['production']).create({'product_id':product.id,
        'product_qty':1,'company_id':co.id,'picking_type_id':wh.manu_type_id.id})
    mo.action_confirm()
    assert mo.state == 'confirmed'
    fixtures[co.id] = {'sale.order':sale.sudo(),'sale.order.line':sale.order_line.sudo(),
        'purchase.order':purchase.sudo(),'purchase.order.line':purchase.order_line.sudo(),
        'stock.picking':purchase.picking_ids.sudo(),'stock.move':purchase.picking_ids.move_ids.sudo(),
        'stock.move.line':purchase.picking_ids.move_line_ids.sudo(),'mrp.production':mo.sudo(),
        'account.move':draft,'account.move.line':draft.line_ids,'account.payment':payment}
    results.append(prefix+'sales_purchase_receipt_delivery_posting_manufacturing_pass')

for prefix, coid in [('',1),('trade_',2)]:
    for role in ('sales','purchase','warehouse','production','finance'):
        user = env['res.users'].search([('login','=',prefix+role+'@chengchuan.example')])
        for required, models in POLICY.items():
            for model in models:
                records = fixtures[coid].get(model)
                if not records:
                    continue
                target = as_user(records[:1],user)
                if not any(user.has_group(g) for g in required.split(',')):
                    denied(prefix+role+':'+model+':write',lambda: target.write({'company_id':coid}))
                    denied(prefix+role+':'+model+':delete_access',lambda: target.check_access('unlink'))
                other = as_user(fixtures[3-coid][model][:1],user)
                denied(prefix+role+':'+model+':other_company',lambda: other.read(['id']))
                denied(prefix+role+':'+model+':forged_company',lambda: other.with_context(
                    allowed_company_ids=[1,2]).read(['id']))
        if role != 'finance':
            denied(prefix+role+':invoice_create',lambda: as_user(env['account.move'],user).create({
                'move_type':'entry','company_id':coid,
                'journal_id':env['account.journal'].search([('company_id','=',coid),('type','=','general')],limit=1).id}))
        if role != 'sales':
            denied(prefix+role+':sale_create',lambda: as_user(env['sale.order'],user).create({
                'partner_id':fixtures[coid]['sale.order'].partner_id.id,'company_id':coid}))
        colleague = env['hr.employee'].search([('company_id','=',coid),('user_id','!=',user.id)],limit=1)
        denied(prefix+role+':private_hr',lambda: as_user(colleague,user).read(['private_email']))
        denied(prefix+role+':self_grant',lambda: as_user(user,user).write({
            'group_ids':[(4,env.ref('base.group_system').id)]}))
for coid, records in fixtures.items():
    for model, record in records.items():
        as_user(record,manager).read(['id'])
        as_user(record,manager).check_access('write')
results.append('manager_both_companies_preserved')
env.cr.precommit.run()
env.cr.rollback()
print('ENTERPRISE_RESULT='+json.dumps({'passed':len(results),'checks':results,'rolled_back':True,'paid_calls':0}))
'''


def check_http():
    """Verify the running service with employee credentials, including cache propagation."""
    accounts = json.loads((RUN / 'accounts.json').read_text(encoding='utf-8'))
    server = xmlrpc.client.ServerProxy('http://127.0.0.1:18079/xmlrpc/2/object')
    checks = []

    def call(role, model, method, args, kwargs=None):
        account = accounts[role]
        return server.execute_kw('erp_harness_enterprise_v1', account['uid'], account['password'],
                                 model, method, args, kwargs or {})

    def refused(label, operation):
        try:
            operation()
        except xmlrpc.client.Fault as error:
            # Odoo XML-RPC AccessError code; don't depend on translated error text.
            assert error.faultCode == 4, error.faultCode
            checks.append(label)
        else:
            raise AssertionError(label)

    for company, prefix in [(1, ''), (2, 'trade_')]:
        sale = call('manager', 'sale.order', 'search_read', [[['company_id', '=', company]]],
                    {'fields': ['id', 'client_order_ref'], 'limit': 1})[0]
        purchase = call('manager', 'purchase.order', 'search_read', [[['company_id', '=', company]]],
                        {'fields': ['id', 'partner_ref'], 'limit': 1})[0]
        other = call('manager', 'sale.order', 'search', [[['company_id', '=', 3-company]]], {'limit': 1})
        for base_role in ('sales', 'purchase', 'warehouse', 'production', 'finance'):
            role = prefix + base_role
            refused(role+':cross_company', lambda: call(role, 'sale.order', 'read', [other], {'fields': ['id']}))
            model, record, field = ('purchase.order', purchase, 'partner_ref') if base_role == 'sales' else (
                'sale.order', sale, 'client_order_ref')
            refused(role+':cross_role_write', lambda: call(role, model, 'write', [[record['id']], {field: record[field]}]))
            if base_role == 'sales':
                assert call(role, 'sale.order', 'search', [[['company_id', '=', company]]], {'limit': 1})
                checks.append(role+':own_read')
        for model, record in [('sale.order', sale), ('purchase.order', purchase)]:
            assert call('manager', model, 'read', [[record['id']]], {'fields': ['id']})
            checks.append('manager:'+str(company)+':'+model)
    counts = {m: call('manager', m, 'search_count', [[]]) for m in
              ('sale.order', 'purchase.order', 'mrp.production', 'account.move')}
    expected = json.loads((RUN / 'staff-20260930/result.json').read_text(encoding='utf-8'))['business_counts']
    assert counts == expected, (counts, expected)
    return {'passed':len(checks), 'checks':checks, 'business_counts':counts}


if __name__ == '__main__':
    result = shell('POLICY='+repr(POLICY)+'\n'+SOURCE)
    (RUN / 'access-20260930/checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'checks'}))
    http = check_http()
    (RUN / 'access-20260930/http-checks.json').write_text(json.dumps(http, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Live HTTP checks:', http['passed'])
