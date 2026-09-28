"""Producer-side intraday V7 derivative from certified ARTE 1s bars.

This module is not imported by Backtest. It computes a ticker-day once using
the unchanged causal streaming fitter, then emits compact scalar intervals and
valid completed-second clocks for a separate coverage-last publisher.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS,
    iter_persisted_v7_seconds, market_day_boundary,
)
from src.backend.fixed_v7_stream import FixedV7Stream
from src.backend.structural_v7_seed import (
    CertifiedSeedPlan, load_seed, split_evidence,
)
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7_intervals import (
    V7IntervalProjector, V7LevelInterval,
)


@dataclass(frozen=True, slots=True)
class DerivedV7TickerDay:
    build_id: str
    session_date: str
    ticker: str
    bars_attempt_id: str
    source_checkpoint_hash: str
    decoded_seed_hash: str
    seed_source_plan_hash: str
    split_evidence_hash: str
    seed_input_policy: str
    valid_seconds: tuple[int, ...]
    intervals: tuple[V7LevelInterval, ...]


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def derive_ticker_day(*, market: CertifiedMarketDayPlan,
                      seeds: CertifiedSeedPlan, session_date: str,
                      ticker: str, reader: Any) -> DerivedV7TickerDay:
    """Causally replay only one pinned ticker-day, with no market write."""
    session = date.fromisoformat(session_date)
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(seeds, CertifiedSeedPlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or seeds.build_id != market.build_id
            or session_date not in market.sessions
            or ticker not in market.tickers
            or not callable(getattr(reader, "execute", None))
            or not callable(getattr(reader, "iter_json_each_row", None))):
        raise ValueError("V7 derivative needs certified fixed source authority")
    bars = [unit for unit in market.units
            if unit.stage == "bars" and unit.session_date == session_date
            and unit.ticker == ticker]
    pinned = [unit for unit in seeds.units
              if unit["backtest_session"] == session_date
              and unit["ticker"] == ticker]
    if len(bars) != 1 or len(pinned) != 1:
        raise ValueError("V7 derivative lacks one pinned bar and seed unit")
    bar_attempt = str(UUID(bars[0].attempt_id))
    coverage = pinned[0]
    prior = load_seed(reader, ticker=ticker, session=session, coverage=coverage)
    checkpoint_hash = str(coverage["source_checkpoint_hash"])
    decoded_hash = str(prior.get("checkpoint_hash") or "")
    if (prior.get("source_checkpoint_hash") != checkpoint_hash
            or re.fullmatch(r"[0-9a-f]{64}", decoded_hash) is None
            or re.fullmatch(r"[0-9a-f]{64}",
                            str(coverage["source_plan_hash"])) is None):
        raise ValueError("V7 derivative seed differs from its certified plan")
    split_rows = split_evidence(
        reader, ticker=ticker, seed_session=date.fromisoformat(prior["session"]),
        session=session)
    stream = FixedV7Stream(prior, ticker=ticker, session=session,
                           splits=split_rows, consume_seed=True)
    seed_policy = (str(prior["input_policy"]) if prior["levels"] else POLICY)
    projector = V7IntervalProjector()
    projector.observe(boundary_ms=0, levels=stream.strategy_one_levels(
        as_of=market_day_boundary(session, 0), seed_policy=seed_policy),
        valid_completed_second=False)
    revision = getattr(stream.engine, "_projection_revision", 0)
    previous_bucket = (SESSION_OPEN_OFFSET_MS // 1_000) - 1
    for row in iter_persisted_v7_seconds(
            market, session_date=session_date, ticker=ticker,
            through_boundary_ms=57_600_000, client=reader):
        bucket = int(row["bucket_index"])
        boundary = (bucket + 1) * 1_000 - SESSION_OPEN_OFFSET_MS
        if (row.get("ticker") != ticker or int(row["resolution_ms"]) != 1_000
                or bucket <= previous_bucket or not 0 < boundary <= 57_600_000):
            raise ValueError("V7 derivative source bar order or identity changed")
        previous_bucket = bucket
        if not (int(row.get("price_valid") or 0)
                and int(row.get("extremes_valid") or 0)):
            continue
        stream.update_second(row, completed_second_ms=boundary)
        now_revision = getattr(stream.engine, "_projection_revision", 0)
        if now_revision == revision:
            projector.observe_unchanged(boundary_ms=boundary)
        else:
            projector.observe(boundary_ms=boundary,
                levels=stream.strategy_one_levels(
                    as_of=market_day_boundary(session, boundary),
                    seed_policy=seed_policy),
                valid_completed_second=True)
            revision = now_revision
    clocks, intervals = projector.finish()
    return DerivedV7TickerDay(
        market.build_id, session_date, ticker, bar_attempt,
        checkpoint_hash, decoded_hash, str(coverage["source_plan_hash"]),
        _hash(split_rows), seed_policy, clocks, intervals)
