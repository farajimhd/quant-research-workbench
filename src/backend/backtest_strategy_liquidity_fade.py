"""Vectorized rolling producer observations for the unpublished liquidity exit.

This is a necessary activity condition, not trade admission. Inputs must come
from independently certified producer attempts; this compiler does not grant
that certification. No fill/outcome labels, market reads or order calls exist.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

import polars as pl

from src.trading_runtime.strategy_liquidity_fade_failure import MAX_TRADE_COUNT, LiquidityFadeCandle

SOURCE_KEYS = ("source_build_id", "session_date", "ticker", "source_attempt_id")
REQUIRED = (*SOURCE_KEYS, "boundary_ms", "trade_count")


def compile_liquidity_fade_observations(frame: pl.DataFrame) -> pl.DataFrame:
    """Keep shape (n_rows,) and input order; derive four completed 5s counts.

    Each shift stays within one build/day/ticker/attempt. Missing/zero-activity
    observations are not dropped before the window calculation. The output's
    activity bit still requires scalar original-entry, whole-held, price,
    momentum, fresh-quote and financial checks before an exit can be proposed.
    """
    if type(frame) is not pl.DataFrame or any(c not in frame.columns for c in REQUIRED):
        raise ValueError("Liquidity compiler requires its exact producer columns")
    if (any(frame.schema[c] != pl.String for c in SOURCE_KEYS)
            or not frame.schema["boundary_ms"].is_integer()
            or not frame.schema["trade_count"].is_integer()):
        raise ValueError("Liquidity compiler requires typed source keys and integer observations")
    invalid = pl.any_horizontal(
        *(pl.col(c).is_null() | (pl.col(c).str.len_chars() == 0) for c in SOURCE_KEYS),
        pl.col("boundary_ms").is_null(), (pl.col("boundary_ms") <= 0),
        (pl.col("boundary_ms") > 57_600_000), (pl.col("boundary_ms") % 5000 != 0),
        (pl.col("trade_count") < 0).fill_null(False),
    )
    if frame.select(invalid.any()).item():
        raise ValueError("Liquidity compiler has invalid source identity, clock or activity")
    maximum = frame.select(pl.col("trade_count").max()).item()
    if maximum is not None and maximum > MAX_TRADE_COUNT:
        raise ValueError("Liquidity compiler trade count exceeds native UInt64 authority")
    if frame.select(pl.struct(*SOURCE_KEYS, "boundary_ms").is_duplicated().any()).item():
        raise ValueError("Liquidity compiler repeats a producer candle")
    # Internal names never shadow producer columns. No source row is silently
    # overwritten, normalized to zero, filtered away or deduplicated.
    outputs = tuple(f"trade_count_{i}" for i in range(4)) + (
        "activity_window_complete", "prior_10s_trade_count", "recent_10s_trade_count", "liquidity_fade")
    if any(c.startswith("_liquidity_") or c in outputs for c in frame.columns):
        raise ValueError("Liquidity compiler cannot overwrite existing source columns")
    f = frame.with_row_index("_liquidity_order").sort([*SOURCE_KEYS, "boundary_ms"])
    # Four lag columns each have shape (n_rows,). Group keys prevent a later
    # attempt or another session from filling gaps in this attempt's window.
    f = f.with_columns(
        *[pl.col("trade_count").shift(3-i).over(SOURCE_KEYS).alias(f"trade_count_{i}")
          for i in range(4)],
        pl.col("boundary_ms").shift(3).over(SOURCE_KEYS).alias("_liquidity_oldest"),
    )
    f = f.with_columns(
        ((pl.col("boundary_ms") - pl.col("_liquidity_oldest") == 15_000)
         & pl.all_horizontal(*(pl.col(f"trade_count_{i}").is_not_null() for i in range(4))))
        .fill_null(False).alias("activity_window_complete"),
        # Decimal integer arithmetic covers sums/multiples of UInt64 counts
        # without unsigned overflow or floating ratio-boundary rounding.
        (pl.col("trade_count_0").cast(pl.Decimal(38, 0))
         + pl.col("trade_count_1").cast(pl.Decimal(38, 0))).alias("prior_10s_trade_count"),
        (pl.col("trade_count_2").cast(pl.Decimal(38, 0))
         + pl.col("trade_count_3").cast(pl.Decimal(38, 0))).alias("recent_10s_trade_count"),
    )
    return f.with_columns(
        (pl.col("activity_window_complete") & (pl.col("prior_10s_trade_count") > 0)
         & (4 * pl.col("recent_10s_trade_count") <= pl.col("prior_10s_trade_count")))
        .fill_null(False).alias("liquidity_fade"),
    ).sort("_liquidity_order").drop("_liquidity_order", "_liquidity_oldest")


@dataclass(frozen=True, slots=True)
class CompletedLiquidityFadeWindow:
    """An activity-only candidate; original risk/price/quote checks still apply."""
    boundary_ms: int
    candles: tuple[LiquidityFadeCandle, ...]


class CompiledLiquidityFadeLookup:
    """One-session columnar cache; no market I/O during held-position decisions.

    Construction compiles all native 5s activity once with Polars. Per ticker,
    the cache retains immutable arrays of shape (n_completed_bars,) and four
    count arrays of that same shape. A binary search selects only the latest
    completed row at/before a 100ms decision; later loaded rows cannot leak.
    The caller must independently certify the supplied plan and producer frame.
    Native publication/cold recovery still verifies exact producer observations.
    """
    def __init__(self, frame, *, plan, session_date, max_rows=2_000_000,
                 strategy_number=35):
        from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit
        if (type(max_rows) is not int or not 1 <= max_rows <= 20_000_000
                or type(frame) is not pl.DataFrame or frame.height > max_rows):
            raise ValueError('Liquidity lookup exceeds its explicit native row/memory bound')
        if (type(plan) is not CertifiedMarketDayPlan or type(session_date) is not date
                or plan.sessions != (session_date.isoformat(),)
                or 5000 not in plan.required_resolutions_ms
                or type(plan.units) is not tuple or len(plan.units) > 65_536
                or any(type(u) is not MarketDayUnit for u in plan.units)):
            raise ValueError('Liquidity lookup requires one independently certified native session')
        if type(strategy_number) is not int or strategy_number not in (35,36,37,38,39, 40):
            raise ValueError('Liquidity lookup requires an exact supported strategy number')
        activity_column = 'liquidity_fade'
        if strategy_number in (39, 40):
            from .backtest_strategy_half_risk_liquidity_fade import (
                HALF_RISK_ACTIVITY_FADE, compile_half_risk_liquidity_observations,
            )
            compiled = compile_half_risk_liquidity_observations(frame)
            activity_column = HALF_RISK_ACTIVITY_FADE
        else:
            compiled = compile_liquidity_fade_observations(frame)
        units = {}
        for unit in plan.units:
            if unit.stage != 'bars':
                continue
            if (unit.build_id != plan.build_id or unit.session_date != session_date.isoformat()
                    or unit.ticker not in plan.tickers or unit.ticker in units):
                raise ValueError('Liquidity lookup has foreign or duplicate native bar units')
            units[unit.ticker] = unit.attempt_id
        if set(units) != set(plan.tickers):
            raise ValueError('Liquidity lookup lacks its exact native ticker/unit population')
        groups = compiled.partition_by(SOURCE_KEYS, as_dict=True)
        arrays = {}
        for key, group in groups.items():
            build, day, ticker, attempt = key
            if (build != plan.build_id or day != session_date.isoformat()
                    or ticker not in units or attempt != units[ticker]):
                raise ValueError('Liquidity lookup observation differs from its pinned source')
            group = group.sort('boundary_ms')
            # Missing storage values use a separate validity mask. Their zero
            # slots are never read as native observations: complete+fade must
            # both be true before any count is materialized into a witness.
            columns = [group['boundary_ms'].to_numpy(),
                       group['activity_window_complete'].to_numpy(), group[activity_column].to_numpy()]
            columns.extend(group[f'trade_count_{i}'].fill_null(0).cast(pl.UInt64).to_numpy()
                           for i in range(4))
            for values in columns:
                values.flags.writeable = False
            arrays[ticker] = tuple(columns)
        self._arrays = MappingProxyType(arrays)
        self._bar_attempts = MappingProxyType(units)
        self.session_date = session_date
        self.source_build_id = plan.build_id
        self.market_plan_token = plan.token
        self.strategy_number = strategy_number

    def window_at(self, ticker, boundary_ms):
        """O(log bars) lookup, then at most four exact native integer counts."""
        if (type(ticker) is not str or ticker not in self._bar_attempts
                or type(boundary_ms) is not int or not 0 < boundary_ms <= 57_600_000
                or boundary_ms % 100):
            raise ValueError('Liquidity lookup decision differs from its native session scope')
        arrays = self._arrays.get(ticker)
        if arrays is None:
            return None
        index = bisect_right(arrays[0], boundary_ms) - 1
        if index < 0:
            return None
        completed = int(arrays[0][index])
        if boundary_ms - completed >= 5000 or not arrays[1][index] or not arrays[2][index]:
            return None
        candles = tuple(LiquidityFadeCandle(completed-offset, int(arrays[3+i][index]))
                        for i, offset in enumerate((15_000, 10_000, 5000, 0)))
        return CompletedLiquidityFadeWindow(completed, candles)
