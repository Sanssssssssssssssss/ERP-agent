"""Versioned evidence repair; old bundles keep their original projection."""
import json

from .build_cases import TOKEN
from .routing_state import routing_state


def evidence_state(request):
    value = json.loads(routing_state(request))
    messages = request['messages']
    for observation in value['recent_observations']:
        try:
            raw = json.loads(messages[observation['source_message']]['content'])
        except (ValueError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        result = raw.get('result')
        if (observation['tool'].endswith('get_model_fields') and isinstance(result, dict)
                and result.get('__world_schema_table__') is True
                and isinstance(result.get('fields'), list)
                and all(isinstance(f, str) for f in result['fields'])):
            # Codec keys are not ERP fields. Preserve the trained field_names shape.
            observation['observation']['result'] = {'field_names': result['fields']}
    return TOKEN.sub('[APPROVAL_TOKEN_REDACTED]', json.dumps(value, ensure_ascii=False, separators=(',', ':')))
