"""Check immutable historical references against the three recorded v7 business runs."""
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from erp_harness.context.projection import project_read_history
from erp_harness.context.world import WorldStore, READ_TOOLS
from erp_harness.runtime.messages import AssistantMessage, ToolResultMessage

ROOT = Path(__file__).resolve().parents[2]


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for case in ('E01', 'E02', 'E03'):
        folder = ROOT / '.runtime/laya-host-live-20260926' / case
        summary = json.loads((folder / 'summary.json').read_text(encoding='utf8'))
        source = folder / 'profile/data/runs' / summary['run_id'] / 'world-observations.jsonl'
        target = output / case / source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        records = [json.loads(line) for line in source.read_text(encoding='utf8').splitlines()]
        identities = {r['identity']['instance']: r['identity'] for r in records if r.get('type') == 'world_observation'}
        def load():
            # Use recorded identities only; this offline test has no ERP credentials.
            with patch('erp_harness.context.world._safe_identities', return_value=(next(iter(identities)), identities)):
                return WorldStore(target)
        world = load()
        checked = 0
        for line in source.read_text(encoding='utf8').splitlines():
            row = json.loads(line)
            if row.get('type') != 'world_observation' or row.get('tool') not in READ_TOOLS:
                continue
            # Use the exact recorded provider text, not reconstructed raw ERP data.
            call = row['call_id']
            requests = source.parent / 'requests'
            text = None
            for path in sorted(requests.glob('*.request.json')):
                messages = json.loads(path.read_text(encoding='utf8'))['messages']
                text = next((m['content'] for m in messages if m.get('tool_call_id') == call
                             and isinstance(m.get('content'), str)
                             and hashlib.sha256(m['content'].encode()).hexdigest() == row['result_sha256']), None)
                if text is not None:
                    break
            if text is None:
                continue
            messages = [ToolResultMessage(tool_call_id=call, tool_name=row['tool'], content=text),
                        *[AssistantMessage(content='consumed') for _ in range(3)]]
            initial = project_read_history(world, messages)[0].text
            resumed = load()
            assert project_read_history(resumed, messages)[0].text == initial
            resumed.invalidate(instance=row['identity'].get('instance', 'default'), reason='offline_test', call_id='test')
            assert project_read_history(resumed, messages)[0].text == initial
            assert messages[0].text == text
            checked += 1
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        results.append({'case': case, 'observations_checked': checked, 'source': str(source), 'sha256': digest})
    assert all(row['observations_checked'] for row in results)
    (output / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    run(ROOT / '.runtime/laya-prefix-fix-20260929/prefix-replay')
