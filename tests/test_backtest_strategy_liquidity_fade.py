"""Native rolling observations preserve missingness, attempt boundaries and order."""
from decimal import Decimal

import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade import compile_liquidity_fade_observations


def frame(counts=(57, 18, 8, 5)):
    return pl.DataFrame({
        "source_build_id": ["build"]*4, "session_date": ["2026-08-10"]*4,
        "ticker": ["PLUG"]*4, "source_attempt_id": ["attempt"]*4,
        "boundary_ms": [44_790_000, 44_795_000, 44_800_000, 44_805_000],
        "trade_count": pl.Series(counts, dtype=pl.UInt64),
        "price_valid": [True, False, True, True],
    })


def test_observed_window_retains_zero_price_validity_and_exact_input_order():
    f = frame().reverse()
    result = compile_liquidity_fade_observations(f)
    assert result.height == f.height and result.select(f.columns).equals(f)
    row = result.row(0, named=True)
    assert row["activity_window_complete"] and row["liquidity_fade"]
    assert tuple(row[f"trade_count_{i}"] for i in range(4)) == (57, 18, 8, 5)
    assert row["prior_10s_trade_count"] == Decimal(75)
    assert row["recent_10s_trade_count"] == Decimal(13)
    assert result["liquidity_fade"].sum() == 1


@pytest.mark.parametrize("key", ["source_build_id", "session_date", "ticker", "source_attempt_id"])
def test_different_native_sources_cannot_complete_each_others_window(key):
    f = frame().with_columns(pl.Series(key, ["a", "a", "b", "b"]))
    result = compile_liquidity_fade_observations(f)
    assert result["activity_window_complete"].sum() == 0
    assert result["liquidity_fade"].sum() == 0


def test_missing_candle_missing_count_and_real_zero_are_not_interchangeable():
    gap = frame().with_columns(pl.Series("boundary_ms", [44_785_000, 44_795_000, 44_800_000, 44_805_000]))
    assert not compile_liquidity_fade_observations(gap)["activity_window_complete"][-1]
    assert not compile_liquidity_fade_observations(frame((57, None, 8, 5)))["activity_window_complete"][-1]
    real_zero = compile_liquidity_fade_observations(frame((57, 18, 0, 0)))
    assert real_zero["activity_window_complete"][-1] and real_zero["liquidity_fade"][-1]
    assert not compile_liquidity_fade_observations(frame((0, 0, 0, 0)))["liquidity_fade"][-1]


def test_integer_ratio_does_not_overflow_uint64_or_round_boundary():
    limit = (1 << 64)-1
    assert compile_liquidity_fade_observations(frame((limit, limit, 0, 0)))["prior_10s_trade_count"][-1] == 2*Decimal(limit)
    assert compile_liquidity_fade_observations(frame((40, 0, 10, 0)))["liquidity_fade"][-1]
    assert not compile_liquidity_fade_observations(frame((40, 0, 11, 0)))["liquidity_fade"][-1]
    oversized = frame().with_columns(pl.Series("trade_count", [1 << 64, 18, 8, 5], dtype=pl.Int128))
    with pytest.raises(ValueError, match="UInt64 authority"):
        compile_liquidity_fade_observations(oversized)


def test_duplicate_malformed_and_output_column_collision_reject():
    with pytest.raises(ValueError, match="repeats"):
        compile_liquidity_fade_observations(pl.concat([frame(), frame().head(1)]))
    with pytest.raises(ValueError, match="integer"):
        compile_liquidity_fade_observations(frame().with_columns(pl.col("trade_count").cast(pl.Float64)))
    with pytest.raises(ValueError, match="invalid"):
        compile_liquidity_fade_observations(frame().with_columns(pl.lit(-1).alias("trade_count")))
    with pytest.raises(ValueError, match="overwrite"):
        compile_liquidity_fade_observations(frame().with_columns(pl.lit(False).alias("liquidity_fade")))


def test_empty_typed_frame_returns_empty_output_without_synthetic_rows():
    result = compile_liquidity_fade_observations(frame().head(0))
    assert result.height == 0 and result.schema["liquidity_fade"] == pl.Boolean
