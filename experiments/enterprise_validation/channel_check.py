"""Real Odoo transaction checks; all test business changes are rolled back."""
import json

from manage import RUN, shell

setup = json.loads((RUN / 'channels-20260930/setup.json').read_text(encoding='utf-8'))
source = '''
import json
from unittest.mock import patch
result=[]
manager=env['res.users'].search([('login','=','manager@chengchuan.example')])
for model,category in [('sale.order','sales'),('purchase.order','purchase'),('stock.picking','inventory'),('mrp.production','manufacturing'),('account.move','finance'),('account.payment','finance')]:
    row=next(c for c in SETUP['channels'] if c['company_id']==1 and c['category']==category)
    user=env['res.users'].browse(next(uid for uid in row['users'] if uid not in [2,manager.id]))
    document=env[model].search([('company_id','=',1)],limit=1).with_user(user).with_context(mail_notify_force_send=False,mail_notify_noemail=True)
    msg=document.message_post(body='频道机制验证（事务回滚）',subtype_xmlid='mail.mt_note')
    domain=[('model','=','discuss.channel'),('res_id','=',row['id']),('subject','=','erp-chatter-event:%s'%msg.id)]
    mirrored=env['mail.message'].search(domain)
    assert len(mirrored)==1 and user.login in str(mirrored.body), model
    env['ir.actions.server'].browse(SETUP['action_id']).with_context(active_model='mail.message',active_ids=msg.ids,active_id=msg.id).run()
    assert env['mail.message'].search_count(domain)==1
    assert env['discuss.channel'].with_user(user).search_count([('id','=',row['id'])])==1
    other=next(c for c in SETUP['channels'] if c['company_id']==2 and c['category']==category)
    assert not env['discuss.channel'].with_user(user).search([('id','=',other['id'])])
    assert not env['discuss.channel'].with_user(env.ref('base.public_user')).search([('id','=',row['id'])])
    result.append({'model':model,'mirror':True,'actor':user.login,'duplicate_suppressed':True,'visibility_checked':True})
sales=env['res.users'].search([('login','=','sales@chengchuan.example')])
order=env['sale.order'].search([('company_id','=',1),('state','=','draft')],limit=1).with_user(sales).with_context(mail_notify_force_send=False,mail_notify_noemail=True)
assert order
previous=env['mail.message'].search([],order='id desc',limit=1).id
order.action_cancel()
env.cr.precommit.run()
sales_channel=next(c['id'] for c in SETUP['channels'] if c['company_id']==1 and c['category']=='sales')
mirrors=env['mail.message'].search([('model','=','discuss.channel'),('res_id','=',sales_channel),('id','>',previous)])
assert any('→' in str(m.body) for m in mirrors), [str(m.body) for m in mirrors]
result.append({'business_method':'sale.order.action_cancel','tracked_transition':True,'messages':[str(m.body) for m in mirrors]})
# A failed channel insert cannot abort the original business transaction.
def broken_channel(self, *args, **kwargs):
    self.env.cr.execute('SELECT 1/0 /* CHANNEL_CHECK injected failure */')
with patch.object(type(env['discuss.channel']), 'message_post', broken_channel):
    msg=order.message_post(body='频道故障隔离验证（事务回滚）',subtype_xmlid='mail.mt_note')
    assert msg.exists()
assert env['sale.order'].browse(order.id).state=='cancel'
result.append({'channel_failure_does_not_abort_business':True})
env.cr.rollback()
print('ENTERPRISE_RESULT='+json.dumps({'checks':result,'rolled_back':True,'paid_calls':0}))
'''
if __name__ == '__main__':
    result = shell('SETUP=' + repr(setup) + '\n' + source)
    (RUN / 'channels-20260930/checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Passed: 6 model routes, actors, duplicate suppression, access, real state change and failure isolation. Rolled back.')
