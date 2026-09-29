"""GPU-only Laya selection; no ERP access or execution authority."""
from contextlib import redirect_stdout
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
def read(path): return json.loads(Path(path).read_text(encoding="utf8"))
def sha(path): return sha256(Path(path).read_bytes()).hexdigest()

class CapabilityRouter:
    """Load once and reuse per turn in the isolated Laya environment."""


    @staticmethod
    def project(request):
        from experiments.tool_routing.routing_state import routing_state
        return routing_state(request)

    def __init__(self, directory, device='cuda'):
        import laya
        import torch
        directory = Path(directory)
        manifest = read(directory/'router.json')
        self.projection = manifest.get('projection', 'legacy')
        if manifest.get('projection', 'legacy') == 'evidence_v2':
            from experiments.tool_routing.evidence_state import evidence_state
            self.project = evidence_state
        elif manifest.get('projection') == 'host_facts_v1':
            self.project = lambda state: json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        elif manifest.get('projection', 'legacy') == 'legacy':
            from experiments.tool_routing.routing_state import routing_state
            self.project = routing_state
        else:
            raise ValueError('Unknown routing projection')
        for name,digest in manifest['files'].items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Invalid bundle path')
            if sha(directory/name)!=digest:
                raise ValueError('Model bundle changed: '+name)
        for name,digest in manifest['projection_sources'].items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Invalid source path')
            if sha(ROOT/name)!=digest:
                raise ValueError('Routing projection changed; revalidate the bundle: '+name)
        torch.set_num_threads(4)
        self.agent = laya.load(str(directory.resolve()),device=device)
        self.device_type = torch.device(device).type
        if self.agent.device.type != self.device_type:
            raise RuntimeError('Requested inference device unavailable')
        def require_gpu(module, args):
            if self.agent.device.type != 'cuda' or next(module.parameters()).device.type != 'cuda':
                raise ValueError('CPU inference is forbidden')
        self.agent.model.register_forward_pre_hook(require_gpu)
        self.questions = read(directory/'questions.json')
        self.model_sha256 = manifest['files']['model.safetensors']
        self.release_scratch = torch.cuda.empty_cache

    def _predict(self, state, questions, positive):
        # Questions are independent rows. Small batches keep long inputs in VRAM.
        names = list(questions)
        batch = getattr(self, 'question_batch_size', len(names))
        result = {'answers': {}, 'usage': {'input_tokens': 0, 'output_tokens': 0}}
        for start in range(0, len(names), batch):
            part = self.agent.system_one(state, {g: questions[g] for g in names[start:start+batch]})
            result['answers'].update(part['answers'])
            for key in result['usage']:
                result['usage'][key] += part['usage'][key]
        if self.agent.device.type!=self.device_type or set(result['answers'])!=set(questions):
            raise RuntimeError('Unexpected inference device or answer set')
        if any(a['choice'] not in {'A','B'} or not math.isfinite(a['probabilities'][positive])
               for a in result['answers'].values()):
            raise RuntimeError('Invalid model decision')
        return result, [g for g,a in result['answers'].items() if a['choice']==positive]

    def route(self, request, *, verify_labels=False, groups=None):
        if self.projection == 'host_facts_v1':
            if (not isinstance(request,dict) or request.get('version') != self.projection
                    or not isinstance(request.get('task'),dict) or not isinstance(request.get('action_ledger'),dict)):
                raise ValueError('Provide a versioned host fact snapshot')
        else:
            if not isinstance(request,dict) or not isinstance(request.get('messages'),list) or not isinstance(request.get('tools'),list):
                raise ValueError('Provide a complete model request with messages and tools')
            if not all(isinstance(m,dict) and m.get('role') in {'system','developer','user','assistant','tool'}
                       for m in request['messages']) or not any(m.get('role')=='user' for m in request['messages']):
                raise ValueError('Request requires provider message roles and user context')
        if self.agent.device.type != self.device_type:
            raise RuntimeError('Inference device changed; use the existing router')
        started = time.perf_counter()
        state = self.project(request)
        size = len(self.agent.tok(state.replace(self.agent.tok.mask_token,' '),add_special_tokens=False)['input_ids'])
        # Conservative room for the whole decision head; never silently truncate state.
        if size+self.agent.cfg['head_max_len']+4 > self.agent.cfg['max_len']:
            raise ValueError('Routing input exceeds model context; use the existing model route')
        names = list(self.questions) if groups is None else groups
        if (not isinstance(names, list) or len(names) != len(set(names))
                or any(g not in self.questions for g in names)):
            raise ValueError('Invalid question subset')
        questions = {g: self.questions[g] for g in names}
        self.question_batch_size = 2
        result, selected = self._predict(state,questions,'A')
        primary_elapsed_ms = (time.perf_counter()-started)*1000
        usage = result['usage']; review_selected = selected
        if verify_labels:
            # Change label tokens only. Never choose the answer that happens to score better.
            questions = {g:{**q,'criteria':{{'A':'B','B':'A'}[k]:v for k,v in q['criteria'].items()}}
                         for g,q in questions.items()}
            review, review_selected = self._predict(state,questions,'B')
            usage = {k:usage[k]+review['usage'][k]
                     if usage.get(k) is not None and review['usage'].get(k) is not None else None
                     for k in usage.keys() | review['usage'].keys()}
        receipt = {'status':'ok','capabilities':selected,
                'projection':getattr(self, 'projection', 'unknown'),
                'state_sha256':sha256(state.encode('utf8')).hexdigest(),
                'probabilities':{g:a['probabilities']['A'] for g,a in result['answers'].items()},
                'probabilities_calibrated':False,'state_tokens':size,'usage':usage,
                'inference_calls':math.ceil(len(questions)/self.question_batch_size)*(2 if verify_labels else 1),
                'question_batch_size':self.question_batch_size,
                'primary_elapsed_ms':round(primary_elapsed_ms,2),
                'elapsed_ms':round((time.perf_counter()-started)*1000,2),
                'model_sha256':self.model_sha256,'scope':'capability_publication_only'}
        if set(selected)!=set(review_selected):
            receipt.update(status='fallback',reason='unstable_capability_selection',use_existing_router=True,
                           candidate_capabilities=receipt.pop('capabilities'),review_capabilities=review_selected)
        return receipt


def select_packet(router, packet):
    groups = packet['groups']
    if (packet.get('context_version') != 'host_facts_v1' or not isinstance(groups, list)
            or len(groups) != len(set(groups)) or any(g not in router.questions for g in groups)):
        raise ValueError('Invalid Laya capability contract')
    try:
        result = router.route(packet['state'], groups=groups)
    finally:
        # Classification has no cross-turn KV cache. Keep weights resident; release
        # unused variable-length attention buffers before the next client request.
        release = getattr(router, 'release_scratch', None)
        if release is not None:
            release()
    selected = set(result['capabilities'])
    result.update(id=packet['id'], capabilities=[g for g in groups if g in selected],
        result={'decisions': {g: {'answer': 'A' if g in selected else 'B',
            'scores': [result['probabilities'][g], 1-result['probabilities'][g]]} for g in groups}})
    return result


def main():
    config = read(sys.argv[1])
    with redirect_stdout(sys.stderr):
        router = CapabilityRouter(config['model'], device='cuda')
    for line in sys.stdin:
        packet = json.loads(line)
        try:
            with redirect_stdout(sys.stderr):
                result = select_packet(router, packet)
        except (ValueError, KeyError, TypeError, RuntimeError) as error:
            result = {'id': packet.get('id'), 'scope': 'capability_publication_only',
                      'status': 'fallback', 'reason': 'invalid_input_or_inference_failure',
                      'error_type': type(error).__name__}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
