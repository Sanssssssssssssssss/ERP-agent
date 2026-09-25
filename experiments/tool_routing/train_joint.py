"""Joint Laya adaptation; frozen token embeddings fit the local 8 GB GPU.

Uses reviewed hard labels and CE. This is not a reproduction of upstream RLCD.
No ERP or paid model calls. Evaluate the frozen test only after dev selection.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

from .build_cases import read
from .laya_probe import WORK, sha
from .model_comparison import verdict
from .publication_probe import publication_questions
from .reviewed_dataset import training_labels, validate
from .train_reviewed import score, training_items


def unique_items(items):
    """Keep the reviewed weight mass without repeatedly presenting identical pairs."""
    result = {}
    for item in items:
        key = item['case_id'], item['group'], item['variant']
        if key not in result:
            result[key] = {**item, 'weight': 0.}
        assert result[key]['label'] == item['label']
        result[key]['weight'] += item['weight']
    return list(result.values())


def epoch_items(items, epoch):
    # Every pair sees all four formats in four epochs; formats are mixed within each epoch.
    rows = [dict(i) for i in items if i['variant'] ==
            (int(hashlib.sha256((i['case_id']+'|'+i['group']).encode()).hexdigest()[:8], 16)+epoch-1) % 4]
    scale = len(rows) / sum(i['weight'] for i in rows)
    for row in rows:
        row['weight'] *= scale
    random.Random(20260924+epoch).shuffle(rows)
    return rows


def rank(result):
    return -result['pass'], result['missing_required'], result['unrelated']


def joint_questions(groups, label, first):
    questions = publication_questions(groups, label, first)
    for name, group in groups.items():
        questions[name]['criteria'][label] = 'Publish '+name+': '+group['description']
        questions[name]['criteria']['B' if label == 'A' else 'A'] = 'Leave '+name+' unpublished; base or other capabilities suffice.'
    return questions


def main(args):
    os.environ.update(HF_HOME=str(WORK/'hf-cache'), HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    import laya
    import torch
    from laya.common import collate_items, render_options

    seed = 20260924
    random.seed(seed); torch.manual_seed(seed); torch.set_num_threads(4)
    source = read(args.source/'frozen.json')
    for name in ['cases.json', 'groups.json']:
        assert sha(args.source/name) == source['hashes'][name]
    cases = read(args.source/'cases.json'); groups = read(args.source/'groups.json'); names = list(groups)
    splits = {s: [r for r in cases if r['split'] == s] for s in ['train', 'dev', 'test']}
    validate(splits['train'], splits['test'], groups, splits['dev'])
    lock = read(WORK/'model-lock.json'); model_path = WORK/'model-multilingual'
    assert sha(model_path/'model.safetensors') == lock['files_sha256']['model.safetensors']
    args.output.mkdir(parents=True, exist_ok=False)
    agent = laya.load(str(model_path.resolve()), device='cuda')
    assert agent.device.type == 'cuda' and agent._fast is None and list(agent.temperature) == [1., 1., 1.]
    specs = [joint_questions(groups, label, first) for label in ['A', 'B'] for first in [False, True]]
    for spec in specs:
        for question in spec.values():
            internal = agent._to_internal(question)
            sizes = [len(agent.tok(' '+s, add_special_tokens=False)['input_ids']) for s in render_options(internal)]
            assert max(sizes) <= 48
            assert len(agent.tok('choice question: '+internal['ins'], add_special_tokens=False)['input_ids'])+sum(sizes)+2 <= agent.cfg['head_max_len']
    encoded = {}; lengths = {}
    for row in cases:
        lengths[row['id']] = len(agent.tok(row['state'].replace(agent.tok.mask_token, ' '), add_special_tokens=False)['input_ids'])+260
        assert lengths[row['id']] <= 8192, row['id']
        encoded[row['id']] = [agent._encode_state(row['state'], names, {g: agent._to_internal(q) for g, q in spec.items()}, max_len=lengths[row['id']]) for spec in specs]
    items = unique_items(training_items(splits['train'], names, specs, encoded))
    frozen = {'source_sha256': sha(args.source/'frozen.json'), 'model_lock': lock, 'seed': seed,
              'questions': specs, 'max_len_by_case': lengths, 'epochs': args.epochs,
              'lr_encoder': 2.5e-5, 'lr_head': 1.e-4, 'batch': 16, 'microbatch': 1,
              'selection': 'Dev whole-route passes, then required misses, then unrelated selections. All four formats. Epoch 0 eligible.',
              'objective': 'Reviewed weighted hard-label cross entropy; unknown labels masked. No RLCD or fabricated soft targets.',
              'format_schedule': 'Each judged pair once per epoch; all four formats in every four consecutive epochs.',
              'encoder_scope': 'All encoder layers trained; input embeddings frozen to fit local VRAM.',
              'baseline_from': str(args.baseline_from.resolve()) if args.baseline_from else None,
              'baseline_hashes': {n: sha(args.baseline_from/n) for n in ['frozen.json', 'summary.json', 'base-dev.jsonl']} if args.baseline_from else None,
              'sources': {str(p): sha(p) for p in [Path(__file__), Path(training_items.__code__.co_filename), Path(training_labels.__code__.co_filename), Path(publication_questions.__code__.co_filename), Path(verdict.__code__.co_filename)]}}
    (args.output/'frozen.json').write_text(json.dumps(frozen, indent=2), encoding='utf8')
    (args.output/'sources').mkdir()
    for path in frozen['sources']:
        (args.output/'sources'/Path(path).name).write_bytes(Path(path).read_bytes())
    summary = {'paid_calls': 0, 'odoo_calls': 0, 'epochs': [], 'selected_epoch': 0}
    start = time.perf_counter()

    @torch.no_grad()
    def evaluate(subset, tag):
        agent.model.eval(); rows = []
        with (args.output/(tag+'.jsonl')).open('x', encoding='utf8') as log:
            for row in subset:
                for v, spec in enumerate(specs):
                    selected = []; predictions = {}
                    for gi, group in enumerate(names):
                        logits, _ = agent._infer(collate_items([[encoded[row['id']][v][gi]]], agent.tok.pad_token_id))
                        assert agent.device.type == 'cuda' and torch.isfinite(logits).all()
                        slot = list(spec[group]['criteria']).index('A' if v < 2 else 'B')
                        predictions[group] = float(logits.softmax(-1)[0, slot])
                        if int(logits.argmax(-1)) == slot:
                            selected.append(group)
                    result = {'id': row['id'], 'variant': v, 'probabilities': predictions, **verdict(selected, row)}
                    rows.append(result); log.write(json.dumps(result)+'\n'); log.flush()
        result = score(rows)
        result['by_variant'] = {str(v): score([r for r in rows if r['variant'] == v]) for v in range(4)}
        print(json.dumps({'stage': tag, **result}), flush=True)
        return rows, result

    if args.baseline_from:
        prior = read(args.baseline_from/'frozen.json')
        for key in ['source_sha256', 'model_lock', 'questions', 'max_len_by_case']:
            assert prior[key] == frozen[key], f'Baseline differs: {key}'
        baseline = read(args.baseline_from/'summary.json')['baseline']
        summary['baseline'] = baseline
        (args.output/'base-dev.jsonl').write_bytes((args.baseline_from/'base-dev.jsonl').read_bytes())
    else:
        _, dev = evaluate(splits['dev'], 'base-dev')
        summary['baseline'] = {'dev': dev}
    best = rank(summary['baseline']['dev'])
    agent.model.requires_grad_(True)
    agent.model.act_head.requires_grad_(False)
    agent.model.encoder.get_input_embeddings().requires_grad_(False)
    agent.model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    agent.model.head_checkpointing = True
    params = [p for p in agent.model.parameters() if p.requires_grad]
    encoder = [p for p in agent.model.encoder.parameters() if p.requires_grad]
    head = [p for n, p in agent.model.named_parameters() if p.requires_grad and not n.startswith('encoder.')]
    assert encoder and head and not any(p.requires_grad for p in agent.model.encoder.get_input_embeddings().parameters())
    summary['trainable_parameters'] = sum(p.numel() for p in params)
    optimizer = torch.optim.AdamW([{'params': encoder, 'lr': 2.5e-5, 'initial_lr': 2.5e-5},
                                  {'params': head, 'lr': 1.e-4, 'initial_lr': 1.e-4}], weight_decay=.01)
    total_steps = sum(math.ceil(len(epoch_items(items, e))/16) for e in range(1, args.epochs+1))
    step = 0
    for epoch in range(1, args.epochs+1):
        agent.model.train(); rows = epoch_items(items, epoch); loss_sum = 0.
        for offset in range(0, len(rows), 16):
            chunk = rows[offset:offset+16]; optimizer.zero_grad(set_to_none=True)
            factor = min(1., (step+1)/20) * .5*(1+math.cos(math.pi*step/total_steps))
            for pg in optimizer.param_groups:
                pg['lr'] = pg['initial_lr'] * max(.01, factor)
            for item in chunk:
                batch = collate_items([[item]], agent.tok.pad_token_id)
                inputs = {k: batch[k].to(agent.device) for k in ['input_ids', 'attention_mask', 'marker_pos', 'marker_mask', 'qtype']}
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    logits, _ = agent.model(**inputs)
                    loss = torch.nn.functional.cross_entropy(logits, batch['label'].to(agent.device)) * item['weight']
                assert torch.isfinite(loss)
                (loss/len(chunk)).backward(); loss_sum += float(loss.detach())
            torch.nn.utils.clip_grad_norm_(params, 1., error_if_nonfinite=True)
            optimizer.step(); step += 1
            if step % 20 == 0:
                print(json.dumps({'epoch': epoch, 'updates': step, 'of': total_steps, 'loss': loss_sum/min(offset+16, len(rows)), 'gpu_peak_bytes': torch.cuda.max_memory_allocated()}), flush=True)
        _, dev = evaluate(splits['dev'], f'epoch-{epoch}-dev')
        if rank(dev) < best:
            best = rank(dev); summary['selected_epoch'] = epoch
            torch.save({n: p.detach().cpu() for n, p in agent.model.named_parameters() if p.requires_grad}, args.output/'selected.pt')
        summary['epochs'].append({'epoch': epoch, 'updates': step, 'training_loss': loss_sum/len(rows), 'dev': dev})
        (args.output/'training.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
        torch.save({'epoch': epoch, 'step': step, 'model': {n: p.detach().cpu() for n, p in agent.model.named_parameters() if p.requires_grad},
                    'optimizer': optimizer.state_dict(), 'torch_rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all()}, args.output/'latest.pt')
    del optimizer
    agent.model.cpu(); del agent
    torch.cuda.empty_cache()
    agent = laya.load(str(model_path.resolve()), device='cuda')
    if summary['selected_epoch']:
        agent.model.load_state_dict(torch.load(args.output/'selected.pt', weights_only=True), strict=False)
    routes, summary['test'] = evaluate(splits['test'], 'candidate-test')
    agent.model.eval(); parity = []
    with (args.output/'sdk-parity.jsonl').open('x', encoding='utf8') as log:
        for row in splits['test']:
            for v, spec in enumerate(specs):
                selected = []; delta = 0.
                expected = next(r for r in routes if r['id'] == row['id'] and r['variant'] == v)
                for group in names:
                    out = agent.system_one(row['state'], {group: spec[group]}, max_len=lengths[row['id']])
                    assert agent.device.type == 'cuda'
                    label = 'A' if v < 2 else 'B'; answer = out['answers'][group]
                    if answer['choice'] == label:
                        selected.append(group)
                    delta = max(delta, abs(answer['probabilities'][label]-expected['probabilities'][group]))
                check = {'id': row['id'], 'variant': v, 'same_set': sorted(selected) == expected['selected_groups'], 'max_delta': delta}
                parity.append(check); log.write(json.dumps(check)+'\n'); log.flush()
    assert all(r['same_set'] and r['max_delta'] < .001 for r in parity)
    summary.update(sdk_parity={'cases': len(parity), 'same_sets': sum(r['same_set'] for r in parity)}, elapsed_seconds=time.perf_counter()-start,
                   gpu_peak_bytes=torch.cuda.max_memory_allocated(), selection_note='Selected only on dev; fixed heldout is development-visible, not a new blind benchmark.')
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
    print(json.dumps({'stage': 'done', **summary}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--epochs', type=int, default=4); p.add_argument('--baseline-from', type=Path)
    a = p.parse_args()
    if a.epochs < 4 or a.epochs % 4:
        p.error('Use complete four-epoch format cycles.')
    main(a)
