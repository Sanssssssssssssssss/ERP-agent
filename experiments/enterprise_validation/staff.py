"""Create the small fictional organisation using native Odoo HR; preserve business roles."""
import json

from manage import RUN, shell, snapshot


SOURCE = r'''
import json
assert env.cr.dbname == 'erp_harness_enterprise_v1'
manager = env['res.users'].search([('login','=','manager@chengchuan.example')])
assert len(manager) == 1
counts = {m: env[m].search_count([]) for m in ('sale.order','purchase.order','mrp.production','account.move')}
users = env['res.users'].search([('share','=',False),('active','=',True)])
before_users = {u.id: (u.login, set(u.group_ids.ids)) for u in users}
created = []

def ensure(model, key, values, domain):
    record = env.ref('enterprise_staff.'+key, raise_if_not_found=False)
    if not record:
        record = env[model].with_context(active_test=False).search(domain)
        assert len(record) <= 1, 'Ambiguous existing staff record: '+key
        if not record:
            record = env[model].with_context(mail_create_nosubscribe=True).create(values)
            created.append([model, record.id])
        env['ir.model.data'].create({'module':'enterprise_staff','name':key,
            'model':model,'res_id':record.id,'noupdate':True})
    # Re-running does not overwrite later manual personnel changes.
    assert record._name == model and record.company_id.id == values['company_id']
    return record

rows = []
roles = [('sales','销售部','销售专员'), ('purchase','采购部','采购专员'),
         ('warehouse','仓储部','仓储专员'), ('production','生产部','生产协调员'),
         ('finance','财务部','财务专员')]
for company_name, prefix, names in [
    ('澄川工业部件有限公司','', ['林悦','陈航','周宁','许川','沈清']),
    ('澄川机电贸易有限公司','trade_', ['苏禾','陆远','程安','叶青','顾言'])]:
    company = env['res.company'].search([('name','=',company_name)])
    assert len(company) == 1 and company in manager.company_ids
    head = ensure('hr.department', prefix+'management',
        {'name':'经营管理部','company_id':company.id}, [('name','=','经营管理部'),('company_id','=',company.id)])
    lead = ensure('hr.employee', prefix+'manager', {'name':'陈澄（模拟）', 'company_id':company.id,
        'user_id':manager.id,'department_id':head.id,'job_title':'业务经理', 'work_email':manager.login},
        [('user_id','=',manager.id),('company_id','=',company.id)])
    if not head.manager_id:
        head.manager_id = lead
    rows.append({'employee_id':lead.id,'name':lead.name,'company':company.name,'login':manager.login,
                 'department':head.name,'responsibility':'统筹业务与审批','manager':False})
    for (role, department_name, title), name in zip(roles, names):
        user = env['res.users'].search([('login','=',prefix+role+'@chengchuan.example')])
        assert len(user) == 1 and company in user.company_ids
        department = ensure('hr.department', prefix+role+'_department',
            {'name':department_name,'company_id':company.id,'parent_id':head.id,'manager_id':lead.id},
            [('name','=',department_name),('company_id','=',company.id)])
        employee = ensure('hr.employee', prefix+role, {'name':name+'（模拟）','company_id':company.id,
            'user_id':user.id,'department_id':department.id,'parent_id':lead.id,
            'job_title':title,'work_email':user.login}, [('user_id','=',user.id),('company_id','=',company.id)])
        assert employee.user_id == user and employee.parent_id == lead and employee.department_id == department
        rows.append({'employee_id':employee.id,'name':employee.name,'company':company.name,'login':user.login,
                     'department':department.name,'responsibility':title,'manager':lead.name})
hr_group = env.ref('hr.group_hr_manager')
manager.write({'group_ids':[(4, hr_group.id)]})
assert len(rows) == 12 and len({r['employee_id'] for r in rows}) == 12
assert counts == {m:env[m].search_count([]) for m in counts}
assert set(users.ids) == set(env['res.users'].search([('share','=',False),('active','=',True)]).ids)
for user in users:
    login, groups = before_users[user.id]
    assert user.login == login
    assert groups <= set(user.group_ids.ids) if user == manager else groups == set(user.group_ids.ids)
visible = env['hr.employee'].with_user(manager).with_context(allowed_company_ids=manager.company_ids.ids).search([
    ('id','in',[r['employee_id'] for r in rows])])
assert len(visible) == 12, 'Manager must be able to read both companies staff'
env.cr.commit()
print('ENTERPRISE_RESULT='+json.dumps({'employees':rows,'created':created,
    'business_counts':counts,'manager_readable':len(visible),'new_login_accounts':0},ensure_ascii=False))
'''


def main():
    folder = RUN / 'staff-20260930'
    folder.mkdir(exist_ok=True)
    with (folder / 'install.log').open('a', encoding='utf-8') as log:
        if not (RUN / 'snapshots/before-staff-20260930/manifest.json').exists():
            snapshot('before-staff-20260930', False, log)
    module = shell("""import json
assert env.cr.dbname == 'erp_harness_enterprise_v1'
module = env['ir.module.module'].search([('name','=','hr')])
if module.state != 'installed':
    module.button_immediate_install()
print('ENTERPRISE_RESULT='+json.dumps({'hr':'installed'}))
""")
    result = shell(SOURCE)
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'module':module,'employees':len(result['employees']),
                      'created':result['created'],'manager_readable':result['manager_readable']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
