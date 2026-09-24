"""Dev-only controls for boolean result fields and concrete capability descriptions."""
import json
import os

from .input_probe import compact
from .laya_probe import WORK, questions, read, sha


def status_words(value):
    if isinstance(value, list):
        return [status_words(v) for v in value]
    if not isinstance(value, dict):
        return value
    return {('tool_outcome' if k == 'success' and isinstance(v, bool) else k):
            ('succeeded' if v else 'failed') if k == 'success' and isinstance(v, bool) else status_words(v)
            for k, v in value.items()}


def main():
    os.environ['HF_HOME'] = str(WORK / 'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import laya

    groups = read(WORK / 'dataset/catalog.json')['capability_groups']
    original = questions(groups)
    intents = {
        'actions': 'Does the user ask to create or change ERP records, confirm orders, post invoices, or send a message?',
        'accounting': 'Does the user request an accounts receivable aging report, accounts payable aging report, or accounting health report?',
        'attachments': 'Does the user ask to read or download the contents of an attached file?',
        'async_reads': 'Does the user request managing asynchronous background data reading jobs?',
        'cross_instance': 'Does the user request comparing data across multiple separate Odoo database instances?',
        'diagnostics': 'Does the latest tool result report an error that needs investigation?',
        'employee': 'Does the user request finding an employee in the staff directory?',
        'knowledge': 'Does the user request searching or indexing the local knowledge base?',
        'migration': 'Does the user request an Odoo version migration or software upgrade analysis?',
        'time_off': 'Does the user request employee leave or holiday records?',
    }
    concrete = {name: {'type': 'noul', 'instructions': text} for name, text in intents.items()}
    category = {'capability': {'type': 'choice', 'instructions': 'Which capability best fits the next business step? Select base_only for ordinary reads, field lookup, reporting completion, or clarification.',
                 'criteria': {'base_only': 'Ordinary record search/read, field metadata, or reply to user.',
                              **{name: group['description'] for name, group in groups.items()}}}}
    cases = {c['id']: c for c in (json.loads(s) for s in (WORK / 'dataset/cases.jsonl').read_text(encoding='utf8').splitlines())}
    jobs = []
    for key in read(WORK / 'format-controls-v1/frozen.json')['ids']:
        clean = compact(cases[key])
        neutral = status_words(clean)
        for variant, state, specs in [('status_words_original_questions', neutral, original),
                                     ('compact_concrete_intents', neutral, concrete),
                                     ('goal_only_concrete_intents', {'goal': clean['goal']}, concrete),
                                     ('compact_primary_choice', neutral, category)]:
            jobs.append({'id': key, 'variant': variant, 'state': state, 'questions': specs})
    output = WORK / 'semantic-controls-v1'
    output.mkdir(exist_ok=False)
    (output / 'frozen.json').write_text(json.dumps({'script_sha256': sha(__file__), 'input_helper_sha256': sha(compact.__code__.co_filename),
        'jobs': jobs, 'limit': 'Exploratory dev controls, no label fitting. Goal-only ignores execution progress; primary choice is single-label, not full multiselect.'}, ensure_ascii=False, indent=2), encoding='utf8')
    agent = laya.load(str((WORK / 'model-multilingual').resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    with (output / 'predictions.jsonl').open('x', encoding='utf8') as log:
        for job in jobs:
            response = agent.system_one(job['state'], job['questions'])
            row = {'id': job['id'], 'variant': job['variant'], 'response': response}
            log.write(json.dumps(row, ensure_ascii=False) + '\n'); log.flush()
            print(json.dumps({'id': job['id'], 'variant': job['variant'],
                  'selection': [k for k, a in response['answers'].items() if a.get('noul', 0) >= .5],
                  'choice': response['answers'].get('capability', {}).get('choice')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    assert status_words({'success': False, 'nested': {'success': True}, 'active': False}) == {
        'tool_outcome': 'failed', 'nested': {'tool_outcome': 'succeeded'}, 'active': False}
    main()
