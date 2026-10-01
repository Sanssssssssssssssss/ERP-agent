"""Local capability publication. No provider requests, ERP calls, or write approval."""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from .build_cases import ROOT, catalog, read
from .laya_probe import WORK, sha
from .routing_state import routing_state
from .decision_dataset import bounded


from erp_harness.providers.laya_worker import CapabilityRouter as ProductRouter


class CapabilityRouter(ProductRouter):
    """Historical model projections are confined to experiment replay."""
    project = staticmethod(routing_state)

    def _configure_projection(self, manifest):
        self.projection = manifest.get('projection', 'legacy')
        if self.projection == 'evidence_v2':
            from .evidence_state import evidence_state
            self.project = evidence_state
        elif self.projection == 'host_facts_v1':
            self.project = ProductRouter.project
        elif self.projection != 'legacy':
            raise ValueError('Unknown historical projection')

    def _verify_sources(self, manifest):
        if 'runtime_files' in manifest:
            return super()._verify_sources(manifest)
        for name, digest in manifest['projection_sources'].items():
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT.resolve()) or sha(path) != digest:
                raise ValueError('Historical source changed; use its frozen checkout')

    def _validate_request(self, request):
        if self.projection == 'host_facts_v1':
            return super()._validate_request(request)
        if (not isinstance(request, dict) or not isinstance(request.get('messages'), list)
                or not isinstance(request.get('tools'), list)):
            raise ValueError('Provide a complete model request with messages and tools')
        if (not all(isinstance(m, dict) and m.get('role') in {'system','developer','user','assistant','tool'}
                    for m in request['messages']) or not any(m.get('role') == 'user' for m in request['messages'])):
            raise ValueError('Request requires provider message roles and user context')



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
    for name,digest in frozen['sources'].items():
        assert sha(name)==digest, 'Training source changed; revalidate export: '+name
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
    if frozen.get('projection') == 'host_facts_v1':
        sources=[ROOT/'experiments/tool_routing/host_state_v1.py',ROOT/'src/erp_harness/context/world.py',Path(catalog.__code__.co_filename),*catalog()[3]]
    manifest={'files':{p.relative_to(destination).as_posix():sha(p) for p in destination.rglob('*') if p.is_file()},
              'projection':frozen.get('projection','legacy'),
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
