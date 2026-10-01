"""One native invoice demonstration. Local Mailpit only; no tax-platform requests."""
import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

from manage import RUN, shell, snapshot

SOURCE = r'''
import base64, json
from odoo import fields
assert env.cr.dbname == 'erp_harness_enterprise_v1'
assert not env.ref('enterprise_invoice_demo.sale', raise_if_not_found=False), 'Demo already exists; never recreate or resend automatically'
company = env['res.company'].search([('name','=','澄川工业部件有限公司')])
manager = env['res.users'].search([('login','=','manager@chengchuan.example')])
assert len(company) == len(manager) == 1
server = env['ir.mail_server'].search([('name','=','Enterprise local test inbox'),('smtp_host','=','mailpit'),('smtp_port','=',1025)])
assert len(server) == 1 and server.active and server.smtp_encryption == 'none'

def bind(name, record):
    env['ir.model.data'].create({'module':'enterprise_invoice_demo','name':name,
        'model':record._name,'res_id':record.id,'noupdate':True})

view = env['ir.ui.view'].create({'name':'模拟电子发票（非税票）','type':'qweb',
    'key':'enterprise_invoice_demo.document','arch_db':REPORT_XML})
bind('document',view)
report = env['ir.actions.report'].create({'name':'模拟电子发票（非税票）','model':'account.move',
    'report_type':'qweb-pdf','report_name':'enterprise_invoice_demo.document'})
bind('report',report)
business = env(user=manager.id, context=dict(env.context, allowed_company_ids=company.ids,
    mail_notify_force_send=False, mail_notify_noemail=True, tracking_disable=True))
partner = business['res.partner'].create({'name':'苏州星港设备（发票演示）','company_id':company.id,
    'email':'invoice-demo@chengchuan.example'})
bind('customer',partner)
tax = env['account.tax'].search([('company_id','=',company.id),('type_tax_use','=','sale'),
    ('amount','=',13),('amount_type','=','percent'),('price_include','=',False)],limit=1)
assert tax
product = env['product.product'].create({'name':'工业设备调试服务（模拟）','type':'service',
    'invoice_policy':'order','list_price':1000,'taxes_id':[(6,0,tax.ids)],'company_id':company.id})
bind('product',product)
sale = business['sale.order'].create({'partner_id':partner.id,'company_id':company.id,
    'client_order_ref':'DEMO-INVOICE-20260930','order_line':[(0,0,{'product_id':product.id,
        'product_uom_qty':2,'price_unit':1000,'tax_ids':[(6,0,tax.ids)]})]})
bind('sale',sale)
sale.action_confirm()
invoice = sale._create_invoices()
invoice.invoice_date = fields.Date.today()
invoice.action_post()
bind('invoice',invoice)
assert invoice.amount_untaxed == 2000 and invoice.amount_tax == 260 and invoice.amount_total == 2260
assert invoice.invoice_line_ids.sale_line_ids.order_id == sale
reversal = business['account.move.reversal'].with_context(active_model='account.move',active_ids=invoice.ids).create({
    'move_ids':[(6,0,invoice.ids)],'date':fields.Date.today(),'reason':'模拟红字展示；未连接税局','journal_id':invoice.journal_id.id})
reversal.reverse_moves(False)
credit = reversal.new_move_ids
credit.action_post()
bind('credit',credit)
assert credit.reversed_entry_id == invoice and credit.amount_total == invoice.amount_total
documents = []
attachments = []
for label, move in [('blue',invoice),('red',credit)]:
    pdf, _ = report.with_user(manager)._render_qweb_pdf(report.report_name, res_ids=move.ids)
    assert pdf.startswith(b'%PDF-')
    filename = 'DEMO-'+label+'-'+str(move.id)+'.pdf'
    attachment = business['ir.attachment'].create({'name':filename,'type':'binary',
        'datas':base64.b64encode(pdf),'mimetype':'application/pdf','res_model':'account.move','res_id':move.id})
    move.message_post(body='模拟发票展示附件：非税务发票，未进行官方查验。',attachment_ids=attachment.ids,
                      message_type='comment',subtype_xmlid='mail.mt_note')
    attachments.append(attachment)
    documents.append({'label':label,'id':move.id,'name':move.name,'attachment_id':attachment.id,
        'filename':filename,'pdf':base64.b64encode(pdf).decode(),'amount_total':move.amount_total})
# Fixture administration queues one explicitly requested message to local Mailpit.
# Do not grant ordinary business users direct mail.mail creation rights.
mail = env['mail.mail'].create({'subject':'【模拟发票演示】'+sale.name+' · 账单与对应贷项',
    'body_html':'<p>您好：</p><p>附件为本次工业设备调试服务的模拟账单及对应贷项，各为人民币 2,260.00 元。请核对项目和金额，如有疑问请联系澄川业务经理。</p><p>本邮件及附件仅供演示，非税务发票，不可报销或抵扣。谢谢。</p>',
    'email_from':company.email,'email_to':partner.email,'mail_server_id':server.id,
    'attachment_ids':[(6,0,[a.id for a in attachments])],'auto_delete':False,
    'model':'account.move','res_id':invoice.id})
bind('mail',mail)
result = {'sale_id':sale.id,'sale_name':sale.name,'documents':documents,'mail_id':mail.id,
    'mail_subject':mail.subject,'mail_state':mail.state,'local_facts_checked':True,
    'official_tax_verification':'not_connected','real_tax_invoice':False,'paid_model_calls':0}
# Queue durably. Sending happens separately; an interrupted send must never recreate documents.
env.cr.commit()
env.registry.signal_changes()
print('ENTERPRISE_RESULT='+json.dumps(result,ensure_ascii=False))
'''


def check(folder):
    """Fresh local readback and mailbox hashes; never reports official tax verification."""
    result = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
    evidence = shell(r'''
import base64, hashlib, json
assert env.cr.dbname == 'erp_harness_enterprise_v1'
sale = env.ref('enterprise_invoice_demo.sale')
invoice = env.ref('enterprise_invoice_demo.invoice')
credit = env.ref('enterprise_invoice_demo.credit')
mail = env.ref('enterprise_invoice_demo.mail')
assert sale.state == 'sale' and invoice.state == credit.state == 'posted'
assert invoice.invoice_line_ids.sale_line_ids.order_id == sale
assert credit.reversed_entry_id == invoice
assert invoice.company_id == credit.company_id == sale.company_id
assert invoice.partner_id == credit.partner_id == sale.partner_id
assert invoice.amount_total == credit.amount_total == 2260
assert mail.state == 'sent' and mail.email_to == 'invoice-demo@chengchuan.example'
assert len(mail.attachment_ids) == 2
hashes = {a.name:hashlib.sha256(base64.b64decode(a.datas)).hexdigest() for a in mail.attachment_ids}
assert set(mail.attachment_ids.mapped('res_id')) == set((invoice | credit).ids)
assert all(a.res_model == 'account.move' for a in mail.attachment_ids)
print('ENTERPRISE_RESULT='+json.dumps({'sale_id':sale.id,'invoice_id':invoice.id,'credit_id':credit.id,'hashes':hashes}))
''')
    assert evidence['sale_id'] == result['sale_id']
    message = json.load(urlopen('http://127.0.0.1:18080/api/v1/message/'+result['mailpit_id'], timeout=10))
    received = {a['FileName']:a['Checksums']['SHA256'] for a in message['Attachments']}
    assert len(received) == 2
    for document in result['documents']:
        assert hashlib.sha256((folder / document['filename']).read_bytes()).hexdigest() == document['sha256']
        assert evidence['hashes'][document['filename']] == received[document['filename']] == document['sha256']
    evidence.update(mailpit_attachments_match=True, official_tax_verification='not_connected', paid_model_calls=0)
    (folder / 'checks.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Passed: fresh Odoo facts, reversal relation, PDF hashes and two Mailpit attachments. Tax platform not connected.')


if __name__ == '__main__':
    folder = RUN / 'invoice-demo-20260930'
    folder.mkdir(exist_ok=True)
    if '--check' in sys.argv:
        check(folder)
        raise SystemExit(0)
    receipt = folder / 'receipt.json'
    if receipt.exists():
        raise SystemExit('Demo already prepared. Inspect receipt.json and Mailpit; no automatic resend.')
    with (folder / 'setup.log').open('a', encoding='utf-8') as log:
        if not (RUN / 'snapshots/before-invoice-demo-20260930/manifest.json').exists():
            snapshot('before-invoice-demo-20260930', False, log)
    report_xml = Path(__file__).with_name('assets').joinpath('invoice-demo.xml').read_text(encoding='utf-8')
    result = shell('REPORT_XML='+repr(report_xml)+'\n'+SOURCE)
    for document in result['documents']:
        pdf = base64.b64decode(document.pop('pdf'))
        (folder / document['filename']).write_bytes(pdf)
        document['sha256'] = hashlib.sha256(pdf).hexdigest()
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    sent = shell("""import json
assert env.cr.dbname == 'erp_harness_enterprise_v1'
mail = env.ref('enterprise_invoice_demo.mail')
assert mail.mail_server_id.smtp_host == 'mailpit' and mail.email_to == 'invoice-demo@chengchuan.example'
if mail.state == 'outgoing':
    mail.send(raise_exception=True)
assert mail.state == 'sent'
env.cr.commit()
print('ENTERPRISE_RESULT='+json.dumps({'mail_state':mail.state,'message_id':mail.message_id}))
""")
    result.update(sent)
    messages = json.load(urlopen('http://127.0.0.1:18080/api/v1/messages', timeout=10))['messages']
    captured = [m for m in messages if m['Subject'] == result['mail_subject']]
    assert len(captured) == 1, 'Inspect Mailpit; never resend to fix a missing receipt'
    message = json.load(urlopen('http://127.0.0.1:18080/api/v1/message/'+captured[0]['ID'], timeout=10))
    assert len(message['Attachments']) == 2
    result['mailpit_id'] = captured[0]['ID']
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'documents'}, ensure_ascii=False))
    check(folder)
