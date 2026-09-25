"""Strategy 1 prefilter uses only causal persisted-product arrays."""
import numpy as np
import pytest

from src.trading_runtime.strategy_one_columnar import (
    CompletedMacd, CompletedThirtySecondLow, REJECT_MACD, REJECT_QUOTE,
    REJECT_STOP_BAR, prepare_strategy_one_entries,
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


def test_missing_macd_and_misaligned_source_fail_closed():
    data = inputs()
    data["macd"][30_000] = CompletedMacd([], [], [])
    assert not np.any(prepare_strategy_one_entries(**data).entry_mask)
    data["macd"][30_000] = CompletedMacd([30_100], [.2], [.1])
    with pytest.raises(ValueError, match="completed boundaries"):
        prepare_strategy_one_entries(**data)
