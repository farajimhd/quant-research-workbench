"""Restart-safe, coverage-last publication of sparse Strategy 1 candidates.

This is a producer authority, never imported by Backtest. It accepts only a
certified market-day plan and a completed columnar preparation result. The
caller must own ClickHouse write credentials scoped to these two new tables.
Existing bars, indicators, and liquidity are SELECT-only inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID, uuid4

from pipelines.market_sip.events.market_day_sql import literal
from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.backend.backtest_strategy_one_candidate_contract import (
    VALUE_FIELDS, candidate_content_hash, project_candidate_rows,
    validate_candidate_rows,
)
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.backend.fixed_bar_signal import first_squeeze_sql
from src.trading_runtime.strategy_one_candidate_schema import (
    CANDIDATE_TABLE, COVERAGE_TABLE, RULE_DIGEST,
)


_STAGES = ("bars", "technical", "broker_100ms")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class PublicationScope:
    build_id: str
    session_date: str
    ticker: str
    candidate_rule_digest: str
    scan_query_sha256: str
    source_attempts: tuple[str, str, str]
    source_rows: int


@dataclass(frozen=True, slots=True)
class CandidateScanAuthority:
    market_plan_token: str
    through_boundary_ms: int
    query_sha256: str


def candidate_scan_authority(market: CertifiedMarketDayPlan, *,
                             through_boundary_ms: int) -> CandidateScanAuthority:
    """Build the full-universe scan seal once, never once per ticker."""
    query = first_squeeze_sql(market, through_boundary_ms=through_boundary_ms)
    return CandidateScanAuthority(
        market.token, through_boundary_ms, sha256(query.encode()).hexdigest())


def _rows(client: Any, query: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        query + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _scope(market: CertifiedMarketDayPlan, *, session_date: str,
           ticker: str, candidate_rule_digest: str,
           scan_authority: CandidateScanAuthority) -> PublicationScope:
    if (market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or market.sessions != (session_date,)
            or ticker not in market.tickers
            or candidate_rule_digest != RULE_DIGEST
            or not isinstance(scan_authority, CandidateScanAuthority)
            or scan_authority.market_plan_token != market.token
            or type(scan_authority.through_boundary_ms) is not int
            or _HASH.fullmatch(scan_authority.query_sha256) is None):
        raise ValueError("Strategy 1 producer lacks certified fixed source scope")
    scan_query_sha256 = scan_authority.query_sha256
    stages = {}
    for unit in market.units:
        if unit.session_date == session_date and unit.ticker == ticker:
            if unit.stage in stages:
                raise ValueError("Strategy 1 producer source stage is duplicate")
            stages[unit.stage] = unit
    if set(stages) != set(_STAGES):
        raise ValueError("Strategy 1 producer source scope is incomplete")
    attempts = tuple(str(UUID(stages[stage].attempt_id)) for stage in _STAGES)
    count = int(stages["broker_100ms"].output_rows)
    if not 0 < count <= 1_000_000:
        raise ValueError("Strategy 1 producer liquidity row count is invalid")
    return PublicationScope(market.build_id, session_date, ticker,
                            candidate_rule_digest, scan_query_sha256,
                            attempts, count)


def _published(client: Any, scope: PublicationScope) -> list[dict[str, Any]]:
    return _rows(client, f"""SELECT
      toString(derivation_attempt_id) AS derivation_attempt_text,
      toString(bars_attempt_id) AS bars_attempt_text,
      toString(technical_attempt_id) AS technical_attempt_text,
      toString(liquidity_attempt_id) AS liquidity_attempt_text,
      candidate_rule_digest,scan_query_sha256,candidate_count,content_hash
      FROM {COVERAGE_TABLE}
      WHERE source_build_id={literal(scope.build_id)}
        AND session_date=toDate({literal(scope.session_date)})
        AND ticker={literal(scope.ticker)}
        AND scan_query_sha256={literal(scope.scan_query_sha256)}""")


def _read_child(client: Any, scope: PublicationScope,
                attempt: str) -> tuple[dict[str, Any], ...]:
    columns = ",".join(VALUE_FIELDS)
    found = _rows(client, f"""SELECT {columns} FROM {CANDIDATE_TABLE}
      WHERE source_build_id={literal(scope.build_id)}
        AND session_date=toDate({literal(scope.session_date)})
        AND ticker={literal(scope.ticker)}
        AND derivation_attempt_id=toUUID({literal(attempt)})
      ORDER BY boundary_ms""")
    return tuple({
        "source_build_id": scope.build_id, "session_date": scope.session_date,
        "ticker": scope.ticker, "derivation_attempt_id": attempt, **row,
    } for row in found)


def _verify(client: Any, scope: PublicationScope, *,
            expected_rows: tuple[dict[str, Any], ...],
            expected_hash: str) -> str | None:
    certificates = _published(client, scope)
    if len(certificates) > 1:
        raise RuntimeError("Strategy 1 candidate scope has multiple published attempts")
    if not certificates:
        return None
    fact = certificates[0]
    attempt = str(UUID(fact["derivation_attempt_text"]))
    attempts = tuple(fact[f"{name}_attempt_text"] for name in (
        "bars", "technical", "liquidity"))
    if (attempts != scope.source_attempts
            or fact["candidate_rule_digest"] != scope.candidate_rule_digest
            or fact["scan_query_sha256"] != scope.scan_query_sha256
            or int(fact["candidate_count"]) != len(expected_rows)
            or fact["content_hash"] != expected_hash):
        raise RuntimeError("Published Strategy 1 candidates differ from pinned inputs")
    rows = _read_child(client, scope, attempt)
    if (validate_candidate_rows(rows, source_rows=scope.source_rows)
            != expected_hash or len(rows) != len(expected_rows)):
        raise RuntimeError("Published Strategy 1 candidate rows differ from coverage")
    if tuple(tuple(row[name] for name in VALUE_FIELDS) for row in rows) != tuple(
            tuple(row[name] for name in VALUE_FIELDS) for row in expected_rows):
        raise RuntimeError("Published Strategy 1 candidate rows differ from preparation")
    return attempt


def _insert_rows(client: Any, rows: tuple[dict[str, Any], ...]) -> None:
    columns = ("source_build_id", "session_date", "ticker",
               "derivation_attempt_id", *VALUE_FIELDS)
    for start in range(0, len(rows), 1_000):
        chunk = rows[start:start + 1_000]
        values = ",".join("(" + ",".join((
            literal(row["source_build_id"]),
            f"toDate({literal(row['session_date'])})",
            literal(row["ticker"]),
            f"toUUID({literal(row['derivation_attempt_id'])})",
            *(str(row[name]) for name in VALUE_FIELDS),
        )) + ")" for row in chunk)
        client.execute(f"INSERT INTO {CANDIDATE_TABLE} "
                       f"({','.join(columns)}) VALUES {values}")


def publish_unit(client: Any, market: CertifiedMarketDayPlan, *,
                 session_date: str, ticker: str, candidate_rule_digest: str,
                 scan_authority: CandidateScanAuthority,
                 has_episode: bool,
                 prepared: PreparedStrategyOneTicker | None) -> str:
    """Publish one exact ticker-day or verify a completed prior publication.

    An uncertain child INSERT is never retried under the same attempt ID.
    A fresh retry leaves orphan rows unreferenced. Coverage is the only read
    admission authority, so an interrupted unit cannot leak into Backtest.
    """
    scope = _scope(market, session_date=session_date, ticker=ticker,
                   candidate_rule_digest=candidate_rule_digest,
                   scan_authority=scan_authority)
    if type(has_episode) is not bool or (prepared is not None) != has_episode:
        raise ValueError("Strategy 1 producer episode scope lacks exact preparation")
    if prepared is not None and (prepared.ticker != ticker
                                 or prepared.source_rows != scope.source_rows):
        raise ValueError("Strategy 1 producer preparation differs from source scope")
    # Derivation identity is not part of the content seal. Compute expected
    # scalar values under a provisional UUID, then allocate an attempt only
    # when no certified publication already exists.
    provisional = str(UUID(int=0))
    expected_rows = (project_candidate_rows(
        prepared, build_id=scope.build_id, session_date=session_date,
        derivation_attempt_id=provisional) if prepared is not None else ())
    expected_hash = candidate_content_hash(expected_rows)
    if _verify(client, scope, expected_rows=expected_rows,
               expected_hash=expected_hash) is not None:
        return "skipped"
    attempt = str(uuid4())
    rows = tuple({**row, "derivation_attempt_id": attempt}
                 for row in expected_rows)
    if rows:
        _insert_rows(client, rows)
    observed = _read_child(client, scope, attempt)
    if (len(observed) != len(rows)
            or validate_candidate_rows(observed, source_rows=scope.source_rows)
            != expected_hash):
        raise RuntimeError("Strategy 1 candidate child INSERT failed verification")
    client.execute(f"""INSERT INTO {COVERAGE_TABLE}
      (source_build_id,session_date,ticker,derivation_attempt_id,
       bars_attempt_id,technical_attempt_id,liquidity_attempt_id,
       candidate_rule_digest,scan_query_sha256,candidate_count,content_hash,certified_at)
      SELECT {literal(scope.build_id)},toDate({literal(scope.session_date)}),
        {literal(scope.ticker)},toUUID({literal(attempt)}),
        toUUID({literal(scope.source_attempts[0])}),
        toUUID({literal(scope.source_attempts[1])}),
        toUUID({literal(scope.source_attempts[2])}),
        {literal(scope.candidate_rule_digest)},
        {literal(scope.scan_query_sha256)},toUInt32({len(rows)}),
        {literal(expected_hash)},now64(6,'UTC')""")
    if _verify(client, scope, expected_rows=expected_rows,
               expected_hash=expected_hash) != attempt:
        raise RuntimeError("Strategy 1 candidate coverage publication was not exact")
    return "published"
