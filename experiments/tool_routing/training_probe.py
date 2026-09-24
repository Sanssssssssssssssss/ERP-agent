"""Trainability check on four existing train nodes; no API, Odoo or heldout fitting."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .build_cases import read
from .laya_probe import WORK, sha
from .reviewed_dataset import training_labels
from .train_head import concrete_questions, fit_epoch


def main(args):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    import laya
    import torch
    from laya.common import collate_items

    torch.manual_seed(20260924); torch.set_num_threads(4)
    manifest = read(args.source/'frozen.json')
    for name in ['cases.json', 'groups.json']:
        assert sha(args.source/name) == manifest['hashes'][name]
    cases = {r['id']: r for r in read(args.source/'cases.json')}
    groups = read(args.source/'groups.json')
    selections = [
        ('E05:r_c60fb73ae99f47dbb868c77f0b46f840:0016', 'actions'),
        ('S01499:r_dc43075e24a9418cbd78279b48b09d1b:0007', 'actions'),
        ('history:912bc30695b0245e232f', 'knowledge'),
        ('E03:r_923977c281284ce0bf648c2ae0e83948:0001', 'knowledge'),
    ]
    model_dir = WORK/'model-multilingual'
    lock = read(WORK/'model-lock.json')
    assert sha(model_dir/'model.safetensors') == lock['files_sha256']['model.safetensors']
    args.output.mkdir(parents=True, exist_ok=False)
    agent = laya.load(str(model_dir.resolve()), device='cuda')
    assert agent._fast is None and agent.device.type == 'cuda'
    specs = [concrete_questions(groups, label, first) for label in ['A', 'B'] for first in [False, True]]
    rows = []
    for case_id, group in selections:
        case = cases[case_id]; assert case['split'] == 'train'
        target = training_labels(case, list(groups))[group]; assert target in [0, 1]
        length = len(agent.tok(case['state'], add_special_tokens=False)['input_ids']) + 260
        assert length <= 8192
        for variant, spec in enumerate(specs):
            q = spec[group]; positive = 'A' if variant < 2 else 'B'
            slot = list(q['criteria']).index(positive)
            item = agent._encode_state(case['state'], [group], {group: agent._to_internal(q)}, max_len=length)[0]
            item['label'] = slot if target else 1-slot
            rows.append(dict(id=case_id, group=group, target=target, variant=variant,
                             item=item, state=case['state'], question=q, max_len=length))
    frozen = dict(source_sha256=sha(args.source/'frozen.json'), script_sha256=sha(__file__),
                  helper_sha256=sha(fit_epoch.__code__.co_filename), model_lock=lock,
                  selections=[{k: r[k] for k in ['id', 'group', 'target', 'variant']} for r in rows],
                  training_variants=list(range(4)) if args.balance_formats else [0], steps=args.steps, lr=3e-5, seed=20260924,
                  limitation='Same-node fitting sanity check, not generalization or routing acceptance.')
    (args.output/'frozen.json').write_text(json.dumps(frozen, indent=2), encoding='utf8')
    (args.output/'training_probe.py').write_bytes(Path(__file__).read_bytes())
    agent.model.requires_grad_(False)
    parts = ('head', 'type_emb', 'scorer')
    for name in parts: getattr(agent.model, name).requires_grad_(True)
    params = [p for p in agent.model.parameters() if p.requires_grad]
    original_encoder = agent.model.encoder.forward
    cache = {}; encoder_calls = 0

    def cached_encoder(input_ids, attention_mask):
        nonlocal encoder_calls
        assert not agent.model.encoder.training
        key = (tuple(input_ids.shape), input_ids.cpu().numpy().tobytes(),
               attention_mask.cpu().numpy().tobytes(), torch.is_autocast_enabled('cuda'))
        if key not in cache:
            with torch.no_grad():
                cache[key] = original_encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state.detach()
            encoder_calls += 1
        return SimpleNamespace(last_hidden_state=cache[key])

    @torch.no_grad()
    def evaluate(step, sdk=False):
        agent.model.eval(); results = []
        for row in rows:
            batch = collate_items([[row['item']]], agent.tok.pad_token_id)
            logits, _ = agent._infer(batch)
            prediction = int(logits.argmax(-1)); label = row['item']['label']
            loss = torch.nn.functional.cross_entropy(logits, torch.tensor([label], device=agent.device)).item()
            if sdk:
                out = agent.system_one(row['state'], {row['group']: row['question']}, max_len=row['max_len'])
                assert out['answers'][row['group']]['choice'] == list(row['question']['criteria'])[prediction]
            results.append(dict(id=row['id'], group=row['group'], variant=row['variant'], label=label,
                                prediction=prediction, loss=loss, logits=logits.cpu().tolist()[0]))
        summary = {str(v): dict(correct=sum(r['prediction']==r['label'] for r in results if r['variant']==v),
                               loss=sum(r['loss'] for r in results if r['variant']==v)/len(selections)) for v in range(4)}
        with (args.output/'evaluations.jsonl').open('a', encoding='utf8') as log:
            log.write(json.dumps(dict(step=step, sdk_checked=sdk, summary=summary, rows=results))+'\n')
        print(json.dumps(dict(step=step, evaluation=summary)), flush=True)
        return results

    optimizer = torch.optim.AdamW(params, lr=3e-5, weight_decay=.01)
    items = [r['item'] for r in rows if args.balance_formats or r['variant'] == 0]
    step = tokens = 0
    with patch.object(agent.model.encoder, 'forward', side_effect=cached_encoder):
        evaluate(0, sdk=True)
        while step < args.steps:
            step, loss, used = fit_epoch(agent, items, optimizer, params, step)
            tokens += used
            if step % 16 == 0 or step == args.steps:
                print(json.dumps(dict(step=step, training_loss=loss)), flush=True)
                cached_result = evaluate(step)
    # Same official forward, fresh encoder, then save/load: catch stale-cache/path failures.
    fresh_result = evaluate(step, sdk=True)
    for a, b in zip(cached_result, fresh_result):
        assert a['logits'] == b['logits'], 'Cached and live encoder predictions differ.'
    torch.save({k:v.detach().cpu() for k,v in agent.model.state_dict().items() if k.split('.')[0] in parts}, args.output/'head.pt')
    agent.model.load_state_dict(torch.load(args.output/'head.pt', weights_only=True), strict=False)
    assert evaluate(step) == fresh_result
    (args.output/'summary.json').write_text(json.dumps(dict(steps=step, encoder_calls_cached=encoder_calls,
        training_token_presentations=tokens, paid_calls=0, odoo_calls=0,
        fixed_format_correct=sum(r['prediction']==r['label'] for r in fresh_result if r['variant']==0),
        all_format_correct=sum(r['prediction']==r['label'] for r in fresh_result),
        cached_live_reload_parity=True), indent=2), encoding='utf8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--steps', type=int, default=128)
    p.add_argument('--balance-formats', action='store_true', help='Every node appears in all four label/order variants per update.')
    args = p.parse_args()
    if args.steps < 1: p.error('--steps must be positive')
    main(args)
