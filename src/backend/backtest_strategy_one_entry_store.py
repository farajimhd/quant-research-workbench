"""SELECT-only certification of producer-owned Strategy 1 entry evidence.

Coverage is authoritative only after all three normalized child tables have
been read back and reconciled with the certified candidate, activation, pivot,
HOD, and V7 seed plans. Backtest cannot build or repair a missing unit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from src.backend.backtest_market_data import CertifiedMarketDayPlan, _literal
from src.backend.backtest_strategy_one_activation import CertifiedActivationPlan
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_entry_product import (
    ActivationFact, CandidateFact, content_hash,
)
from src.backend.backtest_strategy_one_hod_store import CertifiedHodPlan
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_entry_evidence_schema import (
    ACTIVATION_TABLE, ACTIVATION_RESISTANCE_TABLE, COVERAGE_TABLE,
    EVIDENCE_TABLE, PRODUCT_DIGEST, verify_tables,
)


@dataclass(frozen=True, slots=True)
class EntryEvidenceCoverage:
    ticker: str
    derivation_attempt_id: str
    activation_count: int
    resistance_count: int
    evidence_count: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class CertifiedEntryEvidencePlan:
    source_build_id: str
    session_date: str
    coverage: tuple[EntryEvidenceCoverage, ...]
    activations: tuple[ActivationFact, ...]
    candidates: tuple[CandidateFact, ...]
    token: str
    _index: Mapping[tuple[str, int], CandidateFact] = field(
        init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {(row.ticker, row.boundary_ms): row for row in self.candidates}
        if len(index) != len(self.candidates):
            raise ValueError("Strategy 1 entry plan repeats a candidate boundary")
        object.__setattr__(self, "_index", MappingProxyType(index))

    def lookup(self, ticker: str, boundary_ms: int) -> CandidateFact:
        try:
            return self._index[(ticker, boundary_ms)]
        except KeyError as exc:
            raise ValueError("Strategy 1 candidate lacks certified entry evidence") from exc


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _identities(market: CertifiedMarketDayPlan,
                candidates: CertifiedCandidatePlan,
                activations: CertifiedActivationPlan,
                pivots: CertifiedPivotPlan, hod: CertifiedHodPlan,
                seeds: CertifiedSeedPlan) -> tuple[str, dict[str, Any]]:
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or not isinstance(activations, CertifiedActivationPlan)
            or not isinstance(pivots, CertifiedPivotPlan)
            or not isinstance(hod, CertifiedHodPlan)
            or not isinstance(seeds, CertifiedSeedPlan)
            or len(market.sessions) != 1
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or any(plan_id != market.build_id for plan_id in (
                candidates.source_build_id, pivots.source_build_id,
                hod.source_build_id, seeds.build_id))
            or pivots.session_date != market.sessions[0]
            or hod.session_date != market.sessions[0]
            or any(len(token) != 64 for token in (
                candidates.token, activations.token, pivots.token,
                hod.token, seeds.token))):
        raise ValueError("Strategy 1 entry product lacks pinned source plans")
    session = market.sessions[0]
    selected = {row.ticker: row for row in candidates.prepared}
    if not selected or len(selected) != len(candidates.prepared):
        raise ValueError("Strategy 1 entry product needs unique candidate tickers")
    source = {row.ticker: row for row in candidates.coverage
              if row.session_date == session and row.ticker in selected}
    bars = {row.ticker: row for row in market.units
            if row.stage == "bars" and row.session_date == session
            and row.ticker in selected}
    activation_rows = [row for row in activations.rows if row.ticker in selected]
    if (len(source) != len(selected) or len(bars) != len(selected)
            or len(source) != sum(row.session_date == session
                                 and row.ticker in selected
                                 for row in candidates.coverage)
            or len(bars) != sum(row.stage == "bars"
                               and row.session_date == session
                               and row.ticker in selected for row in market.units)
            or len({(row.ticker, row.boundary_ms) for row in activation_rows})
               != len(activation_rows)
            or {ticker for ticker, _ in pivots.intervals} != set(selected)
            or {ticker for ticker, _ in hod.contexts} != set(selected)
            or {row["ticker"] for row in seeds.units
                if row["backtest_session"] == session} != set(selected)):
        raise ValueError("Strategy 1 entry product source ticker sets differ")
    by_ticker: dict[str, Any] = {}
    for ticker, prepared in selected.items():
        candidate_coverage = source[ticker]
        starts = {int(value) for value in prepared.episode_start_ms}
        activation_prices = {row.boundary_ms: row.price_int
                             for row in activation_rows if row.ticker == ticker}
        if (candidate_coverage.candidate_count != len(prepared.boundary_ms)
                or not starts or set(activation_prices) != starts
                or candidate_coverage.source_attempts[0] !=
                   str(UUID(bars[ticker].attempt_id))):
            raise ValueError("Strategy 1 entry product source rows differ")
        by_ticker[ticker] = (prepared, candidate_coverage, bars[ticker],
                             activation_prices)
    return session, by_ticker


def certify_entry_evidence_plan(
    market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan,
    activations: CertifiedActivationPlan, pivots: CertifiedPivotPlan,
    hod: CertifiedHodPlan, seeds: CertifiedSeedPlan, *, client: Any,
    batch_size: int = 128, max_rows: int = 250_000,
) -> CertifiedEntryEvidencePlan:
    """Fail closed on missing, duplicate, orphan, stale, or mismatched rows."""
    session, source = _identities(
        market, candidates, activations, pivots, hod, seeds)
    if (type(batch_size) is not int or not 1 <= batch_size <= 256
            or type(max_rows) is not int or max_rows < 1
            or sum(len(unit[0].boundary_ms) for unit in source.values()) > max_rows):
        raise ValueError("Strategy 1 entry evidence exceeds bounded read scope")
    verify_tables(client)
    coverage: dict[str, dict[str, Any]] = {}
    tickers = tuple(sorted(source))
    for offset in range(0, len(tickers), batch_size):
        batch = tickers[offset:offset + batch_size]
        selected = ",".join(_literal(ticker) for ticker in batch)
        rows = _rows(client, f"""SELECT ticker,
          toString(derivation_attempt_id) AS attempt_id,
          toString(bars_attempt_id) AS bars_attempt_id,
          toString(candidate_attempt_id) AS candidate_attempt_id,
          candidate_content_hash,candidate_plan_token,activation_plan_token,
          pivot_plan_token,hod_plan_token,v7_seed_plan_token,product_digest,
          activation_count,resistance_count,evidence_count,content_hash
          FROM {COVERAGE_TABLE}
          WHERE source_build_id={_literal(market.build_id)}
          AND session_date=toDate({_literal(session)})
          AND ticker IN ({selected})""")
        for row in rows:
            ticker = str(row["ticker"])
            if ticker not in batch or ticker in coverage:
                raise ValueError("Strategy 1 entry coverage is duplicated or foreign")
            coverage[ticker] = row
    if set(coverage) != set(tickers):
        raise RuntimeError("Strategy 1 entry evidence coverage is incomplete")
    for ticker in tickers:
        row = coverage[ticker]
        prepared, candidate, bar, starts = source[ticker]
        if (str(UUID(row["attempt_id"])) != row["attempt_id"]
                or str(UUID(row["bars_attempt_id"])) != str(UUID(bar.attempt_id))
                or str(UUID(row["candidate_attempt_id"])) !=
                   str(UUID(candidate.derivation_attempt_id))
                or row["candidate_content_hash"] != candidate.content_hash
                or row["candidate_plan_token"] != candidates.token
                or row["activation_plan_token"] != activations.token
                or row["pivot_plan_token"] != pivots.token
                or row["hod_plan_token"] != hod.token
                or row["v7_seed_plan_token"] != seeds.token
                or row["product_digest"] != PRODUCT_DIGEST
                or int(row["activation_count"]) != len(starts)
                or int(row["evidence_count"]) != len(prepared.boundary_ms)
                or int(row["resistance_count"]) > 1024 * len(starts)):
            raise RuntimeError("Strategy 1 entry coverage differs from sources")

    children: dict[str, dict[str, list[dict[str, Any]]]] = {
        ticker: {"activation": [], "resistance": [], "evidence": []}
        for ticker in tickers}
    queries = (
        ("activation", ACTIVATION_TABLE,
         "episode_start_ms,price_int,average_gap,resistance_count"),
        ("resistance", ACTIVATION_RESISTANCE_TABLE,
         "episode_start_ms,ordinal,level_id"),
        ("evidence", EVIDENCE_TABLE,
         "boundary_ms,episode_start_ms,bos_break_boundary_ms,bos_pivot_id,"
         "bos_break_close_int,bos_support_kind,bos_support_level_id,"
         "bos_support_pivot_id,protection_valid,stop_price,target_price,"
         "target_level_id,target_ordinal"),
    )
    for offset in range(0, len(tickers), batch_size):
        batch = tickers[offset:offset + batch_size]
        pairs = ",".join(f"({_literal(ticker)},toUUID("
                         f"{_literal(coverage[ticker]['attempt_id'])}))"
                         for ticker in batch)
        for family, table, columns in queries:
            for row in _rows(client, f"""SELECT ticker,{columns} FROM {table}
                WHERE source_build_id={_literal(market.build_id)}
                AND session_date=toDate({_literal(session)})
                AND (ticker,derivation_attempt_id) IN ({pairs})"""):
                ticker = str(row["ticker"])
                if ticker not in batch:
                    raise RuntimeError("Strategy 1 entry child changed ticker scope")
                children[ticker][family].append(row)

    sealed = []
    all_activations: list[ActivationFact] = []
    all_candidates: list[CandidateFact] = []
    for ticker in tickers:
        groups = children[ticker]
        prepared, _, _, starts = source[ticker]
        activation_rows = sorted(groups["activation"],
                                 key=lambda row: int(row["episode_start_ms"]))
        resistance_rows = sorted(groups["resistance"],
                                 key=lambda row: (int(row["episode_start_ms"]),
                                                  int(row["ordinal"])))
        fact_activations = []
        index = 0
        for row in activation_rows:
            start = int(row["episode_start_ms"])
            count = int(row["resistance_count"])
            child = resistance_rows[index:index + count]
            if (len(child) != count or start not in starts
                    or int(row["price_int"]) != starts[start]
                    or any(int(item["episode_start_ms"]) != start
                           or int(item["ordinal"]) != ordinal
                           for ordinal, item in enumerate(child, 1))):
                raise RuntimeError("Strategy 1 activation children differ from source")
            index += count
            fact_activations.append(ActivationFact(
                ticker, start, int(row["price_int"]),
                float(row["average_gap"]) if row["average_gap"] is not None else None,
                tuple(str(item["level_id"]) for item in child)))
        if index != len(resistance_rows):
            raise RuntimeError("Strategy 1 activation has orphan resistance rows")
        evidence_rows = sorted(groups["evidence"],
                               key=lambda row: int(row["boundary_ms"]))
        fact_candidates = []
        for row in evidence_rows:
            validity = int(row["protection_valid"])
            if validity not in (0, 1):
                raise RuntimeError("Strategy 1 protection flag is invalid")
            fact_candidates.append(CandidateFact(
                ticker, int(row["boundary_ms"]), int(row["episode_start_ms"]),
                int(row["bos_break_boundary_ms"])
                if row["bos_break_boundary_ms"] is not None else None,
                str(row["bos_pivot_id"]),
                int(row["bos_break_close_int"])
                if row["bos_break_close_int"] is not None else None,
                str(row["bos_support_kind"]), str(row["bos_support_level_id"]),
                str(row["bos_support_pivot_id"]), bool(validity),
                float(row["stop_price"]) if row["stop_price"] is not None else None,
                float(row["target_price"])
                if row["target_price"] is not None else None,
                str(row["target_level_id"]),
                int(row["target_ordinal"])
                if row["target_ordinal"] is not None else None))
        if (tuple(item.boundary_ms for item in fact_candidates)
                != tuple(int(value) for value in prepared.boundary_ms)
                or tuple(item.episode_start_ms for item in fact_candidates)
                != tuple(int(value) for value in prepared.episode_start_ms)
                or {item.episode_start_ms for item in fact_activations}
                   != set(starts)
                or len(fact_activations) != int(coverage[ticker]["activation_count"])
                or len(resistance_rows) != int(coverage[ticker]["resistance_count"])
                or len(fact_candidates) != int(coverage[ticker]["evidence_count"])
                or content_hash(fact_activations, fact_candidates)
                   != coverage[ticker]["content_hash"]):
            raise RuntimeError("Strategy 1 entry child rows differ from coverage")
        all_activations.extend(fact_activations)
        all_candidates.extend(fact_candidates)
        sealed.append(EntryEvidenceCoverage(
            ticker, coverage[ticker]["attempt_id"], len(fact_activations),
            len(resistance_rows), len(fact_candidates),
            coverage[ticker]["content_hash"]))
    digest = sha256(b"strategy-one-entry-evidence-plan-v1\0")
    for value in (market.token, candidates.token, activations.token,
                  pivots.token, hod.token, seeds.token, PRODUCT_DIGEST):
        digest.update(value.encode())
    for row in sealed:
        for value in (row.ticker, row.derivation_attempt_id, row.content_hash):
            encoded = value.encode()
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    return CertifiedEntryEvidencePlan(
        market.build_id, session, tuple(sealed), tuple(all_activations),
        tuple(all_candidates), digest.hexdigest())
