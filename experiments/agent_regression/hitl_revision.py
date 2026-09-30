"""Observed HITL mail feedback: four isolated single-POST nodes; never execute tools."""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

from erp_harness.app.business import completion_target_instruction
from erp_harness.app.runner import build_revision_resume_message
from erp_harness.erp.store import ActionStore
from .freeze import read, digest, write_once
from .prepare import replace, validate_patches
from .runner import complete
from .oracle import evaluate, is_write

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.runtime/hitl-mail-revision-20260930'


def prepare(output):
    frozen = read(OUT / 'frozen.json')
    for name, sha in frozen['files'].items():
        assert digest((OUT / 'observed' / name).read_bytes()) == sha, name
    state = read(OUT / 'observed/state.json')
    action_id = '80bcead6-6fb1-4762-85eb-a7bed21343db'
    row = next(r for r in ActionStore.read_receipts(OUT / 'observed/actions.sqlite3') if r['action_id'] == action_id)
    approval = next(a for a in state['approvals'] if a['action_id'] == action_id)
    draft = {k: approval.get(k) for k in ('model', 'operation', 'record_ids', 'values')}
    draft['mail'] = {k: row['prestate']['invoice_mail'][k] for k in ('subject', 'body', 'email_to')}
    specs = read(Path(__file__).with_suffix('.json'))
    for spec in specs:
        source = ROOT / spec['source']
        assert digest(source.read_bytes()) == spec['request_sha256']
        original = read(source)
        candidate, patches = copy.deepcopy(original), []
        allowed = spec['allowed_patch_paths']
        if spec['id'].startswith('HITL'):
            old_resume = original['messages'][int(allowed[0].split('/')[2])]['content']
            evidence = json.loads(old_resume.split('Host recovery evidence: ', 1)[1])
            evidence.update(reason='revision', revision={'text': frozen['feedback'], 'action_id': action_id, 'draft': draft})
            replace(candidate, original, patches, allowed, allowed[0], build_revision_resume_message(evidence), 'runner.build_revision_resume_message')
        elif spec['id'] == 'B02':
            text = original['messages'][1]['content']
            # Preserve all historical user instructions; replace only the host's completion rule.
            lines = text.splitlines(keepends=True)
            line = next(i for i, s in enumerate(lines) if s.startswith('Completion target: sent.'))
            lines[line] = 'Completion target: sent. ' + completion_target_instruction('invoice_delivery', 'sent') + '\n'
            replace(candidate, original, patches, allowed, allowed[0], ''.join(lines), 'business.completion_target_instruction')
        validate_patches(original, candidate, patches, allowed)
        folder = output / spec['id']
        write_once(folder / 'baseline.json', original)
        write_once(folder / 'candidate.json', candidate)
        write_once(folder / 'patches.json', patches)
        write_once(folder / 'case.json', spec)
    paths = ['src/erp_harness/app/host.py', 'src/erp_harness/app/runner.py', 'src/erp_harness/app/business.py',
             'src/erp_harness/erp/invoice_mail.py', 'src/erp_harness/tools/sops.py', 'experiments/agent_regression/hitl_revision.py']
    write_once(output / 'frozen.json', {'cases': [s['id'] for s in specs], 'maximum_posts': len(specs), 'retries': 0,
               'hashes': {p: digest((ROOT / p).read_bytes()) for p in paths},
               'requests': {s['id']: digest((output / s['id'] / 'candidate.json').read_bytes()) for s in specs}})


def prepare_followup(output):
    """One corrective turn from actual model intents and production tool contracts."""
    from erp_harness.erp.invoice_mail import draft_requirement
    from erp_harness.tools.sops import build_sop_payload
    parents = OUT / 'model'
    for ident in ('HITL02', 'B02'):
        parent = parents / ident
        payload, result = read(parent / 'candidate.json'), read(parent / 'api/output.json')
        calls = result['tool_calls']
        assert len(calls) == 1 and not result['error']
        call = calls[0]
        if ident == 'HITL02':
            assert call['name'] == 'mcp_odoo_execute_method' and call['arguments']['method'] == 'message_post'
            tool_result = draft_requirement(call['arguments']['kwargs'])
            assert tool_result and tool_result['error_code'] == 'mail_draft_required'
        else:
            assert call['name'] == 'get_odoo_sop'
            with patch.dict(os.environ, {'ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS':'account.move.message_post'}):
                tool_result = build_sop_payload(**call['arguments'])
            assert tool_result['success']
        payload['messages'].append({'role':'assistant', 'content':result['text'], 'reasoning_content':result['reasoning'],
            'tool_calls':[{'id':call['id'], 'type':'function', 'function':{'name':call['name'], 'arguments':json.dumps(call['arguments'],ensure_ascii=False)}}]})
        payload['messages'].append({'role':'tool', 'tool_call_id':call['id'], 'content':json.dumps(tool_result,ensure_ascii=False)})
        folder = output / ident
        write_once(folder / 'case.json', read(parent / 'case.json'))
        write_once(folder / 'candidate.json', payload)
        write_once(folder / 'provenance.json', {'parent_request':digest((parent/'candidate.json').read_bytes()),
                   'actual_model_output':digest((parent/'api/output.json').read_bytes()), 'tool_execution':False,
                   'producer':'invoice_mail.draft_requirement' if ident=='HITL02' else 'sops.build_sop_payload'})
    paths = ['src/erp_harness/erp/actions.py', 'src/erp_harness/erp/invoice_mail.py', 'src/erp_harness/tools/sops.py',
             'experiments/agent_regression/hitl_revision.py']
    write_once(output / 'frozen.json', {'cases':['HITL02','B02'], 'maximum_posts':2, 'retries':0,
               'hashes':{p:digest((ROOT/p).read_bytes()) for p in paths},
               'requests':{k:digest((output/k/'candidate.json').read_bytes()) for k in ('HITL02','B02')}})
    for name in paths:
        p=output/'source'/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes((ROOT/name).read_bytes())


def verdict(spec, payload, result, reference):
    if not spec['id'].startswith('HITL'):
        return evaluate(spec, payload, result, reference)
    errors = []
    for call in result.get('tool_calls', []):
        name, args = call['name'], call['arguments']
        if not is_write(name):
            continue
        kwargs = args.get('kwargs', {})
        if (name != 'mcp_odoo_execute_method' or args.get('model') != 'account.move'
                or args.get('method') != 'message_post' or kwargs.get('ids') != [1] or kwargs.get('partner_ids') != [19]):
            errors.append('wrong_action_or_new_business')
        elif not all(isinstance(kwargs.get(k), str) and kwargs[k].strip() for k in ('subject', 'body')):
            errors.append('revised_copy_missing')
        elif set(kwargs) - {'ids', 'partner_ids', 'subject', 'body'}:
            errors.append('delivery_binding_override')
    return {'status': 'inconclusive' if result.get('error') else 'fail' if errors else 'needs_review',
            'violations': errors, 'oracle': spec['oracle'], 'semantic_review': 'Review courtesy, facts and whether execution continues; no golden trace or model judge.'}


async def paid(output):
    frozen = read(output / 'frozen.json')
    for p, sha in frozen['hashes'].items():
        assert digest((ROOT / p).read_bytes()) == sha, p
    for ident in frozen['cases']:
        folder = output / ident
        assert digest((folder / 'candidate.json').read_bytes()) == frozen['requests'][ident]
        spec, payload = read(folder / 'case.json'), read(folder / 'candidate.json')
        result = await complete(payload, folder / 'api', api_key=os.environ.get('COMMAND_CODE_API_KEY') or os.environ['LLM_API_KEY'])
        reference = read((ROOT / spec['source']).with_name(Path(spec['source']).name.replace('.request.json', '.output.json')))
        write_once(folder / 'verdict.json', verdict(spec, payload, result, reference))
        print(json.dumps({'id':ident, 'posts': result['posts'], 'error': result['error'], 'usage':result['usage']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paid', action='store_true')
    parser.add_argument('--followup', action='store_true', help='Prepare one corrective turn for the two observed tool intents')
    parser.add_argument('--output', type=Path, default=OUT / 'model')
    args = parser.parse_args()
    if args.paid:
        asyncio.run(paid(args.output))
    elif args.followup:
        prepare_followup(args.output)
    else:
        prepare(args.output)
