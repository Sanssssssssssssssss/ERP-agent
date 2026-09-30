"""Prepare a candidate deployment bundle; GPU replay is still required for acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from erp_harness.providers.laya_worker import PACKAGE_ROOT, RUNTIME_FILES


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(config_path, output):
    config = json.loads(config_path.read_text(encoding='utf8'))
    source = Path(config['model']).resolve()
    manifest = json.loads((source / 'router.json').read_text(encoding='utf8'))
    if config.get('backend') != 'laya' or manifest.get('projection') != 'host_facts_v1':
        raise ValueError('Only the accepted host-state model can be deployed')
    for name, expected in manifest['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or digest(path) != expected:
            raise ValueError('Invalid or changed model file: ' + name)
    if not {'model.safetensors', 'questions.json', 'rl_agent_config.json'} <= manifest['files'].keys():
        raise ValueError('Incomplete model manifest')
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / 'bundle'
    bundle.mkdir()
    for name in manifest['files']:
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source / name, target)
        except OSError:
            shutil.copy2(source / name, target)
    candidate = {
        'projection': 'host_facts_v1', 'files': manifest['files'],
        'runtime_files': {name: digest(PACKAGE_ROOT / name) for name in RUNTIME_FILES},
        'source_manifest_sha256': digest(source / 'router.json'),
        'acceptance': 'pending_gpu_replay',
    }
    (bundle / 'router.json').write_text(json.dumps(candidate, indent=2), encoding='utf8')
    config['model'] = str(bundle.resolve())
    (output / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf8')
    print('Candidate prepared; unchanged weights and questions; run GPU replay before enabling.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.config, args.output)
