"""Operator-owned causal-history publication over certified database parents.

No flatfiles, private research bank or strategy-specific persistence is used.
Every unit verifies source pins, inserts bounded scalar children, audits their
readback and seals coverage last. Ambiguous/unsealed attempts require explicit
operator reconciliation, never an automatic overwrite or duplicate retry.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass, field, replace
from collections import OrderedDict
from threading import RLock
from hashlib import sha256
import json
from datetime import date, datetime, timezone
from itertools import islice
from uuid import uuid4

import polars as pl

from .market_day_sql import literal
from .opening_price_reference_producer import CertifiedOpeningInput, source_storage_preflight
from .liquidity_resolution_producer import CertifiedLiquidityInput
from . import liquidity_resolution_sql as liquidity_sql
from src.backend.backtest_market_data import _verify_market_batch
from src.backend.backtest_liquidity_resolution import CertifiedLiquidityResolution, certify_liquidity_resolution
from src.backend.qmd_opening_reference import CertifiedOpeningReference, certify_opening_reference
from src.backend.qmd_columnar_history import _rows, scope_sql, read_seal, permission_preflight, certify_history_product
from src.market_engine import columnar_history_product as c
from src.trading_runtime.arte_journal_schema import storage_preflight
from research.mlops.clickhouse import insert_json_each_row
from src.market_engine.typed_product_insert import insert_product_rows


class OpeningProofCache:
    """Publisher-owned, bounded, inventory-fenced native opening proofs."""

    def __init__(self, maximum_entries=2):
        if type(maximum_entries) is not int or not 1 <= maximum_entries <= 3:
            raise ValueError('Opening proof cache requires one to three entries')
        self.maximum_entries = maximum_entries
        self._items = OrderedDict()
        self._lock = RLock()
        self._closed = False

    @staticmethod
    def _eligible(reader, source):
        from research.mlops.clickhouse import ClickHouseHttpClient
        from src.market_engine.regular_close_history import CertifiedPriorClosePlan
        return (type(reader) is ClickHouseHttpClient and type(source) is CertifiedOpeningInput
            and source.listing_basis == 'shared-reference-routing-tradability-v1'
            and type(source.prior_market) is CertifiedPriorClosePlan
            and reader.default_query_params.get('readonly') == '1')

    def verify(self, reader, source):
        with self._lock:
            if self._closed:
                raise RuntimeError('Opening proof cache owner is closed')
            if not self._eligible(reader, source):
                source.verify(reader)
                return
            key = (reader.base_url, reader.user, source.market.build_id, source.market.token,
                source.prior_market.token, source.identity.token, source.population_reference_hash,
                source.listing_basis)
            try:
                before = self._inventory(reader, source)
            except Exception:
                self._items.clear()
                raise
            entry = self._items.get(key)
            if entry is not None and entry[0] == source and entry[1] == before:
                try:
                    after = self._inventory(reader, source)
                except Exception:
                    self._items.pop(key, None)
                    raise
                if after != before:
                    self._items.pop(key, None)
                    raise ValueError('Opening parent inventory changed during cache verification')
                self._items.move_to_end(key)
                return
            self._items.pop(key, None)
            source.verify(reader)
            if self._inventory(reader, source) != before:
                raise ValueError('Opening parent inventory changed during cold verification')
            self._items[key] = (source, before)
            self._items.move_to_end(key)
            while len(self._items) > self.maximum_entries:
                self._items.popitem(last=False)

    @staticmethod
    def _keeper_fence(source):
        from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
        from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
        from src.trading_runtime.arte_market_day_session_seal import _receipt_path
        with MARKET_CERTIFICATE_KEEPER_POOL.borrow() as session:
            keeper = MarketDayKeeperReader(session.client)
            proof = keeper.load(source.market.build_id)
            if proof is None:
                raise ValueError('Opening Keeper attestation is absent')
            receipt, _ = session.client.get(_receipt_path(source.market.build_id,source.market.sessions[0]))
            if keeper.load(source.market.build_id) != proof:
                raise ValueError('Opening Keeper proof changed during inventory read')
            return proof.wire(), receipt

    @classmethod
    def _inventory(cls, reader, source):
        from src.market_engine.regular_close_history import TABLES as PRIOR_TABLES
        from .shared_reference_listing_basis import TABLES as SHARED_TABLES
        arte = {'market_day_session_seal_v1','market_day_planned_scope_v1',
            'market_day_stage_certificate_v1','market_day_seed_v1','market_day_source_unit_v1'}
        optional = {f'strategy_one_identity{suffix}_v{version}' for version in (1,2,3)
            for suffix in ('','_coverage')} | {'strategy_one_identity_resolution_v3'}
        q_live = set(SHARED_TABLES) | {t.name for t in PRIOR_TABLES} | {
            'feature_tradable_universe_snapshot_v2','feature_tradable_universe_snapshot_coverage_v2',
            'market_stock_split_v1'}
        pairs = {('arte',name) for name in arte | optional} | {('q_live',name) for name in q_live}
        required = pairs - {('arte',name) for name in optional}
        def condition(key):
            return f'(database,{key}) IN (' + ','.join('('+literal(db)+','+literal(name)+')'
                for db,name in sorted(pairs)) + ')'
        queries = (
            ('tables','database,name,uuid,engine,storage_policy,partition_key,sorting_key,create_table_query,metadata_modification_time',
                'name','database,name'),
            ('columns','database,table,name,type,position','table','database,table,position,name'),
            ('parts','database,table,name,uuid,partition_id,min_block_number,max_block_number,level,data_version,disk_name,rows,bytes_on_disk,hash_of_all_files',
                'table','database,table,name'))
        inventory = []
        for table,columns,key,order in queries:
            sql = f'SELECT {columns} FROM system.{table} WHERE {condition(key)}'
            if table == 'parts': sql += ' AND active'
            raw = reader.execute(sql + f' ORDER BY {order} LIMIT 20001 FORMAT JSONEachRow')
            if type(raw) is not str or len(raw.encode()) > 16*1024*1024:
                raise ValueError('Opening parent inventory exceeds byte bound')
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if len(rows) > 20000 or any(set(row) != set(columns.split(','))
                    or (row['database'],row[key]) not in pairs for row in rows):
                raise ValueError('Opening parent inventory is incomplete or malformed')
            if table in ('tables','columns'):
                present = {(row['database'],row[key]) for row in rows}
                if not required <= present:
                    raise ValueError('Opening parent inventory lacks required metadata')
                if table == 'tables':
                    if len(present) != len(rows) or any(row['storage_policy'] != 'live_market_ssd'
                            for row in rows if (row['database'],row['name']) in required):
                        raise ValueError('Opening parent inventory storage policy changed')
                    known = present
                elif present != known:
                    raise ValueError('Opening parent inventory lacks column metadata')
            else:
                if any(row['disk_name'] != 'live_market_ssd' for row in rows
                        if (row['database'],row['table']) in required):
                    raise ValueError('Opening parent inventory active parts moved outside SSD')
            inventory.append(rows)
        keeper = cls._keeper_fence(source)
        return sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest(),keeper

    def close(self):
        with self._lock:
            self._items.clear()
            self._closed = True


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
    feature_day: object = None
    _opening_proof_cache: OpeningProofCache | None = field(default=None,repr=False,compare=False)

    def __post_init__(self):
        if self._opening_proof_cache is not None and type(self._opening_proof_cache) is not OpeningProofCache:
            raise TypeError('History opening proof cache requires its concrete owner')
        if (type(self.opening_source) is not CertifiedOpeningInput
                or type(self.opening_reference) is not CertifiedOpeningReference
                or type(self.liquidity_source) is not CertifiedLiquidityInput
                or type(self.liquidity) is not CertifiedLiquidityResolution):
            raise TypeError("History producer needs typed certified source products")
        c.selected_catalog(self.fields)
        if self.feature_day is not None:
            from .qmd_feature_preparation import PreparedQmdFeatureDay
            if (type(self.feature_day) is not PreparedQmdFeatureDay
                    or self.feature_day.request.opening_source != self.opening_source
                    or self.ticker not in self.feature_day.request.tickers):
                raise ValueError('Extended history requires its native prepared feature day')
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
                volume_window_seconds=self.volume_window_seconds,
                feature_plan_hash=self.feature_day.plan.plan_hash if self.feature_day is not None else ''), self.first_bucket, self.stop_bucket)

    def parents(self, code_hash):
        return c.HistoryParents(self.bar_unit.attempt_id, c.content_hash(asdict(self.bar_unit)),
            self.liquidity.derivation_attempt_id, self.liquidity.content_hash,
            self.opening_reference.content_hash, self.opening_source.market.token, code_hash, self.volume_window_seconds,
            self.feature_day.plan.plan_hash if self.feature_day is not None else '')

    def verify(self, reader):
        if self.feature_day is not None:
            self.feature_day.verify(reader)
        if self._opening_proof_cache is None:
            self.opening_source.verify(reader)
        else:
            self._opening_proof_cache.verify(reader,self.opening_source)
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


def read_history_activity_window(reader, certificate, *, first_bucket, stop_bucket):
    """Read only certified activity scalars; history never consumes fill levels."""
    if (type(certificate) is not CertifiedLiquidityResolution
            or type(first_bucket) is not int or type(stop_bucket) is not int
            or not 14400 <= first_bucket < stop_bucket <= 72000
            or stop_bucket - first_bucket > 4096):
        raise ValueError('History activity requires a bounded certified clock window')
    predicate = liquidity_sql.target_scope(certificate.source, certificate.derivation_attempt_id)
    rows = _rows(reader, f'SELECT bucket_index,execution_notional FROM arte.{liquidity_sql.LIQUIDITY.name} '
        f'WHERE {predicate} AND bucket_index>={first_bucket} AND bucket_index<{stop_bucket} '
        'ORDER BY bucket_index LIMIT 4097', 4097)
    previous = first_bucket - 1
    for row in rows:
        if set(row) != {'bucket_index', 'execution_notional'}:
            raise ValueError('History activity projection differs')
        bucket = row['bucket_index']
        if type(bucket) is str and bucket.isascii() and bucket.isdecimal():
            bucket = int(bucket)
        notional = row['execution_notional']
        if (type(bucket) is not int or not previous < bucket < stop_bucket
                or type(notional) not in (int, float) or not math.isfinite(notional)
                or notional < 0):
            raise ValueError('History activity contains duplicate, invalid or out-of-window scalars')
        row['bucket_index'] = bucket
        previous = bucket
    return tuple(rows)


def materialize_history(reader, source, scope, *, code_hash):
    if type(source) is not CertifiedHistoryInput or scope != source.scope(scope.attempt_id, code_hash):
        raise ValueError("History materialization requires its exact certified scope")
    bars, liquidity = [], []
    unit = source.bar_unit
    predicate = (f"build_id={literal(unit.build_id)} AND session_date=toDate({literal(unit.session_date)}) "
        f"AND ticker={literal(unit.ticker)} AND attempt_id=toUUID({literal(unit.attempt_id)}) AND resolution_ms=1000")
    for first in range(scope.first_bucket, scope.stop_bucket, 4096):
        stop = min(first + 4096, scope.stop_bucket)
        rows = _rows(reader, "SELECT bucket_index,price_valid,extremes_valid,close_int,high_int,low_int,volume "
            f"FROM arte.bars_v1 WHERE {predicate} AND bucket_index>={first} AND bucket_index<{stop} "
            "ORDER BY bucket_index LIMIT 4097", 4097)
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
            for r in read_history_activity_window(reader, source.liquidity, first_bucket=first, stop_bucket=stop))
    bar_frame = pl.DataFrame(bars, schema={"bucket_index": pl.UInt32, "price_valid": pl.UInt8,
        "extremes_valid": pl.UInt8, "close_int": pl.UInt64, "high_int": pl.UInt64, "low_int": pl.UInt64,
        "volume": pl.Float64})
    liquid_frame = pl.DataFrame(liquidity, schema={"bucket_index": pl.UInt32, "execution_notional": pl.Float64})
    feature_frame = (source.feature_day.project(source,scope,bar_frame,liquid_frame)
                     if source.feature_day is not None else None)
    return c.prepare_product(scope, bar_frame, liquid_frame, source.fields,
        volume_window_seconds=source.volume_window_seconds, producer_code_hash=code_hash,
        feature_frame=feature_frame,
        feature_plan_hash=source.feature_day.plan.plan_hash if source.feature_day is not None else '')


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
    if table not in (c.FIELD, c.CLOCK, c.VALUE) or not 1 <= len(rows) <= 4096:
        raise ValueError("History insertion requires bounded centrally declared children")
    insert_product_rows(writer, table, [c.restore_record(table, row) for row in rows])


def publish_history(reader, writer, source, *, ownership, code_hash,
                    reconcile_attempt_id=None, expected_content_hash=None):
    if type(source) is not CertifiedHistoryInput:
        raise TypeError("History publication requires certified database parents")
    c.require_hash(code_hash)
    if (reconcile_attempt_id is None) != (expected_content_hash is None):
        raise ValueError('History reconciliation requires an explicit attempt and projected content hash')
    if reconcile_attempt_id is not None:
        from uuid import UUID
        if str(UUID(reconcile_attempt_id)) != reconcile_attempt_id:
            raise ValueError('History reconciliation attempt is not canonical')
        c.require_hash(expected_content_hash)
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
    scope = source.scope(reconcile_attempt_id or str(uuid4()), code_hash)
    parents = source.parents(code_hash)
    existing = read_seal(reader, scope)
    if existing:
        header = existing[0]
        if reconcile_attempt_id is not None and (header['derivation_attempt_id'] != reconcile_attempt_id
                or header['content_hash'] != expected_content_hash):
            raise ValueError('History reconciliation differs from existing coverage')
        scope = source.scope(header["derivation_attempt_id"], code_hash)
        certify_history_product(reader, scope, source.fields, parents, expected_content_hash=header["content_hash"],
            feature_plan_cache=source.feature_day._plan_cache if source.feature_day is not None else None)
        prepared = materialize_history(reader, source, scope, code_hash=code_hash)
        if c.history_seal(prepared, parents, certified_at=header["certified_at"]) != header:
            raise ValueError("History persisted product differs from its certified source")
        ownership.assert_owner()
        return "skipped_verified"
    for table in (c.FIELD, c.CLOCK, c.VALUE):
        rows = _rows(reader, f"SELECT DISTINCT derivation_attempt_id FROM q_live.{table.name} "
                f"WHERE {scope_sql(scope, attempt=False)} LIMIT 2", 2)
        if rows and (reconcile_attempt_id is None or any(
                row['derivation_attempt_id'] != reconcile_attempt_id for row in rows)):
            raise ValueError("Unsealed history attempt requires operator reconciliation")
    prepared = materialize_history(reader, source, scope, code_hash=code_hash)
    header = c.history_seal(prepared, parents, certified_at=datetime.now(timezone.utc))
    if reconcile_attempt_id is not None and header['content_hash'] != expected_content_hash:
        raise ValueError('History reconciliation projection differs from its explicit content pin')
    if reconcile_attempt_id is None:
        for table in (c.FIELD, c.CLOCK, c.VALUE):
            records = iter(c.child_records(prepared, table))
            while chunk := list(islice(records, 4096)):
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
    certificate = certify_history_product(reader, scope, source.fields, parents, expected_content_hash=header["content_hash"],
        feature_plan_cache=source.feature_day._plan_cache if source.feature_day is not None else None)
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
        count = 0
        last = None
        columns = tuple(order.split(','))
        # Resume by the complete ordered key, avoiding repeated prefix scans.
        # The independent full count/uniqueness audit also catches duplicates
        # straddling a page boundary, which a strict key cursor would skip.
        while True:
            after = '' if last is None else (
                f"AND {key}>{last[0]} " if len(columns) == 1 else
                f"AND {key}>({','.join(str(v) for v in last)}) ")
            rows = _rows(reader, f"SELECT * FROM q_live.{table.name} WHERE {predicate} "
                f"{after}ORDER BY {order} LIMIT 1024", 1024)
            actual = [c.restore_record(table, row) for row in rows]
            if actual != list(islice(expected, len(rows))):
                raise ValueError("History child INSERT readback differs")
            count += len(rows)
            if actual:
                last = tuple(actual[-1][column] for column in columns)
            if len(rows) < 1024:
                if next(expected, None) is not None:
                    raise ValueError("History child INSERT readback is incomplete")
                break
        summary = _rows(reader, f"SELECT count() AS row_count,uniqExact({key}) AS unique_keys "
            f"FROM q_live.{table.name} WHERE {predicate}", 1)
        if (len(summary) != 1 or int(summary[0]["row_count"]) != count
                or int(summary[0]["unique_keys"]) != count):
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
        self._opening_proof_cache = OpeningProofCache(maximum_entries=2)

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
        source = replace(source,_opening_proof_cache=self._opening_proof_cache)

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

    def close(self):
        if self.active:
            raise RuntimeError('History pipeline cannot close while units are active')
        self._opening_proof_cache.close()
