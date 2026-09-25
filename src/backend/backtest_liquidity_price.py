"""Read-only certification of passive-fill price levels for fixed Backtest.

The producer owns rows and coverage; Backtest only SELECTs pinned attempts.
No canonical event, builder, market write, or disk ledger is available here.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any
from uuid import UUID

from src.backend.backtest_market_data import CertifiedMarketDayPlan, _literal
from src.trading_runtime.eligible_price_contract import (
    matches_summary_digest, volumes_match,
)


_TABLE = "arte.liquidity_execution_price_100ms_v1"
_COVERAGE = "arte.liquidity_execution_price_coverage_v1"
PRICE_READ_TABLES = frozenset({
    "liquidity_execution_price_100ms_v1",
    "liquidity_execution_price_coverage_v1",
})
_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class PriceLevelUnit:
    session_date: str
    ticker: str
    source_attempt_id: str
    derivation_attempt_id: str
    price_row_count: int
    eligible_bucket_count: int
    total_execution_volume: float
    content_hash: str


@dataclass(frozen=True, slots=True)
class PriceLevelPlan:
    source_build_id: str
    units: tuple[PriceLevelUnit, ...]
    token: str

    def projected(self, market: CertifiedMarketDayPlan) -> "PriceLevelPlan":
        if self.source_build_id != market.build_id:
            raise ValueError("Price-level source build differs from market-day plan")
        selected = {(unit.session_date, unit.ticker) for unit in market.units
                    if unit.stage == "broker_100ms"}
        units = tuple(unit for unit in self.units
                      if (unit.session_date, unit.ticker) in selected)
        if len(units) != len(selected):
            raise ValueError("Projected price-level scope lacks certified coverage")
        return PriceLevelPlan(self.source_build_id, units, _token(self.source_build_id, units))


def _rows(client: Any, query: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        query + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _token(build_id: str, units: tuple[PriceLevelUnit, ...]) -> str:
    payload = [build_id, [[unit.session_date, unit.ticker,
        unit.source_attempt_id, unit.derivation_attempt_id, unit.price_row_count,
        unit.eligible_bucket_count, format(unit.total_execution_volume, ".17g"),
        unit.content_hash] for unit in units]]
    return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def certify_price_level_plan(market: CertifiedMarketDayPlan,
                             client: Any) -> PriceLevelPlan:
    """Certify exact child attempts in bounded grouped SELECTs before execution."""
    if market.execution_interval.kind != "fixed" or not market.units:
        raise ValueError("Price levels require a pinned fixed market-day plan")
    table_names = ("liquidity_execution_price_100ms_v1",
                   "liquidity_execution_price_coverage_v1")
    catalog = _rows(client, "SELECT name,storage_policy FROM system.tables "
                    "WHERE database='arte' AND name IN "
                    f"('{table_names[0]}','{table_names[1]}')")
    if (len(catalog) != 2 or {row["name"] for row in catalog} != set(table_names)
            or any(row["storage_policy"] != "live_market_ssd" for row in catalog)):
        raise RuntimeError("Eligible-price tables are missing or not on SSD policy")
    parts = _rows(client, "SELECT table,disk_name FROM system.parts "
                  "WHERE active AND database='arte' AND table IN "
                  f"('{table_names[0]}','{table_names[1]}') "
                  "AND disk_name!='live_market_ssd' LIMIT 1")
    if parts:
        raise RuntimeError("Eligible-price active parts are outside live_market_ssd")
    expected = {(unit.session_date, unit.ticker): unit.attempt_id
                for unit in market.units if unit.stage == "broker_100ms"}
    if not expected or len(expected) != sum(unit.stage == "broker_100ms"
                                       for unit in market.units):
        raise ValueError("Market-day liquidity attempts are missing or duplicate")
    results: list[PriceLevelUnit] = []
    ordered = sorted(expected.items())
    for offset in range(0, len(ordered), 128):
        batch = ordered[offset:offset + 128]
        scopes = ",".join(
            f"(toDate({_literal(day)}),{_literal(ticker)},toUUID({_literal(attempt)}))"
            for (day, ticker), attempt in batch)
        coverage = _rows(client, f"""SELECT session_date,ticker,
          toString(source_attempt_id) AS source_attempt_text,
          toString(derivation_attempt_id) AS derivation_attempt_text,
          price_row_count,eligible_bucket_count,total_execution_volume,content_hash
          FROM {_COVERAGE} WHERE source_build_id={_literal(market.build_id)}
          AND (session_date,ticker,source_attempt_id) IN ({scopes})""")
        if len(coverage) != len(batch):
            raise RuntimeError(
                "Eligible-price coverage is missing or duplicate: "
                f"expected {len(batch)}, found {len(coverage)}; "
                f"first scope {batch[0][0][0]} {batch[0][0][1]} "
                f"attempt {batch[0][1]}")
        covered = {}
        for row in coverage:
            key = (str(row["session_date"]), str(row["ticker"]))
            if key in covered or expected.get(key) != row["source_attempt_text"]:
                raise RuntimeError("Eligible-price coverage differs from pinned liquidity")
            UUID(row["derivation_attempt_text"])
            count = int(row["price_row_count"])
            buckets = int(row["eligible_bucket_count"])
            volume = float(row["total_execution_volume"])
            if (count < buckets or buckets < 0 or not math.isfinite(volume)
                    or volume < 0 or not _HASH.fullmatch(row["content_hash"])):
                raise RuntimeError("Eligible-price coverage contains invalid typed values")
            covered[key] = PriceLevelUnit(
                key[0], key[1], row["source_attempt_text"],
                row["derivation_attempt_text"], count, buckets, volume,
                row["content_hash"])
        if set(covered) != {key for key, _ in batch}:
            raise RuntimeError("Eligible-price coverage omits a pinned ticker-day")
        attempts = ",".join(
            f"(toDate({_literal(unit.session_date)}),{_literal(unit.ticker)},"
            f"toUUID({_literal(unit.source_attempt_id)}),"
            f"toUUID({_literal(unit.derivation_attempt_id)}))"
            for unit in covered.values())
        summaries = _rows(client, f"""SELECT session_date,ticker,
          toString(source_attempt_id) AS source_attempt_text,
          toString(derivation_attempt_id) AS derivation_attempt_text,
          count() AS row_count,uniqExact((bucket_index,price_int)) AS unique_keys,
          uniqExact(bucket_index) AS eligible_bucket_count,
          sum(execution_volume) AS total_execution_volume,
          toString(sum(cityHash64(tuple(*)))) AS row_hash
          FROM {_TABLE} WHERE source_build_id={_literal(market.build_id)}
          AND (session_date,ticker,source_attempt_id,derivation_attempt_id)
            IN ({attempts}) GROUP BY session_date,ticker,source_attempt_id,
            derivation_attempt_id""")
        seen = set()
        for row in summaries:
            key = (str(row["session_date"]), str(row["ticker"]))
            unit = covered.get(key)
            if (unit is None or key in seen
                    or row["source_attempt_text"] != unit.source_attempt_id
                    or row["derivation_attempt_text"] != unit.derivation_attempt_id
                    or int(row["row_count"]) != int(row["unique_keys"])
                    or int(row["row_count"]) != unit.price_row_count
                    or int(row["eligible_bucket_count"]) != unit.eligible_bucket_count
                    or not volumes_match(row["total_execution_volume"],
                                         unit.total_execution_volume)
                    or not matches_summary_digest(
                        row, content_hash=unit.content_hash,
                        published_volume=unit.total_execution_volume)):
                raise RuntimeError("Eligible-price rows differ from published coverage")
            seen.add(key)
        if seen != {key for key, unit in covered.items() if unit.price_row_count > 0}:
            raise RuntimeError("Eligible-price rows are missing from published coverage")
        results.extend(covered[key] for key, _ in batch)
    units = tuple(results)
    return PriceLevelPlan(market.build_id, units, _token(market.build_id, units))
