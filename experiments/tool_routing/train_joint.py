"""Joint Laya adaptation; frozen token embeddings fit the local 8 GB GPU.

Uses reviewed hard labels and CE. This is not a reproduction of upstream RLCD.
No ERP or paid model calls. Evaluate the frozen test only after dev selection.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
import sys

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


def epoch_items(items, epoch, sample_by_weight=False, cover_weighted_pairs=False):
    # Rotate formats; random sampling can omit pairs, covered exposures cannot.
    rows = [dict(i) for i in items if i['variant'] ==
            (int(hashlib.sha256((i['case_id']+'|'+i['group']).encode()).hexdigest()[:8], 16)+epoch-1) % 4]
    scale = len(rows) / sum(i['weight'] for i in rows)
    for row in rows:
        row['weight'] *= scale
    rng = random.Random(20260924+epoch)
    if sample_by_weight:
        # Repeated unit-weight exposures avoid large rare-example gradients being clipped away.
        return [{**row, 'weight': 1.} for row in rng.choices(rows, weights=[r['weight'] for r in rows], k=len(rows))]
    if cover_weighted_pairs:
        rows = [{**row, 'weight': row['weight']/math.ceil(row['weight'])}
                for row in rows for _ in range(math.ceil(row['weight']))]
    rng.shuffle(rows)
    return rows


def rank(result):
    return -result['pass'], result['missing_required'], result['unrelated']


def balance_sources(items, cases):
    """Keep each class's mass, sharing it between historical and authored inputs."""
    origins = {r['id']: 'authored' if r.get('source_kind') == 'new_scripted_contract_probe'
               else 'historical' for r in cases}
    masses = defaultdict(lambda: defaultdict(float))
    for row in items:
        key = row['group'], row['variant'], row['semantic_target']
        masses[key][origins[row['case_id']]] += row['weight']
    result = []
    for row in items:
        bucket = masses[row['group'], row['variant'], row['semantic_target']]
        scale = sum(bucket.values()) / (len(bucket) * bucket[origins[row['case_id']]])
        result.append({**row, 'weight': row['weight'] * scale})
    return result


def balance_phases(items, cases):
    """Preserve family mass; balance judged include/exclude phases within it."""
    families = {r['id']: r['business_group'] for r in cases}
    masses = defaultdict(lambda: defaultdict(float))
    for row in items:
        masses[row['group'], row['variant'], families[row['case_id']]][row['semantic_target']] += row['weight']
    result = []
    for row in items:
        bucket = masses[row['group'], row['variant'], families[row['case_id']]]
        result.append({**row, 'weight': row['weight'] * sum(bucket.values()) /
                       (len(bucket)*bucket[row['semantic_target']])})
    return result


def joint_questions(groups, label, first):
    questions = publication_questions(groups, label, first)
    for name, group in groups.items():
        questions[name]['criteria'][label] = 'Publish '+name+': '+group['description']
        questions[name]['criteria']['B' if label == 'A' else 'A'] = 'Leave '+name+' unpublished; base or other capabilities suffice.'
    return questions


def validate_states(cases, groups):
    targets = {}
    for row in cases:
        state = json.dumps(json.loads(row['state']), sort_keys=True, ensure_ascii=False)
        for group, label in training_labels(row, groups).items():
            if label is None:
                continue
            key = state, group
            if key in targets and targets[key][0] != label:
                raise ValueError(f'Conflicting supervision for identical state: {targets[key][1]} / {row["id"]} / {group}')
            targets[key] = label, row['id']


def main(args):
    os.environ.update(HF_HOME=str(WORK/'hf-cache'), HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    import laya
    import torch
    from laya.common import collate_items, render_options

    seed = 20260924
    random.seed(seed); torch.manual_seed(seed); torch.set_num_threads(4)
    source = read(args.source/'frozen.json')
    for name, digest in source.get('sources', {}).items():
        assert sha(name) == digest, 'Dataset projector changed: '+name
    for name in ['cases.json', 'groups.json']:
        assert sha(args.source/name) == source['hashes'][name]
    cases = read(args.source/'cases.json'); groups = read(args.source/'groups.json'); names = list(groups)
    splits = {s: [r for r in cases if r['split'] == s] for s in ['train', 'dev', 'test']}
    validate(splits['train'], splits['test'], groups, splits['dev'])
    validate_states(cases, groups)
    lock = read(WORK/'model-lock.json'); model_path = args.initial_model or WORK/'model-multilingual'
    if not args.initial_model:
        assert sha(model_path/'model.safetensors') == lock['files_sha256']['model.safetensors']
    args.output.mkdir(parents=True, exist_ok=False)
    if args.initial_model:
        from .router import CapabilityRouter
        agent = CapabilityRouter(model_path).agent
    else:
        agent = laya.load(str(model_path.resolve()), device='cuda')
    assert agent.device.type == 'cuda' and agent._fast is None and list(agent.temperature) == [1., 1., 1.]
    question_factory = joint_questions
    if source.get('projection') == 'host_facts_v1':
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'src'))
        from erp_harness.app.routing_state import disclosure_questions
        question_factory = disclosure_questions
    specs = [question_factory(groups, label, first) for label in ['A', 'B'] for first in [False, True]]
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
    if args.balance_sources:
        items = balance_sources(items, splits['train'])
    if args.balance_phases:
        items = balance_phases(items, splits['train'])
    lr_encoder, lr_head = (2.5e-6, 1.e-5) if args.initial_model else (2.5e-5, 1.e-4)
    frozen = {'source_path': str(args.source.resolve()), 'source_sha256': sha(args.source/'frozen.json'), 'model_lock': lock, 'seed': seed,
              'projection':source.get('projection','legacy'),
              'loaded_model_files_sha256': {name:sha(model_path/name) for name in lock['files_sha256']},
              'questions': specs, 'max_len_by_case': lengths, 'epochs': args.epochs,
              'lr_encoder': lr_encoder, 'lr_head': lr_head, 'batch': 16, 'microbatch': 1,
              'initial_model': str(model_path.resolve()) if args.initial_model else None,
              'initial_model_manifest_sha256': sha(model_path/'router.json') if args.initial_model else None,
              'selection': 'Dev whole-route passes, then required misses, then unrelated selections. All four formats. Epoch 0 eligible.',
              'objective': 'Reviewed weighted hard-label cross entropy; unknown labels masked. No RLCD or fabricated soft targets.',
              'format_schedule': ('Weighted sampling with replacement; each pair rotates format over four epochs. Rare pairs repeat, low-weight pairs may be absent.'
                                  if args.sample_by_weight else 'Every judged pair covered; rotate all four formats in four epochs. Split large weights into bounded exposures when enabled.'),
              'balance_sources': args.balance_sources,
              'balance_phases': args.balance_phases,
              'sample_by_weight': args.sample_by_weight,
              'cover_weighted_pairs': args.cover_weighted_pairs,
              'evaluation_batch': 'All capability questions together, matching the deployed SDK call.',
              'encoder_scope': 'All encoder layers trained; input embeddings frozen to fit local VRAM.',
              'baseline_from': str(args.baseline_from.resolve()) if args.baseline_from else None,
              'baseline_hashes': {n: sha(args.baseline_from/n) for n in ['frozen.json', 'summary.json', 'base-dev.jsonl']} if args.baseline_from else None,
              'sources': {str(p): sha(p) for p in [Path(__file__), Path(question_factory.__code__.co_filename), Path(training_items.__code__.co_filename), Path(training_labels.__code__.co_filename), Path(publication_questions.__code__.co_filename), Path(verdict.__code__.co_filename), *map(Path,source.get('sources',{}))]}}
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
                    logits, _ = agent._infer(collate_items([encoded[row['id']][v]], agent.tok.pad_token_id))
                    assert agent.device.type == 'cuda' and torch.isfinite(logits).all()
                    for gi, group in enumerate(names):
                        slot = list(spec[group]['criteria']).index('A' if v < 2 else 'B')
                        predictions[group] = float(logits.softmax(-1)[gi, slot])
                        if int(logits[gi].argmax(-1)) == slot:
                            selected.append(group)
                    result = {'id': row['id'], 'variant': v, 'probabilities': predictions, **verdict(selected, row)}
                    rows.append(result); log.write(json.dumps(result)+'\n'); log.flush()
        result = score(rows)
        result['by_variant'] = {str(v): score([r for r in rows if r['variant'] == v]) for v in range(4)}
        print(json.dumps({'stage': tag, **result}), flush=True)
        return rows, result

    if args.baseline_from:
        prior = read(args.baseline_from/'frozen.json')
        for key in ['source_sha256', 'model_lock', 'loaded_model_files_sha256', 'questions', 'max_len_by_case', 'evaluation_batch']:
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
    optimizer = torch.optim.AdamW([{'params': encoder, 'lr': lr_encoder, 'initial_lr': lr_encoder},
                                  {'params': head, 'lr': lr_head, 'initial_lr': lr_head}], weight_decay=.01)
    total_steps = sum(math.ceil(len(epoch_items(items, e, args.sample_by_weight, args.cover_weighted_pairs))/16) for e in range(1, args.epochs+1))
    step = 0
    for epoch in range(1, args.epochs+1):
        agent.model.train(); rows = epoch_items(items, epoch, args.sample_by_weight, args.cover_weighted_pairs); loss_sum = 0.
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
                out = agent.system_one(row['state'], spec, max_len=lengths[row['id']])
                assert agent.device.type == 'cuda'
                for group in names:
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
    p.add_argument('--initial-model', type=Path, help='Continue from a verified exported router; use one tenth of the initial learning rate.')
    p.add_argument('--balance-sources', action='store_true', help='Equal historical/authored mass within each reviewed capability class.')
    p.add_argument('--balance-phases', action='store_true', help='Equal known positive/negative phase mass within each business family and capability.')
    exposure=p.add_mutually_exclusive_group()
    exposure.add_argument('--sample-by-weight', action='store_true', help='Sample proportional to reviewed weights and train each exposure at unit weight.')
    exposure.add_argument('--cover-weighted-pairs', action='store_true', help='Cover every pair, splitting weights above one into repeated bounded-loss exposures.')
    a = p.parse_args()
    if a.epochs < 4 or a.epochs % 4:
        p.error('Use complete four-epoch format cycles.')
    main(a)
