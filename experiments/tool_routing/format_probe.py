"""Development-only format/order diagnostic on six existing historical prefixes."""
import json
import os
from pathlib import Path

from .laya_probe import WORK, choose, questions, read, score, sha


def main():
    os.environ['HF_HOME'] = str(WORK / 'hf-cache')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import laya

    dataset = WORK / 'dataset'
    cases = [json.loads(s) for s in (dataset / 'cases.jsonl').read_text(encoding='utf8').splitlines()]
    groups = read(dataset / 'catalog.json')['capability_groups']
    original = questions(groups)
    selected = []
    for category in ['initial', 'before_write', 'recovery', 'terminal', 'after_approval', 'post_write_readback']:
        case = next(c for c in cases if c['split'] == 'dev' and c['dynamic_router_applicable']
                    and category in c['categories'] and c['id'] not in [x['id'] for x in selected])
        selected.append(case)
    variants = {order: {name: {'type': 'choice', 'instructions': q['instructions'],
                    'criteria': {key: q['criteria'][key] for key in keys}}
                       for name, q in original.items()}
                for order, keys in [('false_first', ['false', 'true']), ('true_first', ['true', 'false'])]}
    output = WORK / 'format-controls-v1'
    output.mkdir(exist_ok=False)
    frozen = {'ids': [c['id'] for c in selected], 'selection': 'First unique dev node per declared category; not selected by model score.',
              'dataset_sha256': sha(dataset / 'cases.jsonl'), 'script_sha256': sha(__file__),
              'variants': variants, 'original': original, 'model_lock': read(WORK / 'model-lock.json'),
              'baseline_frozen_sha256': sha(WORK / 'noul-v1/frozen.json'),
              'limit': 'Exploratory format/order diagnostic; no threshold fitting or heldout rerun.'}
    (output / 'frozen.json').write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding='utf8')
    agent = laya.load(str((WORK / 'model-multilingual').resolve()), device='cuda')
    assert agent.device.type == 'cuda'
    rows = []
    with (output / 'predictions.jsonl').open('x', encoding='utf8') as log:
        for case in selected:
            for name, specs in variants.items():
                response = agent.system_one(case['input']['state'], specs)
                # Read the semantic key, never the option index; reversed order preserves meaning.
                probabilities = {g: {'noul': a['probabilities']['true']} for g, a in response['answers'].items()}
                decision = choose({'answers': probabilities}, groups, case['active_capabilities'])
                row = {'id': case['id'], 'variant': name, 'response': response, 'decision': decision,
                       'score': score(case, decision, groups)}
                rows.append(row)
                log.write(json.dumps(row, ensure_ascii=False) + '\n'); log.flush()
    print(json.dumps({'decision_model_calls': len(rows), 'binary_decisions': len(rows) * len(groups),
                      'generative_api_calls': 0, 'odoo_calls': 0, 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
