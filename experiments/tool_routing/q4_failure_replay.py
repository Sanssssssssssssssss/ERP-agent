"""Replay four observed routing failures with the frozen thinking/surface contract."""
import copy
import sys
from pathlib import Path
from . import q4_inputs as base, q4_thinking as trial, q4_thinking_surface as surface

OUT = base.ROOT / '.runtime/q4-failure-replay-20260928'
KEYS = ('E06:r_9c7393c6b2404c14812361507823e516:0033', 'host-live:E02:0015',
        'phase:8cb4a6fdd97665eb0ce9', 'host-live:E03:0032')


def prepare():
    OUT.mkdir(exist_ok=True)
    inputs = base.OUT / 'holdout-inputs.json'
    labels = base.OUT / 'labels.json'
    rows, evidence = [], []
    for original in base.read(inputs):
        if original['case_id'] not in KEYS or original['variant'] != 'plain':
            continue
        row = copy.deepcopy(original)
        row['id'] = row['id'].replace('|plain|', '|surface|')
        row['variant'] = 'surface'
        group = row['group']
        spec = row['question'].split(group + ':', 1)[1].strip()
        row['question'] = f'Should the candidate capability {group} be exposed for a concrete useful next call within the current scope? Candidate definition: {spec}. Base tools remain available either way.'
        for option in row['options']:
            option['description'] = (f'Yes: expose {group} in addition to the base tools.' if option['id'] == 'A'
                                     else f'No: keep the base tools available; do not expose {group}.')
        assert row['state'] == original['state']
        assert [o['id'] for o in row['options']] == [o['id'] for o in original['options']]
        rows.append(row)
    assert len(rows) == 8 and len({r['id'] for r in rows}) == 8
    for label in base.read(labels)['holdout']:
        if label['id'] in KEYS:
            assert base.sha(label['request_path']) == label['request_sha256']
            evidence.append(label)
    assert len(evidence) == 4
    base.save(OUT / 'probe-inputs.json', rows)
    base.save(OUT / 'labels.json', evidence)
    files = [Path(__file__), Path(trial.__file__), Path(surface.__file__), Path(base.__file__),
             inputs, labels, base.OUT / 'holdout-results.jsonl', OUT / 'probe-inputs.json', OUT / 'labels.json']
    base.save(OUT / 'frozen.json', {'sources': {str(p.resolve()): base.sha(p) for p in files},
        'scope': 'Known historical errors, not a blind test; no changed state or labels. Eight local GPU decisions.',
        'contrast': 'Old plain direct scores versus existing surface rules plus thinking; multiple factors differ.',
        'policy': 'No retries, paid API, tools, or production modifications.'})


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare()
    else:
        trial.OUT = OUT
        trial.RULES += '\n' + surface.BOUNDARY
        trial.run('probe')
