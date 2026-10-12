from types import SimpleNamespace

import numpy as np
import pytest
import torch

from research.rl_trading.v6.feedback_inputs import prefix_windows
from research.rl_trading.v6.feedback_teacher import FeedbackTeacher
from research.rl_trading.v6.transformer_search import TransformerSpec


def session():
    scalar = np.zeros((20, 37), np.float32)
    scalar[:, :4] = np.log(np.arange(20)[:, None] + 10.)
    scalar[:, 35] = 1
    candles = SimpleNamespace(close_us=np.arange(1, 21, dtype=np.int64) * 1_000_000,
        scalar=scalar, levels=np.zeros((20, 2, 5, 11), np.float32))
    return SimpleNamespace(role='train', previous=None,
        bank=SimpleNamespace(listing=lambda identity: candles)), candles


def test_prefix_cutoffs_resets_and_reference_entry_visibility():
    s, candles = session()
    held = np.zeros((1, 11), np.float32); held[0, :4] = 2, 16, 3, .123
    first = prefix_windows(s, 'A', np.array([10_000_000], np.int64), held, 4_000_000)
    assert not first['available'][0, :2].any()
    assert first['reset'][0, 2]
    assert not first['held'][0, :5].any()  # Entry at t=7, no entry price before it.
    assert torch.equal(first['held'][0, -1], torch.from_numpy(held[0]))
    assert torch.all(first['max_input_close_us'] < first['clocks_us'])
    candles.scalar[9:] = 999  # Source at target and later must have no effect.
    second = prefix_windows(s, 'A', np.array([10_000_000], np.int64), held, 4_000_000)
    for name in first:
        assert torch.equal(first[name], second[name])


def test_sealed_prefix_denied():
    s, _ = session(); s.role = 'sealed_test'
    with pytest.raises(ValueError, match='Sealed'):
        prefix_windows(s, 'A', np.array([10_000_000], np.int64), np.zeros((1, 11)), 0)


def test_real_feedback_teacher_frozen_base_and_trainable_feedback():
    torch.set_num_threads(2)
    norm = {n: dict(mean=[0.] * d, std=[1.] * d) for n, d in
            (('multi', 16), ('liquidity', 30), ('market', 30))}
    model = FeedbackTeacher(TransformerSpec('patch', 32, 1, 4, 2, 4),
        dict(mean=[0.] * 147, std=[1.] * 147), dict(mean=[0.] * 35, std=[1.] * 35), norm)
    sequence = dict(raw=torch.zeros(1, 9, 120, 147),
        present=torch.ones(1, 9, 120, dtype=torch.bool), held=torch.zeros(1, 9, 11),
        multi=torch.zeros(1, 9, 3, 120, 16),
        multi_present=torch.zeros(1, 9, 3, 120, dtype=torch.bool),
        liquidity=torch.zeros(1, 9, 30), market=torch.zeros(1, 9, 30),
        clocks_us=torch.arange(9, dtype=torch.int64)[None] * 1_000_000,
        available=torch.ones(1, 9, dtype=torch.bool), reset=torch.zeros(1, 9, dtype=torch.bool))
    model.head_parameters()
    with torch.no_grad(): base = model.base_sequence(sequence)
    context, prediction = model.feedback_context(base, sequence)
    assert prediction.shape == (1, 9, 7)
    model.heads.entry(context).sum().backward()
    assert not any(p.requires_grad for p in model.encoder.parameters())
    assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in model.intent_feedback.parameters())
    # Recompute feedback from cached base after head updates; no stale cache.
    before = prediction.detach().clone()
    with torch.no_grad(): model.heads.entry.bias[0].add_(1.)
    _, after = model.feedback_context(base, sequence)
    assert not torch.equal(before, after)
