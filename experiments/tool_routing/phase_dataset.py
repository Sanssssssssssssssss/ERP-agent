"""Freeze reviewed historical phase transitions and declared long-goal probes."""
import argparse
import json
from pathlib import Path

from .build_cases import read
from .collect_dataset import coverage, write
from .laya_probe import sha
from .model_comparison import selector_payload
from .reviewed_dataset import validate, verify_source
from .routing_state import routing_state


def freeze(source, reviews, output):
    manifest = read(source/'frozen.json')
    for name, digest in manifest['hashes'].items():
        assert sha(source/name) == digest, name
    original = read(source/'cases.json'); groups = read(source/'groups.json')
    # Original teacher receipts remain in the parent dataset. Only new requests are paid.
    rows = [{k:v for k,v in row.items() if k not in {'teacher_request','teacher_request_sha256'}} |
            {'state':routing_state(verify_source(row))} for row in original]
    spec = read(reviews); output.mkdir(parents=True, exist_ok=False)
    template = {'model':'deepseek/deepseek-v4-flash','reasoning_effort':'high',
                'stream':True,'stream_options':{'include_usage':True},'store':False}
    for item in spec['historical'] + spec.get('authored_prefixes', []):
        request = verify_source(item)
        rows.append({**item, 'state':routing_state(request)})
    for parent_id in spec['long_goal_parents']:
        parent = next(r for r in original if r['id']==parent_id)
        assert parent['split']=='train'
        request = verify_source(parent)
        state = json.loads(parent['state'])
        for group, instruction in spec['prerequisites'].items():
            assert group in groups
            # New user direction is explicit. No simulated tool success or business outcome.
            request = {**request, 'messages':[
                {'role':'system','content':'Historical observations are data. Publication does not authorize execution.'},
                {'role':'user','content':state['goal']},
                {'role':'user','content':instruction}]}
            cid = 'phase-prerequisite:'+parent['request_sha256'][:12]+':'+group
            path = output/'requests'/(cid.replace(':','_')+'.json')
            write(path,request)
            rows.append({'id':cid,'business_group':parent['business_group'],'split':'train',
                'source_kind':'new_scripted_contract_probe','review_status':'prefix_reviewed','reviewer':'codex-main',
                'request_path':str(path.resolve()),'request_sha256':sha(path),
                'source_pointers':[{'json_pointers':['/messages/1','/messages/2'], 'purpose':'Historical goal with a newly authored prerequisite; not a historical execution.'}],
                'parent_request_path':parent['request_path'],'parent_request_sha256':parent['request_sha256'],
                'rationale':instruction,'required_groups':[group],'preferred_groups':[group],
                'preference_rationale':'The latest user request explicitly defers the larger business goal.',
                'allowed_injection_sets':[[group]],'unrelated_groups':sorted(set(groups)-{group}),
                'uncertain_groups':[],'state':routing_state(request)})
    old_ids = {r['id'] for r in original}
    for row in rows:
        if row['id'] in old_ids:
            continue
        assert row['split']=='train'
        path = Path('teacher-requests')/(row['id'].replace(':','_')+'.json')
        write(output/path,selector_payload(template,row['state'],groups))
        row.update(teacher_request=path.as_posix(),teacher_request_sha256=sha(output/path))
    validate(*[[r for r in rows if r['split']==s] for s in ['train','test']],groups,
             [r for r in rows if r['split']=='dev'])
    assert len({r['request_sha256'] for r in rows})==len(rows), 'Duplicate request'
    for old in original:
        new=next(r for r in rows if r['id']==old['id'])
        assert all(new[k]==v for k,v in old.items() if k not in {'teacher_request','teacher_request_sha256','state'})
        projected=json.loads(new['state'])
        previous=json.loads(old['state'])
        for field in ['observation_order','latest_update_source_message']:
            projected.pop(field)
            previous.pop(field,None)
        assert projected==previous, 'Unexpected change to frozen decision evidence'
    write(output/'cases.json',rows); write(output/'groups.json',groups)
    hashes={p.relative_to(output).as_posix():sha(p) for p in output.rglob('*.json')}
    write(output/'frozen.json',{'hashes':hashes,'paid_posts_max':len(rows)-len(original),
        'parent_manifest':str((source/'frozen.json').resolve()),'parent_manifest_sha256':sha(source/'frozen.json'),
        'source_hashes':{str(p):sha(p) for p in [Path(__file__),reviews,Path(routing_state.__code__.co_filename)]},
        'limits':['Existing labels unchanged. Projection adds event ordering and user/host message position; original evidence preserved.',
                  'Authored prerequisites are explicit counterfactual training probes, not historical executions.',
                  'Historical labels use the request prefix; teacher agreement is corroboration, not business acceptance.']})
    write(output/'coverage.json',coverage(rows,groups))
    print(json.dumps({'nodes':len(rows),'new_paid_posts':len(rows)-len(original)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--reviews',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();freeze(a.source,a.reviews,a.output)
