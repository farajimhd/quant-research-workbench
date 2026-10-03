"""SELECT-only certification and causal lookup of published liquidity windows."""
from dataclasses import dataclass
from types import MappingProxyType
import json
import struct
from uuid import UUID, uuid5, NAMESPACE_URL
from math import isfinite

from .backtest_market_data import _literal
from src.trading_runtime.strategy_forty_five_liquidity_schema import (
    FACT_TABLE, COVERAGE_TABLE, FACT_COLUMNS, PRODUCT_DIGEST, verify_tables)
from src.trading_runtime.strategy_forty_three_source_codec import scalar_hash
from src.trading_runtime.strategy_forty_five_liquidity import LiquidityFact


def rows(reader, sql):
    return [json.loads(line) for line in reader.execute(sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def scope(build, day, ticker, attempt=None):
    sql = f"source_build_id={_literal(build)} AND session_date=toDate({_literal(day)}) AND ticker={_literal(ticker)}"
    if attempt:
        sql += f" AND derivation_attempt_id=toUUID({_literal(attempt)})"
    return sql


def read_facts(reader, selected):
    columns = ["reinterpretAsUInt64(volume_300s) AS volume_bits" if name == "volume_300s" else name
               for name, _ in FACT_COLUMNS]
    result = rows(reader, f"SELECT {','.join(columns)} FROM {FACT_TABLE} WHERE {selected} ORDER BY boundary_ms")
    for row in result:
        bits = row.pop("volume_bits")
        row["volume_300s"] = None if bits is None else struct.unpack("<d", struct.pack("<Q", int(bits)))[0]
        for key in ("boundary_ms", "history_ready"):
            row[key] = int(row[key])
        if row["trades_60s"] is not None:
            row["trades_60s"] = int(row["trades_60s"])
    return result


@dataclass(frozen=True, slots=True)
class CertifiedLiquidityPlan:
    market_token: str
    token: str
    session_end_ms: int
    features: object

    def fact(self, ticker, decision_ms):
        if ticker not in self.features or not 0 < decision_ms <= self.session_end_ms or decision_ms % 1000:
            raise ValueError("Strategy 45 liquidity lookup outside its certified grid")
        completed = decision_ms // 30000 * 30000
        row = self.features[ticker][completed // 30000 - 1] if completed else None
        fact = LiquidityFact(ticker, decision_ms, completed, bool(row["history_ready"]) if row else False,
            row["trades_60s"] if row else None, row["volume_300s"] if row else None,
            row["fact_id"] if row else "", self.token)
        fact.validate()
        return fact


def certify_liquidity(plan, reader):
    verify_tables(reader)
    units = {u.ticker: u for u in plan.market.units if u.stage == "bars"}
    features, seals = {}, []
    for ticker in plan.tickers:
        selected = scope(plan.market.build_id, plan.market.sessions[0], ticker)
        published = rows(reader, f"SELECT * FROM {COVERAGE_TABLE} WHERE {selected}")
        if len(published) != 1:
            raise RuntimeError(f"Strategy 45 {ticker} liquidity coverage absent or ambiguous")
        seal = published[0]
        expected = dict(product_digest=PRODUCT_DIGEST, bars_attempt_id=units[ticker].attempt_id,
            source_market_token=plan.market.token, identity_token=plan.identity.token,
            snapshot_hash=plan.snapshot_hash, session_end_ms=plan.session_end_ms,
            fact_count=plan.session_end_ms // 30000)
        if any(str(seal.get(k)) != str(v) for k, v in expected.items()):
            raise RuntimeError(f"Strategy 45 {ticker} liquidity parent differs")
        attempt = str(UUID(seal["derivation_attempt_id"]))
        facts = read_facts(reader, scope(plan.market.build_id, plan.market.sessions[0], ticker, attempt))
        if len(facts) != expected["fact_count"] or scalar_hash(facts) != seal["fact_hash"]:
            raise RuntimeError(f"Strategy 45 {ticker} liquidity children differ")
        for i, row in enumerate(facts, 1):
            boundary = i * 30000
            identity = str(uuid5(NAMESPACE_URL, f"strategy-45-liquidity:{plan.market.build_id}:{plan.market.sessions[0]}:{ticker}:{attempt}:{boundary}"))
            if row["boundary_ms"] != boundary or row["fact_id"] != identity or row["history_ready"] != int(i >= 10):
                raise RuntimeError("Strategy 45 liquidity grid/identity differs")
            if ((i < 2) != (row["trades_60s"] is None) or (i < 10) != (row["volume_300s"] is None)
                    or row["trades_60s"] is not None and row["trades_60s"] < 0
                    or row["volume_300s"] is not None and (not isfinite(row["volume_300s"]) or row["volume_300s"] < 0)):
                raise RuntimeError("Strategy 45 liquidity window validity differs")
        features[ticker] = tuple(MappingProxyType(r) for r in facts)
        seals.append({k: v for k, v in seal.items() if k != "certified_at"})
    return CertifiedLiquidityPlan(plan.market.token, scalar_hash(seals), plan.session_end_ms, MappingProxyType(features))
