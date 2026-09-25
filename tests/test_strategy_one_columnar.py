"""Strategy 1 prefilter uses only causal persisted-product arrays."""
import numpy as np
import pytest

from src.trading_runtime.strategy_one_columnar import (
    CompletedMacd, CompletedThirtySecondLow, REJECT_LIQUIDITY, REJECT_MACD,
    REJECT_PRICE, REJECT_QUOTE,
    REJECT_STOP_BAR, prepare_strategy_one_entries,
    schedule_strategy_one_entries,
)


def inputs():
    boundaries = np.array([29_900, 30_000, 30_100, 59_900, 60_000, 60_100])
    origin = 1_800_000_000_000_000
    epochs = origin + boundaries * 1000
    macd = {resolution: CompletedMacd(
        np.array([30_000, 60_000]), np.array([.2, .2]), np.array([.1, .1]))
        for resolution in (1_000, 5_000, 10_000, 30_000)}
    return dict(evaluation_boundary_ms=boundaries, evaluation_epoch_us=epochs,
                close_int=np.full(6, 100_000), price_valid=np.ones(6),
                bid_int=np.full(6, 99_900), ask_int=np.full(6, 100_100),
                quote_valid=np.ones(6), quote_timestamp_us=epochs - 100_000,
                execution_vwap=np.full(6, 9.5), previous_close=np.full(6, 9.),
                cumulative_volume=np.full(6, 30_000.),
                cumulative_notional=np.full(6, 300_000.),
                volume_trade_count=np.full(6, 30),
                macd=macd,
                thirty_second_low=CompletedThirtySecondLow(
                    np.array([30_000]), np.array([97_000]),
                    np.array([1]), np.array([1])))


def test_completed_asof_join_rejects_future_stale_and_missing_stop_bar():
    batch = prepare_strategy_one_entries(**inputs())
    assert batch.entry_mask.tolist() == [False, True, True, False, False, False]
    assert batch.rejection_bits[0] & REJECT_MACD
    assert batch.rejection_bits[0] & REJECT_STOP_BAR
    assert batch.rejection_bits[3] & REJECT_MACD
    assert batch.rejection_bits[4] & REJECT_STOP_BAR
    assert batch.stop_bar_boundary_ms.tolist() == [-1, 30_000, 30_000,
                                                    30_000, -1, -1]
    assert batch.stop_low_int.tolist() == [0, 97_000, 97_000, 97_000, 0, 0]


def test_future_tail_and_quote_clock_are_causal():
    data = inputs()
    base = prepare_strategy_one_entries(**data)
    future = {}
    for resolution, series in data["macd"].items():
        future[resolution] = CompletedMacd(
            np.append(series.boundary_ms, 90_000),
            np.append(series.line, -10.), np.append(series.signal, 10.))
    data["macd"] = future
    data["thirty_second_low"] = CompletedThirtySecondLow(
        np.array([30_000, 90_000]), np.array([97_000, 1]),
        np.array([1, 1]), np.array([1, 1]))
    assert np.array_equal(prepare_strategy_one_entries(**data).rejection_bits,
                          base.rejection_bits)
    data["quote_timestamp_us"] = data["evaluation_epoch_us"] + 1
    assert np.all(prepare_strategy_one_entries(**data).rejection_bits & REJECT_QUOTE)


def test_sub_dollar_episode_remains_watchable_but_cannot_enter():
    data = inputs()
    data["close_int"][1] = 9_999
    data["bid_int"][1] = 9_900
    data["ask_int"][1] = 10_000
    data["execution_vwap"][1] = .5
    batch = prepare_strategy_one_entries(**data)
    assert batch.rejection_bits[1] == REJECT_PRICE
    assert not batch.entry_mask[1]
    assert batch.entry_mask[2]


def test_missing_macd_and_misaligned_source_fail_closed():
    data = inputs()
    data["macd"][30_000] = CompletedMacd([], [], [])
    assert not np.any(prepare_strategy_one_entries(**data).entry_mask)
    data["macd"][30_000] = CompletedMacd([30_100], [.2], [.1])
    with pytest.raises(ValueError, match="completed boundaries"):
        prepare_strategy_one_entries(**data)


def test_squeeze_episode_schedule_is_causal_inclusive_and_compact():
    data = inputs()
    data["evaluation_boundary_ms"] = np.array(
        [30_000, 30_100, 330_000, 330_100, 630_000])
    data["evaluation_epoch_us"] = 1_800_000_000_000_000 + data["evaluation_boundary_ms"] * 1000
    for name in ("close_int", "price_valid", "bid_int", "ask_int", "quote_valid",
                 "quote_timestamp_us", "execution_vwap", "previous_close",
                 "cumulative_volume", "cumulative_notional", "volume_trade_count"):
        data[name] = np.resize(data[name], 5)
    data["quote_timestamp_us"] = data["evaluation_epoch_us"] - 100_000
    data["macd"] = {resolution: CompletedMacd(
        np.arange(resolution, 630_001, resolution, dtype=np.int64),
        np.full(630_000 // resolution, .2),
        np.full(630_000 // resolution, .1))
        for resolution in (1_000, 5_000, 10_000, 30_000)}
    data["thirty_second_low"] = CompletedThirtySecondLow(
        np.arange(30_000, 630_001, 30_000), np.full(21, 97_000),
        np.ones(21), np.ones(21))
    batch = prepare_strategy_one_entries(**data)
    schedule = schedule_strategy_one_entries(batch, [30_000, 630_000])
    assert schedule.evaluation_boundary_ms.tolist() == [30_000, 30_100, 330_000, 630_000]
    assert schedule.episode_start_boundary_ms.tolist() == [30_000] * 3 + [630_000]
    assert schedule.row_index.tolist() == [0, 1, 2, 4]
    assert schedule_strategy_one_entries(batch, []).row_index.size == 0
    with pytest.raises(ValueError, match="overlap"):
        schedule_strategy_one_entries(batch, [30_000, 30_100])


def test_completed_liquidity_purchase_gate_is_causal_and_rejects_sparse_rate():
    data = inputs()
    baseline = prepare_strategy_one_entries(**data)
    assert baseline.entry_mask[1:3].tolist() == [True, True]
    data["cumulative_volume"][:] = 24_999
    assert np.all(prepare_strategy_one_entries(**data).rejection_bits & REJECT_LIQUIDITY)
    data = inputs()
    data["volume_trade_count"][:] = 0
    assert np.all(prepare_strategy_one_entries(**data).rejection_bits & REJECT_LIQUIDITY)
    data = inputs()
    data["ask_int"][:] = 105_000
    assert np.all(prepare_strategy_one_entries(**data).rejection_bits & REJECT_LIQUIDITY)
    data = inputs()
    data["cumulative_notional"][-1] = 0
    with pytest.raises(ValueError, match="accumulators"):
        prepare_strategy_one_entries(**data)
