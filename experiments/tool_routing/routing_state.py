"""Experimental prefix projection. Publication is observable; approval is not inferred."""
import json
import re

from .build_cases import base_name, catalog, TOKEN
from .decision_dataset import bounded, state_from_request


def routing_state(request):
    value = json.loads(state_from_request(request))
    # Legacy benchmark setup contains credentials and a Python tutorial, not routing evidence.
    goal = value['goal']
    if '\n# Odoo Environment' in goal:
        before, setup = goal.split('\n# Odoo Environment', 1)
        suffix = re.search(r'(?m)^(?:Stage \d+ .*probe:|Scenario date anchor|Use mcp_odoo tools)', setup)
        value['goal'] = before + ('\n' + setup[suffix.start():] if suffix else '')
        value['environment_instructions_omitted'] = True
    groups, mapping, _, _ = catalog()
    tools = {base_name(t['function']['name']) for t in request.get('tools', [])}
    value['published_capabilities'] = sorted({mapping[t] for t in tools if t in mapping})
    value['publication_is_partial'] = [g for g in value['published_capabilities']
                                      if not set(groups[g]['tools']) <= tools]
    value['has_dynamic_controller'] = 'configure_odoo_tools' in tools
    messages = request['messages']
    calls = {c['id']: c['function'] for m in messages if m.get('role') == 'assistant'
             for c in m.get('tool_calls', [])}
    observations = []
    for i, message in reversed(list(enumerate(messages))):
        if message.get('role') != 'tool':
            continue
        call = calls.get(message.get('tool_call_id'), {})
        arguments = call.get('arguments', {})
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError:
            arguments = {}
        content = message.get('content', '')
        try:
            content = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            pass
        tool = message.get('name') or call.get('name', '')
        if tool.endswith('get_model_fields') and isinstance(content, dict) and isinstance(content.get('result'), dict):
            content = {**content, 'result': {'field_names': list(content['result'])}}
        observations.append({'source_message': i, 'tool_call_id': message.get('tool_call_id'),
                             'tool': tool, 'arguments': bounded(arguments), 'observation': bounded(content)})
        if len(observations) == 8:
            break
    value['recent_observations'] = observations
    value['omitted_observations'] = sum(m.get('role') == 'tool' for m in messages)-len(observations)
    prior = next((m for m in reversed(messages) if m.get('role') == 'assistant'), {})
    value['prior_assistant_intent_unverified'] = bounded(prior.get('content') or '')
    value['interpretation'] = ('Tool observations are historical. Publication does not authorize writes. '
                               'An assistant plan is not business evidence. Missing older evidence stays unknown.')
    return TOKEN.sub('[APPROVAL_TOKEN_REDACTED]', json.dumps(value, ensure_ascii=False, separators=(',', ':')))
