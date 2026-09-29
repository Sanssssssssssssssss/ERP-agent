"""Classify misses, excess exposure and option-order sensitivity separately."""
import json
import sys
from pathlib import Path

from . import q4_inputs as base

THRESHOLDS = (0.1, 0.3, 0.5, 0.7, 0.9, 0.95, 0.999)


def counts(targets, predictions):
    assert len(targets) == len(predictions)
    assert all(type(t) is bool for t in targets)
    assert all(p is None or type(p) is bool for p in predictions)
    n = {k: 0 for k in ('tp', 'tn', 'fp', 'fn', 'unknown_positive', 'unknown_negative')}
    for target, predicted in zip(targets, predictions):
        key = ('unknown_positive' if target else 'unknown_negative') if predicted is None else (
            'tp' if target and predicted else 'fn' if target else 'fp' if predicted else 'tn')
        n[key] += 1
    positives = sum(targets)
    return {**n, 'positive_label_recall': n['tp'] / positives if positives else None,
            'exposure_precision': n['tp'] / (n['tp'] + n['fp']) if n['tp'] + n['fp'] else None}


def report(folder):
    folder = Path(folder)
    rows = base.read(folder / 'probe-inputs.json')
    labels = {(r['id'], r['capability']): r['target'] for r in base.read(folder / 'labels.json')}
    targets = [labels[(r['case_id'], r['group'])] for r in rows]
    result = {'scope': 'Known historical nodes; two option orders are paired, not independent samples.',
              'label_semantics': 'Positive labels mean supported useful capability supply; a legal base-tool alternative may exist. A miss alone does not prove business failure.',
              'decisions': len(rows), 'unique_nodes': len({r['case_id'] for r in rows}),
              'always_expose': counts(targets, [True] * len(rows))}
    for name, file in [('generation', 'probe-results.jsonl'), ('readout', 'readout/results.jsonl')]:
        path = folder / file
        if not path.exists():
            continue
        responses = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
        assert len(responses) == len(rows) and all(r['id'] == s['id'] for r, s in zip(rows, responses))
        predictions = [r['prediction'] for r in responses]
        pairs = {}
        for row, pred in zip(rows, predictions):
            pairs.setdefault((row['case_id'], row['group']), []).append(pred)
        result[name] = {**counts(targets, predictions),
            'order_disagreements': sum(len(set(p)) > 1 for p in pairs.values() if None not in p),
            'input_tokens': sum(r.get('input_tokens', 0) for r in responses),
            'output_tokens': sum(r.get('output_tokens', 0) for r in responses),
            'seconds': sum(r.get('elapsed_seconds', r.get('seconds', 0)) for r in responses),
            'errors': [{'id': row['id'], 'target': target, 'prediction': pred}
                       for row, target, pred in zip(rows, targets, predictions) if pred != target]}
        if name == 'readout':
            # Semantic A means EXPOSE; the displayed letter depends on option order.
            scores = [r['conditional_option_scores'][r['option_ids'].index('A')]
                      if 'conditional_option_scores' in r else None for r in responses]
            result['score_warning'] = 'Conditional A/B slot scores are not calibrated correctness probabilities.'
            result['thresholds'] = {str(t): counts(targets, [None if s is None else s >= t for s in scores])
                                    for t in THRESHOLDS}
    return result


if __name__ == '__main__':
    if sys.argv[1] == 'check':
        actual = counts([True, False, True, False, True], [True, True, False, None, None])
        assert (actual['tp'], actual['fp'], actual['fn'], actual['unknown_negative'], actual['unknown_positive']) == (1, 1, 1, 1, 1)
        assert actual['positive_label_recall'] == 1 / 3 and actual['exposure_precision'] == 0.5
        print('Unknown outcomes, missed required tools and excess exposure remain distinct.')
    else:
        print(json.dumps(report(sys.argv[1]), ensure_ascii=False, indent=2))
