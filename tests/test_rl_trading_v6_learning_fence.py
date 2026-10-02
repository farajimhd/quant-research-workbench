"""Learning fences preserve warmup events and the intended update budget."""
from types import SimpleNamespace
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import torch

from research.rl_trading.v6.training import _event_chunks, train_session, TeacherDecision
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.model import BracketPolicy
from research.rl_trading.v6.session_data import PackedSession


def test_rth_fence_preserves_all_events_and_starts_32_learning_chunks():
    events = [SimpleNamespace(close_us=i) for i in range(19799 + 1024)]
    chunks = list(_event_chunks(iter(events), 32, 19799))
    assert [event for chunk in chunks for event in chunk] == events
    assert all(not (chunk[0].close_us < 19799 <= chunk[-1].close_us)
               for chunk in chunks)
    learning = [chunk for chunk in chunks if chunk[0].close_us >= 19799]
    assert len(learning) == 32
    assert all(len(chunk) == 32 for chunk in learning)
    assert len(chunks[618]) == 23


def test_no_fence_preserves_original_batching_and_partial_tail():
    events = [SimpleNamespace(close_us=i) for i in range(67)]
    chunks = list(_event_chunks(iter(events), 32))
    assert chunks == [tuple(events[:32]), tuple(events[32:64]), tuple(events[64:])]


def test_fence_at_first_event_or_between_sparse_clocks():
    events = [SimpleNamespace(close_us=i) for i in (1, 4, 9, 15)]
    assert list(_event_chunks(iter(events), 2, 1)) == [tuple(events[:2]), tuple(events[2:])]
    assert list(_event_chunks(iter(events), 3, 7)) == [tuple(events[:2]), tuple(events[2:])]


def test_real_trainer_warms_all_candles_and_performs_32_learning_updates(monkeypatch):
    from research.rl_trading.v6.candle_stream import SparseCandleState
    warmup = 23
    clocks = np.arange(1, warmup + 1025, dtype=np.int64) * 1_000_000
    scalar = np.zeros((len(clocks), 37), np.float32)
    scalar[:, 0] = np.arange(len(clocks)) / 1000
    bank = SessionBank(Path('unused'), {'offsets': {'A': [0, len(clocks)]}},
        clocks, scalar, np.zeros((len(clocks), 2, 5, 11), np.float32))
    session = PackedSession(date(2026, 7, 31), 'train', Path('unused'),
        'certificate', bank, None, ('A',))
    labels = tuple(TeacherDecision(int(clock), 0, 1,
        np.asarray([10000., 10000., 0., 0., 0., 0., 0.], np.float32),
        np.empty(0, np.int64), np.empty((0, 9), np.float32),
        np.asarray([True]), np.empty(0, bool), np.empty(0, bool),
        np.empty(0, bool), size_fraction=.25) for clock in clocks[warmup:])
    observed = []
    advance = SparseCandleState.advance
    def capture(self, encoder, indices, values, levels, **kwargs):
        observed.append((float(values[0, 0]), torch.is_grad_enabled()))
        return advance(self, encoder, indices, values, levels, **kwargs)
    monkeypatch.setattr(SparseCandleState, 'advance', capture)
    policy = BracketPolicy(width=4)
    optimizer = torch.optim.Adam(policy.parameters(), lr=.0003)
    report = train_session(policy, optimizer, session, labels, (),
        device=torch.device('cpu'), teacher_loss='balanced-v2',
        learning_start_us=int(clocks[warmup]))
    assert report.optimizer_steps == 32 and report.decisions == 1024
    np.testing.assert_array_equal([x[0] for x in observed], scalar[:, 0])
    assert not any(x[1] for x in observed[:warmup])
    assert all(x[1] for x in observed[warmup:])
    with pytest.raises(ValueError, match='discard supplied teacher labels'):
        train_session(policy, optimizer, session, labels, (),
            device=torch.device('cpu'), learning_start_us=int(clocks[warmup + 1]))
