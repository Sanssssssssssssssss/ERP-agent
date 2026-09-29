"""Frozen real-prefix replay using the runtime's canonical context/messages."""
import copy
import sys
from pathlib import Path

from . import q4_inputs as base, q4_thinking as trial

OUT = base.ROOT/'.runtime/q4-standard-context-20260928'
FAILURES = base.ROOT/'.runtime/q4-failure-replay-20260928'
CONTROL_IDS = {'2278:agent:0011', '2003:agent:0020'}
EXTRA_KEYS = {('history:0282e2fb879c97b36f78', 'knowledge'),
    ('phase:8ba212155df45c8d23be', 'attachments'), ('phase:8ba212155df45c8d23be', 'diagnostics'),
    ('host-live:E03:0015', 'actions'), ('host-live:E01:0013', 'actions'),
    ('host-live:E02:0061', 'actions'), ('2003:agent:0020', 'actions'),
    ('history:4fce471e001938d66c39', 'accounting')}


def prepare(extra=False, recovery=False, all_cases=False):
    from erp_harness.app import routing_context as contract
    OUT.mkdir(exist_ok=True)
    source = base.read(FAILURES/'probe-inputs.json') + [r for r in base.read(
        base.ROOT/'.runtime/q4-thinking-surface-20260928/probe-inputs.json') if r['case_id'] in CONTROL_IDS]
    labels = base.read(FAILURES/'labels.json') + [r for split in base.read(base.OUT/'labels.json').values()
        for r in split if r['id'] in CONTROL_IDS and r['capability'] in {'actions', 'accounting'}]
    extra = extra or recovery
    selected_keys = {k for k in EXTRA_KEYS if k[1] in {'attachments', 'diagnostics'}} if recovery else EXTRA_KEYS
    extra_sources = [base.OUT/'tuning-inputs.json', base.OUT/'holdout-inputs.json'] if extra or all_cases else []
    if extra or all_cases:
        added = [r for path in extra_sources for r in base.read(path)
                  if r['variant'] == 'plain' and (r['case_id'], r['group']) in selected_keys]
        source = source + added if all_cases else added
        labels += [r for split in base.read(base.OUT/'labels.json').values() for r in split]
    keys = {(r['case_id'],r['group']) for r in source}
    labels = {(r['id'],r['capability']):r for r in labels if (r['id'],r['capability']) in keys}
    expected = 14 if all_cases else len(selected_keys) if extra else 6
    assert len(source)==2*expected and len(labels)==expected
    files = [Path(__file__),Path(contract.__file__),Path(trial.__file__),Path(base.__file__),
        base.ROOT/'src/erp_harness/app/routing_state.py',base.ROOT/'src/erp_harness/tools/dynamic_tools.py',
        base.ROOT/'src/erp_harness/app/business.py',
        base.ROOT/'src/erp_harness/tools/router.py',base.ROOT/'src/erp_harness/tools/native_tool_catalog.json',
        FAILURES/'probe-inputs.json',FAILURES/'labels.json',base.OUT/'labels.json',
        base.ROOT/'.runtime/q4-thinking-surface-20260928/probe-inputs.json']
    files += extra_sources
    rows, audits = [], []
    for original in source:
        label = labels[(original['case_id'],original['group'])]
        path = Path(label['request_path'])
        assert base.sha(path)==label['request_sha256']
        files.append(path)
        request = base.read(path)
        row = copy.deepcopy(original)
        before = base.encode(row['state'])
        context = contract.assemble_context(request,state=row['state'])
        assert base.encode(row['state'])==before
        assert {a.get('action_id'):a.get('status') for a in context['action_ledger']['actions']} == {
            a.get('action_id'):a.get('status') for a in row['state']['action_ledger']['unresolved']+row['state']['action_ledger']['recent_finished']}
        row['state']=context; row['id']=f"{row['case_id']}|{row['group']}|standard|{row['order']}";row['variant']='standard'
        row['selector_messages']=contract.selection_messages(context,row['group'],row['options'])
        audits.append({'id':row['id'],'request_sha256':base.sha(path),'request_path':str(path),
            'goal_preserved':context['current_task']['goal']==original['state']['task']['goal'],
            'superseded_observations':len(context.get('historical_evidence_refs', [])),
            'ledger_statuses_preserved':True,'source_positions_known':sum(f.get('source_position') is not None for f in context['observations']),
            'observation_count':len(context['observations']),'role':'extension' if extra else 'protection' if row['case_id'] in CONTROL_IDS else 'known_failure'})
        rows.append(row)
    for name,value in [('probe-inputs.json',rows),('labels.json',list(labels.values())),('input-audit.json',audits)]:
        base.save(OUT/name,value); files.append(OUT/name)
    base.save(OUT/'frozen.json',{'sources':{str(p.resolve()):base.sha(p) for p in files},
        'contract':contract.VERSION,'policy':f'{len(rows)} local GPU generations, no paid calls or Odoo execution; no automatic retry.',
        'scope':('All fourteen existing historical case/group pairs.' if all_cases else 'Two observed attachment/diagnostics failures.' if recovery else 'Eight additional historical case/group pairs; one historical knowledge probe.' if extra else 'Four known failures and two previously correct controls.')+' Both option orders; original labels unchanged, not blind.',
        'changes':'Canonical context and system/catalog/options; sampling and parser unchanged. No claim of a single-factor ablation.'})


if __name__=='__main__':
    if len(sys.argv)>2:
        OUT=Path(sys.argv[2]).resolve()
    if sys.argv[1] in {'prepare', 'prepare-extra', 'prepare-recovery', 'prepare-all'}:
        prepare(extra=sys.argv[1]=='prepare-extra', recovery=sys.argv[1]=='prepare-recovery', all_cases=sys.argv[1]=='prepare-all')
    else:
        trial.OUT=OUT
        # Prepared by the actual runtime function; hashes checked before GPU inference.
        # The isolated inference environment needs no business-runtime dependencies.
        trial.messages=lambda row,variant:row['selector_messages']
        trial.run('probe')
