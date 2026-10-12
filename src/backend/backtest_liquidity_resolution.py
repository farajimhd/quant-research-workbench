"""SELECT-only consumption of sealed broker liquidity at100ms or1s resolution.

This lane consumes producer coverage. It cannot build missing coverage, install
tables, or substitute higher-resolution price bars for executable liquidity.
The strategy decision interval is deliberately absent from this contract.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import math
import re
from uuid import UUID

from pipelines.market_sip.events import liquidity_resolution_sql as sql
from pipelines.market_sip.events.market_day_sql import literal
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.eligible_price_contract import volumes_match
from .qmd_columnar_history_transport import _owned_worker


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=str).encode()).hexdigest()


def _rows(reader, query):
    response = reader.execute(query + " FORMAT JSONEachRow")
    if not isinstance(response, str) or len(response.encode()) > 8 * 1024 * 1024:
        raise ValueError("Liquidity certification response exceeds its bound")
    return [json.loads(line) for line in response.splitlines() if line.strip()]


def _read_permission(reader):
    user = reader.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Liquidity reader identity is invalid")
    required = {("arte", table.name) for table in sql.TABLES}
    required |= {("system", name) for name in
                 ("storage_policies", "tables", "columns", "parts", "data_skipping_indices")}
    reads = set()
    for line in reader.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(r"GRANT SELECT ON ([A-Za-z_][A-Za-z0-9_]*)\."
                             r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(3) != user:
            raise ValueError("Liquidity consumer must have explicit SELECT-only grants")
        reads.add(match.group(1, 2))
    if not required <= reads:
        raise ValueError("Liquidity reader grants lack the certified product")


@dataclass(frozen=True)
class CertifiedLiquidityResolution:
    source: sql.LiquiditySourceUnit
    derivation_attempt_id: str
    producer_code_hash: str
    content_hash: str
    liquidity_row_count: int
    price_row_count: int
    total_execution_volume: float

    def __post_init__(self):
        if type(self.source) is not sql.LiquiditySourceUnit:
            raise TypeError("Liquidity certificate needs a typed pinned parent")
        if str(UUID(self.derivation_attempt_id)) != self.derivation_attempt_id:
            raise ValueError("Liquidity derivation UUID must be canonical")
        if any(re.fullmatch(r"[0-9a-f]{64}", value or "") is None
               for value in (self.producer_code_hash, self.content_hash)):
            raise ValueError("Liquidity certificate needs immutable hashes")
        if (type(self.liquidity_row_count) is not int or not 0 <= self.liquidity_row_count <= 57600
                or type(self.price_row_count) is not int or self.price_row_count < 0
                or not math.isfinite(self.total_execution_volume) or self.total_execution_volume < 0):
            raise ValueError("Liquidity certificate counts/volume are invalid")

    @property
    def resolution_ms(self):
        return 1000


def _coverage(reader, source):
    scope = (f"source_build_id={literal(source.build_id)} "
        f"AND session_date=toDate({literal(source.session_date.isoformat())}) "
        f"AND ticker={literal(source.ticker)} "
        f"AND source_attempt_id=toUUID({literal(source.source_attempt_id)}) AND resolution_ms=1000")
    columns = ",".join(name for name, _ in sql.COVERAGE.columns)
    rows = _rows(reader, f"SELECT {columns} FROM arte.{sql.COVERAGE.name} WHERE {scope} LIMIT 2")
    if len(rows) != 1:
        raise ValueError("One-second liquidity coverage is missing or duplicate")
    row = rows[0]
    if set(row) != {name for name, _ in sql.COVERAGE.columns}:
        raise ValueError("Liquidity coverage schema differs")
    for name, kind in sql.COVERAGE.columns:
        if kind.startswith("UInt"):
            row[name] = int(row[name])
        elif kind == "Float64":
            row[name] = float(row[name])
        elif kind == "Date":
            row[name] = date.fromisoformat(row[name])
    return row


def certify_liquidity_resolution(reader, source, *, producer_code_hash,
                                 expected_execution_volume):
    """Audit existing persisted children, without any publication capability."""
    if type(source) is not sql.LiquiditySourceUnit:
        raise TypeError("Liquidity preflight requires typed source pins")
    if (re.fullmatch(r"[0-9a-f]{64}", producer_code_hash or "") is None
            or not math.isfinite(expected_execution_volume) or expected_execution_volume < 0):
        raise ValueError("Liquidity preflight needs valid code and parent volume")
    _read_permission(reader)
    storage_preflight(reader, tables=sql.TABLES)
    row = _coverage(reader, source)
    expected = dict(source_build_id=source.build_id, session_date=source.session_date,
        ticker=source.ticker, source_attempt_id=source.source_attempt_id, resolution_ms=1000,
        source_price_attempt_id=source.source_price_attempt_id,
        source_market_unit_hash=source.source_market_unit_hash,
        source_price_unit_hash=source.source_price_unit_hash, producer_code_hash=producer_code_hash)
    if any(row[name] != value for name, value in expected.items()):
        raise ValueError("Liquidity coverage differs from pinned parents/code")
    if row["content_hash"] != _digest({k: v for k, v in row.items()
                                       if k not in {"content_hash", "certified_at"}}):
        raise ValueError("Liquidity coverage hash differs")
    certificate = CertifiedLiquidityResolution(source, row["derivation_attempt_id"],
        producer_code_hash, row["content_hash"], row["liquidity_row_count"],
        row["price_row_count"], row["total_execution_volume"])
    if _rows(reader, sql.bucket_parity_sql(source, certificate.derivation_attempt_id)):
        raise ValueError("Liquidity eligible-price bucket volumes differ")
    for table, count_field, hash_field in (
            (sql.LIQUIDITY, "liquidity_row_count", "liquidity_content_hash"),
            (sql.PRICE, "price_row_count", "price_content_hash")):
        key = "bucket_index" if table == sql.LIQUIDITY else "(bucket_index,price_int)"
        summaries = _rows(reader, f"SELECT count() AS row_count,uniqExact({key}) AS unique_keys,"
            "sum(execution_volume) AS total_execution_volume,"
            "toString(sum(cityHash64(tuple(*)))) AS row_hash "
            f"FROM arte.{table.name} WHERE {sql.target_scope(source, certificate.derivation_attempt_id)}")
        if len(summaries) != 1:
            raise ValueError("Liquidity child summary is ambiguous")
        child = summaries[0]
        count = int(child["row_count"])
        digest = _digest(dict(row_count=count, row_hash=str(child["row_hash"])))
        if (count != row[count_field] or count != int(child["unique_keys"])
                or digest != row[hash_field]
                or not volumes_match(child["total_execution_volume"], expected_execution_volume)
                or not volumes_match(row["total_execution_volume"], expected_execution_volume)):
            raise ValueError("Liquidity children differ from certified coverage/parent volume")
    if _coverage(reader, source) != row:
        raise ValueError("Liquidity seal changed during certification")
    return certificate


def liquidity_window_sql(certificate, *, first_bucket, stop_bucket):
    """Read a bounded completed-bucket window with exact eligible price levels."""
    if type(certificate) is not CertifiedLiquidityResolution:
        raise TypeError("Liquidity read requires its certified product")
    if (type(first_bucket) is not int or type(stop_bucket) is not int
            or not 14400 <= first_bucket < stop_bucket <= 72000
            or stop_bucket - first_bucket > 256):
        raise ValueError("Liquidity reads require at most 256 completed one-second buckets")
    scope = sql.target_scope(certificate.source, certificate.derivation_attempt_id)
    window = f" AND bucket_index>={first_bucket} AND bucket_index<{stop_bucket}"
    # Ephemeral result arrays represent normalized child rows; no array/JSON
    # column is persisted. Quote-only liquidity remains the driving relation.
    columns = ",".join("l." + name for name, _ in sql.LIQUIDITY.columns)
    return f"""SELECT {columns},p.execution_price_levels AS execution_price_levels
      FROM (SELECT * FROM arte.{sql.LIQUIDITY.name} WHERE {scope}{window}) l
      LEFT JOIN (SELECT bucket_index,arraySort(x->x.1,groupArray((price_int,execution_volume)))
        AS execution_price_levels FROM arte.{sql.PRICE.name} WHERE {scope}{window}
        GROUP BY bucket_index) p ON p.bucket_index=l.bucket_index
      ORDER BY l.bucket_index LIMIT 257"""


def read_liquidity_window(reader, certificate, *, first_bucket, stop_bucket):
    rows = _rows(reader, liquidity_window_sql(certificate, first_bucket=first_bucket,
                                              stop_bucket=stop_bucket))
    if len(rows) > stop_bucket - first_bucket:
        raise ValueError("Liquidity window has duplicate buckets")
    previous = first_bucket - 1
    for row in rows:
        if set(row) != {name for name, _ in sql.LIQUIDITY.columns} | {"execution_price_levels"}:
            raise ValueError("Liquidity window schema differs")
        for name, kind in sql.LIQUIDITY.columns:
            if kind.startswith("UInt"):
                row[name] = int(row[name])
                if not 0 <= row[name] < 2 ** int(kind[4:]):
                    raise ValueError("Liquidity integer is outside its declared type")
            elif kind == "Float64":
                row[name] = float(row[name])
                if not math.isfinite(row[name]):
                    raise ValueError("Liquidity numeric value is not finite")
        bucket = int(row["bucket_index"])
        if not previous < bucket < stop_bucket:
            raise ValueError("Liquidity window ordering differs")
        previous = bucket
        if (row["ticker"] != certificate.source.ticker or int(row["resolution_ms"]) != 1000
                or row["source_build_id"] != certificate.source.build_id
                or row["session_date"] != certificate.source.session_date.isoformat()
                or str(row["source_attempt_id"]) != certificate.source.source_attempt_id
                or str(row["derivation_attempt_id"]) != certificate.derivation_attempt_id):
            raise ValueError("Liquidity window identity differs")
        row["execution_price_levels"] = tuple(
            {"price_int": int(price), "volume": float(volume)}
            for price, volume in row["execution_price_levels"])
        levels = row["execution_price_levels"]
        prices = [level["price_int"] for level in levels]
        if (prices != sorted(set(prices)) or any(level["price_int"] <= 0
                or not math.isfinite(level["volume"]) or level["volume"] <= 0 for level in levels)
                or not volumes_match(sum(level["volume"] for level in levels), row["execution_volume"])):
            raise ValueError("Liquidity window eligible prices differ")
    return tuple(rows)


async def certify_liquidity_resolution_async(reader_factory, source, **pins):
    def run():
        reader = reader_factory()
        try:
            return certify_liquidity_resolution(reader, source, **pins)
        finally:
            reader.close()
    return await _owned_worker(run)


async def read_liquidity_window_async(reader_factory, certificate, **window):
    """An owned bounded read; never share a blocking connection across workers."""
    def run():
        reader = reader_factory()
        try:
            return read_liquidity_window(reader, certificate, **window)
        finally:
            reader.close()
    return await _owned_worker(run)


async def read_liquidity_cohort_async(reader_factory, certificates, *,
        first_bucket, stop_bucket, concurrency):
    """Read sealed per-ticker windows using the admitted bounded worker count.

    Every request is validated before opening any client. Sparse or empty rows
    stay explicit; no price bar or fill is fabricated for a missing bucket.
    """
    if (not callable(reader_factory) or not isinstance(certificates, (tuple, list))
            or not certificates or type(concurrency) is not int or not 1 <= concurrency <= 32
            or type(first_bucket) is not int or type(stop_bucket) is not int
            or len(certificates) * (stop_bucket - first_bucket) > 262144):
        raise ValueError("Liquidity cohort exceeds its explicit reader/concurrency bounds")
    certificates = tuple(certificates)
    if (any(type(row) is not CertifiedLiquidityResolution for row in certificates)
            or len({row.source.session_date for row in certificates}) != 1
            or len({row.source.ticker for row in certificates}) != len(certificates)):
        raise ValueError("Liquidity cohort needs unique tickers from one certified session")
    for certificate in certificates:
        liquidity_window_sql(certificate, first_bucket=first_bucket, stop_bucket=stop_bucket)
    async def read(certificate):
        return await read_liquidity_window_async(reader_factory, certificate,
            first_bucket=first_bucket, stop_bucket=stop_bucket)
    return await _read_liquidity_pool({row.source.ticker: row for row in certificates}, concurrency, read)


async def _read_liquidity_pool(scopes, concurrency, read):
    from types import MappingProxyType
    pending = iter(sorted(scopes.items()))
    results = {}
    async def worker():
        while True:
            item = next(pending, None)
            if item is None:
                return
            ticker, scope = item
            results[ticker] = await read(scope)
    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(scopes))):
            group.create_task(worker())
    return MappingProxyType({ticker: results[ticker] for ticker in sorted(results)})


async def read_fixed_liquidity_cohort_async(reader_factory, market_plan, price_plan, *,
        after_boundary_ms, through_boundary_ms, concurrency):
    """Bounded100ms windows from the existing certified market/price readers."""
    from .backtest_market_data import (CertifiedMarketDayPlan, project_market_day_plan,
        market_day_source_sqls, iter_market_day_rows, CompletedBoundaryValidator)
    from .backtest_liquidity_price import PriceLevelPlan
    if (not callable(reader_factory) or type(market_plan) is not CertifiedMarketDayPlan
            or type(price_plan) is not PriceLevelPlan or len(market_plan.sessions) != 1
            or not market_plan.tickers or len(set(market_plan.tickers)) != len(market_plan.tickers)
            or market_plan.required_resolutions_ms != (100,)
            or type(concurrency) is not int or not 1 <= concurrency <= 32
            or type(after_boundary_ms) is not int or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms - after_boundary_ms <= 256000
            or len(market_plan.tickers) * ((through_boundary_ms - after_boundary_ms) // 100) > 262144):
        raise ValueError("Fixed liquidity cohort exceeds its certified100ms scope or reader bounds")
    scopes = {}
    for ticker in market_plan.tickers:
        plan = project_market_day_plan(market_plan, (ticker,))
        prices = price_plan.projected(plan)
        market_day_source_sqls(plan, after_boundary_ms=after_boundary_ms,
            through_boundary_ms=through_boundary_ms, price_plan=prices)
        scopes[ticker] = (plan, prices)
    async def read(scope):
        def run():
            plan, prices = scope
            reader = reader_factory()
            stream = iter_market_day_rows(plan, reader, after_boundary_ms=after_boundary_ms,
                through_boundary_ms=through_boundary_ms, price_plan=prices)
            try:
                rows = []
                previous = after_boundary_ms
                validator = CompletedBoundaryValidator(plan)
                for row in stream:
                    boundary = row["boundary_ms"]
                    if not previous < boundary <= through_boundary_ms:
                        raise ValueError("Fixed liquidity cohort ordering or window differs")
                    validator.validate(row["session_date"], boundary, [(row["ticker"], {100: row})])
                    rows.append(row)
                    previous = boundary
                    if len(rows) > (through_boundary_ms - after_boundary_ms) // 100:
                        raise ValueError("Fixed liquidity cohort exceeds its completed-bucket bound")
                return tuple(rows)
            finally:
                stream.close()
                reader.close()
        return await _owned_worker(run)
    return await _read_liquidity_pool(scopes, concurrency, read)
