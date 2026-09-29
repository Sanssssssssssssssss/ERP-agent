"""Shared thought, independent option readouts, and incomplete-output protection."""
from types import SimpleNamespace

import numpy as np
import pytest

from erp_harness.providers.openjev_worker import generate, sample
from experiments.tool_routing.q4_thinking import sample as replay_sample


def test_worker_uses_frozen_sampler_and_restores_each_branch(tmp_path):
    logits = np.random.default_rng(1).normal(size=100)
    live, replay = np.random.default_rng(42), np.random.default_rng(42)
    assert [sample(logits, {1, 2}, live, np) for _ in range(30)] == [
        replay_sample(logits, {1, 2}, replay, np) for _ in range(30)]

    def peak(token):
        scores = np.full(100, -100.)
        scores[token] = 100.
        return scores

    restored = []
    def branch(position, suffix):
        assert position == 3
        assert len(restored) > 0
        return peak(7 if suffix == [10] else 8)
    tokenizer = SimpleNamespace(apply_chat_template=lambda *a, **kw: 'prompt<think>\n',
        encode=lambda text, **kw: [{'A': 7, 'B': 8, '\n\n': 9, 'actions=': 10, 'attachments=': 11}.get(text, 1)],
        convert_tokens_to_ids=lambda _: 2,
        decode=lambda tokens, **kw: ''.join('</think>' if t == 2 else '.' for t in tokens))
    engine = SimpleNamespace(context_tokens=100, lib=SimpleNamespace(llama_vocab_is_eog=lambda *a: False),
        full_logits=lambda ids: peak(2 if len(ids) == 1 else 7),
        save_state=lambda: 'shared-prefix', restore_state=lambda state: restored.append(state),
        branch_logits=branch)
    model = SimpleNamespace(engine=engine, vocab=None)
    backend = SimpleNamespace(_gguf_tokenize=lambda *a: [1])
    first = generate(model, tokenizer, backend, [], tmp_path / 'first', ['actions', 'attachments'])
    second = generate(model, tokenizer, backend, [], tmp_path / 'second', ['attachments', 'actions'])
    assert first['decisions'] == second['decisions']
    assert first['decisions']['actions']['answer'] == 'A'
    assert first['decisions']['attachments']['answer'] == 'B'
    assert restored == ['shared-prefix'] * 4
    assert first['output_tokens'] == 3  # One sampled close + two decision tokens; no host labels counted.
    assert first['readout_input_tokens'] == 5
    assert first['conditional_option_scores'] is None
    engine.lib.llama_vocab_is_eog = lambda *a: True
    failed = generate(model, tokenizer, backend, [], tmp_path / 'incomplete', ['actions'])
    assert failed['stop'] == 'eog' and failed['decisions'] == {} and failed['answer'] is None
    engine.context_tokens = 20
    with pytest.raises(ValueError, match='no truncation'):
        generate(model, tokenizer, backend, [], tmp_path / 'overflow', ['actions'])
