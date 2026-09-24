"""Paired question-contract check on training nodes only; no API or ERP calls."""
import argparse
import json
import os
from pathlib import Path

from .build_cases import read
from .laya_probe import WORK, sha
from .reviewed_dataset import training_labels
from .train_head import concrete_questions, chosen


def publication_questions(groups, true_label='A', true_first=False):
    false_label = 'B' if true_label == 'A' else 'A'
    result = {}
    for name, group in groups.items():
        choices = [(false_label, 'Leave this optional capability unpublished.'),
                   (true_label, 'Publish this optional capability for the next assistant turn.')]
        result[name] = {'type': 'choice', 'instructions':
            'Should the next ERP assistant turn have this capability available? '
            'Choose publication, not the next tool call. Base tools stay available. '
            'Include it for the pending operation even if a base read comes first. '
            'Publication does not approve execution. Ignore unrelated current publication. '
            f'Capability: {name}. {group["description"]} Tools: '+', '.join(group['tools']),
            'criteria': dict(reversed(choices) if true_first else choices)}
    return result


def main(args):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    import laya
    import torch
    from laya.common import render_options

    torch.set_num_threads(2)
    manifest = read(args.source/'frozen.json')
    for name in ['cases.json', 'groups.json']:
        assert sha(args.source/name) == manifest['hashes'][name]
    groups = read(args.source/'groups.json')
    cases = [r for r in read(args.source/'cases.json') if r['split'] == 'train'
             and r.get('source_kind') == 'new_scripted_contract_probe' and r['id'].endswith(':0')]
    # Fixed selection by fixture order and reviewed labels, before predictions.
    pairs = [(next(r for r in cases if training_labels(r, groups)[g] == target), g, target)
             for g in groups for target in [0, 1]]
    specs = {name: [factory(groups, label, first) for label in ['A', 'B'] for first in [False, True]]
             for name, factory in [('step', concrete_questions), ('publication', publication_questions)]}
    args.output.mkdir(parents=True, exist_ok=False)
    frozen = {'source_sha256': sha(args.source/'frozen.json'), 'script_sha256': sha(__file__),
              'model_lock': read(WORK/'model-lock.json'), 'questions': specs,
              'pairs': [(r['id'], g, t) for r, g, t in pairs], 'device': 'cpu',
              'limitation': 'Same frozen states and pretrained weights; training nodes only. Pair accuracy is not whole-route or business acceptance.'}
    (args.output/'frozen.json').write_text(json.dumps(frozen, indent=2), encoding='utf8')
    model = WORK/'model-multilingual'
    assert sha(model/'model.safetensors') == frozen['model_lock']['files_sha256']['model.safetensors']
    agent = laya.load(str(model.resolve()), device='cpu')
    assert agent.device.type == 'cpu' and agent._fast is None
    rows = []
    with (args.output/'predictions.jsonl').open('x', encoding='utf8') as log:
        for arm, variants in specs.items():
            for case, group, target in pairs:
                length = len(agent.tok(case['state'], add_special_tokens=False)['input_ids']) + 260
                assert length <= 8192
                for variant, spec in enumerate(variants):
                    q = spec[group]; internal = agent._to_internal(q)
                    option_lengths = [len(agent.tok(' '+s, add_special_tokens=False)['input_ids']) for s in render_options(internal)]
                    assert max(option_lengths) <= 48
                    assert len(agent.tok('choice question: '+internal['ins'], add_special_tokens=False)['input_ids']) + sum(option_lengths) + 2 <= agent.cfg['head_max_len']
                    result = agent.system_one(case['state'], {group: q}, max_len=length)
                    prediction = chosen(result['answers'][group], 'A' if variant < 2 else 'B')
                    row = {'arm': arm, 'id': case['id'], 'group': group, 'variant': variant,
                           'target': target, 'correct': prediction == bool(target), 'response': result}
                    rows.append(row); log.write(json.dumps(row)+'\n'); log.flush()
            print(json.dumps({'arm': arm, 'correct': sum(r['correct'] for r in rows if r['arm'] == arm),
                              'pairs': sum(r['arm'] == arm for r in rows)}), flush=True)
    summary = {arm: {'correct': sum(r['correct'] for r in rows if r['arm'] == arm), 'pairs': len(pairs)*4}
               for arm in specs}
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    main(p.parse_args())
