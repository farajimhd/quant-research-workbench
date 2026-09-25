"""Pure, bounded columnar entry prefilter for immutable Strategy 1.

STRATEGY CREATION RULES: source arrays are SELECTed from certified arte
products. This module never builds bars, indicators, or V7 structure. The
prefilter is a necessary condition only: activation, resistance breaks,
position state, broker fills, and shared cash stay in causal state machines.
Changing a published gate requires a new Strategy number, not an in-place edit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from .strategy_one_contract import closed_macd_candidate_mask


MACD_RESOLUTIONS_MS = (1_000, 5_000, 10_000, 30_000)
REJECT_PRICE = 1
REJECT_QUOTE = 2
REJECT_VWAP = 4
REJECT_PRIOR_CLOSE = 8
REJECT_MACD = 16
REJECT_STOP_BAR = 32


@dataclass(frozen=True, slots=True)
class CompletedMacd:
    boundary_ms: np.ndarray
    line: np.ndarray
    signal: np.ndarray


@dataclass(frozen=True, slots=True)
class CompletedThirtySecondLow:
    boundary_ms: np.ndarray
    low_int: np.ndarray
    price_valid: np.ndarray
    extremes_valid: np.ndarray


@dataclass(frozen=True, slots=True)
class StrategyOneCandidateBatch:
    evaluation_boundary_ms: np.ndarray
    entry_mask: np.ndarray
    rejection_bits: np.ndarray
    macd_boundary_ms: np.ndarray
    stop_bar_boundary_ms: np.ndarray
    stop_low_int: np.ndarray


@dataclass(frozen=True, slots=True)
class StrategyOneEntrySchedule:
    """Compact, causal entry work; position-owned work is scheduled separately."""

    row_index: np.ndarray
    evaluation_boundary_ms: np.ndarray
    episode_start_boundary_ms: np.ndarray


def schedule_strategy_one_entries(
    batch: StrategyOneCandidateBatch, episode_start_boundary_ms,
    *, episode_ttl_ms: int = 300_000,
) -> StrategyOneEntrySchedule:
    """Intersect completed-bar gates with certified Early Squeeze episodes.

    An episode remains active at its expiry boundary, matching the historical
    source-native activation rule. This is only an entry prefilter: an existing
    position and working orders must still visit every relevant market boundary.
    """
    if not isinstance(batch, StrategyOneCandidateBatch):
        raise ValueError("Strategy 1 schedule needs a candidate batch")
    evaluation = _clock(batch.evaluation_boundary_ms, resolution=100,
                        name="evaluation")
    eligible = _aligned(batch.entry_mask, len(evaluation), name="entry mask",
                        dtype=np.bool_)
    starts = _clock(episode_start_boundary_ms, resolution=100,
                    name="squeeze episode")
    if type(episode_ttl_ms) is not int or episode_ttl_ms != 300_000:
        raise ValueError("Strategy 1 requires the certified five-minute episode rule")
    if len(starts) > 1 and np.any(np.diff(starts) < episode_ttl_ms):
        raise ValueError("Strategy 1 squeeze episodes overlap")
    if not len(starts) or not len(evaluation):
        empty = np.empty(0, dtype=np.int64)
        return StrategyOneEntrySchedule(empty, empty, empty)
    index = np.searchsorted(starts, evaluation, side="right") - 1
    safe = np.maximum(index, 0)
    age = evaluation - starts[safe]
    rows = np.flatnonzero(eligible & (index >= 0)
                         & (age >= 0) & (age <= episode_ttl_ms))
    return StrategyOneEntrySchedule(rows, evaluation[rows], starts[index[rows]])


def _clock(values, *, resolution: int, name: str) -> np.ndarray:
    result = np.asarray(values)
    if result.ndim == 1 and not len(result):
        return np.empty(0, dtype=np.int64)
    if (result.ndim != 1 or result.dtype.kind not in "iu"
            or np.any(result <= 0) or np.any(result > 57_600_000)
            or np.any(result % resolution) or np.any(np.diff(result) <= 0)):
        raise ValueError(f"Strategy 1 {name} has invalid completed boundaries")
    return result.astype(np.int64, copy=False)


def _aligned(values, count: int, *, name: str, dtype) -> np.ndarray:
    result = np.asarray(values, dtype=dtype)
    if result.shape != (count,):
        raise ValueError(f"Strategy 1 {name} differs from evaluation boundaries")
    return result


def _asof(evaluation: np.ndarray, source: np.ndarray,
          resolution: int) -> tuple[np.ndarray, np.ndarray]:
    if not len(source):
        return np.zeros(len(evaluation), dtype=np.int64), np.zeros(len(evaluation), dtype=bool)
    index = np.searchsorted(source, evaluation, side="right") - 1
    safe = np.maximum(index, 0)
    age = evaluation - source[safe]
    ready = (index >= 0) & (age >= 0) & (age < resolution)
    return safe, ready


def prepare_strategy_one_entries(*, evaluation_boundary_ms,
                                 evaluation_epoch_us, close_int,
                                 price_valid, bid_int, ask_int,
                                 quote_valid, quote_timestamp_us,
                                 execution_vwap, previous_close,
                                 macd: Mapping[int, CompletedMacd],
                                 thirty_second_low: CompletedThirtySecondLow,
                                 quote_freshness_us: int = 1_000_000,
                                 ) -> StrategyOneCandidateBatch:
    """Vectorize only stateless necessary entry gates over one ticker/session.

    All as-of joins require a *completed* source boundary no more than one
    source period old. A missing 30s bucket is not backfilled from an older
    low. Position-owned state must be evaluated separately even when this
    entry mask is false.
    """
    if type(quote_freshness_us) is not int or not 0 < quote_freshness_us <= 1_000_000:
        raise ValueError("Strategy 1 quote freshness must be at most one second")
    evaluation = _clock(evaluation_boundary_ms, resolution=100, name="evaluation")
    n = len(evaluation)
    epoch = _aligned(evaluation_epoch_us, n, name="epoch", dtype=np.int64)
    if np.any(np.diff(epoch) <= 0):
        raise ValueError("Strategy 1 evaluation epoch is not causal")
    price = _aligned(close_int, n, name="close", dtype=np.int64)
    price_ready = _aligned(price_valid, n, name="price validity", dtype=np.uint8)
    bid = _aligned(bid_int, n, name="bid", dtype=np.int64)
    ask = _aligned(ask_int, n, name="ask", dtype=np.int64)
    quote_ready = _aligned(quote_valid, n, name="quote validity", dtype=np.uint8)
    quote_at = _aligned(quote_timestamp_us, n, name="quote clock", dtype=np.int64)
    vwap = _aligned(execution_vwap, n, name="execution VWAP", dtype=np.float64)
    prior = _aligned(previous_close, n, name="prior close", dtype=np.float64)
    if set(macd) != set(MACD_RESOLUTIONS_MS):
        raise ValueError("Strategy 1 requires the exact four completed MACD resolutions")
    source_clock = np.full((n, 4), -1, dtype=np.int64)
    lines = np.full((n, 4), np.nan, dtype=np.float64)
    signals = np.full((n, 4), np.nan, dtype=np.float64)
    for column, resolution in enumerate(MACD_RESOLUTIONS_MS):
        series = macd[resolution]
        source = _clock(series.boundary_ms, resolution=resolution,
                        name=f"{resolution}ms MACD")
        line = _aligned(series.line, len(source), name="MACD line", dtype=np.float64)
        signal = _aligned(series.signal, len(source), name="MACD signal", dtype=np.float64)
        index, ready = _asof(evaluation, source, resolution)
        if len(source):
            source_clock[ready, column] = source[index[ready]]
            lines[ready, column] = line[index[ready]]
            signals[ready, column] = signal[index[ready]]
    bullish = closed_macd_candidate_mask(lines, signals, source_clock, evaluation)
    lows = thirty_second_low
    low_clock = _clock(lows.boundary_ms, resolution=30_000, name="30s low")
    low_values = _aligned(lows.low_int, len(low_clock), name="30s low", dtype=np.int64)
    low_price_valid = _aligned(lows.price_valid, len(low_clock),
                               name="30s price validity", dtype=np.uint8)
    low_extremes_valid = _aligned(lows.extremes_valid, len(low_clock),
                                  name="30s extremes validity", dtype=np.uint8)
    low_index, low_ready = _asof(evaluation, low_clock, 30_000)
    selected_low = np.zeros(n, dtype=np.int64)
    selected_low_clock = np.full(n, -1, dtype=np.int64)
    if len(low_clock):
        low_ready &= ((low_values[low_index] > 0) & (low_price_valid[low_index] == 1)
                      & (low_extremes_valid[low_index] == 1))
        selected_low[low_ready] = low_values[low_index[low_ready]]
        selected_low_clock[low_ready] = low_clock[low_index[low_ready]]
    quote_age = epoch - quote_at
    # Candidate 350's purchase floor survives the closed-bar redesign:
    # a sub-$1 squeeze may stay watched, but cannot authorize a purchase.
    # Persisted prices are exact 1/10000-dollar integers at this gate.
    good_price = (price_ready == 1) & (price >= 10_000)
    good_quote = ((quote_ready == 1) & (bid > 0) & (ask >= bid)
                  & (quote_age >= 0) & (quote_age <= quote_freshness_us))
    good_vwap = np.isfinite(vwap) & (vwap > 0) & (vwap * 10_000 < price)
    good_prior = np.isfinite(prior) & (prior > 0) & (prior < 20)
    rejection = np.zeros(n, dtype=np.uint8)
    for bit, good in ((REJECT_PRICE, good_price), (REJECT_QUOTE, good_quote),
                      (REJECT_VWAP, good_vwap), (REJECT_PRIOR_CLOSE, good_prior),
                      (REJECT_MACD, bullish), (REJECT_STOP_BAR, low_ready)):
        rejection[~good] |= bit
    return StrategyOneCandidateBatch(evaluation, rejection == 0, rejection,
                                     source_clock, selected_low_clock, selected_low)
