"""SELECT-only certification of producer-owned Strategy 1 pivot intervals.

Every requested candidate ticker must have exactly one coverage row pinned to
the certified bar attempt. A missing or changed product blocks Backtest before
navigation. No builder, repair, insert, or filesystem fallback is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from src.backend.backtest_market_data import CertifiedMarketDayPlan, market_day_boundary
from src.trading_runtime.strategy_one_pivot_product import (
    PivotInterval, interval_content_hash,
)
from src.trading_runtime.strategy_one_pivot_schema import (
    COVERAGE_TABLE, PIVOT_TABLE, PRODUCT_DIGEST, verify_tables,
)


@dataclass(frozen=True, slots=True)
class CertifiedPivotCoverage:
    ticker: str
    derivation_attempt_id: str
    bars_attempt_id: str
    interval_count: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class CertifiedPivotPlan:
    source_build_id: str
    session_date: str
    coverage: tuple[CertifiedPivotCoverage, ...]
    intervals: tuple[tuple[str, tuple[PivotInterval, ...]], ...]
    token: str


def _rows(client: Any, query: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(
        query + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _literal(value: str) -> str:
    from src.backend.backtest_market_data import _literal as market_literal
    return market_literal(value)


def certify_pivot_plan(
    market: CertifiedMarketDayPlan, *, session_date: str,
    candidate_tickers: tuple[str, ...], client: Any,
    batch_size: int = 256,
) -> CertifiedPivotPlan:
    """Reject any absent, duplicate, future, overlapping, or unsealed interval."""
    if (not isinstance(market, CertifiedMarketDayPlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or market.sessions != (session_date,)
            or not candidate_tickers
            or tuple(sorted(set(candidate_tickers))) != candidate_tickers
            or not set(candidate_tickers) <= set(market.tickers)
            or type(batch_size) is not int or not 1 <= batch_size <= 256):
        raise ValueError("Strategy 1 pivot plan lacks certified candidate scope")
    source = {}
    for unit in market.units:
        if unit.stage == "bars" and unit.session_date == session_date:
            if unit.ticker in source:
                raise ValueError("Strategy 1 pivot source has duplicate bar attempt")
            source[unit.ticker] = str(UUID(unit.attempt_id))
    if not set(candidate_tickers) <= set(source):
        raise ValueError("Strategy 1 pivot source lacks candidate bar attempt")
    verify_tables(client)
    coverage: list[CertifiedPivotCoverage] = []
    intervals: list[tuple[str, tuple[PivotInterval, ...]]] = []
    token = sha256(b"strategy-one-pivot-plan-v1\0")
    for value in (market.token, market.build_id, session_date, PRODUCT_DIGEST):
        token.update(value.encode())
        token.update(b"\0")
    origin_us = round(market_day_boundary(session_date, 0).timestamp() * 1_000_000)
    for offset in range(0, len(candidate_tickers), batch_size):
        batch = candidate_tickers[offset:offset + batch_size]
        selected = ",".join(_literal(ticker) for ticker in batch)
        facts = _rows(client, f"""SELECT ticker,
          toString(derivation_attempt_id) AS derivation_attempt_text,
          toString(bars_attempt_id) AS bars_attempt_text,
          product_digest,interval_count,content_hash
          FROM {COVERAGE_TABLE}
          WHERE source_build_id={_literal(market.build_id)}
            AND session_date=toDate({_literal(session_date)})
            AND ticker IN ({selected})""")
        if len(facts) != len(batch):
            raise RuntimeError("Strategy 1 pivot coverage is missing or duplicate")
        by_ticker = {}
        for fact in facts:
            ticker = str(fact["ticker"])
            if ticker not in batch or ticker in by_ticker:
                raise RuntimeError("Strategy 1 pivot coverage duplicates a ticker")
            attempt = str(UUID(fact["derivation_attempt_text"]))
            bars_attempt = str(UUID(fact["bars_attempt_text"]))
            count = int(fact["interval_count"])
            digest = str(fact["content_hash"])
            if (bars_attempt != source[ticker]
                    or fact["product_digest"] != PRODUCT_DIGEST
                    or not 0 <= count <= 100_000
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)):
                raise RuntimeError("Strategy 1 pivot coverage differs from authority")
            by_ticker[ticker] = CertifiedPivotCoverage(
                ticker, attempt, bars_attempt, count, digest)
        if set(by_ticker) != set(batch):
            raise RuntimeError("Strategy 1 pivot coverage omits a candidate ticker")
        for ticker in batch:
            fact = by_ticker[ticker]
            rows = _rows(client, f"""SELECT side,price_int,pivot_at_us,
              confirmed_at_us,valid_from_boundary_ms,valid_to_boundary_ms
              FROM {PIVOT_TABLE}
              WHERE source_build_id={_literal(market.build_id)}
                AND session_date=toDate({_literal(session_date)})
                AND ticker={_literal(ticker)}
                AND derivation_attempt_id=toUUID({_literal(fact.derivation_attempt_id)})
              ORDER BY valid_from_boundary_ms,side,price_int,pivot_at_us,
                       confirmed_at_us,valid_to_boundary_ms""")
            values = tuple(PivotInterval(
                str(row["side"]), int(row["price_int"]),
                int(row["pivot_at_us"]), int(row["confirmed_at_us"]),
                int(row["valid_from_boundary_ms"]),
                (None if row["valid_to_boundary_ms"] is None
                 else int(row["valid_to_boundary_ms"]))) for row in rows)
            if (len(values) != fact.interval_count
                    or interval_content_hash(values) != fact.content_hash):
                raise RuntimeError("Strategy 1 pivot rows differ from coverage")
            for item in values:
                if (item.pivot_at_us < origin_us
                        or item.confirmed_at_us >
                        origin_us + item.valid_from_boundary_ms * 1_000):
                    raise RuntimeError("Strategy 1 pivot visibility is noncausal")
            coverage.append(fact)
            intervals.append((ticker, values))
            for value in (ticker, fact.derivation_attempt_id,
                          fact.bars_attempt_id, str(fact.interval_count),
                          fact.content_hash):
                token.update(value.encode())
                token.update(b"\0")
    return CertifiedPivotPlan(market.build_id, session_date,
                              tuple(coverage), tuple(intervals), token.hexdigest())
