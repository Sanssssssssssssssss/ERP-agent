"""Local capability publication. No provider requests, ERP calls, or write approval."""
import argparse
from contextlib import redirect_stdout
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import time

from .build_cases import ROOT, catalog, read
from .laya_probe import WORK, sha
from .routing_state import routing_state
from .decision_dataset import bounded


class CapabilityRouter:
    """Load once and reuse per turn in the isolated Laya environment."""

    project = staticmethod(routing_state)

    def __init__(self, directory, device='cuda'):
        import laya
        import torch
        directory = Path(directory)
        manifest = read(directory/'router.json')
        self.projection = manifest.get('projection', 'legacy')
        if manifest.get('projection', 'legacy') == 'evidence_v2':
            from .evidence_state import evidence_state
            self.project = evidence_state
        elif manifest.get('projection', 'legacy') == 'legacy':
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
        self.questions = read(directory/'questions.json')
        self.model_sha256 = manifest['files']['model.safetensors']

    def _predict(self, state, questions, positive):
        result = self.agent.system_one(state,questions)
        if self.agent.device.type!=self.device_type or set(result['answers'])!=set(questions):
            raise RuntimeError('Unexpected inference device or answer set')
        if any(a['choice'] not in {'A','B'} or not math.isfinite(a['probabilities'][positive])
               for a in result['answers'].values()):
            raise RuntimeError('Invalid model decision')
        return result, [g for g,a in result['answers'].items() if a['choice']==positive]

    def route(self, request, *, verify_labels=False):
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
        result, selected = self._predict(state,self.questions,'A')
        primary_elapsed_ms = (time.perf_counter()-started)*1000
        usage = result['usage']; review_selected = selected
        if verify_labels:
            # Change label tokens only. Never choose the answer that happens to score better.
            questions = {g:{**q,'criteria':{{'A':'B','B':'A'}[k]:v for k,v in q['criteria'].items()}}
                         for g,q in self.questions.items()}
            review, review_selected = self._predict(state,questions,'B')
            usage = {k:usage[k]+review['usage'][k]
                     if usage.get(k) is not None and review['usage'].get(k) is not None else None
                     for k in usage.keys() | review['usage'].keys()}
        receipt = {'status':'ok','capabilities':selected,
                'projection':getattr(self, 'projection', 'unknown'),
                'state_sha256':sha256(state.encode('utf8')).hexdigest(),
                'probabilities':{g:a['probabilities']['A'] for g,a in result['answers'].items()},
                'probabilities_calibrated':False,'state_tokens':size,'usage':usage,
                'inference_calls':2 if verify_labels else 1,
                'primary_elapsed_ms':round(primary_elapsed_ms,2),
                'elapsed_ms':round((time.perf_counter()-started)*1000,2),
                'model_sha256':self.model_sha256,'scope':'capability_publication_only'}
        if set(selected)!=set(review_selected):
            receipt.update(status='fallback',reason='unstable_capability_selection',use_existing_router=True,
                           candidate_capabilities=receipt.pop('capabilities'),review_capabilities=review_selected)
        return receipt


async def publish_next_turn(controller, decision, call_id, *, host_owns_selection=False, unresolved_write=False):
    """Experimental host seam. Flags come from live host state, never model text or trace."""
    if host_owns_selection or unresolved_write or decision.get('status')!='ok':
        return {'applied':False,'reason':'host_override' if host_owns_selection else
                'unresolved_write' if unresolved_write else 'router_fallback'}
    configure = next(t for t in controller.tools if t.name=='configure_odoo_tools')
    result = await configure.execute(call_id,{'capabilities':decision.get('capabilities')})
    return {'applied':result.details.get('success') is True,'result':result.details}


def export(run, destination):
    import shutil
    import torch
    from safetensors.torch import load_file, save_file
    frozen=read(run/'frozen.json'); summary=read(run/'summary.json')
    assert summary['sdk_parity']['same_sets']==summary['sdk_parity']['cases']
    base=Path(frozen['initial_model']) if frozen.get('initial_model') else WORK/'model-multilingual'
    for name,digest in frozen['loaded_model_files_sha256'].items():
        assert sha(base/name)==digest, name
    destination.mkdir(parents=True,exist_ok=False)
    for name in ['encoder','tokenizer']:
        shutil.copytree(base/name,destination/name)
    weights=load_file(base/'model.safetensors')
    if summary['selected_epoch']:
        patch=torch.load(run/'selected.pt',map_location='cpu',weights_only=True)
        assert patch.keys()<=weights.keys()
        assert sum(p.numel() for p in patch.values())==summary['trainable_parameters']
        for name,tensor in patch.items():
            assert tensor.shape==weights[name].shape and torch.isfinite(tensor).all(),name
        weights.update(patch)
    save_file(weights,destination/'model.safetensors')
    config=read(base/'rl_agent_config.json'); config['max_len']=8192
    config['training']={'selected_epoch':summary['selected_epoch'],'objective':frozen['objective'],
                        'encoder_scope':frozen['encoder_scope'],'run_manifest_sha256':sha(run/'frozen.json')}
    (destination/'rl_agent_config.json').write_text(json.dumps(config,indent=2),encoding='utf8')
    (destination/'questions.json').write_text(json.dumps(frozen['questions'][0],indent=2),encoding='utf8')
    sources=[Path(routing_state.__code__.co_filename),Path(bounded.__code__.co_filename),
             Path(catalog.__code__.co_filename),*catalog()[3]]
    manifest={'files':{p.relative_to(destination).as_posix():sha(p) for p in destination.rglob('*') if p.is_file()},
              'projection_sources':{p.relative_to(ROOT).as_posix():sha(p) for p in sources},'source_run':str(run.resolve()),
              'source_manifest_sha256':sha(run/'frozen.json'),'scope':'capability_publication_only'}
    (destination/'router.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--verify-labels',action='store_true',help='Check swapped option labels; disagreement returns fallback, not a publishable set')
    operation=p.add_mutually_exclusive_group(required=True)
    operation.add_argument('--request',type=Path)
    operation.add_argument('--jsonl',action='store_true',help='Persistent stdin worker: one complete request per line')
    operation.add_argument('--export-run',type=Path)
    a=p.parse_args()
    if a.export_run:
        export(a.export_run,a.model);return
    with redirect_stdout(sys.stderr):
        router=CapabilityRouter(a.model)
    lines=sys.stdin if a.jsonl else [a.request.read_text(encoding='utf8')]
    for line in lines:
        try:
            with redirect_stdout(sys.stderr):
                result=router.route(json.loads(line),verify_labels=a.verify_labels)
            result={'status':'ok',**result}
        except (ValueError,KeyError,TypeError,AttributeError,IndexError,RuntimeError):
            # No raw request, credentials, or approval values in diagnostic output.
            result={'status':'fallback','reason':'invalid_input_or_inference_failure','use_existing_router':True}
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
