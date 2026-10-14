"""Operator-only coverage-last1s liquidity publication from certified ARTE.

No strategy or Backtest admission path may call this producer. The operator
supplies separate reader/writer clients and exclusive ownership. Failed or
ambiguous writes stop the unit; orphan attempts remain invisible. No DDL,
mutation, raw-event fallback, disk journal or assumed broker fill is available.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Protocol
from uuid import uuid4, UUID

from . import liquidity_resolution_sql as sql
from .market_day_sql import literal
from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, _verify_market_batch
from src.backend.backtest_liquidity_price import PriceLevelUnit
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.eligible_price_contract import matches_summary_digest, volumes_match
from src.market_engine.typed_product_insert import insert_product_rows


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=str).encode()).hexdigest()


def _rows(client, query):
    text = client.execute(query + " FORMAT JSONEachRow")
    if not isinstance(text, str) or len(text.encode()) > 8 * 1024 * 1024:
        raise ValueError("Liquidity certificate response exceeds its bounded contract")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@dataclass(frozen=True)
class CertifiedLiquidityInput:
    market: CertifiedMarketDayPlan
    market_unit: MarketDayUnit
    price_unit: PriceLevelUnit

    def __post_init__(self):
        m, p = self.market_unit, self.price_unit
        if (type(self.market) is not CertifiedMarketDayPlan or type(m) is not MarketDayUnit
                or type(p) is not PriceLevelUnit or m not in self.market.units
                or m.stage != "broker_100ms"
                or m.build_id != self.market.build_id or m.session_date not in self.market.sessions
                or m.ticker not in self.market.tickers
                or (p.session_date, p.ticker, p.source_attempt_id) != (
                    m.session_date, m.ticker, m.attempt_id)):
            raise ValueError("Liquidity input must bind the existing market/price certificates")

    @property
    def unit(self):
        return sql.LiquiditySourceUnit(self.market.build_id, date.fromisoformat(self.market_unit.session_date),
            self.market_unit.ticker, self.market_unit.attempt_id, self.price_unit.derivation_attempt_id,
            _digest({"build_id": self.market.build_id, "definition_hash": self.market.definition_hash,
                     "unit": asdict(self.market_unit)}), _digest(asdict(self.price_unit)))

    def verify(self, reader):
        _verify_market_batch(self.market, "broker_100ms", "liquidity_100ms_v1",
            self.market_unit.session_date, (self.market_unit,), reader)
        rows = _rows(reader, "SELECT count() AS row_count,"
            "uniqExact((bucket_index,price_int)) AS unique_keys,"
            "uniqExact(bucket_index) AS eligible_bucket_count,"
            "sum(execution_volume) AS total_execution_volume,"
            "toString(sum(cityHash64(tuple(*)))) AS row_hash "
            "FROM arte.liquidity_execution_price_100ms_v1 WHERE " + sql.source_scope(self.unit, price=True))
        if len(rows) != 1:
            raise ValueError("Liquidity price input summary is ambiguous")
        row, expected = rows[0], self.price_unit
        if (int(row["row_count"]) != expected.price_row_count
                or int(row["row_count"]) != int(row["unique_keys"])
                or int(row["eligible_bucket_count"]) != expected.eligible_bucket_count
                or not volumes_match(row["total_execution_volume"], expected.total_execution_volume)
                or not matches_summary_digest(row, content_hash=expected.content_hash,
                    published_volume=expected.published_volume_text or expected.total_execution_volume)):
            raise ValueError("Certified liquidity price input changed")


class ProducerOwnership(Protocol):
    def assert_owner(self) -> None: ...


def _scope(unit):
    return (f"source_build_id={literal(unit.build_id)} "
        f"AND session_date=toDate({literal(unit.session_date.isoformat())}) "
        f"AND ticker={literal(unit.ticker)} "
        f"AND source_attempt_id=toUUID({literal(unit.source_attempt_id)}) AND resolution_ms=1000")


def _coverage(reader, unit):
    columns = ",".join(name for name, _ in sql.COVERAGE.columns)
    rows = _rows(reader, f"SELECT {columns} FROM arte.{sql.COVERAGE.name} WHERE {_scope(unit)} LIMIT 2")
    if len(rows) > 1:
        raise ValueError("Liquidity source has multiple published derivations")
    for row in rows:
        for name, kind in sql.COVERAGE.columns:
            if kind.startswith("UInt"):
                row[name] = int(row[name])
            elif kind == "Float64":
                row[name] = float(row[name])
            elif kind == "Date":
                row[name] = date.fromisoformat(row[name])
    return rows


def _source_placement_preflight(reader):
    names = "'liquidity_100ms_v1','liquidity_execution_price_100ms_v1'"
    tables = _rows(reader, "SELECT name,engine,storage_policy FROM system.tables "
        f"WHERE database='arte' AND name IN ({names})")
    if ({row["name"] for row in tables} != {"liquidity_100ms_v1", "liquidity_execution_price_100ms_v1"}
            or len(tables) != 2 or any((row["engine"], row["storage_policy"]) != (
                "MergeTree", "live_market_ssd") for row in tables)):
        raise ValueError("Liquidity parent tables require the explicit SSD policy")
    if _rows(reader, "SELECT table,disk_name FROM system.parts "
            f"WHERE database='arte' AND table IN ({names}) AND active AND disk_name!='live_market_ssd' LIMIT 1"):
        raise ValueError("Liquidity parent parts are outside live_market_ssd")


def _summary(reader, table, unit, attempt):
    keys = "bucket_index" if table == sql.LIQUIDITY else "(bucket_index,price_int)"
    invalid = "execution_volume<=0 OR price_int=0" if table == sql.PRICE else (
        "event_count=0 OR first_event_us>last_event_us OR bucket_index<14400 OR bucket_index>=72000 "
        "OR (price_valid=1 AND (open_int=0 OR close_int=0)) "
        "OR (extremes_valid=1 AND (low_int=0 OR low_int>high_int)) "
        "OR (quote_valid=1 AND (quote_timestamp_us=0 OR quote_timestamp_us>last_event_us OR bid_int=0 OR ask_int<bid_int))")
    rows = _rows(reader, f"SELECT count() AS row_count,uniqExact({keys}) AS unique_keys,"
        "sum(execution_volume) AS total_execution_volume,"
        f"countIf(NOT isFinite(execution_volume) OR execution_volume<0 OR {invalid}) AS invalid_rows,"
        "toString(sum(cityHash64(tuple(*)))) AS row_hash "
        f"FROM arte.{table.name} WHERE {sql.target_scope(unit, attempt)}")
    if len(rows) != 1:
        raise ValueError("Liquidity product summary is ambiguous")
    row = rows[0]
    count, unique = int(row["row_count"]), int(row["unique_keys"])
    volume = float(row["total_execution_volume"])
    if count != unique or int(row["invalid_rows"]) or not math.isfinite(volume) or volume < 0:
        raise ValueError("Liquidity product has invalid/duplicate authoritative rows")
    # Exact individual float bits are already bound by the row hash. Aggregate
    # volume is checked separately using the existing eligible-volume tolerance.
    return dict(row_count=count, total_execution_volume=volume,
                content_hash=_digest(dict(row_count=count, row_hash=str(row["row_hash"]))))


def _verify_product(reader, unit, attempt, expected_volume):
    if _rows(reader, sql.bucket_parity_sql(unit, attempt)):
        raise ValueError("Liquidity product and eligible-price bucket volumes differ")
    liquid = _summary(reader, sql.LIQUIDITY, unit, attempt)
    price = _summary(reader, sql.PRICE, unit, attempt)
    if (not volumes_match(liquid["total_execution_volume"], price["total_execution_volume"])
            or not volumes_match(liquid["total_execution_volume"], expected_volume)):
        raise ValueError("Liquidity product lost or added eligible execution volume")
    return liquid, price


def _verify_coverage(reader, source, row, *, code_hash):
    unit = source.unit
    expected = dict(source_build_id=unit.build_id, session_date=unit.session_date,
        ticker=unit.ticker, source_attempt_id=unit.source_attempt_id,
        resolution_ms=1000, source_price_attempt_id=unit.source_price_attempt_id,
        source_market_unit_hash=unit.source_market_unit_hash,
        source_price_unit_hash=unit.source_price_unit_hash, producer_code_hash=code_hash)
    if any(row.get(name) != value for name, value in expected.items()):
        raise ValueError("Liquidity coverage parent/code identities differ")
    UUID(row["derivation_attempt_id"])
    if row["content_hash"] != _digest({k: v for k, v in row.items() if k not in {"content_hash", "certified_at"}}):
        raise ValueError("Liquidity coverage content hash differs")
    liquid, price = _verify_product(reader, unit, row["derivation_attempt_id"], source.price_unit.total_execution_volume)
    if (row["liquidity_row_count"] != liquid["row_count"] or row["price_row_count"] != price["row_count"]
            or row["liquidity_content_hash"] != liquid["content_hash"]
            or row["price_content_hash"] != price["content_hash"]
            or not volumes_match(row["total_execution_volume"], liquid["total_execution_volume"])):
        raise ValueError("Liquidity coverage differs from persisted children")


def publish_unit(reader, writer, source: CertifiedLiquidityInput, *,
                 ownership: ProducerOwnership, code_hash: str) -> str:
    """Dedicated operator lane; never silently repair an existing sealed unit."""
    if reader is writer or not isinstance(source, CertifiedLiquidityInput):
        raise ValueError("Liquidity publication needs separate certified reader/writer lanes")
    if (not isinstance(getattr(reader, "base_url", None), str)
            or reader.base_url != getattr(writer, "base_url", None)):
        raise ValueError("Liquidity reader/writer endpoints differ")
    if not isinstance(code_hash, str) or len(code_hash) != 64 or any(c not in "0123456789abcdef" for c in code_hash):
        raise ValueError("Liquidity producer code identity is invalid")
    storage_preflight(reader, tables=sql.TABLES)
    storage_preflight(writer, tables=sql.TABLES)
    publication_permission_preflight(reader, writable=False)
    publication_permission_preflight(writer, writable=True)
    _source_placement_preflight(reader)
    ownership.assert_owner()
    source.verify(reader)
    unit = source.unit
    existing = _coverage(reader, unit)
    if existing:
        _verify_coverage(reader, source, existing[0], code_hash=code_hash)
        ownership.assert_owner()
        return "skipped_verified"
    attempt = str(uuid4())
    for table in (sql.LIQUIDITY, sql.PRICE):
        ownership.assert_owner()
        writer.execute(sql.insert_sql(table, unit, attempt))
    liquid, price = _verify_product(reader, unit, attempt, source.price_unit.total_execution_volume)
    source.verify(reader)
    if _coverage(reader, unit):
        raise ValueError("Liquidity coverage appeared during exclusive publication")
    header = dict(source_build_id=unit.build_id, session_date=unit.session_date,
        ticker=unit.ticker, source_attempt_id=unit.source_attempt_id, derivation_attempt_id=attempt,
        resolution_ms=1000, source_price_attempt_id=unit.source_price_attempt_id,
        source_market_unit_hash=unit.source_market_unit_hash, source_price_unit_hash=unit.source_price_unit_hash,
        producer_code_hash=code_hash, liquidity_row_count=liquid["row_count"], price_row_count=price["row_count"],
        total_execution_volume=liquid["total_execution_volume"],
        liquidity_content_hash=liquid["content_hash"], price_content_hash=price["content_hash"])
    header["content_hash"] = _digest(header)
    header["certified_at"] = datetime.now(timezone.utc)
    ownership.assert_owner()
    insert_product_rows(writer, sql.COVERAGE, [header])
    final = _coverage(reader, unit)
    if len(final) != 1 or final[0]["derivation_attempt_id"] != attempt:
        raise ValueError("Liquidity coverage acknowledgement is ambiguous")
    _verify_coverage(reader, source, final[0], code_hash=code_hash)
    ownership.assert_owner()
    return "published"


def publication_permission_preflight(client, *, writable: bool):
    """Reuse an explicit SELECT-only app reader; constrain producer INSERTs."""
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Liquidity principal identity is invalid")
    target = {t.name for t in sql.TABLES}
    required = {("arte", name) for name in (*target, "liquidity_100ms_v1", "liquidity_execution_price_100ms_v1")}
    required |= {("system", name) for name in ("storage_policies", "tables", "columns", "parts", "data_skipping_indices")}
    reads, inserts = set(), set()
    lines = client.execute("SHOW GRANTS FINAL").splitlines()
    for line in lines:
        match = re.fullmatch(r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*)\."
                             r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(4) != user:
            raise ValueError("Liquidity principal has unknown/delegated grants")
        privileges = {v.strip() for v in match.group(1).split(",")}
        scope = match.group(2, 3)
        for privilege in privileges:
            if privilege == "SELECT":
                reads.add(scope)
            elif privilege == "INSERT" and writable and scope[0] == "arte" and scope[1] in target:
                inserts.add(scope[1])
            else:
                raise ValueError("Liquidity principal has unauthorized write/DDL authority")
    if not required <= reads or inserts != (target if writable else set()):
        raise ValueError("Liquidity publication grants are incomplete")


async def publish_unit_async(reader_factory, writer_factory, source, **authority):
    """Offload every blocking operation and close both owned connections."""
    def run():
        reader = reader_factory()
        try:
            writer = writer_factory()
            try:
                return publish_unit(reader, writer, source, **authority)
            finally:
                writer.close()
        finally:
            reader.close()
    # A cancelled caller must not leave an unowned publication thread behind.
    # Drain the bounded unit before propagating cancellation; the seal remains
    # the only visibility boundary, including an ambiguous acknowledgement.
    task = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise
