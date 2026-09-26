"""SELECT-only certification of producer-owned Strategy 1 candidate rows.

No missing-product fallback exists. The producer publishes coverage last; an
orphan child attempt is never visible. Backtest checks exact pinned source
attempts, completed clocks, row counts, and an exact scalar content seal.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID

import numpy as np

from src.backend.backtest_market_data import CertifiedMarketDayPlan, _literal
from src.backend.backtest_strategy_one_candidate_contract import (
    VALUE_FIELDS, validate_candidate_rows,
)
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker
from src.backend.fixed_bar_signal import first_squeeze_sql
from src.trading_runtime.strategy_one_candidate_schema import (
    CANDIDATE_TABLE, COVERAGE_TABLE, RULE_DIGEST, STORAGE_POLICY,
)


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_STAGES = ("bars", "technical", "broker_100ms")


@dataclass(frozen=True, slots=True)
class CandidateCoverage:
    session_date: str
    ticker: str
    derivation_attempt_id: str
    source_attempts: tuple[str, str, str]
    candidate_count: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class CertifiedCandidatePlan:
    source_build_id: str
    candidate_rule_digest: str
    scan_query_sha256: str
    coverage: tuple[CandidateCoverage, ...]
    prepared: tuple[PreparedStrategyOneTicker, ...]
    token: str


def _rows(client: Any, query: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        query + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _token(build_id: str, digest: str, scan_query_sha256: str,
           coverage: tuple[CandidateCoverage, ...]) -> str:
    result = sha256(b"strategy-one-candidate-plan-v1\0")
    result.update(build_id.encode())
    result.update(digest.encode())
    result.update(scan_query_sha256.encode())
    for item in coverage:
        for value in (item.session_date, item.ticker, item.derivation_attempt_id,
                      *item.source_attempts, str(item.candidate_count),
                      item.content_hash):
            encoded = value.encode()
            result.update(len(encoded).to_bytes(4, "big"))
            result.update(encoded)
    return result.hexdigest()


def certify_candidate_plan(market: CertifiedMarketDayPlan, *,
                           candidate_rule_digest: str,
                           through_boundary_ms: int, client: Any,
                           batch_size: int = 512) -> CertifiedCandidatePlan:
    """Verify every ticker-day, including certified empty candidate sets."""
    if (market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or len(market.sessions) != 1
            or not market.units or candidate_rule_digest != RULE_DIGEST
            or type(through_boundary_ms) is not int
            or type(batch_size) is not int or not 1 <= batch_size <= 512):
        raise ValueError("Strategy 1 candidate plan lacks pinned fixed authority")
    scan_query_sha256 = sha256(first_squeeze_sql(
        market, through_boundary_ms=through_boundary_ms).encode()).hexdigest()
    names = tuple(table.split(".", 1)[1] for table in (CANDIDATE_TABLE, COVERAGE_TABLE))
    catalog = _rows(client, "SELECT name,storage_policy FROM system.tables "
                    "WHERE database='arte' AND name IN "
                    f"('{names[0]}','{names[1]}')")
    if (len(catalog) != 2 or {row.get("name") for row in catalog} != set(names)
            or any(row.get("storage_policy") != STORAGE_POLICY for row in catalog)):
        raise RuntimeError("Strategy 1 candidate tables lack SSD storage policy")
    misplaced = _rows(client, "SELECT table,disk_name FROM system.parts "
                      "WHERE active AND database='arte' AND table IN "
                      f"('{names[0]}','{names[1]}') "
                      f"AND disk_name!='{STORAGE_POLICY}' LIMIT 1")
    if misplaced:
        raise RuntimeError("Strategy 1 candidate active parts are outside SSD")
    scoped: dict[tuple[str, str], dict[str, Any]] = {}
    for unit in market.units:
        if unit.stage not in _STAGES:
            raise ValueError("Strategy 1 market plan contains an unsupported stage")
        stage_map = scoped.setdefault((unit.session_date, unit.ticker), {})
        if unit.stage in stage_map:
            raise ValueError("Strategy 1 market plan repeats a source attempt")
        stage_map[unit.stage] = unit
    expected = {(day, ticker) for day in market.sessions for ticker in market.tickers}
    if set(scoped) != expected or any(set(value) != set(_STAGES)
                                     for value in scoped.values()):
        raise ValueError("Strategy 1 market plan has incomplete source coverage")
    all_coverage: list[CandidateCoverage] = []
    all_prepared: list[PreparedStrategyOneTicker] = []
    ordered = sorted(scoped)
    for offset in range(0, len(ordered), batch_size):
        batch = ordered[offset:offset + batch_size]
        scopes = ",".join(f"(toDate({_literal(day)}),{_literal(ticker)}))"
                          for day, ticker in batch)
        facts = _rows(client, f"""SELECT session_date,ticker,
          toString(derivation_attempt_id) AS derivation_attempt_text,
          toString(bars_attempt_id) AS bars_attempt_text,
          toString(technical_attempt_id) AS technical_attempt_text,
          toString(liquidity_attempt_id) AS liquidity_attempt_text,
          candidate_rule_digest,scan_query_sha256,candidate_count,content_hash
          FROM {COVERAGE_TABLE}
          WHERE source_build_id={_literal(market.build_id)}
          AND scan_query_sha256={_literal(scan_query_sha256)}
          AND (session_date,ticker) IN ({scopes})""")
        if len(facts) != len(batch):
            raise RuntimeError("Strategy 1 candidate coverage is missing or duplicate")
        covered: dict[tuple[str, str], CandidateCoverage] = {}
        for fact in facts:
            key = (str(fact["session_date"]), str(fact["ticker"]))
            source = scoped.get(key)
            attempts = tuple(fact[f"{stage}_attempt_text"] for stage in (
                "bars", "technical", "liquidity"))
            if (key not in batch or key in covered or source is None
                    or fact["candidate_rule_digest"] != candidate_rule_digest
                    or fact["scan_query_sha256"] != scan_query_sha256
                    or attempts != tuple(source[stage].attempt_id
                                         for stage in _STAGES)
                    or not 0 <= int(fact["candidate_count"]) <=
                    int(source["broker_100ms"].output_rows)
                    or _HASH.fullmatch(str(fact["content_hash"])) is None):
                raise RuntimeError("Strategy 1 candidate coverage differs from pinned authority")
            attempt = str(UUID(fact["derivation_attempt_text"]))
            covered[key] = CandidateCoverage(
                key[0], key[1], attempt, attempts,
                int(fact["candidate_count"]), str(fact["content_hash"]))
        if set(covered) != set(batch):
            raise RuntimeError("Strategy 1 candidate coverage omits a ticker-day")
        attempts_sql = ",".join(
            f"(toDate({_literal(day)}),{_literal(ticker)},"
            f"toUUID({_literal(covered[day, ticker].derivation_attempt_id)}))"
            for day, ticker in batch)
        columns = ",".join(VALUE_FIELDS)
        child_rows = _rows(client, f"""SELECT session_date,ticker,
          toString(derivation_attempt_id) AS derivation_attempt_text,{columns}
          FROM {CANDIDATE_TABLE}
          WHERE source_build_id={_literal(market.build_id)}
          AND (session_date,ticker,derivation_attempt_id) IN ({attempts_sql})
          ORDER BY session_date,ticker,boundary_ms""")
        by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in child_rows:
            key = (str(row["session_date"]), str(row["ticker"]))
            unit = covered.get(key)
            if unit is None or row["derivation_attempt_text"] != unit.derivation_attempt_id:
                raise RuntimeError("Strategy 1 candidate child row lacks coverage")
            by_scope.setdefault(key, []).append({
                "source_build_id": market.build_id, "session_date": key[0],
                "ticker": key[1],
                "derivation_attempt_id": unit.derivation_attempt_id,
                **{name: row[name] for name in VALUE_FIELDS},
            })
        for key in batch:
            unit = covered[key]
            rows = tuple(by_scope.get(key, ()))
            source_rows = int(scoped[key]["broker_100ms"].output_rows)
            digest = validate_candidate_rows(rows, source_rows=source_rows)
            if len(rows) != unit.candidate_count or digest != unit.content_hash:
                raise RuntimeError(f"Strategy 1 candidate rows differ from coverage: {key}")
            all_coverage.append(unit)
            if rows:
                values = {name: np.fromiter((row[name] for row in rows),
                                            dtype=(np.uint64 if name == "stop_low_int"
                                                   else np.int64), count=len(rows))
                          for name in VALUE_FIELDS}
                macd = np.column_stack(tuple(values[f"macd_{label}_boundary_ms"]
                                        for label in ("1s", "5s", "10s", "30s")))
                all_prepared.append(PreparedStrategyOneTicker(
                    key[1], source_rows, values["source_row_index"],
                    values["boundary_ms"], values["episode_start_ms"], macd,
                    values["stop_30s_boundary_ms"], values["stop_low_int"]))
    coverage = tuple(all_coverage)
    return CertifiedCandidatePlan(market.build_id, candidate_rule_digest,
                                  scan_query_sha256, coverage,
                                  tuple(all_prepared),
                                  _token(market.build_id, candidate_rule_digest,
                                         scan_query_sha256, coverage))
