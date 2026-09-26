"""Producer-only late-HOD context from pinned completed ARTE bars and V7.

The 100ms scan is performed once per candidate ticker, then only exact
candidate boundaries are retained. Backtest never imports this producer or
rescans the 100ms history. Missing buckets are gaps, not synthetic candles.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from src.backend.backtest_market_data import (
    SESSION_OPEN_OFFSET_MS, market_day_boundary,
)
from src.backend.fixed_v7_stream import FixedV7Stream
from src.trading_runtime.strategy_one_hod import (
    HodObservation, observe_completed_hod,
)
from src.trading_runtime.strategy_one_hod_product import HodContext


def _boundary(row: Mapping, *, ticker: str, resolution_ms: int,
              previous_bucket: int | None) -> tuple[int, int]:
    if not isinstance(row, Mapping):
        raise ValueError("Strategy 1 HOD source row is malformed")
    bucket = row.get("bucket_index")
    if (row.get("ticker") != ticker
            or row.get("resolution_ms") != resolution_ms
            or type(bucket) is not int
            or previous_bucket is not None and bucket <= previous_bucket
            or bucket < SESSION_OPEN_OFFSET_MS // resolution_ms
            or bucket >= (SESSION_OPEN_OFFSET_MS + 57_600_000) // resolution_ms):
        raise ValueError("Strategy 1 HOD source row is unpinned or unordered")
    return bucket, (bucket + 1) * resolution_ms - SESSION_OPEN_OFFSET_MS


def derive_hod_context(
    bars_100ms: Iterable[Mapping], seconds: Iterable[Mapping], *,
    ticker: str, session_date: str,
    candidate_boundaries: tuple[int, ...],
    stream: FixedV7Stream, seed_policy: str,
) -> tuple[HodContext, ...]:
    """Scan once in causal boundary order; retain only candidate scalars."""
    if (not ticker or ticker != ticker.upper()
            or not isinstance(stream, FixedV7Stream)
            or not isinstance(seed_policy, str) or not seed_policy
            or not isinstance(candidate_boundaries, tuple)
            or not candidate_boundaries
            or any(type(value) is not int or value <= 0 or value % 100
                   or value > 57_600_000
                   for value in candidate_boundaries)
            or any(left >= right for left, right in zip(
                candidate_boundaries, candidate_boundaries[1:]))):
        raise ValueError("Strategy 1 HOD producer needs ordered certified keys")
    day = date.fromisoformat(session_date)
    if stream.engine.session != session_date:
        raise ValueError("Strategy 1 HOD V7 stream differs from source session")
    seconds_iter = iter(seconds)
    next_second = next(seconds_iter, None)
    prior_second_bucket: int | None = None
    prior_bar_bucket: int | None = None
    state = HodObservation()
    output = []
    next_candidate = 0
    for source in bars_100ms:
        bucket, boundary = _boundary(
            source, ticker=ticker, resolution_ms=100,
            previous_bucket=prior_bar_bucket)
        prior_bar_bucket = bucket
        if boundary > candidate_boundaries[-1]:
            break
        while next_second is not None:
            second_bucket, second_boundary = _boundary(
                next_second, ticker=ticker, resolution_ms=1_000,
                previous_bucket=prior_second_bucket)
            if second_boundary > boundary:
                break
            prior_second_bucket = second_bucket
            if int(next_second.get("price_valid") or 0):
                if int(next_second.get("extremes_valid") or 0) != 1:
                    raise ValueError("Strategy 1 HOD V7 second lacks valid extremes")
                stream.update_second(next_second,
                    at=market_day_boundary(day, second_boundary))
            next_second = next(seconds_iter, None)
        levels = stream.strategy_one_levels(
            as_of=market_day_boundary(day, boundary),
            seed_policy=seed_policy)
        bar = dict(source, boundary_ms=boundary)
        state = observe_completed_hod(state, bar, admitted_levels=levels)
        if boundary == candidate_boundaries[next_candidate]:
            if source.get("price_valid") != 1:
                raise ValueError("Strategy 1 HOD candidate lacks a price bar")
            output.append(HodContext.from_observation(state))
            next_candidate += 1
            if next_candidate == len(candidate_boundaries):
                break
        elif boundary > candidate_boundaries[next_candidate]:
            raise ValueError("Strategy 1 HOD source omitted a candidate boundary")
    if next_candidate != len(candidate_boundaries):
        raise ValueError("Strategy 1 HOD source ended before all candidates")
    return tuple(output)
