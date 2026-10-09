"""Installed source/fence path with controlled transport, not financial fills."""
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from src.backend.backtest_native_channel_sequences import (
    NativeChannelSequencePolicy, qualify_installed_native_channel_sequences,
)
from src.market_engine.native_causal_channel_contract import issue_source_plan
from src.trading_runtime.native_completed_bar_sequences import CompletedBarSequencePolicy


def call(inputs, *, acquired=None, held=None, decisions=None, comparison='ge', bars=1):
    client, authority, packet, original, _ = inputs
    left = original if decisions is None else decisions
    policy = NativeChannelSequencePolicy(packet.request.policy,
        CompletedBarSequencePolicy(bars, 1000), 60000, 'close_return', comparison, 0.0, 1000)
    return qualify_installed_native_channel_sequences(client, packet.request,
        packet.feature_attempt_id, left,
        np.ones(left.height, dtype=bool) if held is None else held,
        np.zeros(left.height, dtype=np.int64) if acquired is None else acquired,
        policy=policy, producer_source_hash=issue_source_plan(packet).producer_source_hash,
        authority=authority)


def test_installed_sequence_preserves_order_and_held_mask(installed):
    assert call(installed).tolist() == [True, False, False, True]
    output = call(installed, held=np.array([False, True, True, True]))
    assert output.tolist() == [False, False, False, True]
    assert not output.flags.writeable
    assert len(installed[0].writes) == 2


def test_completion_must_be_strictly_after_actual_acquisition(installed):
    acquired = installed[3]['decision_day_ms'].to_numpy() * 1000
    assert not call(installed, acquired=acquired).any()
    assert not call(installed, bars=2).any()


def test_foreign_identity_and_future_acquisition_rejected(installed):
    with pytest.raises(ValueError, match='outside installed'):
        call(installed, decisions=installed[3].with_columns(pl.lit('FOREIGN').alias('ticker')))
    before = len(installed[0].sql)
    with pytest.raises(ValueError, match='later than decision'):
        call(installed, acquired=np.full(4, 999999999, dtype=np.int64))
    assert len(installed[0].sql) == before


def test_policy_requires_explicit_comparison_and_finite_threshold(installed):
    p = NativeChannelSequencePolicy(installed[2].request.policy,
        CompletedBarSequencePolicy(2, 1000), 60000, 'close_return', 'lt', 0.0, 1000)
    assert p.payload()['comparison'] == 'lt'
    for kwargs in ({'threshold': float('nan')}, {'threshold': 0}, {'comparison': 'negative'}):
        with pytest.raises(ValueError):
            replace(p, **kwargs)
