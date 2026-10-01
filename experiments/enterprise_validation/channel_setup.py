"""Install private Odoo chatter mirrors in the isolated enterprise, without runtime changes."""
import json
from pathlib import Path

from manage import RUN, shell


def install():
    receipt = RUN / 'channels-20260930/setup.json'
    if receipt.exists():
        raise SystemExit('Already installed; inspect setup.json before changing configuration.')
    event_code = Path(__file__).with_name('channel_event.py').read_text(encoding='utf-8')
    source = '''
import json
assert env.cr.dbname == 'erp_harness_enterprise_v1'
assert env['ir.module.module'].search([('name','=','base_automation')]).state == 'installed'
assert not env['base.automation'].search([('name','=','澄川业务日志汇总')])
roles = {'sales': '销售', 'purchase': '采购', 'inventory': '库存', 'manufacturing': '制造', 'finance': '财务'}
logins = {'sales':'sales','purchase':'purchase','inventory':'warehouse','manufacturing':'production','finance':'finance'}
channels = []
mapping = {}
for company_name, prefix, label in [('澄川工业部件有限公司','','澄川工业'), ('澄川机电贸易有限公司','trade_','澄川贸易')]:
    company = env['res.company'].search([('name','=',company_name)])
    assert len(company) == 1
    for category, name in roles.items():
        members = env['res.users'].search([('login','in',[prefix+logins[category]+'@chengchuan.example','manager@chengchuan.example','admin'])])
        assert len(members) == 3
        channel_name = label+' · '+name+'留档'
        assert not env['discuss.channel'].search([('name','=',channel_name)])
        channel = env['discuss.channel'].with_user(env.ref('base.user_admin')).with_context(mail_create_nosubscribe=True).create({
            'name':channel_name, 'channel_type':'group',
            'description':'仅汇总本公司原生单据日志。Odoo执行账号不等于桌面HITL批准人；原单与审批账本保留权威证据。',
            'channel_partner_ids':[(4,pid) for pid in members.partner_id.ids]})
        mapping[(company.id,category)] = channel.id
        channels.append({'id':channel.id,'name':channel.name,'company_id':company.id,'category':category,'users':members.ids})
model = env['ir.model']._get('mail.message')
automation = env['base.automation'].create({'name':'澄川业务日志汇总','model_id':model.id,'trigger':'on_create',
    'filter_domain':str([('model','in',['sale.order','purchase.order','stock.picking','mrp.production','account.move','account.payment'])])})
action = env['ir.actions.server'].create({'name':'转发到对应私密业务群','model_id':model.id,'state':'code',
    'usage':'base_automation','base_automation_id':automation.id,'code':'CHANNELS = '+repr(mapping)+'\\n'+EVENT_CODE})
for item in channels:
    env['discuss.channel'].browse(item['id']).with_context(mail_notify_noemail=True).message_post(
        body='业务留档已启用。从现在开始汇总对应公司单据的状态变化、业务消息和备注；记录执行账号并链接原单。历史请查原单日志。桌面 HITL 批准人仍以工作台审批账本为准。',
        subject='ERP channel enabled', message_type='comment', subtype_xmlid='mail.mt_comment')
env.cr.commit()
env.registry.signal_changes()
print('ENTERPRISE_RESULT='+json.dumps({'channels':channels,'automation_id':automation.id,'action_id':action.id}))
'''
    result = shell('EVENT_CODE = ' + repr(event_code) + '\n' + source)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    install()
