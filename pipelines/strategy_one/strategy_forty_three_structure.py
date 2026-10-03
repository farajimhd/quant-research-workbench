"""Producer-only V7 witness at the one permitted Strategy 43 entry boundary.

Targets remain frozen after acquisition, so this derivative has no reason to
fit bars after admission. It is a point witness, not a full-session interval
product and cannot claim to describe structure at a later boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, iter_persisted_v7_seconds,
    market_day_boundary,
)
from src.backend.fixed_v7_stream import FixedV7Stream
from src.backend.structural_v7_seed import CertifiedSeedPlan, load_seed, split_evidence
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_v7_intervals import _geometry
from pipelines.strategy_one.v7_interval_derivation import _hash


@dataclass(frozen=True, slots=True)
class EntryStructure:
    build_id: str
    session_date: str
    ticker: str
    boundary_ms: int
    bars_attempt_id: str
    source_checkpoint_hash: str
    decoded_seed_hash: str
    seed_source_plan_hash: str
    split_evidence_hash: str
    seed_input_policy: str
    levels: tuple[tuple, ...]


def derive_entry(*, market, seeds, session_date, ticker, boundary_ms, reader):
    if (type(market) is not CertifiedMarketDayPlan or type(seeds) is not CertifiedSeedPlan
            or market.execution_interval.milliseconds != 100
            or seeds.build_id != market.build_id or session_date not in market.sessions
            or ticker not in market.tickers or ticker == "LGHL"
            or type(boundary_ms) is not int or boundary_ms % 1000
            or not 0 < boundary_ms < 19_800_000 - 10_000):
        raise ValueError("Strategy 43 entry structure needs its certified premarket boundary")
    bars = [unit for unit in market.units if unit.stage == "bars"
            and unit.session_date == session_date and unit.ticker == ticker]
    pinned = [unit for unit in seeds.units if unit["backtest_session"] == session_date
              and unit["ticker"] == ticker]
    if len(bars) != 1 or len(pinned) != 1:
        raise ValueError("Strategy 43 entry structure lacks exact bar/seed lineage")
    session = date.fromisoformat(session_date)
    coverage = pinned[0]
    prior = load_seed(reader, ticker=ticker, session=session, coverage=coverage)
    if prior.get("source_checkpoint_hash") != coverage["source_checkpoint_hash"]:
        raise ValueError("Strategy 43 entry structure seed differs from certification")
    splits = split_evidence(reader, ticker=ticker, seed_session=date.fromisoformat(prior["session"]),
                            session=session)
    stream = FixedV7Stream(prior, ticker=ticker, session=session, splits=splits, consume_seed=True)
    policy = str(prior["input_policy"]) if prior["levels"] else POLICY
    previous = SESSION_OPEN_OFFSET_MS // 1000 - 1
    for row in iter_persisted_v7_seconds(market, session_date=session_date, ticker=ticker,
                                       through_boundary_ms=boundary_ms, client=reader):
        bucket = row["bucket_index"]
        boundary = (bucket + 1) * 1000 - SESSION_OPEN_OFFSET_MS
        if (row.get("ticker") != ticker or row.get("resolution_ms") != 1000
                or type(bucket) is not int or bucket <= previous or not 0 < boundary <= boundary_ms):
            raise ValueError("Strategy 43 structure prefix contains future or unordered bars")
        previous = bucket
        if row.get("price_valid") == 1 and row.get("extremes_valid") == 1:
            stream.update_second(row, completed_second_ms=boundary)
    levels = tuple(_geometry(row) for row in stream.strategy_one_levels(
        as_of=market_day_boundary(session, boundary_ms), seed_policy=policy))
    return EntryStructure(market.build_id, session_date, ticker, boundary_ms,
        str(UUID(bars[0].attempt_id)), str(coverage["source_checkpoint_hash"]),
        str(prior["checkpoint_hash"]), str(coverage["source_plan_hash"]),
        _hash(splits), policy, levels)
