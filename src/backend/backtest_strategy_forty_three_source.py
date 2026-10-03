"""SELECT-only certificate for normalized Strategy 43 completed history.

Source hashes alone never authorize trading. This reader checks them against
the independently certified market, identity and streaming V7 plans before
exposing a typed feature. It cannot install, repair or publish any product.
"""
from dataclasses import dataclass
import json
import struct
from types import MappingProxyType
from uuid import UUID

from src.backend.backtest_market_data import CertifiedMarketDayPlan, _literal
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.trading_runtime.strategy_forty_three_fact_schema import (
    FACT_COLUMNS, FACT_TABLE, POPULATION_TABLE, COVERAGE_TABLE, PRODUCT_DIGEST, verify_tables,
)
from src.trading_runtime.strategy_forty_three_source_codec import scalar_hash


def _rows(reader, sql):
    return [json.loads(line) for line in reader.execute(sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


@dataclass(frozen=True, slots=True)
class CertifiedFortyThreeHistory:
    build_id: str
    session_date: str
    market_token: str
    identity_token: str
    structure_token: str
    token: str
    populations: tuple
    features: object
    coverage: tuple

    def feature(self, ticker, boundary_ms):
        if type(boundary_ms) is not int or boundary_ms % 1000:
            raise ValueError("Strategy 43 feature requires a completed second")
        value = self.features.get(ticker)
        if value is None or value["boundary_ms"] != boundary_ms:
            raise ValueError("Strategy 43 feature is outside certified coverage")
        return value

    def fact_id(self, facts):
        if facts.source_token != self.token:
            raise ValueError("Strategy 43 fact reference crossed its certified plan")
        ticker = getattr(facts, "ticker", None)
        if ticker is None:
            raise ValueError("Group facts require an explicitly owned ticker for source resolution")
        seal = next((row for row in self.coverage if row["ticker"] == ticker), None)
        if seal is None or not 1000 <= facts.boundary_ms <= seal["session_end_ms"] or facts.boundary_ms % 1000:
            raise ValueError("Strategy 43 fact reference is outside its certified ticker grid")
        from uuid import NAMESPACE_URL, uuid5
        return str(uuid5(NAMESPACE_URL,
            f"strategy-43-fact:{self.build_id}:{self.session_date}:{ticker}:"
            f"{seal['derivation_attempt_id']}:{facts.boundary_ms}"))

    def load_active_history(self, ticker, reader):
        """Load one active ticker, rechecking its complete immutable seal.

        Preflight retains only first-signal facts for rejected candidates.
        Full histories are allocated only for tickers with native orders.
        """
        seal = next((row for row in self.coverage if row["ticker"] == ticker), None)
        if seal is None:
            raise ValueError("Strategy 43 active ticker is outside certified coverage")
        rows = _fact_rows(reader, _scope(self.build_id, self.session_date, ticker,
                                      seal["derivation_attempt_id"]))
        if len(rows) != seal["fact_count"] or scalar_hash(rows) != seal["fact_hash"]:
            raise RuntimeError("Strategy 43 active history changed after certification")
        return tuple(MappingProxyType(row) for row in rows)


def _scope(build, day, ticker, attempt):
    return (f"source_build_id={_literal(build)} AND session_date=toDate({_literal(day)}) "
            f"AND ticker={_literal(ticker)} AND derivation_attempt_id=toUUID({_literal(attempt)})")


def _fact_rows(reader, selected):
    columns = [f"reinterpretAsUInt64({name}) AS {name}_bits" if "Float64" in kind
               else f"toString({name}) AS {name}" if kind in {"Date", "UUID"} else name
               for name, kind in FACT_COLUMNS]
    rows = _rows(reader, f"SELECT {','.join(columns)} FROM {FACT_TABLE} WHERE {selected} ORDER BY boundary_ms")
    for row in rows:
        for name, kind in FACT_COLUMNS:
            if "Float64" in kind:
                bits = row.pop(name + "_bits")
                row[name] = None if bits is None else struct.unpack("<d", struct.pack("<Q", int(bits)))[0]
    return rows


def certify_history(*, market, identity, structure, candidate_tickers,
                    snapshot_hash, signal_query_hash, session_end_ms, reader):
    if (type(market) is not CertifiedMarketDayPlan or type(identity) is not CertifiedIdentityPlan
            or type(structure) is not CertifiedV7IntervalPlan
            or market.execution_interval.milliseconds != 100 or len(market.sessions) != 1
            or identity.source_build_id != market.build_id or identity.session_date != market.sessions[0]
            or identity.market_token != market.token or structure.source_build_id != market.build_id
            or structure.session_date != market.sessions[0]
            or not candidate_tickers or tuple(sorted(set(candidate_tickers))) != candidate_tickers
            or "LGHL" in candidate_tickers or not set(candidate_tickers) <= set(market.tickers)
            or not set(candidate_tickers) <= set(identity.tickers)
            or tuple(row.ticker for row in structure.coverage) != candidate_tickers
            or type(session_end_ms) is not int or session_end_ms % 1000
            or not 1000 <= session_end_ms <= 57_600_000):
        raise ValueError("Strategy 43 source plans are not aligned")
    for value in (snapshot_hash, signal_query_hash):
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Strategy 43 population/signal proof is invalid")
    verify_tables(reader)
    features, populations, seals = {}, [], []
    for ticker in candidate_tickers:
        where = (f"source_build_id={_literal(market.build_id)} AND "
            f"session_date=toDate({_literal(market.sessions[0])}) AND ticker={_literal(ticker)}")
        coverage = _rows(reader, f"SELECT * FROM {COVERAGE_TABLE} WHERE {where}")
        if len(coverage) != 1:
            raise RuntimeError(f"Strategy 43 {ticker} lacks exactly one history seal")
        seal = coverage[0]
        attempt = str(UUID(seal["derivation_attempt_id"]))
        unit = {row.stage: row for row in market.units if row.ticker == ticker
                and row.session_date == market.sessions[0]}
        expected = dict(product_digest=PRODUCT_DIGEST, source_market_token=market.token,
            source_v7_token=structure.token, bars_attempt_id=unit["bars"].attempt_id,
            liquidity_attempt_id=unit["broker_100ms"].attempt_id,
            session_end_ms=session_end_ms, fact_count=session_end_ms // 1000)
        if any(str(seal.get(key)) != str(value) for key, value in expected.items()):
            raise RuntimeError(f"Strategy 43 {ticker} history parent seal differs")
        selected = where + f" AND derivation_attempt_id=toUUID({_literal(attempt)})"
        population = _rows(reader, f"SELECT * FROM {POPULATION_TABLE} WHERE {selected}")
        for row in population:
            row["conid"], row["admission_ms"] = int(row["conid"]), int(row["admission_ms"])
        if (len(population) != 1 or scalar_hash(population) != seal["population_hash"]
                or population[0]["conid"] != identity.conid_for(ticker)
                or population[0]["source_snapshot_hash"] != snapshot_hash
                or population[0]["source_market_token"] != market.token
                or population[0]["source_signal_query_hash"] != signal_query_hash
                or not 0 < population[0]["admission_ms"] <= session_end_ms
                or population[0]["admission_ms"] % 1000):
            raise RuntimeError(f"Strategy 43 {ticker} tradable population proof differs")
        rows = _fact_rows(reader, selected)
        if (len(rows) != session_end_ms // 1000 or scalar_hash(rows) != seal["fact_hash"]
                or any(row["boundary_ms"] != (index + 1) * 1000
                    or row["observed"] not in (0, 1)
                    or row["swing_available_ms"] >= row["boundary_ms"]
                    or str(UUID(row["fact_id"])) != row["fact_id"]
                    for index, row in enumerate(rows))):
            raise RuntimeError(f"Strategy 43 {ticker} history children differ")
        populations.append(MappingProxyType(population[0]))
        features[ticker] = MappingProxyType(rows[population[0]["admission_ms"] // 1000 - 1])
        seals.append({key: value for key, value in seal.items() if key != "certified_at"})
    token = scalar_hash((dict(market=market.token, identity=identity.token,
        structure=structure.token, snapshot=snapshot_hash, signal=signal_query_hash), *seals))
    return CertifiedFortyThreeHistory(market.build_id, market.sessions[0], market.token,
        identity.token, structure.token, token, tuple(populations), MappingProxyType(features),
        tuple(MappingProxyType(row) for row in seals))
