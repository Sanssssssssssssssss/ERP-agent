"""Follow-up prompted by actual thinking errors; clarify field semantics only."""
import copy
import sys
from pathlib import Path
from . import q4_inputs as base
from . import q4_thinking as trial

PARENT=trial.OUT
OUT=base.ROOT/'.runtime/q4-thinking-schema-20260928'
SCHEMA='''Input schema semantics:
- action_ledger.complete means the ledger collection was fully read. It says nothing about completion of the business task.
- execution_permission=not_granted_by_disclosure is a fixed separation-of-duties notice, not an ACL denial and not a claim that approval is absent. Read action-specific status for approval. Do not infer permission to execute from visibility.
- status=approved means host approval has occurred; it does not mean execution has occurred. Unresolved approved actions can still need action tools, subject to executor checks.
- runtime_retained_capabilities lists currently retained tools. Decide desired availability, not just new additions; a retained capability may still need Yes.
- values_fresh=unknown is unknown recency. Historical errors and previews are not proof of current failure or current success.'''


def prepare():
    OUT.mkdir(exist_ok=True)
    files=[Path(__file__),Path(trial.__file__),PARENT/'frozen.json',
        base.ROOT/'src/erp_harness/app/routing_state.py']
    for phase,name in [('dev','dev'),('protection','check')]:
        rows=[]
        for r in base.read(PARENT/f'{phase}-inputs.json'):
            if r['variant']!='context':continue
            row=copy.deepcopy(r);row['id']=row['id'].replace('|context|','|schema|');row['variant']='schema';rows.append(row)
        base.save(OUT/f'{name}-inputs.json',rows);files.append(OUT/f'{name}-inputs.json')
    base.save(OUT/'frozen.json',{'sources':{str(p.resolve()):base.sha(p) for p in files},
        'reason':'Observed thinking confuses ledger coverage and disclosure notice with completion and ACL denial.',
        'difference':'Same context as parent context variant; only append schema definitions to system prompt.',
        'sampling':'identical to parent','dev_forwards':8,'check_forwards':8,
        'labels':'parent labels unchanged, optional judgments disputed','protection':'frozen before any protection inference'})


if __name__=='__main__':
    action=sys.argv[1]
    if action=='prepare':prepare()
    else:
        if action=='check':assert base.read(OUT/'dev-summary.json')['completed']==8
        trial.OUT=OUT;trial.RULES+='\n'+SCHEMA
        trial.run(action)
