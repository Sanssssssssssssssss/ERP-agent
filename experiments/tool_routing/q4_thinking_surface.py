"""Repair candidate-group versus all-tools ambiguity found in a real failure."""
import copy
import sys
from pathlib import Path
from . import q4_inputs as base,q4_thinking as trial

OUT=base.ROOT/'.runtime/q4-thinking-surface-20260928'
BOUNDARY='''This is a decision about ONE candidate capability, not about whether the agent may use tools at all.
All tools in available_base_tools remain available for either answer. No hides only the candidate capability. It does not prevent querying, verification with base tools, or reporting.
Before deciding Yes, identify a useful tool that actually belongs to the candidate group. A useful base-tool call alone does not justify Yes for a different group. Keep current scope and execution authorization separate.'''


def prepare():
    OUT.mkdir(exist_ok=True)
    source=trial.OUT/'protection-final/check-inputs.json'
    rows=[]
    for r in base.read(source):
        if r['case_id']=='host-live:E03:0015':continue
        row=copy.deepcopy(r);row['id']=row['id'].replace('|rules|','|surface|');row['variant']='surface'
        group=row['group'];spec=row['question'].split(group+':',1)[1].strip()
        row['question']=f'Should the candidate capability {group} be exposed for a concrete useful next call within the current scope? Candidate definition: {spec}. Base tools remain available either way.'
        for o in row['options']:
            o['description']=(f'Yes: expose {group} in addition to the base tools.' if o['id']=='A' else f'No: keep the base tools available; do not expose {group}.')
        rows.append(row)
    assert len(rows)==6
    base.save(OUT/'probe-inputs.json',rows)
    files=[Path(__file__),Path(trial.__file__),source,trial.OUT/'protection-final/check-results.jsonl',OUT/'probe-inputs.json']
    base.save(OUT/'frozen.json',{'sources':{str(p.resolve()):base.sha(p) for p in files},'forwards':6,
        'reason':'Actual failed thinking used read_record as justification for actions, although read_record is always a base tool.',
        'scope':'Post-protection debugging plus two historical normal controls. Not a new holdout; original7/8 score remains unchanged.',
        'difference':'Clarify group boundary in question/options/system only; state and labels unchanged.'})


if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:
        trial.OUT=OUT;trial.RULES+='\n'+BOUNDARY;trial.run('probe')
