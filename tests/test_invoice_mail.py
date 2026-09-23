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
        records['mail.message'][201]=dict(id=201,model=model,res_id=10,message_type='comment',subject=kwargs['subject'],body=kwargs['body'],
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


def history(records, *, status='sent', email='li@example.test', pdf=30, partner=False):
    records['mail.message'][201] = dict(
        id=201, model='account.move', res_id=10, message_type='comment',
        subject='An older template', body='<p>A different message body</p>',
        outgoing_email_to=False if partner else email,
        partner_ids=[8] if partner else [], attachment_ids=[pdf],
        notification_ids=[] if status is None else [301])
    if status is not None:
        records['mail.notification'][301] = dict(
            id=301, notification_type='email', notification_status=status,
            mail_email_address=email, res_partner_id=[8, '李明'] if partner else False)


def another_run(a, path):
    b, writer, _ = _actions(runtime=a.reads.instances['default'], approval_mode='host', path=path)
    b.task_evidence = TaskEvidence(b.reads, copy.deepcopy(a.task_evidence.spec), path.with_suffix('.json'))
    return b, writer


@pytest.mark.parametrize('partner', [False, True])
def test_prior_delivery_in_another_run_does_not_request_approval_or_send(tmp_path, monkeypatch, partner):
    a, _, records = setup(tmp_path, monkeypatch)
    history(records, email='LI@example.test', partner=partner)
    monkeypatch.setenv('PI_AGENT_SESSION_ID', 'new-session')
    b, writer = another_run(a, tmp_path/'new-run.sqlite')
    result = send(b)
    assert result['success'] and result['already_satisfied'] and result['read_only'], result
    assert result['verification']['evidence']['messages'][0]['message_id'] == 201
    assert writer.calls == [] and b.store.summary()['actions'] == 0
    assert not result.get('approval_required')
    assert not send(b, body='This is a new authorization')['success']


@pytest.mark.parametrize('status', ['ready', 'exception', 'bounce', 'canceled', 'unknown', None])
def test_unresolved_prior_delivery_is_not_success_or_a_new_send(tmp_path, monkeypatch, status):
    a, writer, records = setup(tmp_path, monkeypatch)
    history(records, status=status)
    result = send(a)
    assert not result['success'] and result['reconciliation_required'], result
    assert result['verification']['status'] == 'unconfirmed'
    assert result['retry_safe'] is False and not result['approval_required']
    assert writer.calls == [] and a.store.summary()['actions'] == 0


@pytest.mark.parametrize('change', ['different_address', 'different_pdf'])
def test_distinct_delivery_still_requires_human_approval(tmp_path, monkeypatch, change):
    a, writer, records = setup(tmp_path, monkeypatch)
    history(records, email='other@example.test' if change == 'different_address' else 'li@example.test')
    if change == 'different_pdf':
        records['ir.attachment'][31] = {**records['ir.attachment'][30], 'id':31, 'checksum':'new-pdf', 'file_size':100}
        records['account.move'][10]['invoice_pdf_report_id'] = [31, 'invoice-new.pdf']
    result = send(a)
    assert result['approval_required'] and not result['success'], result
    assert writer.calls == []


@pytest.mark.parametrize('status', ['sent', 'ready', 'exception'])
@pytest.mark.parametrize('through_public_method', [False, True])
def test_other_sender_finishing_after_approval_prevents_dispatch(tmp_path, monkeypatch, status, through_public_method):
    a, writer, records = setup(tmp_path, monkeypatch)
    pending = send(a)
    assert pending['approval_required']
    a.store.approve(pending['action_id'], 'human')
    row = a.store.get(pending['action_id'])
    history(records, status=status)
    result = send(a) if through_public_method else a._execute_row(row, lambda: writer.calls.append('duplicate'))
    assert result['success'] is (status == 'sent')
    assert (result.get('already_satisfied') is True) is (status == 'sent')
    stored = a.store.get(pending['action_id'])
    assert stored['status'] == ('verified' if status == 'sent' else 'needs_reconciliation')
    assert result['action_id'] == pending['action_id'] and stored['sent_at'] is None
    assert stored['result']['read_only'] is True
    if status != 'sent':
        assert result['failure']['requires_user_input'] is True
    assert writer.calls == []


@pytest.mark.parametrize('failure', ['notification_missing', 'notification_acl', 'search_failure', 'identity_changed'])
def test_history_evidence_unavailable_never_sends_or_reuses_green(tmp_path, monkeypatch, failure):
    from erp_harness.erp._odoo_core.field_policy import FieldPolicy, ModelFieldRule
    a, writer, records = setup(tmp_path, monkeypatch)
    pending = send(a); a.store.approve(pending['action_id'], 'human')
    row = a.store.get(pending['action_id'])
    history(records)
    runtime = a.reads.instances['default']
    if failure == 'notification_missing':
        records['mail.notification'].clear()
    elif failure == 'notification_acl':
        runtime.policy = FieldPolicy({'default': {'mail.notification': ModelFieldRule('deny', frozenset({'notification_status'}))}})
    elif failure == 'search_failure':
        def unavailable(*args, **kwargs):
            raise ConnectionError('mail history unavailable')
        monkeypatch.setattr(runtime.client, 'search_read', unavailable)
    else:
        runtime.client.context = {'allowed_company_ids':[2]}
        result = a._execute_row(row, lambda: writer.calls.append('sent'))
        assert not result['success'] and 'identity changed' in result['error']
        assert writer.calls == []
        return
    result = send(a)
    assert not result['success'] and not result.get('already_satisfied')
    assert writer.calls == []


def test_sent_history_with_an_unresolved_duplicate_is_not_silently_green(tmp_path, monkeypatch):
    a, writer, records = setup(tmp_path, monkeypatch)
    history(records)
    records['mail.message'][202] = {**records['mail.message'][201], 'id':202, 'notification_ids':[302]}
    records['mail.notification'][302] = {**records['mail.notification'][301], 'id':302, 'notification_status':'ready'}
    result = send(a)
    assert not result['success'] and result['reconciliation_required']
    assert len(result['verification']['evidence']['messages']) == 2
    assert writer.calls == []


def smtp_writer(writer, records):
    def smtp(model, method, **kwargs):
        writer.calls.append(kwargs)
        mid, nid = 201 + len(writer.calls), 301 + len(writer.calls)
        records['mail.message'][mid] = dict(
            id=mid, model=model, res_id=10, message_type='comment', subject=kwargs['subject'], body=kwargs['body'],
            outgoing_email_to=kwargs['outgoing_email_to'], partner_ids=[], attachment_ids=kwargs['attachment_ids'], notification_ids=[nid])
        records['mail.notification'][nid] = dict(
            id=nid, notification_type='email', notification_status='sent', mail_email_address=kwargs['outgoing_email_to'], res_partner_id=False)
        return mid
    writer.execute_method = smtp


def test_verified_local_ledger_does_not_replace_missing_live_receipt(tmp_path, monkeypatch):
    a, writer, records = setup(tmp_path, monkeypatch)
    smtp_writer(writer, records)
    pending = send(a); a.store.approve(pending['action_id'], 'human')
    assert send(a)['action_status'] == 'verified'
    records['mail.message'].clear(); records['mail.notification'].clear()
    result = send(a)
    assert not result['success'] and result['action_status'] == 'needs_reconciliation'
    assert len(writer.calls) == 1


def test_new_pdf_in_same_run_does_not_reuse_old_approval_or_green(tmp_path, monkeypatch):
    a, writer, records = setup(tmp_path, monkeypatch)
    smtp_writer(writer, records)
    first = send(a); a.store.approve(first['action_id'], 'human')
    assert send(a)['action_status'] == 'verified'
    records['ir.attachment'][31] = {**records['ir.attachment'][30], 'id':31, 'checksum':'revised-pdf', 'file_size':100}
    records['account.move'][10]['invoice_pdf_report_id'] = [31, 'invoice.pdf']
    second = send(a)
    assert second['approval_required'] and second['action_id'] != first['action_id']
    assert len(writer.calls) == 1
    a.store.approve(second['action_id'], 'human')
    assert send(a)['action_status'] == 'verified'
    assert len(writer.calls) == 2 and writer.calls[-1]['attachment_ids'] == [31]


def unrelated_chatter(records):
    records['mail.message'][500] = dict(
        id=500, model='account.move', res_id=10, message_type='comment',
        subject='Internal note', body='Internal note', outgoing_email_to=False,
        partner_ids=[], attachment_ids=[], notification_ids=[])


def test_unsent_blocked_action_reconciles_older_delivery_with_a_different_template(tmp_path, monkeypatch):
    a, writer, records = setup(tmp_path, monkeypatch)
    unrelated_chatter(records)
    pending = send(a); a.store.approve(pending['action_id'], 'human')
    row = a.store.get(pending['action_id'])
    assert row['prestate']['invoice_mail']['last_message_id'] == 500
    # Simulate an earlier allocated message becoming visible after approval.
    history(records, status='ready')
    blocked = a._execute_row(row, lambda: writer.calls.append('must not send'))
    assert blocked['action_status'] == 'needs_reconciliation'
    assert a.store.get(row['action_id'])['sent_at'] is None
    records['mail.notification'][301]['notification_status'] = 'sent'
    resolved = a.reconcile(row['action_id'])
    assert resolved['success'] and resolved['action_status'] == 'verified', resolved
    assert resolved['verification']['evidence']['messages'][0]['message_id'] == 201
    assert resolved['result']['read_only'] is True and writer.calls == []


def test_old_sent_receipt_cannot_resolve_an_actually_dispatched_unknown_action(tmp_path, monkeypatch):
    a, writer, records = setup(tmp_path, monkeypatch)
    unrelated_chatter(records)
    pending = send(a); a.store.approve(pending['action_id'], 'human')
    def unknown(*args, **kwargs):
        writer.calls.append('attempt'); raise ConnectionError('lost after dispatch')
    writer.execute_method = unknown
    failed = send(a)
    assert failed['action_status'] == 'needs_reconciliation'
    assert a.store.get(pending['action_id'])['sent_at'] is not None
    history(records)
    resolved = a.reconcile(pending['action_id'])
    assert not resolved['success'] and resolved['action_status'] == 'needs_reconciliation'
    repeated = send(a)
    assert not repeated['success'] and repeated['action_status'] == 'needs_reconciliation'
    assert writer.calls == ['attempt']


@pytest.mark.parametrize('change', ['pdf', 'email'])
def test_changed_delivery_history_cannot_verify_the_old_approved_action(tmp_path, monkeypatch, change):
    a, writer, records = setup(tmp_path, monkeypatch)
    pending = send(a); a.store.approve(pending['action_id'], 'human')
    row = a.store.get(pending['action_id'])
    if change == 'pdf':
        records['ir.attachment'][31] = {**records['ir.attachment'][30], 'id':31, 'checksum':'new-pdf', 'file_size':100}
        records['account.move'][10]['invoice_pdf_report_id'] = [31, 'new.pdf']
        history(records, pdf=31)
    else:
        records['res.partner'][8]['email'] = 'new@example.test'
        # Even a refreshed host contact reference cannot transfer an old approval.
        next(r for r in a.task_evidence.references if r.get('purpose') == 'recipient')['fields']['email'] = 'new@example.test'
        history(records, email='new@example.test')
    result = a._execute_row(row, lambda: writer.calls.append('must not send'))
    assert not result['success'] and 'state changed' in result['error']
    assert a.store.get(row['action_id'])['status'] == 'approved'
    assert not result.get('verification') and writer.calls == []
