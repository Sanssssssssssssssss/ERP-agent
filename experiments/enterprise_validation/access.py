"""Native Odoo write boundaries for the ten demo staff accounts."""
import json

from manage import RUN, shell, snapshot

POLICY = {
    'sales_team.group_sale_salesman': ['sale.order', 'sale.order.line'],
    'purchase.group_purchase_user': ['purchase.order', 'purchase.order.line'],
    # Purchase confirmation creates stock moves under the purchasing user.
    'stock.group_stock_user,purchase.group_purchase_user': ['stock.picking', 'stock.move', 'stock.move.line'],
    'mrp.group_mrp_user': ['mrp.production', 'mrp.workorder', 'mrp.bom', 'mrp.bom.line'],
    'account.group_account_user': ['account.move', 'account.move.line', 'account.payment'],
}

SOURCE = '''
import json
assert env.cr.dbname == 'erp_harness_enterprise_v1'
staff = env['res.users'].search([('login','in',[
    prefix+role+'@chengchuan.example' for prefix in ('','trade_')
    for role in ('sales','purchase','warehouse','production','finance')])])
manager = env['res.users'].search([('login','=','manager@chengchuan.example')])
assert len(staff) == 10 and len(manager) == 1
before = {u.id: (u.login, u.company_ids.ids, u.group_ids.ids) for u in staff | manager}
group = env.ref('enterprise_staff.group_employee', raise_if_not_found=False)
if not group:
    group = env['res.groups'].create({'name':'澄川模拟员工业务边界'})
    env['ir.model.data'].create({'module':'enterprise_staff','name':'group_employee',
        'model':'res.groups','res_id':group.id,'noupdate':True})
staff.write({'group_ids':[(4,group.id)]})
assert group not in manager.all_group_ids
rules = []
for required, models in POLICY.items():
    for model in models:
        key = 'write_'+model.replace('.','_')
        # Global intersection prevents another additive group rule from reopening writes.
        permitted = ' or '.join('user.has_group(%r)' % g for g in required.split(','))
        domain = ("[] if not user.has_group('enterprise_staff.group_employee') or "
                  + permitted + " else [('id','=',0)]")
        values = {'name':'澄川岗位写入 · '+model,'model_id':env['ir.model']._get(model).id,
                  'domain_force':domain,'perm_read':False,'perm_write':True,
                  'perm_create':True,'perm_unlink':True,'active':True,'groups':[(5,0,0)]}
        rule = env.ref('enterprise_staff.'+key, raise_if_not_found=False)
        if rule:
            rule.write(values)
        else:
            rule = env['ir.rule'].create(values)
            env['ir.model.data'].create({'module':'enterprise_staff','name':key,
                'model':'ir.rule','res_id':rule.id,'noupdate':True})
        rules.append({'model':model,'rule_id':rule.id,'required_group':required})
for u in staff | manager:
    login, companies, groups = before[u.id]
    assert u.login == login and u.company_ids.ids == companies
    assert set(u.group_ids.ids) == set(groups) | ({group.id} if u in staff else set())
env.cr.commit()
env.registry.signal_changes()
print('ENTERPRISE_RESULT='+json.dumps({'rules':rules,'employees':staff.ids,'manager':manager.id}))
'''


if __name__ == '__main__':
    folder = RUN / 'access-20260930'
    folder.mkdir(exist_ok=True)
    with (folder / 'install.log').open('a', encoding='utf-8') as log:
        if not (RUN / 'snapshots/before-access-20260930/manifest.json').exists():
            snapshot('before-access-20260930', False, log)
    result = shell('POLICY='+repr(POLICY)+'\n'+SOURCE)
    (folder / 'setup.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Installed native write rules:', len(result['rules']))
