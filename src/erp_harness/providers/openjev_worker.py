"""Local Qwen thinking selector using the pinned OpenJev llama.cpp backend.

The host owns message construction. This process only generates decisions;
it receives no Odoo credentials and cannot execute business tools.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import time


def sample(logits, seen, rng, np):
    values = logits.astype('float64').copy()
    if seen:
        values[list(seen)] -= 1.5
    indexes = np.argpartition(values, -20)[-20:]
    indexes = indexes[np.argsort(values[indexes])[::-1]]
    probabilities = np.exp(values[indexes] - values[indexes[0]])
    probabilities /= probabilities.sum()
    count = int(np.searchsorted(np.cumsum(probabilities), .95)) + 1
    indexes, probabilities = indexes[:count], probabilities[:count]
    probabilities /= probabilities.sum()
    return int(rng.choice(indexes, p=probabilities))


def parse_answer(text):
    if '</think>' not in text:
        return None
    answer = text.rsplit('</think>', 1)[1].strip()
    return answer if re.fullmatch('[AB]', answer) else None


def load(config, log_path):
    for filename, digest in config['files'].items():
        with Path(filename).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Selector dependency hash mismatch')
    sys.path[:0] = config['package_paths']
    import llama_cpp
    from semif_phase1 import llamacpp_backend as backend

    if not llama_cpp.llama_supports_gpu_offload():
        raise RuntimeError('Selector requires the CUDA backend')
    defaults = backend._cpu_model_params

    def gpu_params(lib):
        params = defaults(lib)
        params.n_gpu_layers, params.main_gpu = -1, 0
        return params

    backend._cpu_model_params = gpu_params
    model, tokenizer, metadata = backend.load_model(
        config['tokenizer'], config['revision'], config['gguf'], threads=8,
        context_tokens=config.get('context_tokens', 16384))
    startup = log_path.read_text(encoding='utf8', errors='replace')
    layers = re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU', startup)
    if not layers or not int(layers[-1][0]) or layers[-1][0] != layers[-1][1] or 'CUDA0' not in startup:
        raise RuntimeError('Full GPU offload was not observed')
    metadata.update(device='cuda:0', n_gpu_layers=int(layers[-1][0]))
    return model, tokenizer, metadata, backend


def generate(model, tokenizer, backend, messages, prefix, groups=None):
    import numpy as np

    prompt = tokenizer.apply_chat_template(messages, tokenize=False,
        add_generation_prompt=True, enable_thinking=True)
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    if not prompt.endswith('<think>\n') or ids != backend._gguf_tokenize(model.engine.lib, model.vocab, prompt):
        raise ValueError('Tokenizer or thinking template mismatch')
    if len(ids) >= model.engine.context_tokens - 64:
        raise ValueError('Selector context exceeds its loaded window; no truncation')
    prefix.with_suffix('.prompt.txt').write_text(prompt, encoding='utf8')
    started = time.monotonic()
    generated, seen, rng = [], set(), np.random.default_rng(42)
    logits = model.engine.full_logits(ids)
    stop = 'context_exhausted'
    close = tokenizer.convert_tokens_to_ids('</think>')
    option_scores = None
    decisions = {}
    readout_input_tokens = 0
    rendered_tokens = []
    with prefix.with_suffix('.tokens.jsonl').open('x', encoding='utf8') as trace:
        for position in range(len(ids), model.engine.context_tokens - 1):
            token = sample(logits, seen, rng, np)
            trace.write(json.dumps({'token': token, 'elapsed': time.monotonic() - started}) + '\n')
            trace.flush()
            if model.engine.lib.llama_vocab_is_eog(model.vocab, token):
                stop = 'eog'
                break
            generated.append(token)
            seen.add(token)
            if token == close:
                # OpenJev's decision is a readout over declared choices. Sampling
                # the answer letter adds avoidable randomness after thinking.
                separator = tokenizer.encode('\n\n', add_special_tokens=False)
                prefix_ids = ids + generated + separator
                if len(prefix_ids) >= model.engine.context_tokens - 1:
                    break
                slots = [tokenizer.encode(letter, add_special_tokens=False) for letter in 'AB']
                if any(len(slot) != 1 for slot in slots) or slots[0] == slots[1]:
                    raise ValueError('Decision letters must be distinct single tokens')
                scores = model.engine.full_logits(prefix_ids)
                readout_input_tokens += len(prefix_ids)
                state = model.engine.save_state() if groups else None
                for group in groups or ['candidate']:
                    if groups:
                        suffix = tokenizer.encode(f'{group}=', add_special_tokens=False)
                        if len(prefix_ids) + len(suffix) >= model.engine.context_tokens:
                            raise ValueError('No room for decision readout')
                        model.engine.restore_state(state)
                        scores = model.engine.branch_logits(len(prefix_ids), suffix)
                        readout_input_tokens += len(suffix)
                    values = np.array([scores[slot[0]] for slot in slots], dtype=float)
                    if not np.isfinite(values).all():
                        raise ValueError('Nonfinite decision scores')
                    probabilities = np.exp(values - values.max())
                    option_scores = (probabilities / probabilities.sum()).tolist()
                    decisions[group] = {'answer': 'AB'[int(values.argmax())], 'scores': option_scores}
                del state
                final = '\n'.join(f'{g}={d["answer"]}' for g, d in decisions.items()) if groups else decisions['candidate']['answer']
                decision_tokens = separator + tokenizer.encode(final, add_special_tokens=False)
                rendered_tokens = decision_tokens
                for decision_token in decision_tokens:
                    trace.write(json.dumps({'token': decision_token, 'kind': 'choice_readout',
                        'elapsed': time.monotonic() - started}) + '\n')
                trace.flush()
                stop = 'choice_readout'
                break
            logits = model.engine._decode([token], position, 0, True)
    text = tokenizer.decode(generated + rendered_tokens, skip_special_tokens=False)
    result = {'answer': parse_answer(text) if stop == 'choice_readout' else None, 'text': text,
        'decisions': decisions,
        'input_tokens': len(ids), 'output_tokens': len(generated) + len(decisions),
        'readout_input_tokens': readout_input_tokens, 'decision_tokens': len(decisions),
        'formatted_output_tokens': len(generated) + len(rendered_tokens),
        'reasoning_tokens': generated.index(close) if close in generated else None,
        'seconds': time.monotonic() - started, 'stop': stop,
        'conditional_option_scores': option_scores if not groups else None, 'probabilities_calibrated': False,
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    prefix.with_suffix('.result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
    return result


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    directory = Path(sys.argv[2]).resolve()
    model, tokenizer, metadata, backend = load(config, directory / 'worker.stderr.log')
    (directory / 'gpu.json').write_text(json.dumps(metadata), encoding='utf8')
    for line in sys.stdin:
        request = json.loads(line)
        request_id = request.get('id', '')
        if not re.fullmatch('[a-zA-Z0-9_-]{1,100}', request_id):
            raise ValueError('Invalid selector request ID')
        folder = directory / request_id
        folder.mkdir()  # Duplicate IDs cannot overwrite a prior decision.
        groups = request['groups']
        if (not isinstance(groups, list) or not groups or len(groups) != len(set(groups))
                or any(not isinstance(g, str) or not re.fullmatch('[a-z_]+', g) for g in groups)):
            raise ValueError('Invalid candidate groups')
        result = generate(model, tokenizer, backend, request['messages'], folder / 'selection', groups)
        selected = [g for g, decision in result['decisions'].items() if decision['answer'] == 'A']
        valid = result['stop'] == 'choice_readout' and set(result['decisions']) == set(groups)
        print(json.dumps({'id': request_id, 'status': 'ok' if valid else 'fallback',
            'reason': None if valid else 'invalid_selector_output', 'capabilities': selected,
            'result': {k: v for k, v in result.items() if k != 'text'},
            'scope': 'capability_publication_only'}), flush=True)


if __name__ == '__main__':
    main()
