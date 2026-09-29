"""Recover completed-boundary broker quote caches from certified ARTE rows.

These are state snapshots, not fabricated quote events. Backtest recovery may
read only the pinned liquidity attempt; missing or changed rows fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from math import isfinite
import re
from typing import Any
from uuid import UUID

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal,
    assert_select_only, market_day_boundary,
)
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    BrokerMatchSnapshotRows, verify_broker_match_snapshot,
)


@dataclass(frozen=True, slots=True)
class CompletedBrokerQuote:
    ticker: str
    boundary_ms: int
    quote_timestamp_us: int
    bid: float
    ask: float
    bid_size: float
    ask_size: float


def load_completed_broker_quotes(
    client: Any, *, plan: CertifiedMarketDayPlan,
    broker: BrokerMatchSnapshotRows,
) -> dict[str, CompletedBrokerQuote]:
    """Read exact pinned quote-bearing buckets, never a latest mutable quote."""
    if (not isinstance(plan, CertifiedMarketDayPlan)
            or plan.execution_interval.kind != "fixed"
            or plan.execution_interval.milliseconds != 100
            or len(plan.sessions) != 1
            or not callable(getattr(client, "execute", None))):
        raise ValueError("Broker quote recovery needs one certified 100ms day")
    if (re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", plan.build_id) is None
            or not plan.token or not plan.tickers):
        raise ValueError("Broker quote recovery lacks a pinned market identity")
    verify_broker_match_snapshot(broker)
    root = broker.snapshot
    day = str(root["session_date"])
    if day != plan.sessions[0] or date.fromisoformat(day).isoformat() != day:
        raise RuntimeError("Broker quote recovery day differs from market plan")
    attempts = {(unit.session_date, unit.ticker): unit.attempt_id
                for unit in plan.units if unit.stage == "broker_100ms"}
    if (len(attempts) != sum(unit.stage == "broker_100ms" for unit in plan.units)
            or any(unit.build_id != plan.build_id
                   or unit.session_date != day or unit.ticker not in plan.tickers
                   or str(UUID(unit.attempt_id)) != unit.attempt_id
                   for unit in plan.units if unit.stage == "broker_100ms")):
        raise RuntimeError("Broker quote recovery has duplicate liquidity attempts")
    wanted = {}
    for ticker_row in broker.tickers:
        if not ticker_row["has_quote"]:
            continue
        ticker = str(ticker_row["ticker"])
        boundary = int(ticker_row["last_boundary_ms"])
        if ((day, ticker) not in attempts or ticker not in plan.tickers
                or boundary <= 0 or boundary % 100
                or boundary > root["boundary_ms"]
                or ticker in wanted):
            raise RuntimeError("Broker quote ticker lacks a pinned completed bucket")
        bucket = (boundary + SESSION_OPEN_OFFSET_MS) // 100 - 1
        wanted[ticker] = (boundary, bucket, int(ticker_row["quote_timestamp_us"]))
    if not wanted:
        return {}
    if len(wanted) > 512:
        raise RuntimeError("Broker quote recovery exceeds one bounded read")
    keys = ",".join(
        f"(toDate({_literal(day)}),{_literal(ticker)},"
        f"toUUID({_literal(attempts[(day, ticker)])}),{bucket})"
        for ticker, (_, bucket, _) in sorted(wanted.items()))
    columns = ("session_date,ticker,bucket_index,event_count,last_event_us,"
               "quote_timestamp_us,quote_valid,bid_int,ask_int,bid_size,ask_size")
    sql = assert_select_only(
        f"SELECT {columns} FROM arte.liquidity_100ms_v1 "
        f"WHERE build_id={_literal(plan.build_id)} "
        f"AND (session_date,ticker,attempt_id,bucket_index) IN ({keys}) "
        f"ORDER BY ticker LIMIT {len(wanted) + 1} FORMAT JSONEachRow")
    rows = [json.loads(line) for line in client.execute(sql).splitlines()
            if line.strip()]
    if len(rows) != len(wanted):
        raise RuntimeError("Broker quote recovery lacks exact liquidity rows")
    quotes = {}
    origin_us = round(market_day_boundary(day, 0).timestamp() * 1_000_000)
    for row in rows:
        ticker = str(row.get("ticker") or "")
        target = wanted.get(ticker)
        if target is None or ticker in quotes:
            raise RuntimeError("Broker quote recovery has a foreign or duplicate row")
        boundary, bucket, quote_us = target
        boundary_us = origin_us + boundary * 1000
        if (row.get("session_date") != day
                or row.get("bucket_index") != bucket
                or row.get("quote_valid") != 1
                or row.get("quote_timestamp_us") != quote_us
                or int(row.get("last_event_us") or 0) < boundary_us - 100_000
                or not 0 < quote_us <= int(row.get("last_event_us") or 0) < boundary_us
                or boundary_us - quote_us > 1_000_000
                or int(row.get("event_count") or 0) < 1):
            raise RuntimeError("Broker quote recovery differs from completed bucket")
        bid = int(row["bid_int"]) / 10_000
        ask = int(row["ask_int"]) / 10_000
        bid_size, ask_size = float(row["bid_size"]), float(row["ask_size"])
        if (bid <= 0 or ask < bid or not isfinite(bid_size)
                or not isfinite(ask_size) or min(bid_size, ask_size) < 0):
            raise RuntimeError("Broker quote recovery has invalid prices or sizes")
        quotes[ticker] = CompletedBrokerQuote(
            ticker, boundary, quote_us, bid, ask, bid_size, ask_size)
    return quotes
