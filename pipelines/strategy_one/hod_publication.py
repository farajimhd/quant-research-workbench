"""Coverage-last publication of normalized Strategy 1 late-HOD context.

Producer authority only. Backtest never imports this module or writes the
derived table. An uncertain child insert is abandoned under its attempt UUID;
only exact read-back may authorize a coverage row.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date
import json
from typing import Any, Iterator
from uuid import UUID, uuid4

from pipelines.market_sip.events.market_day_sql import literal
from pipelines.strategy_one.hod_derivation import derive_hod_context
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS,
    iter_persisted_v7_seconds,
)
from src.backend.backtest_strategy_one_candidate_store import (
    CandidateCoverage, CertifiedCandidatePlan,
)
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.backend.fixed_v7_stream import FixedV7Stream
from src.backend.structural_v7_seed import (
    CertifiedSeedPlan, load_seed, split_evidence,
)
from src.market_engine.derived_trade_policy import POLICY
from src.trading_runtime.strategy_one_hod_product import (
    HodContext, context_content_hash,
)
from src.trading_runtime.strategy_one_hod_schema import (
    CONTEXT_TABLE, COVERAGE_TABLE, PRODUCT_DIGEST,
)


@dataclass(frozen=True, slots=True)
class HodPublicationScope:
    build_id: str
    session_date: str
    ticker: str
    bars_attempt_id: str
    candidate_attempt_id: str
    candidate_content_hash: str
    seed_plan_token: str
    candidate_boundaries: tuple[int, ...]


class HodReadbackMismatch(RuntimeError):
    """Safe scalar-only detail about an unpublished child attempt."""


def _rows(client: Any, sql: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _scope(market: CertifiedMarketDayPlan,
           candidates: CertifiedCandidatePlan,
           seeds: CertifiedSeedPlan, *, ticker: str) -> HodPublicationScope:
    if (market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or len(market.sessions) != 1
            or candidates.source_build_id != market.build_id
            or seeds.build_id != market.build_id):
        raise ValueError("Strategy 1 HOD producer lacks pinned fixed authority")
    session = market.sessions[0]
    prepared = [row for row in candidates.prepared if row.ticker == ticker]
    coverage = [row for row in candidates.coverage
                if row.ticker == ticker and row.session_date == session]
    bars = [row for row in market.units if row.stage == "bars"
            and row.session_date == session and row.ticker == ticker]
    seed = [row for row in seeds.units if row["backtest_session"] == session
            and row["ticker"] == ticker]
    if (len(prepared) != 1 or len(coverage) != 1 or len(bars) != 1
            or len(seed) != 1 or coverage[0].candidate_count <= 0
            or len(prepared[0].boundary_ms) != coverage[0].candidate_count
            or str(UUID(bars[0].attempt_id)) != coverage[0].source_attempts[0]):
        raise ValueError("Strategy 1 HOD ticker lacks exact source coverage")
    clocks = tuple(int(value) for value in prepared[0].boundary_ms)
    if (any(value <= 0 or value > 57_600_000 or value % 100
            for value in clocks)
            or any(a >= b for a, b in zip(clocks, clocks[1:]))):
        raise ValueError("Strategy 1 HOD candidate clocks are not ordered")
    return HodPublicationScope(
        market.build_id, session, ticker,
        str(UUID(bars[0].attempt_id)),
        str(UUID(coverage[0].derivation_attempt_id)),
        coverage[0].content_hash, seeds.token, clocks)


def _coverage(client: Any, scope: HodPublicationScope) -> list[dict]:
    return _rows(client, f"""SELECT
      toString(derivation_attempt_id) AS derivation_attempt_text,
      toString(bars_attempt_id) AS bars_attempt_text,
      toString(candidate_attempt_id) AS candidate_attempt_text,
      candidate_content_hash,v7_seed_plan_token,product_digest,
      context_count,content_hash FROM {COVERAGE_TABLE}
      WHERE source_build_id={literal(scope.build_id)}
      AND session_date=toDate({literal(scope.session_date)})
      AND ticker={literal(scope.ticker)}""")


def _child(client: Any, scope: HodPublicationScope,
           attempt: str) -> tuple[HodContext, ...]:
    rows = _rows(client, f"""SELECT boundary_ms,session_open_int,
      prior_hod_int,late_mode,gate_level_id FROM {CONTEXT_TABLE}
      WHERE source_build_id={literal(scope.build_id)}
      AND session_date=toDate({literal(scope.session_date)})
      AND ticker={literal(scope.ticker)}
      AND derivation_attempt_id=toUUID({literal(attempt)})
      ORDER BY boundary_ms""")
    if any(int(row["late_mode"]) not in (0, 1) for row in rows):
        raise RuntimeError("Published HOD late-mode flag is malformed")
    return tuple(HodContext(
        int(row["boundary_ms"]), int(row["session_open_int"]),
        int(row["prior_hod_int"]), bool(int(row["late_mode"])),
        str(row["gate_level_id"])) for row in rows)


def _verify_existing(client: Any, scope: HodPublicationScope) -> str | None:
    covered = _coverage(client, scope)
    if len(covered) > 1:
        raise RuntimeError("Strategy 1 HOD producer found duplicate coverage")
    if not covered:
        return None
    row = covered[0]
    attempt = str(UUID(row["derivation_attempt_text"]))
    if (str(UUID(row["bars_attempt_text"])) != scope.bars_attempt_id
            or str(UUID(row["candidate_attempt_text"]))
               != scope.candidate_attempt_id
            or row["candidate_content_hash"] != scope.candidate_content_hash
            or row["v7_seed_plan_token"] != scope.seed_plan_token
            or row["product_digest"] != PRODUCT_DIGEST
            or int(row["context_count"]) != len(scope.candidate_boundaries)):
        raise RuntimeError("Published HOD coverage differs from pinned sources")
    values = _child(client, scope, attempt)
    if (tuple(item.boundary_ms for item in values)
            != scope.candidate_boundaries
            or context_content_hash(values) != row["content_hash"]):
        raise RuntimeError("Published HOD child rows differ from coverage")
    return attempt


def _bars_100ms(client: Any, scope: HodPublicationScope) -> Iterator[dict]:
    last_bucket = (scope.candidate_boundaries[-1]
                   + SESSION_OPEN_OFFSET_MS) // 100
    sql = f"""SELECT ticker,resolution_ms,bucket_index,price_valid,
      extremes_valid,open_int,high_int,low_int,close_int
      FROM arte.bars_v1 WHERE build_id={literal(scope.build_id)}
      AND session_date=toDate({literal(scope.session_date)})
      AND ticker={literal(scope.ticker)}
      AND attempt_id=toUUID({literal(scope.bars_attempt_id)})
      AND resolution_ms=100
      AND bucket_index>={SESSION_OPEN_OFFSET_MS // 100}
      AND bucket_index<{last_bucket}
      ORDER BY bucket_index FORMAT JSONEachRow"""
    yield from client.iter_json_each_row(sql)


def _derive(reader_100ms: Any, reader_1s: Any,
            market: CertifiedMarketDayPlan, seeds: CertifiedSeedPlan,
            scope: HodPublicationScope) -> tuple[HodContext, ...]:
    session = date.fromisoformat(scope.session_date)
    pinned = next(row for row in seeds.units
                  if row["ticker"] == scope.ticker
                  and row["backtest_session"] == scope.session_date)
    seed = load_seed(reader_1s, ticker=scope.ticker,
                     session=session, coverage=pinned)
    splits = split_evidence(
        reader_1s, ticker=scope.ticker,
        seed_session=date.fromisoformat(seed["session"]), session=session)
    policy = str(pinned["input_policy"]) if int(pinned["level_count"]) else POLICY
    book = FixedV7Stream(seed, ticker=scope.ticker, session=session,
                         splits=splits, consume_seed=True)
    through = scope.candidate_boundaries[-1] // 1_000 * 1_000
    with closing(_bars_100ms(reader_100ms, scope)) as bars, closing(
            iter_persisted_v7_seconds(
                market, session_date=scope.session_date, ticker=scope.ticker,
                through_boundary_ms=through, client=reader_1s)) as seconds:
        values = derive_hod_context(
            bars, seconds, ticker=scope.ticker,
            session_date=scope.session_date,
            candidate_boundaries=scope.candidate_boundaries,
            stream=book, seed_policy=policy)
    context_content_hash(values)
    return values


def _insert_children(client: Any, scope: HodPublicationScope,
                     attempt: str, values: tuple[HodContext, ...]) -> None:
    columns = ("source_build_id,session_date,ticker,derivation_attempt_id,"
               "boundary_ms,session_open_int,prior_hod_int,late_mode,gate_level_id")
    for offset in range(0, len(values), 1_000):
        chunk = values[offset:offset + 1_000]
        rows = ",".join("(" + ",".join((
            literal(scope.build_id), f"toDate({literal(scope.session_date)})",
            literal(scope.ticker), f"toUUID({literal(attempt)})",
            str(item.boundary_ms), str(item.session_open_int),
            str(item.prior_hod_int), str(int(item.late_mode)),
            literal(item.gate_level_id),
        )) + ")" for item in chunk)
        client.execute(f"INSERT INTO {CONTEXT_TABLE} ({columns}) VALUES {rows}")


def publish_unit(writer: Any, reader_100ms: Any, reader_1s: Any,
                 market: CertifiedMarketDayPlan,
                 candidates: CertifiedCandidatePlan,
                 seeds: CertifiedSeedPlan, *, ticker: str) -> str:
    """Skip verified coverage, or publish a new read-back-sealed ticker attempt."""
    scope = _scope(market, candidates, seeds, ticker=ticker)
    if _verify_existing(writer, scope) is not None:
        return "skipped"
    values = _derive(reader_100ms, reader_1s, market, seeds, scope)
    digest = context_content_hash(values)
    attempt = str(uuid4())
    _insert_children(writer, scope, attempt, values)
    observed = _child(writer, scope, attempt)
    if observed != values or context_content_hash(observed) != digest:
        raise HodReadbackMismatch(
            f"child read-back expected_count={len(values)} "
            f"observed_count={len(observed)}")
    writer.execute(f"""INSERT INTO {COVERAGE_TABLE}
      (source_build_id,session_date,ticker,derivation_attempt_id,
       bars_attempt_id,candidate_attempt_id,candidate_content_hash,
       v7_seed_plan_token,product_digest,context_count,content_hash,certified_at)
      SELECT {literal(scope.build_id)},toDate({literal(scope.session_date)}),
        {literal(scope.ticker)},toUUID({literal(attempt)}),
        toUUID({literal(scope.bars_attempt_id)}),
        toUUID({literal(scope.candidate_attempt_id)}),
        {literal(scope.candidate_content_hash)},
        {literal(scope.seed_plan_token)},{literal(PRODUCT_DIGEST)},
        toUInt32({len(values)}),{literal(digest)},now64(6,'UTC')""")
    if _verify_existing(writer, scope) != attempt:
        raise RuntimeError("Strategy 1 HOD coverage publication failed verification")
    return "published"
