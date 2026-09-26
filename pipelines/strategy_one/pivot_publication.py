"""Coverage-last publication of normalized Strategy 1 structural pivots.

This is producer authority, never imported by Backtest. Source reads use the
certified pinned ARTE bar attempt. A failed/uncertain child insert is abandoned
under its UUID; only verified rows receive a separate coverage certificate.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID, uuid4

from pipelines.market_sip.events.market_day_sql import literal
from pipelines.strategy_one.pivot_derivation import derive_pivot_intervals
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, iter_persisted_v7_seconds,
)
from src.trading_runtime.strategy_one_pivot_product import (
    PivotInterval, interval_content_hash,
)
from src.trading_runtime.strategy_one_pivot_schema import (
    COVERAGE_TABLE, PIVOT_TABLE, PRODUCT_DIGEST,
)


@dataclass(frozen=True, slots=True)
class PivotPublicationScope:
    build_id: str
    session_date: str
    ticker: str
    bars_attempt_id: str


class PivotReadbackMismatch(RuntimeError):
    """Safe scalar-only evidence about an unpublished child attempt."""


def _scope(market: CertifiedMarketDayPlan, session_date: str,
           ticker: str) -> PivotPublicationScope:
    if (not isinstance(market, CertifiedMarketDayPlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or market.sessions != (session_date,)
            or ticker not in market.tickers):
        raise ValueError("Pivot producer needs a certified Strategy 1 market scope")
    matching = [unit for unit in market.units if unit.stage == "bars"
                and unit.session_date == session_date and unit.ticker == ticker]
    if len(matching) != 1:
        raise ValueError("Pivot producer lacks one pinned bar attempt")
    return PivotPublicationScope(market.build_id, session_date, ticker,
                                 str(UUID(matching[0].attempt_id)))


def _rows(client: Any, sql: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _coverage(client: Any, scope: PivotPublicationScope) -> list[dict]:
    return _rows(client, f"""SELECT
      toString(derivation_attempt_id) AS derivation_attempt_text,
      toString(bars_attempt_id) AS bars_attempt_text,
      product_digest,interval_count,content_hash
      FROM {COVERAGE_TABLE}
      WHERE source_build_id={literal(scope.build_id)}
        AND session_date=toDate({literal(scope.session_date)})
        AND ticker={literal(scope.ticker)}""")


def _child(client: Any, scope: PivotPublicationScope,
           attempt: str) -> tuple[PivotInterval, ...]:
    rows = _rows(client, f"""SELECT side,price_int,pivot_at_us,confirmed_at_us,
      valid_from_boundary_ms,valid_to_boundary_ms FROM {PIVOT_TABLE}
      WHERE source_build_id={literal(scope.build_id)}
        AND session_date=toDate({literal(scope.session_date)})
        AND ticker={literal(scope.ticker)}
        AND derivation_attempt_id=toUUID({literal(attempt)})
      ORDER BY valid_from_boundary_ms,toString(side),price_int,pivot_at_us,
               confirmed_at_us,valid_to_boundary_ms""")
    return tuple(PivotInterval(
        str(row["side"]), int(row["price_int"]), int(row["pivot_at_us"]),
        int(row["confirmed_at_us"]), int(row["valid_from_boundary_ms"]),
        (None if row["valid_to_boundary_ms"] is None
         else int(row["valid_to_boundary_ms"]))) for row in rows)


def _verify(client: Any, scope: PivotPublicationScope, *,
            expected: tuple[PivotInterval, ...], digest: str) -> str | None:
    certificates = _coverage(client, scope)
    if len(certificates) > 1:
        raise RuntimeError("Pivot producer found duplicate published coverage")
    if not certificates:
        return None
    fact = certificates[0]
    attempt = str(UUID(fact["derivation_attempt_text"]))
    if (str(UUID(fact["bars_attempt_text"])) != scope.bars_attempt_id
            or fact["product_digest"] != PRODUCT_DIGEST
            or int(fact["interval_count"]) != len(expected)
            or fact["content_hash"] != digest):
        raise RuntimeError("Published pivot coverage differs from pinned source")
    observed = _child(client, scope, attempt)
    if observed != expected or interval_content_hash(observed) != digest:
        raise RuntimeError("Published pivot intervals differ from coverage")
    return attempt


def _insert_intervals(client: Any, scope: PivotPublicationScope,
                      attempt: str, intervals: tuple[PivotInterval, ...]) -> None:
    columns = ("source_build_id,session_date,ticker,derivation_attempt_id,"
               "side,price_int,pivot_at_us,confirmed_at_us,"
               "valid_from_boundary_ms,valid_to_boundary_ms")
    for offset in range(0, len(intervals), 1_000):
        chunk = intervals[offset:offset + 1_000]
        values = ",".join("(" + ",".join((
            literal(scope.build_id), f"toDate({literal(scope.session_date)})",
            literal(scope.ticker), f"toUUID({literal(attempt)})",
            literal(item.side), str(item.price_int), str(item.pivot_at_us),
            str(item.confirmed_at_us), str(item.valid_from_boundary_ms),
            ("NULL" if item.valid_to_boundary_ms is None
             else str(item.valid_to_boundary_ms)),
        )) + ")" for item in chunk)
        client.execute(f"INSERT INTO {PIVOT_TABLE} ({columns}) VALUES {values}")


def publish_unit(writer: Any, reader: Any, market: CertifiedMarketDayPlan,
                 *, session_date: str, ticker: str) -> str:
    """Publish or re-verify one ticker-day, including an empty pivot set."""
    scope = _scope(market, session_date, ticker)
    with closing(iter_persisted_v7_seconds(
            market, session_date=session_date, ticker=ticker,
            through_boundary_ms=57_600_000, client=reader)) as bars:
        intervals = derive_pivot_intervals(
            bars, session_date=session_date, ticker=ticker)
    digest = interval_content_hash(intervals)
    if _verify(writer, scope, expected=intervals, digest=digest) is not None:
        return "skipped"
    attempt = str(uuid4())
    if intervals:
        _insert_intervals(writer, scope, attempt, intervals)
    observed = _child(writer, scope, attempt)
    if observed != intervals or interval_content_hash(observed) != digest:
        first = next((index for index, (expected, actual) in
                      enumerate(zip(intervals, observed)) if expected != actual),
                     min(len(intervals), len(observed)))
        expected = intervals[first] if first < len(intervals) else None
        actual = observed[first] if first < len(observed) else None
        raise PivotReadbackMismatch(
            f"child read-back expected_count={len(intervals)} "
            f"observed_count={len(observed)} first_index={first} "
            f"expected={expected!r} observed={actual!r}")
    writer.execute(f"""INSERT INTO {COVERAGE_TABLE}
      (source_build_id,session_date,ticker,derivation_attempt_id,bars_attempt_id,
       product_digest,interval_count,content_hash,certified_at)
      SELECT {literal(scope.build_id)},toDate({literal(scope.session_date)}),
        {literal(scope.ticker)},toUUID({literal(attempt)}),
        toUUID({literal(scope.bars_attempt_id)}),{literal(PRODUCT_DIGEST)},
        toUInt32({len(intervals)}),{literal(digest)},now64(6,'UTC')""")
    if _verify(writer, scope, expected=intervals, digest=digest) != attempt:
        raise RuntimeError("Pivot coverage publication failed exact verification")
    return "published"
