"""Replay the observed interrupted run in a DB/profile clone; never resend its message.

setup copies the local incident. repair is a test operator fixing the existing SMTP
queue. run uses the real host/worker once. No new action is automatically approved.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import time

from experiments.tool_routing.live_trial import docker, compose, manage, ROOT, read, save

OUT = ROOT / '.runtime/interrupted-recovery-20260930/live'
SOURCE = manage.RUN / 'profiles/manager/data'
DATABASE = 'erp_recovery_20260930'
CONTAINER = 'erp-recovery-20260930'
SMTP = 'erp-recovery-smtp-20260930'
DATA = '/tmp/recovery-20260930'
PORT = 18204
INCIDENT = read(OUT.parent / 'frozen.json')
BID, RID = INCIDENT['business_id'], INCIDENT['run_id']


def shell(body):
    result = docker('exec', '-i', CONTAINER, '/entrypoint.sh', 'odoo', 'shell', '-d', DATABASE,
                    '--no-http', '--data-dir=' + DATA, input=f'assert env.cr.dbname == {DATABASE!r}\nimport json\n' + body,
                    capture_output=True, text=True, encoding='utf8')
    return json.loads(next(s.split('=', 1)[1] for s in result.stdout.splitlines() if s.startswith('RECOVERY=')))


def observation():
    return shell("""env.cr.execute('SET TRANSACTION READ ONLY')
m = env['mail.message'].browse(7456).exists()
print('RECOVERY='+json.dumps({'message_ids':m.ids,'invoice':env['account.move'].browse(32).read(['name','state','invoice_pdf_report_id']),
 'messages_on_invoice':env['mail.message'].search_count([('model','=','account.move'),('res_id','=',32)]),
 'notifications':env['mail.notification'].search([('mail_message_id','=',7456)]).read(['notification_status','res_partner_id']),
 'queue':env['mail.mail'].search([('mail_message_id','=',7456)]).read(['state','failure_type','failure_reason'])},default=str))
env.cr.rollback()
""")


def environment():
    config = read(manage.RUN / 'connection.json')
    config.update(ODOO_URL=f'http://127.0.0.1:{PORT}', ODOO_DB=DATABASE)
    os.environ.update(config, ERP_MEMORY_MODE='off', PYTHONUTF8='1')


def setup():
    assert not OUT.exists(), 'Never overwrite an existing recovery trial'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', PORT))
    existing = docker('ps', '-a', '--format', '{{.Names}}', capture_output=True, text=True).stdout.splitlines()
    assert CONTAINER not in existing and SMTP not in existing
    exists = compose('exec', '-T', 'db', 'psql', '-U', 'odoo', '-d', 'postgres', '-Atc',
                     f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'", capture_output=True, text=True)
    assert not exists.stdout.strip()
    OUT.mkdir(parents=True)
    with (OUT / 'setup.log').open('w', encoding='utf8') as log:
        with (OUT / 'database.dump').open('xb') as dump:
            compose('exec', '-T', 'db', 'pg_dump', '-U', 'odoo', '-Fc', '-d', manage.DATABASE, stdout=dump, stderr=log)
        compose('exec', '-T', 'db', 'createdb', '-U', 'odoo', DATABASE, stdout=log, stderr=log)
        with (OUT / 'database.dump').open('rb') as dump:
            compose('exec', '-T', 'db', 'pg_restore', '--exit-on-error', '-U', 'odoo', '-d', DATABASE, stdin=dump, stdout=log, stderr=log)
        with (OUT / 'filestore.tar').open('xb') as tar:
            compose('exec', '-T', 'odoo', 'tar', '-cf', '-', '-C', '/var/lib/odoo/filestore/' + manage.DATABASE, '.', stdout=tar, stderr=log)
        docker('run', '-d', '--name', SMTP, '--network', 'erp-harness-enterprise-validation_default',
               'python:3.11-slim', 'python', '-u', '-m', 'smtpd', '-n', '-c', 'DebuggingServer', '0.0.0.0:1025', stdout=log, stderr=log)
        compose('run', '-d', '--no-deps', '--name', CONTAINER, '-p', f'127.0.0.1:{PORT}:8069',
                'odoo', 'odoo', '-d', DATABASE, '--db-filter=^' + DATABASE + '$', '--no-database-list',
                '--max-cron-threads=0', '--data-dir=' + DATA, stdout=log, stderr=log)
        docker('cp', manage.linux(OUT / 'filestore.tar'), CONTAINER + ':/tmp/filestore.tar', stdout=log, stderr=log)
        docker('exec', CONTAINER, 'mkdir', '-p', DATA + '/filestore/' + DATABASE, stdout=log, stderr=log)
        docker('exec', CONTAINER, 'tar', '-xf', '/tmp/filestore.tar', '-C', DATA + '/filestore/' + DATABASE, stdout=log, stderr=log)
    save(OUT / 'before.json', observation())
    shell(f"""env['ir.mail_server'].search([]).write({{'active':False}})
env['ir.mail_server'].create({{'name':'Isolated recovery SMTP', 'smtp_host':{SMTP!r},'smtp_port':1025,'smtp_encryption':'none'}})
env.cr.commit()
print('RECOVERY='+json.dumps({{'smtp':'local capture only','cron':False}}))
""")
    environment()
    from erp_harness.app.host import Workbench, _connection_identity
    from erp_harness.erp.store import ActionStore
    state = read(OUT.parent / 'observed/workbench-state.json')
    sid = state['businesses'][BID]['session_id']
    state['businesses'] = {BID: state['businesses'][BID]}
    state['sessions'] = {sid: state['sessions'][sid]}
    state['sessions'][sid].update(active_run_id=None, status='idle')
    state['runs'] = {RID: state['runs'][RID]}
    state['approvals'] = {k:v for k,v in state['approvals'].items() if v.get('run_id') == RID}
    state['messages'] = {sid: state['messages'][sid]}
    state['conversation_runs'] = {}
    state['businesses'][BID]['odoo_connection'] = _connection_identity()
    state['businesses'][BID]['artifacts'] = []
    profile = OUT / 'profile/data'
    profile.mkdir(parents=True)
    shutil.copytree(SOURCE / 'runs' / RID, profile / 'runs' / RID)
    shutil.copytree(SOURCE / 'sessions' / BID, profile / 'sessions' / BID)
    save(profile / 'workbench-state.json', state)
    host = Workbench(profile, ROOT, worker_timeout_seconds=None)
    identity = host._native_reads().identity_context('default')
    host.close()
    # Test fixture remapping only. Original ledger and production identity guards are untouched.
    path = profile / 'runs' / RID / 'odoo-actions.sqlite3'
    with sqlite3.connect(path) as db:
        old = db.execute('SELECT DISTINCT identity_sha256 FROM action_ledger').fetchall()
        db.execute('UPDATE action_ledger SET identity=?,identity_sha256=?', (json.dumps(identity), ActionStore.digest(identity)))
    receipt = profile / 'runs' / RID / 'task-evidence.json'
    saved = read(receipt)
    saved['identity'] = identity
    save(receipt, saved)
    save(OUT / 'fixture-remap.json', {'reason':'identical DB snapshot in isolated URL/database; test fixture only',
         'original_identity_hashes':old,'new_identity_hash':ActionStore.digest(identity),'run_id':RID,
         'database_dump_sha256':hashlib.sha256((OUT/'database.dump').read_bytes()).hexdigest()})
    print('Isolated incident ready; no model calls, no resend.')


def run(repair=False):
    environment()
    from erp_harness.app.host import Workbench
    events = (OUT / 'host-events.jsonl').open('a', encoding='utf8')
    host = Workbench(OUT / 'profile/data', ROOT, worker_timeout_seconds=None,
                     event_sink=lambda e:(events.write(json.dumps(e,ensure_ascii=False,default=str)+'\n'),events.flush()))
    sid = host.store.data['businesses'][BID]['session_id']
    try:
        if repair:
            assert not (OUT / 'repair.json').exists()
            result = host.reconcile_business(sid, BID)
            save(OUT / 'blocked-readback.json', result)
            assert host.store.data['runs'][RID]['status'] == 'needs_reconciliation'
            try:
                host.resume_run(sid, BID, RID)
            except RuntimeError:
                pass
            else:
                raise AssertionError('Uncertain write resumed')
            # Authorized test operator repairs the existing queued mail, never calls message_post.
            result = shell("""mail=env['mail.mail'].search([('mail_message_id','=',7456)])
assert len(mail)==1 and mail.state=='exception'
mail.action_retry()
mail.send(raise_exception=True)
env.cr.commit()
print('RECOVERY='+json.dumps({'repaired_queue_id':mail.id,'message_id':7456,'operator':'isolated SMTP fixture'}))
""")
            save(OUT / 'repair.json', result)
            save(OUT / 'after-repair.json', observation())
            save(OUT / 'reconciled-readback.json', host.reconcile_business(sid, BID))
            assert host.store.data['runs'][RID]['status'] == 'interrupted'
            print('Existing message reconciled; original run can resume. No model calls.')
            return
        with (OUT / 'attempt.json').open('x') as stream:
            json.dump({'maximum_business_attempts':1,'model':'deepseek/deepseek-v4.1-flash','memory':'off',
                       'source_hashes':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in (ROOT/'src').rglob('*.py')}},stream)
        os.environ.update(LLM_API_KEY=os.environ['COMMAND_CODE_API_KEY'], LLM_BASE_URL='https://api.commandcode.ai/provider/v1',
                          LLM_MODEL='deepseek/deepseek-v4.1-flash', LLM_THINKING_TYPE='high')
        before_requests = set((OUT/'profile/data/runs'/RID/'requests').glob('*.request.json'))
        before_tools = len(host.store.data['runs'][RID]['tools'])
        host.resume_run(sid, BID, RID)
        previous = None
        while True:
            with host._lock:
                current = host.store.data['runs'][RID]
                status = current['status']
                if status != previous:
                    print(status, flush=True);previous=status
                if status == 'awaiting_approval' and RID not in host._processes:
                    raise RuntimeError('New approval requested; operator review required, never auto-approve')
                if status not in {'running','cancel_requested'} and RID not in host._processes:
                    break
            time.sleep(1)
        save(OUT / 'final-readback.json', host.refresh_business(sid, BID))
        after = observation()
        save(OUT / 'after.json', after)
        before = read(OUT / 'before.json')
        checks = {'no_duplicate_message':before['messages_on_invoice']==after['messages_on_invoice'],
                  'same_message':after['message_ids']==[7456],
                  'smtp_accepted':all(n['notification_status']=='sent' for n in after['notifications']) and bool(after['notifications']),
                  'completed':current['status']=='completed',
                  'all_actions_resolved':all(s=='verified' for s in host._ledger_statuses(current).values())}
        save(OUT / 'summary.json', {'checks':checks,'passed':all(checks.values()),'run_id':RID,
             'new_requests':len(set((OUT/'profile/data/runs'/RID/'requests').glob('*.request.json'))-before_requests),
             'new_tools':len(current['tools'])-before_tools,'usage_cumulative':host._public_usage(current)})
        print(json.dumps(checks), flush=True)
    finally:
        host.close();events.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup','repair','run'])
    command=parser.parse_args().command
    setup() if command=='setup' else run(command=='repair')
