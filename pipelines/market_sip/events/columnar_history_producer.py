"""Operator-owned causal-history publication over certified database parents.

No flatfiles, private research bank or strategy-specific persistence is used.
Every unit verifies source pins, inserts bounded scalar children, audits their
readback and seals coverage last. Ambiguous/unsealed attempts require explicit
operator reconciliation, never an automatic overwrite or duplicate retry.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from itertools import islice
from uuid import uuid4

import polars as pl

from .market_day_sql import literal
from .opening_price_reference_producer import CertifiedOpeningInput, source_storage_preflight
from .liquidity_resolution_producer import CertifiedLiquidityInput
from src.backend.backtest_market_data import _verify_market_batch
from src.backend.backtest_liquidity_resolution import CertifiedLiquidityResolution, certify_liquidity_resolution, read_liquidity_window
from src.backend.qmd_opening_reference import CertifiedOpeningReference, certify_opening_reference
from src.backend.qmd_columnar_history import _rows, scope_sql, read_seal, permission_preflight, certify_history_product
from src.market_engine import columnar_history_product as c
from src.trading_runtime.arte_journal_schema import storage_preflight
from research.mlops.clickhouse import insert_json_each_row


@dataclass(frozen=True)
class CertifiedHistoryInput:
    opening_source: CertifiedOpeningInput
    opening_reference: CertifiedOpeningReference
    liquidity_source: CertifiedLiquidityInput
    liquidity: CertifiedLiquidityResolution
    ticker: str
    first_bucket: int
    stop_bucket: int
    fields: tuple[c.HistoryField, ...]
    volume_window_seconds: int

    def __post_init__(self):
        if (type(self.opening_source) is not CertifiedOpeningInput
                or type(self.opening_reference) is not CertifiedOpeningReference
                or type(self.liquidity_source) is not CertifiedLiquidityInput
                or type(self.liquidity) is not CertifiedLiquidityResolution):
            raise TypeError("History producer needs typed certified source products")
        c.selected_catalog(self.fields)
        market = self.opening_source.market
        day = date.fromisoformat(market.sessions[0])
        listings = [v for v in self.opening_source.listings if v.ticker == self.ticker]
        bars = [v for v in market.units if v.stage == "bars" and v.ticker == self.ticker and v.session_date == day.isoformat()]
        if (len(listings) != 1 or len(bars) != 1 or 1000 not in market.required_resolutions_ms
                or any(v.build_id != market.build_id or v.session_date not in market.sessions
                    or v.ticker not in market.tickers for v in market.units)
                or self.liquidity.source.build_id != market.build_id
                or self.liquidity.source.session_date != day or self.liquidity.source.ticker != self.ticker
                or self.liquidity_source.market != market or self.liquidity_source.unit != self.liquidity.source
                or self.opening_reference.scope != self.opening_source.scope(self.opening_reference.scope.producer_code_hash)):
            raise ValueError("History source scope differs from its certified population")
        row = self.opening_reference.rows_by_listing.get(listings[0].listing_id)
        if row is None or row["ticker"] != self.ticker:
            raise ValueError("History source lacks its effective opening identity")
        self.scope("00000000-0000-0000-0000-000000000001", "0" * 64)
        self.parents("0" * 64)

    @property
    def bar_unit(self):
        market = self.opening_source.market
        return next(v for v in market.units if v.stage == "bars" and v.ticker == self.ticker
            and v.session_date == market.sessions[0] and v.build_id == market.build_id)

    @property
    def listing_id(self):
        return next(v.listing_id for v in self.opening_source.listings if v.ticker == self.ticker)

    def scope(self, attempt_id, code_hash):
        market = self.opening_source.market
        return c.HistoryScope(market.build_id, date.fromisoformat(market.sessions[0]), self.listing_id,
            self.ticker, attempt_id, c.product_revision(self.fields, code_hash,
                volume_window_seconds=self.volume_window_seconds), self.first_bucket, self.stop_bucket)

    def parents(self, code_hash):
        return c.HistoryParents(self.bar_unit.attempt_id, c.content_hash(asdict(self.bar_unit)),
            self.liquidity.derivation_attempt_id, self.liquidity.content_hash,
            self.opening_reference.content_hash, self.opening_source.market.token, code_hash, self.volume_window_seconds)

    def verify(self, reader):
        self.opening_source.verify(reader)
        self.liquidity_source.verify(reader)
        opening = certify_opening_reference(reader, self.opening_reference.scope,
            expected_content_hash=self.opening_reference.content_hash)
        if opening != self.opening_reference:
            raise ValueError("History opening reference changed")
        liquid = certify_liquidity_resolution(reader, self.liquidity.source,
            producer_code_hash=self.liquidity.producer_code_hash,
            expected_execution_volume=self.liquidity.total_execution_volume)
        if liquid != self.liquidity:
            raise ValueError("History liquidity source changed")
        _verify_market_batch(self.opening_source.market, "bars", "bars_v1",
            self.bar_unit.session_date, (self.bar_unit,), reader)


def materialize_history(reader, source, scope, *, code_hash):
    if type(source) is not CertifiedHistoryInput or scope != source.scope(scope.attempt_id, code_hash):
        raise ValueError("History materialization requires its exact certified scope")
    bars, liquidity = [], []
    unit = source.bar_unit
    predicate = (f"build_id={literal(unit.build_id)} AND session_date=toDate({literal(unit.session_date)}) "
        f"AND ticker={literal(unit.ticker)} AND attempt_id=toUUID({literal(unit.attempt_id)}) AND resolution_ms=1000")
    for first in range(scope.first_bucket, scope.stop_bucket, 256):
        stop = min(first + 256, scope.stop_bucket)
        rows = _rows(reader, "SELECT bucket_index,price_valid,extremes_valid,close_int,high_int,low_int,volume "
            f"FROM arte.bars_v1 WHERE {predicate} AND bucket_index>={first} AND bucket_index<{stop} "
            "ORDER BY bucket_index LIMIT 257", 257)
        if len(rows) > stop - first:
            raise ValueError("History source bars contain duplicate clocks")
        for row in rows:
            if set(row) != {"bucket_index", "price_valid", "extremes_valid", "close_int", "high_int", "low_int", "volume"}:
                raise ValueError("History source bar projection differs")
            for name in ("bucket_index", "price_valid", "extremes_valid", "close_int", "high_int", "low_int"):
                value = row[name]
                if type(value) is str and value.isascii() and value.isdecimal():
                    value = int(value)
                if type(value) is not int or value < 0 or value >= 2 ** 64:
                    raise ValueError("History source bar unsigned scalar differs")
                row[name] = value
            bars.append(row)
        liquidity.extend({"bucket_index": r["bucket_index"], "execution_notional": r["execution_notional"]}
            for r in read_liquidity_window(reader, source.liquidity, first_bucket=first, stop_bucket=stop))
    bar_frame = pl.DataFrame(bars, schema={"bucket_index": pl.UInt32, "price_valid": pl.UInt8,
        "extremes_valid": pl.UInt8, "close_int": pl.UInt64, "high_int": pl.UInt64, "low_int": pl.UInt64,
        "volume": pl.Float64})
    liquid_frame = pl.DataFrame(liquidity, schema={"bucket_index": pl.UInt32, "execution_notional": pl.Float64})
    return c.prepare_product(scope, bar_frame, liquid_frame, source.fields,
        volume_window_seconds=source.volume_window_seconds, producer_code_hash=code_hash)


def wire_records(table, rows):
    result = []
    for row in rows:
        normalized = c.restore_record(table, row)
        result.append({name: (value.isoformat() if type(value) is date else
            value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            if type(value) is datetime else value) for name, value in normalized.items()})
    return result


def value_insert_projection(rows):
    """Construct Float32 values from exact bits, bypassing decimal parse drift.

    Explicit scalar SELECTs require no file/format source, temporary-table
    grants or opaque persisted data. The finite batch remains producer-owned.
    """
    if not 1 <= len(rows) <= 128:
        raise ValueError("History INSERT projection requires a bounded child batch")
    wire = wire_records(c.VALUE, rows)
    selects = []
    for row in wire:
        expressions = []
        for name, kind in c.VALUE.columns:
            value = row[name]
            if name == "value" and value is not None:
                expression = f"CAST(reinterpretAsFloat32(toUInt32({row['value_bits']})),'Nullable(Float32)')"
            else:
                expression = f"CAST({'NULL' if value is None else literal(value)},{literal(kind)})"
            expressions.append(expression + " AS `" + name + "`")
        selects.append("SELECT " + ",".join(expressions))
    result = " UNION ALL ".join(selects)
    if len(result.encode()) > 256 * 1024:
        raise ValueError("History INSERT projection exceeds its byte bound")
    return result


def insert_history_rows(writer, table, rows):
    if table not in (c.FIELD, c.CLOCK, c.VALUE) or not 1 <= len(rows) <= 128:
        raise ValueError("History insertion requires bounded centrally declared children")
    if table == c.VALUE:
        columns = ",".join("`" + name + "`" for name, _ in table.columns)
        writer.execute(f"INSERT INTO `q_live`.`{table.name}` ({columns}) " + value_insert_projection(rows))
    else:
        insert_json_each_row(writer, "q_live", table.name, [n for n, _ in table.columns], wire_records(table, rows))


def publish_history(reader, writer, source, *, ownership, code_hash):
    if type(source) is not CertifiedHistoryInput:
        raise TypeError("History publication requires certified database parents")
    c.require_hash(code_hash)
    endpoint = getattr(reader, "base_url", None)
    if (reader is writer or not isinstance(endpoint, str) or not endpoint
            or endpoint != getattr(writer, "base_url", None)):
        raise ValueError("History publication requires separate same-endpoint clients")
    if permission_preflight(reader) == permission_preflight(writer, writable=True):
        raise ValueError("History publication requires isolated principals")
    storage_preflight(reader, tables=c.TABLES)
    source_storage_preflight(reader)
    ownership.assert_owner()
    source.verify(reader)
    scope = source.scope(str(uuid4()), code_hash)
    parents = source.parents(code_hash)
    existing = read_seal(reader, scope)
    if existing:
        header = existing[0]
        scope = source.scope(header["derivation_attempt_id"], code_hash)
        certify_history_product(reader, scope, source.fields, parents, expected_content_hash=header["content_hash"])
        prepared = materialize_history(reader, source, scope, code_hash=code_hash)
        if c.history_seal(prepared, parents, certified_at=header["certified_at"]) != header:
            raise ValueError("History persisted product differs from its certified source")
        ownership.assert_owner()
        return "skipped_verified"
    for table in (c.FIELD, c.CLOCK, c.VALUE):
        if _rows(reader, f"SELECT derivation_attempt_id FROM q_live.{table.name} "
                f"WHERE {scope_sql(scope, attempt=False)} LIMIT 1", 1):
            raise ValueError("Unsealed history attempt requires operator reconciliation")
    prepared = materialize_history(reader, source, scope, code_hash=code_hash)
    header = c.history_seal(prepared, parents, certified_at=datetime.now(timezone.utc))
    for table in (c.FIELD, c.CLOCK, c.VALUE):
        records = iter(c.child_records(prepared, table))
        while chunk := list(islice(records, 128)):
            ownership.assert_owner()
            insert_history_rows(writer, table, chunk)
    # No success seal is issued until all actual children match their projection.
    _verify_children_before_seal(reader, prepared)
    source.verify(reader)
    recomputed = materialize_history(reader, source, scope, code_hash=code_hash)
    if c.history_seal(recomputed, parents, certified_at=header["certified_at"]) != header:
        raise ValueError("History source changed before coverage publication")
    if read_seal(reader, scope):
        raise ValueError("History coverage appeared during exclusive publication")
    ownership.assert_owner()
    insert_json_each_row(writer, "q_live", c.COVERAGE.name, [n for n, _ in c.COVERAGE.columns],
        wire_records(c.COVERAGE, [header]))
    certificate = certify_history_product(reader, scope, source.fields, parents, expected_content_hash=header["content_hash"])
    if dict(certificate.header) != header:
        raise ValueError("History coverage acknowledgement is ambiguous")
    ownership.assert_owner()
    return "published"


def _verify_children_before_seal(reader, prepared):
    scope = prepared.scope
    predicate = scope_sql(scope)
    for table, key, order in ((c.FIELD, "field_id", "field_id"),
            (c.CLOCK, "bucket_index", "bucket_index"),
            (c.VALUE, "(field_id,first_ordinal)", "field_id,first_ordinal")):
        expected = iter(c.child_records(prepared, table))
        offset = 0
        # Offset is safe only inside this exclusive immutable publication unit;
        # the full key/count audit detects conflicting or duplicate children.
        while True:
            rows = _rows(reader, f"SELECT * FROM q_live.{table.name} WHERE {predicate} "
                f"ORDER BY {order} LIMIT 1024 OFFSET {offset}", 1024)
            actual = [c.restore_record(table, row) for row in rows]
            if actual != list(islice(expected, len(rows))):
                raise ValueError("History child INSERT readback differs")
            offset += len(rows)
            if len(rows) < 1024:
                if next(expected, None) is not None:
                    raise ValueError("History child INSERT readback is incomplete")
                break
        summary = _rows(reader, f"SELECT count() AS row_count,uniqExact({key}) AS unique_keys "
            f"FROM q_live.{table.name} WHERE {predicate}", 1)
        if (len(summary) != 1 or int(summary[0]["row_count"]) != offset
                or int(summary[0]["unique_keys"]) != offset):
            raise ValueError("History publication children contain extra or duplicate keys")


async def publish_history_async(reader_factory, writer_factory, source, **authority):
    def work():
        reader = reader_factory()
        try:
            writer = writer_factory()
            try:
                return publish_history(reader, writer, source, **authority)
            finally:
                writer.close()
        finally:
            reader.close()
    task = asyncio.create_task(asyncio.to_thread(work))
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


@dataclass(frozen=True)
class HistoryPublicationReceipt:
    status: str
    scope: c.HistoryScope
    content_hash: str


class HistoryProductPipeline:
    """Explicit compute-funnel capacity, with no hidden pending work queue."""

    def __init__(self, reader_factory, writer_factory, *, maximum_concurrent_units):
        if type(maximum_concurrent_units) is not int or not 1 <= maximum_concurrent_units <= 8:
            raise ValueError("History pipeline requires explicit bounded worker capacity")
        self.reader_factory, self.writer_factory = reader_factory, writer_factory
        self.capacity = maximum_concurrent_units
        self.active, self.failed = set(), set()
        self.completed = self.skipped_verified = 0
        self._loop = None

    async def publish(self, source, *, ownership, code_hash):
        if type(source) is not CertifiedHistoryInput:
            raise TypeError("History pipeline requires a certified source unit")
        c.require_hash(code_hash)
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ValueError("History pipeline cannot cross event-loop ownership")
        self._loop = loop
        scope = source.scope("00000000-0000-0000-0000-000000000001", code_hash)
        key = (scope.build_id, scope.session_date, scope.listing_id, scope.product_revision,
               scope.first_bucket, scope.stop_bucket)
        if key in self.failed:
            raise ValueError("Failed history unit requires operator reconciliation")
        if key in self.active or len(self.active) >= self.capacity:
            raise ValueError("History capacity is full or this unit is already active")
        self.active.add(key)

        def work():
            ownership.assert_owner()
            reader = self.reader_factory()
            try:
                writer = self.writer_factory()
                try:
                    status = publish_history(reader, writer, source, ownership=ownership, code_hash=code_hash)
                    headers = read_seal(reader, scope)
                    if len(headers) != 1:
                        raise ValueError("History publication receipt has no unique seal")
                    header = headers[0]
                    return HistoryPublicationReceipt(status,
                        source.scope(header["derivation_attempt_id"], code_hash), header["content_hash"])
                finally:
                    writer.close()
            finally:
                reader.close()

        task = asyncio.create_task(asyncio.to_thread(work))
        cancelled = False
        try:
            while True:
                try:
                    receipt = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    if task.cancelled():
                        self.failed.add(key)
                        raise
            if receipt.status == "published":
                self.completed += 1
            elif receipt.status == "skipped_verified":
                self.skipped_verified += 1
            else:
                raise ValueError("History producer returned an unknown receipt status")
            if cancelled:
                raise asyncio.CancelledError
            return receipt
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed.add(key)
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        finally:
            self.active.remove(key)
