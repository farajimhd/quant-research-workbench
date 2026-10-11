from datetime import datetime, timezone, timedelta

import numpy as np
import polars as pl
import pytest

from src.market_engine.causal_market_history import prepare_causal_history
from research.vectorized_backtest.v6.torch_backtest.history_bank import calculate, SWING_WINDOWS


def source(n=200):
    rng = np.random.default_rng(2237)
    close = 10 + rng.uniform(-1, 1, n)
    high, low = close + .1, close - .1
    observed = rng.uniform(size=n) > .1
    observed[:3] = False
    notional = np.where(observed, rng.uniform(0, 10000, n), 0.)
    last = np.maximum.accumulate(np.where(observed, np.arange(n), -1))
    known = last >= 0
    price = np.where(known, close[last.clip(0)], np.nan)
    clocks = [datetime(2026, 8, 3, 14, tzinfo=timezone.utc) + timedelta(seconds=i) for i in range(n)]
    frame = pl.DataFrame(dict(listing_id=["listing"] * n, boundary_at=clocks, available_at=clocks,
        observed=observed, source_known=known, close=close, high=high, low=low,
        notional=notional, reference_close=price))
    return frame, calculate(close, high, low, observed, notional)


def test_all_shared_history_channels_match_completed_optimization_history():
    frame, expected = source()
    names = []
    for kind, windows in [("recent_high", range(1, 61)), ("momentum_close", range(1, 61)),
                         ("attention_mean", range(1, 61)), ("average_move", range(2, 33))]:
        names.extend(f"history.{kind}.{w}" for w in windows)
    names.extend(f"history.swing_low.{left}.{right}" for left in SWING_WINDOWS for right in SWING_WINDOWS)
    result = prepare_causal_history(frame, tuple(names))
    assert len(names) == expected.shape[1] == 332
    for index, name in enumerate(names):
        np.testing.assert_array_equal(result[name].to_numpy(), expected[:, index])
        np.testing.assert_array_equal(result[name + "__valid"].to_numpy(),
            np.isfinite(expected[:, index]) & frame["source_known"].to_numpy())


def test_relative_rounding_and_future_tail_independence():
    frame, expected = source()
    name = "history.recent_high.32.relative_to_close"
    result = prepare_causal_history(frame, (name,))
    values = (expected[:, 31].astype(np.float32).astype(np.float64) /
              frame["reference_close"].to_numpy() - 1).astype(np.float32)
    np.testing.assert_array_equal(result[name].to_numpy(), values)
    np.testing.assert_array_equal(prepare_causal_history(frame.head(100), (name,))[name].to_numpy(), values[:100])
    assert result.schema[name] == pl.Float32


def test_missing_elapsed_second_and_future_availability_rejected():
    frame, _ = source()
    with pytest.raises(ValueError, match="gaps"):
        prepare_causal_history(frame.filter(pl.col("boundary_at") != frame["boundary_at"][10]),
                               ("history.attention_mean.58",))
    with pytest.raises(ValueError, match="future"):
        prepare_causal_history(frame.with_columns(pl.col("available_at") + pl.duration(seconds=1)),
                               ("history.attention_mean.58",))
    with pytest.raises(ValueError, match="relative-to-price"):
        prepare_causal_history(frame, ("history.attention_mean.58.relative_to_close",))
