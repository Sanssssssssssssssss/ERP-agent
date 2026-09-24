"""Inspect actual token visibility and compare prefix-only state representations."""
import ast
import json
import os
from pathlib import Path
import re

from .build_cases import TOKEN
from .laya_probe import WORK, questions, read, sha


def compact(case):
    messages = read(case['request_path'])['messages']
    first = next(m['content'] for m in messages if m['role'] == 'user')
    marker = next((s for s in ['Confirmed current phase:\n', 'New user instructions:\n'] if s in first), None)
    goal = first.split(marker, 1)[1].split('\nCompletion target:', 1)[0] if marker else first
    target = next((s for s in first.splitlines() if s.startswith('Completion target:')), '')
    state = {'goal': goal, 'completion_target': target, 'active_capabilities': case['active_capabilities']}
    users = [m for m in messages if m['role'] == 'user']
    if len(users) > 1:
        state['latest_host_or_user_message'] = TOKEN.sub('[REDACTED_APPROVAL_TOKEN]', users[-1]['content'])
    recent = next((m for m in reversed(messages) if m['role'] == 'tool'), None)
    if recent:
        value = json.loads(TOKEN.sub('[REDACTED_APPROVAL_TOKEN]', recent['content']))
        # Remove contract/column enumeration, not observed business facts or failures.
        omitted = ['published_tools', 'added', 'removed', 'tool_contract_sha256', 'fields_used', 'smart_fields_applied']
        state['last_tool'] = {'name': recent.get('name'), 'result': {k: v for k, v in value.items() if k not in omitted}}
    return state


def main():
    os.environ['HF_HOME'] = str(WORK / 'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import laya
    from laya.common import serialize_state

    dataset = WORK / 'dataset'
    cases = [json.loads(s) for s in (dataset / 'cases.jsonl').read_text(encoding='utf8').splitlines()]
    ids = read(WORK / 'format-controls-v1/frozen.json')['ids']
    selected = [next(c for c in cases if c['id'] == key) for key in ids]
    groups = read(dataset / 'catalog.json')['capability_groups']
    original = questions(groups)
    short = {name: {'type': 'noul', 'instructions': 'Does the next step require ' + group['description'] + '?'}
             for name, group in groups.items()}
    body = (WORK / 'upstream/README.md').read_text(encoding='utf8')
    example = next(block for block in re.findall(r'```python\n(.*?)```', body, re.S)
                   if 'Duplicate charge on invoice #4411' in block)
    definitions = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(example).body
                   if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                   and n.targets[0].id in ['state', 'questions']}
    hindi = next(block for block in re.findall(r'router.predict\((\{"body":.*?\}), questions\)', example))
    jobs = [{'id': 'official_en', 'variant': 'official_smoke', 'state': definitions['state'], 'questions': definitions['questions']},
            {'id': 'official_hi', 'variant': 'official_smoke', 'state': ast.literal_eval(hindi), 'questions': definitions['questions']}]
    for case in selected:
        clean = compact(case)
        jobs.extend({'id': case['id'], 'variant': name, 'state': state, 'questions': specs}
                    for name, state, specs in [('compact_original_questions', clean, original),
                                               ('compact_short_questions', clean, short)])
        # Context-length control: identical raw input, two sentinel groups to keep GPU memory bounded.
        jobs.append({'id': case['id'], 'variant': 'raw_long_context', 'state': case['input']['state'],
                     'questions': {k: original[k] for k in ['actions', 'employee']}, 'max_len': 4096})
    output = WORK / 'input-controls-v1'
    output.mkdir(exist_ok=False)
    (output / 'frozen.json').write_text(json.dumps({'script_sha256': sha(__file__), 'dataset_sha256': sha(dataset / 'cases.jsonl'),
        'model_lock': read(WORK / 'model-lock.json'), 'official_readme_sha256': sha(WORK / 'upstream/README.md'),
        'jobs': jobs, 'limit': 'Exploratory dev diagnostics. Official examples are setup checks, not historical ERP cases.'}, ensure_ascii=False, indent=2), encoding='utf8')
    agent = laya.load(str((WORK / 'model-multilingual').resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    with (output / 'predictions.jsonl').open('x', encoding='utf8') as log:
        for job in jobs:
            specs = job['questions']
            internal = {name: agent._to_internal(q) for name, q in specs.items()}
            encoded = agent._encode_state(job['state'], list(specs), internal, max_len=job.get('max_len'))
            response = agent.system_one(job['state'], specs, max_len=job.get('max_len'))
            row = {'id': job['id'], 'variant': job['variant'], 'response': response,
                   'state_tokens': len(agent.tok(serialize_state(job['state']), add_special_tokens=False)['input_ids']),
                   'effective_sequences': {name: {'tokens': len(item['ids']), 'text': agent.tok.decode(item['ids'])}
                                           for name, item in zip(specs, encoded)}}
            log.write(json.dumps(row, ensure_ascii=False) + '\n'); log.flush()
            print(json.dumps({'id': job['id'], 'variant': job['variant'],
                  'answers': {k: v.get('noul', v.get('choice', v.get('score'))) for k, v in response['answers'].items()}}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
