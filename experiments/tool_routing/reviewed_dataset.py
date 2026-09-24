"""Validate and freeze prefix-reviewed routing labels; historical calls stay references."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .build_cases import catalog, read
from .decision_dataset import FIXTURES
from .laya_probe import sha


def require_reference_opt_in(allowed):
    if not allowed:
        raise ValueError('Historical next-call labels are not reviewed routing targets. '
                         'Use --allow-reference-labels only to reproduce the legacy weak-label experiment.')


def validate(train, evaluation, groups):
    train_families = {r['business_group'] for r in train}
    evaluation_families = {r['business_group'] for r in evaluation}
    if train_families & evaluation_families:
        raise ValueError('A business family crosses training/evaluation.')
    if {r['request_sha256'] for r in train} & {r['request_sha256'] for r in evaluation}:
        raise ValueError('A request alias crosses training/evaluation.')
    for purpose, cases in [('train', train), ('evaluation', evaluation)]:
        if len({r['id'] for r in cases}) != len(cases):
            raise ValueError('Duplicate reviewed case ID.')
        for row in cases:
            if purpose == 'train' and row.get('review_status') != 'prefix_reviewed':
                raise ValueError('Training review is not complete.')
            if row['split'] != ('train' if purpose == 'train' else 'test'):
                raise ValueError('Review split differs from its reserved purpose.')
            if not row.get('source_pointers') or not row.get('rationale'):
                raise ValueError('Review requires prefix evidence and a rationale.')
            if len(row['request_sha256']) != 64:
                raise ValueError('Review requires a frozen request hash.')
            alternatives = row['allowed_injection_sets']
            required, unrelated = set(row['required_groups']), set(row['unrelated_groups'])
            uncertain = set(row['uncertain_groups'])
            if not alternatives:
                raise ValueError('Use an explicit empty alternative for base-only, or keep the case unreviewed.')
            for allowed in alternatives:
                allowed = set(allowed)
                if not required <= allowed or allowed & unrelated or (allowed | required | unrelated | uncertain)-set(groups):
                    raise ValueError('Contradictory or unknown capability label.')
            if required & uncertain or unrelated & uncertain:
                raise ValueError('An uncertain capability cannot be required or unrelated.')


def inclusion_labels(row, groups):
    # A permitted preload is positive even if the historical next call was a base read.
    permitted = {g for alt in row['allowed_injection_sets'] for g in alt}
    return {g: None if g in row['uncertain_groups'] else
            1 if g in permitted else 0 if g in row['unrelated_groups'] else None for g in groups}


def load_reviewed():
    train = read(FIXTURES/'reviewed_training.json')['cases']
    evaluation = read(FIXTURES/'reviewed_holdout.json')['cases']
    groups = catalog()[0]
    validate(train, evaluation, groups)
    return train, evaluation, groups


def verify_source(row):
    if sha(row['request_path']) != row['request_sha256']:
        raise ValueError(f'Historical request changed: {row["id"]}')
    request = read(row['request_path'])
    for source in row['source_pointers']:
        for pointer in source['json_pointers']:
            if not pointer.startswith('/messages/'):
                raise ValueError('Evidence must refer to the frozen request prefix.')
            current = request
            for token in pointer.split('/')[1:]:
                current = current[int(token)] if isinstance(current, list) else current[token]
    return request


def main(output):
    train, evaluation, groups = load_reviewed()
    history = [json.loads(line) for line in (FIXTURES/'history_cases.jsonl').read_text(encoding='utf8').splitlines()]
    for row in [*train, *evaluation]:
        verify_source(row)
    output.mkdir(parents=True, exist_ok=False)
    for purpose, cases in [('train', train), ('evaluation', evaluation)]:
        exported = [{'id': r['id'], 'business_group': r['business_group'],
            'input_ref': {'path': r['request_path'], 'sha256': r['request_sha256']},
            'labels': {'supported_inclusion': inclusion_labels(r, groups),
                       **{k:r[k] for k in ['required_groups', 'allowed_injection_sets', 'unrelated_groups', 'uncertain_groups']}}}
            for r in cases]
        (output/(purpose+'.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in exported), encoding='utf8')
    summary = {'label_scope': 'next_turn_optional_publication',
        'training_target': 'Prefix-supported inclusion; null is unjudged, not negative. Not historical next-call imitation.',
        'review_authority': 'Agent prefix review, not human business acceptance.',
        'train_cases': len(train), 'evaluation_cases': len(evaluation),
        'history_quality': {'reference_rows':len(history),
            'missing_prefix':sum(not r.get('request_path') for r in history),
            'unresolved_business_family':sum(bool(r.get('grouping_needs_review')) for r in history),
            'source_or_outcome_needs_review':sum(bool(r.get('needs_review')) for r in history),
            'configure_response_references':sum(bool(r.get('configure_groups')) for r in history)},
        'source_hashes': {name: sha(FIXTURES/name) for name in ['history_cases.jsonl', 'reviewed_training.json', 'reviewed_holdout.json']},
        'export_hashes': {name: sha(output/name) for name in ['train.jsonl', 'evaluation.jsonl']},
        'train_positive_coverage': dict(Counter(g for r in train for g,v in inclusion_labels(r, groups).items() if v == 1)),
        'limits': ['Small reviewed seed; weak history is not silently promoted.', 'Evaluation cases are already inspected, not unseen.']}
    (output/'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
