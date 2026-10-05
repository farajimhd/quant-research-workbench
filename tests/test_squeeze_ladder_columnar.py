from dataclasses import replace

import numpy as np
import pytest

from src.trading_runtime.squeeze_ladder_columnar import (
    LadderGatePolicy, REJECT_HISTORY, REJECT_LIQUIDITY, REJECT_QUOTE,
    REJECT_SESSION, compile_ladder_gate,
)


def columns():
    boundary = np.arange(100, 70_100, 100, dtype=np.int64)
    n = len(boundary)
    epoch = 1_780_000_000_000_000 + boundary * 1000
    close = np.full(n, 99900, dtype=np.int64)
    close[649:] = 100200
    return dict(policy=LadderGatePolicy(25000., 100000., 1., .5, 250.,
                                       10000, 1_000_000, 300_000, 0,
                                       ((0, 19_500_000), (43_200_000, 57_000_000))),
                boundary_ms=boundary, evaluation_epoch_us=epoch, close_int=close,
                price_valid=np.ones(n, dtype=np.uint8), execution_vwap=np.full(n, 10.),
                bid_int=np.full(n, 99900, dtype=np.int64), ask_int=np.full(n, 100000, dtype=np.int64),
                quote_valid=np.ones(n, dtype=np.uint8), quote_timestamp_us=epoch.copy(),
                cumulative_volume=np.full(n, 25000.), cumulative_notional=np.full(n, 100000.),
                volume_trade_count=np.ones(n, dtype=np.int64),
                admission_boundaries_ms=np.array([64000], dtype=np.int64))


def test_full_observation_cross_needs_no_macd_or_thirty_second_stop():
    args = columns()
    result = compile_ladder_gate(**args)
    assert result.vwap_cross_indices.tolist() == [649]
    assert result.admission_boundary_ms[649] == 64000
    assert result.market_indices.tolist() == list(range(649, 700))
    assert not result.market_rejection.flags.writeable
    assert not result.boundary_ms.flags.writeable
    # The mask cannot mutate certified source arrays.
    assert args['boundary_ms'].flags.writeable


def test_cross_cannot_use_price_before_admission_or_cross_admission_reset():
    args = columns()
    args['admission_boundaries_ms'] = np.array([64000, 65000], dtype=np.int64)
    assert not compile_ladder_gate(**args).vwap_cross_indices.size
    args['admission_boundaries_ms'] = np.array([], dtype=np.int64)
    assert not compile_ladder_gate(**args).market_indices.size


def test_missing_bucket_is_not_zero_liquidity_and_quote_only_is_not_a_cross():
    args = columns()
    for name, value in tuple(args.items()):
        if isinstance(value, np.ndarray) and len(value) == 700:
            args[name] = np.delete(value, 99)
    result = compile_ladder_gate(**args)
    assert result.market_rejection[648] & REJECT_HISTORY
    assert not result.vwap_cross_indices.size
    args = columns()
    args['price_valid'][648] = 0
    assert not compile_ladder_gate(**args).vwap_cross_indices.size


def test_complete_certified_sparse_prefix_preserves_counts_without_fabricating_crosses():
    args = columns()
    for name, value in tuple(args.items()):
        if isinstance(value, np.ndarray) and len(value) == 700:
            args[name] = np.delete(value, 99)
    sparse = compile_ladder_gate(**args, certified_history_through_ms=70000)
    assert sparse.vwap_cross_indices.tolist() == [648]
    assert not np.any(sparse.market_rejection & REJECT_HISTORY)
    assert len(sparse.boundary_ms) == 699
    assert sparse.certified_history_through_ms == 70000
    actual_counts = compile_ladder_gate(**{**args, 'policy': replace(args['policy'],
        minimum_trade_rate_10s=10., minimum_trade_rate_60s=10.)},
        certified_history_through_ms=70000)
    assert actual_counts.market_rejection[648] & REJECT_LIQUIDITY
    assert not actual_counts.vwap_cross_indices.size
    # Completeness establishes activity availability, never a missing candle.
    for name, value in tuple(args.items()):
        if isinstance(value, np.ndarray) and len(value) == 699:
            args[name] = np.delete(value, 647)
    assert not compile_ladder_gate(**args, certified_history_through_ms=70000).vwap_cross_indices.size
    for invalid in (True, 69900, 70001, 57_600_100):
        with pytest.raises(ValueError, match='certified history'):
            compile_ladder_gate(**args, certified_history_through_ms=invalid)


def test_future_tail_independence_and_scalar_trade_window_parity():
    args = columns()
    args['volume_trade_count'][::3] = 0
    full = compile_ladder_gate(**args)
    prefix = {name: value[:660].copy() if isinstance(value, np.ndarray) and len(value) == 700 else value
              for name, value in args.items()}
    short = compile_ladder_gate(**prefix)
    np.testing.assert_array_equal(short.market_rejection, full.market_rejection[:660])
    np.testing.assert_array_equal(short.vwap_cross_indices, full.vwap_cross_indices[full.vwap_cross_indices < 660])
    for index in range(649, 700):
        now = int(args['boundary_ms'][index])
        counts = args['volume_trade_count'][:index + 1]
        clocks = args['boundary_ms'][:index + 1]
        rate10 = sum(int(value) for clock, value in zip(clocks, counts) if now - 10000 < clock <= now) / 10
        rate60 = sum(int(value) for clock, value in zip(clocks, counts) if now - 60000 < clock <= now) / 60
        failed = rate10 < 1 or rate60 < .5
        assert bool(full.market_rejection[index] & REJECT_LIQUIDITY) == failed


def test_stale_quote_and_regular_hours_fail_closed():
    args = columns()
    args['quote_timestamp_us'][649] -= 1_000_001
    assert compile_ladder_gate(**args).market_rejection[649] & REJECT_QUOTE
    args = columns()
    args['boundary_ms'] += 20_000_000
    args['evaluation_epoch_us'] += 20_000_000_000
    args['quote_timestamp_us'] += 20_000_000_000
    args['admission_boundaries_ms'] += 20_000_000
    result = compile_ladder_gate(**args)
    assert np.all(result.market_rejection & REJECT_SESSION)
    assert not result.market_indices.size


@pytest.mark.parametrize('changed', [dict(acquisition_windows=((19_000_000, 44_000_000),)),
                                      dict(quote_freshness_us=1_000_001),
                                      dict(minimum_trade_rate_10s=float('nan'))])
def test_policy_cannot_admit_regular_hours_or_invalid_thresholds(changed):
    with pytest.raises(ValueError):
        replace(columns()['policy'], **changed)


def test_count_overflow_and_source_clock_mismatch_are_errors():
    args = columns()
    args['volume_trade_count'][1] = np.iinfo(np.int64).max
    with pytest.raises(ValueError):
        compile_ladder_gate(**args)
    args = columns()
    args['evaluation_epoch_us'][649] += 1
    with pytest.raises(ValueError):
        compile_ladder_gate(**args)
