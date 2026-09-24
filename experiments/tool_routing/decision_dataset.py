"""Prefix-only routing state and task-grouped historical decision dataset."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .build_cases import TOKEN, catalog, read
from .laya_probe import ROOT, WORK, sha

FIXTURES = ROOT / 'tests/fixtures/capability_routing'
OUTPUT = ROOT / '.runtime/capability-routing-20260924'


def bounded(value, depth=0):
    if depth > 3 and isinstance(value, list):
        return '[nested data omitted]'
    if isinstance(value, dict):
        omit = {'published_tools', 'added', 'removed', 'tool_contract_sha256', 'fields_used',
                'host_task_evidence', 'approval_token', 'smart_fields_applied', 'raw', 'schema'}
        if depth > 3:
            value = {**{k:v for k,v in value.items() if not isinstance(v, (dict, list))},
                     '_nested_data_omitted': any(isinstance(v, (dict, list)) for v in value.values())}
        return {k: bounded(v, depth + 1) for k, v in list(value.items())[:24] if k not in omit}
    if isinstance(value, list):
        return [bounded(v, depth + 1) for v in value[:3]] + ([{'omitted_items': len(value)-3}] if len(value) > 3 else [])
    if isinstance(value, str):
        text = TOKEN.sub('[APPROVAL_TOKEN_REDACTED]', value)
        return text if len(text) <= 650 else text[:400] + ' [text omitted] ' + text[-250:]
    return value


def state_from_request(request):
    """No oracle, response, file name, task ID, or future tool call is accepted."""
    messages = request['messages']
    users = [m for m in messages if m.get('role') == 'user']
    first = users[0].get('content', '') if users else ''
    if not isinstance(first, str):
        first = json.dumps(first, ensure_ascii=False)
    marker = next((s for s in ['Confirmed current phase:\n', 'New user instructions:\n'] if s in first), None)
    goal = first.split(marker, 1)[1].split('\nCompletion target:', 1)[0] if marker else first
    value = {'goal': TOKEN.sub('[APPROVAL_TOKEN_REDACTED]', goal),
             'completion_target': next((s for s in first.splitlines() if s.startswith('Completion target:')), '')}
    if len(users) > 1:
        update = users[-1].get('content', '')
        value['latest_user_or_host_update'] = TOKEN.sub('[APPROVAL_TOKEN_REDACTED]',
            update if isinstance(update, str) else json.dumps(update, ensure_ascii=False))
    # A response may contain multiple parallel calls. Keep the final observation batch together.
    recent = []
    calls = {c['id']: c['function'] for m in messages if m.get('role') == 'assistant'
             for c in m.get('tool_calls', [])}
    for message in reversed(messages):
        if message.get('role') == 'assistant':
            break
        if message.get('role') == 'tool':
            content = message.get('content', '')
            try:
                content = json.loads(content) if isinstance(content, str) else content
            except ValueError:
                pass
            call = calls.get(message.get('tool_call_id'), {})
            tool = message.get('name') or call.get('name', '')
            try:
                arguments = json.loads(call.get('arguments', '{}'))
            except (ValueError, TypeError):
                arguments = {}
            # Field definitions describe an interface, not the current business state.
            if tool.endswith('get_model_fields') and isinstance(content, dict) and isinstance(content.get('result'), dict):
                content = {**content, 'result': {'field_names': list(content['result'])}}
            recent.append({'tool': tool, 'target': {k: arguments[k] for k in
                ['model', 'method', 'operation', 'record_id', 'record_ids'] if k in arguments},
                'observation': bounded(content)})
    # Latest readback must precede large earlier metadata; omission remains explicit.
    value['recent_observations'] = recent[:3]
    value['omitted_observations'] = max(0, len(recent)-3)
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def split_family(family):
    # Preserve the previously reserved task families; all reruns of a task stay together.
    if family in {'enterprise:E01', 'enterprise:E04', 'enterprise:E06', 'erpbench:2278'}:
        return 'test'
    if family in {'enterprise:SALE', 'enterprise:E02'}:
        return 'dev'  # Explicitly inspected during earlier template diagnostics.
    if family == 'erpbench:2205':
        return 'dev'  # Rare-group development family; 2262 stays wholly held out.
    bucket = int(hashlib.sha256(('routing-v2|' + family).encode()).hexdigest()[:8], 16) % 10
    return 'test' if bucket < 2 else 'dev' if bucket == 2 else 'train'


def build(folder):
    groups, mapping, _, _ = catalog()
    rows = []
    for c in (json.loads(s) for s in (WORK/'dataset/cases.jsonl').read_text(encoding='utf8').splitlines()):
        if not c['dynamic_router_applicable'] or c['oracle']['needs_review'] or not c['oracle']['response_known']:
            continue
        family = ('erpbench:' if c['business_group'] in {'2003', '2278'} else 'enterprise:') + c['business_group']
        rows.append({'id': c['id'], 'request_path': c['request_path'], 'request_sha256': c['request_sha256'],
                     'business_group': family, 'actual_groups': c['oracle']['observed_required_capabilities'],
                     'configure_groups': c['oracle']['requested_capabilities'], 'reference_only': True,
                     'source': 'original_pool', 'active_capabilities': c['active_capabilities'],
                     'source_run': str(Path(c['request_path']).parent.parent)})
    canonical_family = {c['request_sha256']: c['business_group'] for c in rows}
    for line in (FIXTURES/'history_cases.jsonl').read_text(encoding='utf8').splitlines():
        c = json.loads(line)
        if not c.get('training_eligible') or c.get('needs_review') or c.get('grouping_needs_review'):
            continue
        c['business_group'] = canonical_family.get(c['request_sha256'],
            {'S01499': 'enterprise:S01499', 'S00006': 'enterprise:SALE'}.get(c['business_group'],c['business_group']))
        rows.append({**c, 'source': 'expanded_inventory', 'reference_only': True})
    by_run = {}
    for c in rows:
        by_run.setdefault(str(Path(c['request_path']).parent), []).append(c)
    unique, excluded = {}, []
    for row in rows:
        path = Path(row['request_path'])
        if sha(path) != row['request_sha256']:
            raise ValueError(f'Historical request changed: {path}')
        request = read(path)
        if not request.get('messages'):
            excluded.append({'id': row['id'], 'reason': 'no_messages'}); continue
        # A broad configure request alone is not proof that every selected group was useful.
        following = [c for c in by_run[str(path.parent)] if
                     path.name[:4].isdigit() and Path(c['request_path']).name[:4].isdigit() and
                     0 < int(Path(c['request_path']).name[:4])-int(path.name[:4]) <= 3]
        later_used = {g for c in following for g in c.get('actual_groups', [])}
        config_supported = set(row.get('configure_groups', [])) & later_used
        desired = sorted(set(row.get('actual_groups', [])) | config_supported)
        if row.get('configure_groups') and not row.get('actual_groups') and not config_supported:
            excluded.append({'id': row['id'], 'reason': 'configure_without_nearby_use'}); continue
        if set(desired) - set(groups):
            excluded.append({'id': row['id'], 'reason': 'unknown_historical_group'}); continue
        state = state_from_request(request)
        # Identical model-visible states must not straddle splits, even across copied run directories.
        key = hashlib.sha256(state.encode()).hexdigest()
        family = row['business_group']
        if key in unique:
            old = unique[key]
            if old['target_groups'] != desired:
                old['ambiguous_targets'] = True
            if old['business_group'] != family:
                old['cross_family_duplicate'] = True
            continue
        unique[key] = {**row, 'state': state, 'state_sha256': key, 'target_groups': desired, 'split': split_family(family)}
    cases = []
    for c in unique.values():
        if c.get('ambiguous_targets') or c.get('cross_family_duplicate'):
            excluded.append({'id': c['id'], 'reason': 'ambiguous_or_cross_family_state'}); continue
        cases.append(c)
    summary = {'nodes': len(cases), 'split_counts': dict(Counter(c['split'] for c in cases)),
               'families': {s: sorted({c['business_group'] for c in cases if c['split']==s}) for s in ['train','dev','test']},
               'positive_groups': {s: dict(Counter(g for c in cases if c['split']==s for g in c['target_groups'])) for s in ['train','dev','test']},
               'excluded': excluded, 'source_index_sha256': sha(FIXTURES/'history_cases.jsonl'),
               'label_meaning': 'Historical next-response coverage proxy, not the sole valid trajectory or a business success score.'}
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'cases.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False)+'\n' for c in cases), encoding='utf8')
    (folder/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    (folder/'groups.json').write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: summary[k] for k in ['nodes','split_counts','positive_groups']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT/'dataset-v3')
    build(parser.parse_args().output)
