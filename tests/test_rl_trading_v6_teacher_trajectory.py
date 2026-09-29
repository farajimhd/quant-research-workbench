from datetime import date
import math

import pytest

import numpy as np
import polars as pl

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.teacher_trajectory import (
    _debit_nonnegative, bind_intents, compile_trajectory)


def test_teacher_account_debit_rejects_overspend_but_clears_roundoff():
    assert _debit_nonnegative(.3, .1 + .2) == 0.
    with pytest.raises(ValueError, match='exceeds available'):
        _debit_nonnegative(100., 100.01)


def test_quote_free_teacher_emits_delayed_account_actions(tmp_path):
    clocks = np.asarray([1_000_000, 2_000_000, 3_000_000, 4_000_000],
                        dtype=np.int64)
    scalar = np.zeros((4, len(SCALAR_NAMES)), dtype=np.float32)
    scalar[:, SCALAR_NAMES.index('log_close')] = np.log(
        np.asarray([10., 10.2, 11., 11.], dtype=np.float32))
    scalar[:, SCALAR_NAMES.index('bar_price_valid')] = 1.
    bank = SessionBank(tmp_path / 'bank',
        {'offsets': {'A': [0, 4]}}, clocks, scalar,
        np.zeros((4, 2, 5, 11), dtype=np.float32))
    session = PackedSession(date(2026, 7, 31), 'train', tmp_path / 'day',
                            'bank-hash', bank, None, ('A',))
    allocations = pl.DataFrame({'ticker': ['A'], 'listing_id': ['A'],
        'episode_uid': ['A:1'],
        'time_us': [1_000_000], 'exit_hint_us': [3_000_000],
        'decision_close': [10.], 'exit_hint_close': [11.],
        'desired_budget': [1000.], 'future_reservation': [0.],
        'score': [.1], 'direction': [1]})
    brackets = pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A:1'],
        'entry_us': [1_000_000], 'exit_us': [3_000_000],
        'oracle_stop': [9.], 'oracle_target': [11.1],
        'held_last_max_high_us': [3_000_000],
        'label_available': [True]})
    intents, binding = bind_intents(allocations, brackets, session.listings)
    assert binding['oracle_geometry_accepted'] == 1
    decisions, outcomes, report = compile_trajectory(session, intents,
        hold_sample_seconds=1)
    assert [item.action for item in outcomes] == [1, 3, 4, 2]
    assert [item.bucket_end_us for item in outcomes] == [2_000_000,
        3_000_000, 3_000_000, 4_000_000]
    assert report['completed_positions'] == 1
    assert report['pending_entries'] == report['pending_exits'] == 0
    assert report['ending_trading_cash'] == 10_000.
    assert math.isclose(report['profit_bank'], report['modeled_net_pnl'])
    assert report['modeled_net_pnl'] > 0
    assert any(item.token == 2 for item in decisions)  # Manual EXIT_LONG.
    assert all(item.account.shape == (7,) for item in decisions)


def test_teacher_never_sets_a_target_after_its_oracle_peak(tmp_path):
    clocks = np.asarray([1_000_000, 2_000_000, 3_000_000, 4_000_000],
                        dtype=np.int64)
    scalar = np.zeros((4, len(SCALAR_NAMES)), dtype=np.float32)
    scalar[:, SCALAR_NAMES.index('log_close')] = math.log(10.)
    scalar[:, SCALAR_NAMES.index('bar_price_valid')] = 1.
    bank = SessionBank(tmp_path / 'bank', {'offsets': {'A': [0, 4]}},
        clocks, scalar, np.zeros((4, 2, 5, 11), dtype=np.float32))
    session = PackedSession(date(2026, 7, 31), 'train', tmp_path / 'day',
                            'bank-hash', bank, None, ('A',))
    allocations = pl.DataFrame({'ticker': ['A'], 'listing_id': ['A'],
        'episode_uid': ['A:1'],
        'time_us': [1_000_000], 'exit_hint_us': [3_000_000],
        'decision_close': [10.], 'exit_hint_close': [9.],
        'desired_budget': [1000.], 'future_reservation': [0.],
        'score': [.1], 'direction': [1]})
    brackets = pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A:1'],
        'entry_us': [1_000_000], 'exit_us': [3_000_000],
        'oracle_stop': [8.], 'oracle_target': [11.],
        'held_last_max_high_us': [2_000_000],
        'label_available': [True]})
    intents, _ = bind_intents(allocations, brackets, session.listings)
    decisions, outcomes, report = compile_trajectory(session, intents,
                                                       hold_sample_seconds=1)
    assert 4 not in [outcome.action for outcome in outcomes]
    assert report['profit_bank'] == 0.
    assert report['ending_trading_cash'] < 10_000.
