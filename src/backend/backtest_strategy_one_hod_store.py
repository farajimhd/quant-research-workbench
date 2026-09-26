"""SELECT-only certification of producer-owned Strategy 1 HOD context.

The certified row set must match every sparse candidate boundary exactly.
Missing coverage or an orphan child attempt blocks Backtest before navigation;
this module cannot build, repair, or insert the derived product.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID

from src.backend.backtest_market_data import CertifiedMarketDayPlan, _literal
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_one_hod_product import (
    HodContext, context_content_hash,
)
from src.trading_runtime.strategy_one_hod_schema import (
    CONTEXT_TABLE, COVERAGE_TABLE, PRODUCT_DIGEST, verify_tables,
)


@dataclass(frozen=True, slots=True)
class CertifiedHodPlan:
    source_build_id: str
    session_date: str
    contexts: tuple[tuple[str, tuple[HodContext, ...]], ...]
    token: str
    _index: dict[tuple[str, int], HodContext] = field(
        init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {(ticker, row.boundary_ms): row
                 for ticker, values in self.contexts for row in values}
        if len(index) != sum(len(values) for _, values in self.contexts):
            raise ValueError("Strategy 1 HOD plan contains duplicate candidate keys")
        object.__setattr__(self, "_index", index)

    def lookup(self, ticker: str, boundary_ms: int) -> HodContext:
        try:
            return self._index[(ticker, boundary_ms)]
        except KeyError as exc:
            raise ValueError("Strategy 1 candidate lacks certified HOD context") from exc


def _rows(client: Any, sql: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def certify_hod_plan(
    market: CertifiedMarketDayPlan, candidates: CertifiedCandidatePlan,
    seeds: CertifiedSeedPlan, *, client: Any,
    batch_size: int = 256,
) -> CertifiedHodPlan:
    """Seal exact candidate keys, scalar contents, and source lineage."""
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or not isinstance(seeds, CertifiedSeedPlan)
            or market.execution_interval.kind != "fixed"
            or market.execution_interval.milliseconds != 100
            or len(market.sessions) != 1
            or candidates.source_build_id != market.build_id
            or seeds.build_id != market.build_id
            or type(batch_size) is not int or not 1 <= batch_size <= 256):
        raise ValueError("Strategy 1 HOD plan lacks pinned source authority")
    session = market.sessions[0]
    prepared = {row.ticker: row for row in candidates.prepared}
    covered = {row.ticker: row for row in candidates.coverage
               if row.session_date == session and row.candidate_count > 0}
    if (not prepared or len(prepared) != len(candidates.prepared)
            or set(prepared) != set(covered)
            or not set(prepared) <= set(market.tickers)
            or any(len(prepared[ticker].boundary_ms) != covered[ticker].candidate_count
                   for ticker in prepared)):
        raise ValueError("Strategy 1 HOD candidate scope is incomplete")
    seeded = {row["ticker"] for row in seeds.units
              if row["backtest_session"] == session}
    if seeded != set(prepared):
        raise ValueError("Strategy 1 HOD seed scope differs from candidates")
    verify_tables(client)
    token = sha256(b"strategy-one-hod-plan-v1\0")
    for value in (market.token, candidates.token, seeds.token,
                  session, PRODUCT_DIGEST):
        token.update(value.encode())
        token.update(b"\0")
    contexts = []
    for offset in range(0, len(prepared), batch_size):
        tickers = tuple(sorted(prepared))[offset:offset + batch_size]
        selected = ",".join(_literal(ticker) for ticker in tickers)
        facts = _rows(client, f"""SELECT ticker,
          toString(derivation_attempt_id) AS derivation_attempt_text,
          toString(bars_attempt_id) AS bars_attempt_text,
          toString(candidate_attempt_id) AS candidate_attempt_text,
          candidate_content_hash,v7_seed_plan_token,product_digest,
          context_count,content_hash FROM {COVERAGE_TABLE}
          WHERE source_build_id={_literal(market.build_id)}
          AND session_date=toDate({_literal(session)})
          AND ticker IN ({selected})""")
        if len(facts) != len(tickers):
            raise RuntimeError("Strategy 1 HOD coverage is missing or duplicate")
        by_ticker = {}
        for fact in facts:
            ticker = str(fact["ticker"])
            if ticker not in tickers or ticker in by_ticker:
                raise RuntimeError("Strategy 1 HOD coverage duplicates a ticker")
            candidate = covered[ticker]
            attempt = str(UUID(fact["derivation_attempt_text"]))
            if (str(UUID(fact["bars_attempt_text"])) != candidate.source_attempts[0]
                    or str(UUID(fact["candidate_attempt_text"]))
                       != candidate.derivation_attempt_id
                    or fact["candidate_content_hash"] != candidate.content_hash
                    or fact["v7_seed_plan_token"] != seeds.token
                    or fact["product_digest"] != PRODUCT_DIGEST
                    or int(fact["context_count"]) != candidate.candidate_count
                    or re.fullmatch(r"[0-9a-f]{64}",
                                    str(fact["content_hash"])) is None):
                raise RuntimeError("Strategy 1 HOD coverage changed source lineage")
            by_ticker[ticker] = (attempt, str(fact["content_hash"]))
        if set(by_ticker) != set(tickers):
            raise RuntimeError("Strategy 1 HOD coverage omits a candidate ticker")
        attempt_keys = ",".join(
            f"({_literal(ticker)},toUUID({_literal(by_ticker[ticker][0])}))"
            for ticker in tickers)
        child_rows = _rows(client, f"""SELECT ticker,
          toString(derivation_attempt_id) AS derivation_attempt_text,
          boundary_ms,session_open_int,prior_hod_int,late_mode,gate_level_id
          FROM {CONTEXT_TABLE}
          WHERE source_build_id={_literal(market.build_id)}
          AND session_date=toDate({_literal(session)})
          AND (ticker,derivation_attempt_id) IN ({attempt_keys})
          ORDER BY ticker,boundary_ms""")
        by_child: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
        for row in child_rows:
            ticker = str(row["ticker"])
            if (ticker not in by_child
                    or row["derivation_attempt_text"] != by_ticker[ticker][0]):
                raise RuntimeError("Strategy 1 HOD child row lacks covered attempt")
            by_child[ticker].append(row)
        for ticker in tickers:
            attempt, expected_hash = by_ticker[ticker]
            rows = by_child[ticker]
            if any(int(row["late_mode"]) not in (0, 1) for row in rows):
                raise RuntimeError("Strategy 1 HOD late-mode flag is malformed")
            values = tuple(HodContext(
                int(row["boundary_ms"]), int(row["session_open_int"]),
                int(row["prior_hod_int"]), bool(int(row["late_mode"])),
                str(row["gate_level_id"])) for row in rows)
            if (tuple(item.boundary_ms for item in values)
                    != tuple(prepared[ticker].boundary_ms)
                    or context_content_hash(values) != expected_hash):
                raise RuntimeError("Strategy 1 HOD rows differ from candidate coverage")
            contexts.append((ticker, values))
            for value in (ticker, attempt, expected_hash):
                encoded = value.encode()
                token.update(len(encoded).to_bytes(4, "big"))
                token.update(encoded)
    return CertifiedHodPlan(market.build_id, session,
                            tuple(contexts), token.hexdigest())
