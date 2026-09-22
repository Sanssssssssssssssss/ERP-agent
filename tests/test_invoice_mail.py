"""Guard and ledger regressions; SMTP acceptance is checked separately on real Odoo."""
import copy

import pytest

from erp_harness.erp import invoice_mail
from erp_harness.erp.task_evidence import TaskEvidence
from tests.test_actions import _actions, _Reader, _Runtime


class Reader(_Reader):
    def search_read(self, *args, limit=None, **kwargs):
        return super().search_read(*args, limit=limit or None, **kwargs)


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('ODOO_MCP_ENABLE_WRITES', '1')
    monkeypatch.setenv('ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS', 'account.move.message_post')
    rt = _Runtime()
    rt.client = Reader()
    records = rt.client.records
    records['account.move'][10] = dict(id=10, name='INV/001', state='posted', move_type='out_invoice', company_id=[1,'公司'],
        partner_id=[7,'客户'], commercial_partner_id=[7,'客户'], currency_id=[6,'CNY'], amount_total=3322.2, invoice_pdf_report_id=[30,'invoice.pdf'])
    records['res.partner'][8] = dict(id=8,name='李明', email='li@example.test', active=True, company_id=False,
        parent_id=[7,'客户'], commercial_partner_id=[7,'客户'], type='invoice', function='财务')
    records['res.company'] = {1:dict(id=1,name='公司',email='billing@example.test')}
    records['res.currency'] = {6:dict(id=6,name='CNY')}
    records['ir.attachment'][30] = dict(id=30,name='invoice.pdf',mimetype='application/pdf',checksum='abc',file_size=99,res_model='account.move',res_id=10)
    records['mail.notification'] = {}
    refs = [dict(model=m,id=i,purpose=purpose,fields=copy.deepcopy(records[m][i]),quote=records[m][i]['name'])
            for m,i,purpose in [('account.move',10,'target'),('res.company',1,'target'),('res.partner',8,'recipient')]]
    a,w,_ = _actions(runtime=rt, approval_mode='host', path=tmp_path/'actions.sqlite')
    a.task_evidence = TaskEvidence(a.reads, {'version':1,'instruction_sha256':'test','references':refs}, tmp_path/'task.json')
    return a,w,records


def send(a, **extra):
    return a.execute_method('account.move','message_post',kwargs={'ids':[10],'partner_ids':[8],**extra})


@pytest.mark.parametrize('model,id,field,value', [
    ('account.move',10,'company_id',[2,'另一公司']),
    ('res.partner',8,'commercial_partner_id',[9,'杭州客户']),
    ('res.partner',8,'type','contact'),
    ('res.partner',8,'active',False),
    ('res.partner',8,'email','x@example.test,y@example.test'),
    ('res.partner',8,'email','x@example.test\r\nBcc: secret@example.test'),
    ('res.partner',8,'email',False),
    ('ir.attachment',30,'res_id',11),
    ('ir.attachment',30,'file_size',0),
    ('res.company',1,'email',False),
])
def test_wrong_or_changed_evidence_never_sends(tmp_path, monkeypatch, model,id,field,value):
    a,w,records=setup(tmp_path,monkeypatch)
    records[model][id][field]=value
    assert not send(a)['success']
    assert w.calls==[]


def test_duplicate_invoice_numbers_use_company_and_duplicate_people_use_customer(tmp_path,monkeypatch):
    from erp_harness.app.conversation import resolve_references
    a,w,records=setup(tmp_path,monkeypatch)
    class Reads:
        def call(self, name, args):
            if args['model']=='account.move':
                rows=[records['account.move'][10]]
                if ['company_id','=',1] not in args['domain']:
                    rows.append({**rows[0],'id':11,'company_id':[2,'其他公司']})
            elif args['model']=='res.partner':
                rows=[records['res.partner'][8]]
                if ['commercial_partner_id','=',7] not in args['domain']:
                    rows.append({**rows[0],'id':9,'commercial_partner_id':[9,'其他客户']})
            else:rows=[records['res.company'][1]]
            return {'success':True,'result':copy.deepcopy(rows)}
    refs=[{'resource':'contact','id':8,'quote':'李明','purpose':'recipient'},
          {'resource':'invoice','id':10,'quote':'INV/001'}, {'resource':'company','id':1,'quote':'公司'}]
    resolved=resolve_references(Reads(),refs,'请将公司的 INV/001 发给李明')
    assert invoice_mail.requested(resolved)==(10,8)


def test_approval_freezes_email_and_attachment(tmp_path,monkeypatch):
    a,w,records=setup(tmp_path,monkeypatch)
    prepared=send(a)
    assert prepared['approval_required']
    row=a.store.get(prepared['action_id'])
    kwargs=invoice_mail.execution_kwargs(row['payload'],row['prestate']['invoice_mail'])
    assert kwargs['outgoing_email_to']=='li@example.test'
    assert kwargs['partner_ids']==[] and kwargs['attachment_ids']==[30] and kwargs['notify_skip_followers']
    assert not send(a,outgoing_email_to='wrong@example.test')['success']
    a.store.approve(prepared['action_id'],'test_user')
    records['ir.attachment'][30]['checksum']='changed-after-approval'
    assert not a._execute_row(a.store.get(prepared['action_id']),lambda: w.calls.append('sent'))['success']
    assert w.calls==[]


def test_delivery_receipt_and_duplicate_are_not_inferred_from_pdf(tmp_path,monkeypatch):
    a,w,records=setup(tmp_path,monkeypatch)
    prepared=send(a); a.store.approve(prepared['action_id'],'test_user')
    def smtp(model,method,**kwargs):
        w.calls.append(kwargs)
        records['mail.message'][201]=dict(id=201,model=model,res_id=10,subject=kwargs['subject'],body=kwargs['body'],
            outgoing_email_to=kwargs['outgoing_email_to'],partner_ids=[],attachment_ids=[30],notification_ids=[301])
        records['mail.notification'][301]=dict(id=301,notification_type='email',notification_status='sent',mail_email_address='li@example.test',res_partner_id=False)
        return 201
    w.execute_method=smtp
    result=send(a)
    assert result['action_status']=='verified',result
    assert send(a)['action_status']=='verified'
    assert len(w.calls)==1
    row=a.store.get(prepared['action_id'])
    records['mail.notification'][301]['notification_status']='exception'
    assert a._verify(row,None)['status']!='satisfied'
    records['mail.notification'][301]['notification_status']='sent'
    records['mail.notification'][301]['mail_email_address']='wrong@example.test'
    assert a._verify(row,None)['status']!='satisfied'


def test_unknown_mail_outcome_never_replays(tmp_path,monkeypatch):
    a,w,records=setup(tmp_path,monkeypatch)
    prepared=send(a); a.store.approve(prepared['action_id'],'test_user')
    def lost(*args,**kwargs):
        w.calls.append('attempt'); raise ConnectionError('lost after dispatch')
    w.execute_method=lost
    assert send(a)['action_status']=='needs_reconciliation'
    send(a)
    assert w.calls==['attempt']


def test_empty_sources_and_raw_mail_write_are_rejected(tmp_path,monkeypatch):
    a,w,records=setup(tmp_path,monkeypatch)
    a.task_evidence.references=[]
    assert not send(a)['success']
    with pytest.raises(ValueError,match='direct mail writes'):
        a._native_prestate('write',{'model':'mail.mail','operation':'create','instance':'default','values':{}})
    assert w.calls==[]
