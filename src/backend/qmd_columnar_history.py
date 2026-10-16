"""SELECT-only cold certification of the shared elapsed-history product.

Reads have finite row/byte bounds and own their connections off the event loop.
Missing or unsealed inputs fail closed; this consumer cannot invoke producers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from typing import Mapping

import polars as pl

from pipelines.market_sip.events.market_day_sql import literal
from src.market_engine import columnar_history_product as c
from src.trading_runtime.arte_journal_schema import storage_preflight


def _rows(reader, query, bound):
    response = reader.execute(query + " FORMAT JSONEachRow")
    if not isinstance(response, str) or len(response.encode()) > 8 * 1024 * 1024:
        raise ValueError("QMD history response exceeds its byte bound")
    rows = [json.loads(line) for line in response.splitlines() if line.strip()]
    if len(rows) > bound:
        raise ValueError("QMD history response exceeds its row bound")
    return rows


def permission_preflight(client, *, writable=False, feature_sources=False):
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("QMD history principal is invalid")
    targets = {t.name for t in c.TABLES}
    from src.market_engine.feature_source_plan import TABLES as FEATURE_TABLES
    feature_targets = {t.name for t in FEATURE_TABLES}
    from src.market_engine.qmd_volume_product import TABLES as VOLUME_TABLES
    volume_targets = {t.name for t in VOLUME_TABLES}
    if feature_sources:
        targets |= feature_targets
    required = {("q_live", name) for name in targets}
    required |= {("system", name) for name in (
        "storage_policies", "tables", "columns", "parts", "data_skipping_indices")}
    reads, writes = set(), set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*)\."
            r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(4) != user:
            raise ValueError("QMD history principal has delegated or unknown grants")
        table = match.group(2, 3)
        for privilege in {v.strip() for v in match.group(1).split(",")}:
            if privilege == "SELECT":
                reads.add(table)
            elif privilege == "INSERT" and writable and table[0] == "q_live" and table[1] in targets | feature_targets | volume_targets:
                writes.add(table[1])
            else:
                raise ValueError("QMD history principal has unauthorized write or DDL authority")
    expected_writes = targets if writable else set()
    if writable and not feature_sources and feature_targets <= writes:
        expected_writes |= feature_targets
    if writable and volume_targets <= writes:
        expected_writes |= volume_targets
    if not required <= reads or writes != expected_writes:
        raise ValueError("QMD history product grants are incomplete")
    return user


def scope_sql(scope, *, attempt=True):
    if type(scope) is not c.HistoryScope:
        raise TypeError("QMD history requires its typed source scope")
    result = (f"source_build_id={literal(scope.build_id)} "
        f"AND session_date=toDate({literal(scope.session_date.isoformat())}) "
        f"AND listing_id={literal(scope.listing_id)} AND ticker={literal(scope.ticker)} "
        f"AND product_revision={literal(scope.product_revision)} "
        f"AND first_bucket={scope.first_bucket} AND stop_bucket={scope.stop_bucket}")
    if attempt:
        result += f" AND derivation_attempt_id=toUUID({literal(scope.attempt_id)})"
    return result


def read_seal(reader, scope):
    rows = _rows(reader, f"SELECT * FROM q_live.{c.COVERAGE.name} "
        f"WHERE {scope_sql(scope, attempt=False)} LIMIT 2", 2)
    if len(rows) > 1:
        raise ValueError("QMD history coverage is ambiguous")
    return tuple(c.restore_record(c.COVERAGE, row) for row in rows)


@dataclass(frozen=True)
class CertifiedHistoryProduct:
    prepared: c.PreparedHistory
    parents: c.HistoryParents
    header: Mapping[str, object]

    def window(self, *, first_ordinal, stop_ordinal, evaluation_block):
        return c.expand_values(self.prepared, first_ordinal=first_ordinal,
            stop_ordinal=stop_ordinal, evaluation_block=evaluation_block)

    def pin(self):
        """Retain the cold proof for QMD transport without retaining data frames."""
        return CertifiedHistoryPin(self.prepared.scope, self.prepared.fields, self.parents, self.header)


@dataclass(frozen=True)
class CertifiedHistoryPin:
    scope: c.HistoryScope
    fields: tuple[c.HistoryField, ...]
    parents: c.HistoryParents
    header: Mapping[str, object]

    def __post_init__(self):
        if type(self.scope) is not c.HistoryScope or type(self.parents) is not c.HistoryParents:
            raise TypeError("QMD history pin requires typed cold-certified source contracts")
        c.selected_catalog(self.fields)
        header = c.restore_record(c.COVERAGE, dict(self.header))
        if (any(header[n] != v for n, v in {**self.scope.prefix(), **self.parents.payload()}.items())
                or header["content_hash"] != c.content_hash({n: v for n, v in header.items()
                    if n not in {"content_hash", "certified_at"}})
                or header["field_count"] != len(self.fields)
                or self.scope.product_revision != c.product_revision(self.fields, self.parents.producer_code_hash,
                    volume_window_seconds=self.parents.volume_window_seconds, feature_plan_hash=self.parents.feature_plan_hash)
                or header["field_content_hash"] != c.records_hash(c.FIELD,
                    ({**self.scope.prefix(), "field_id": f.field_id, "field_name": f.name, "unit": f.unit} for f in self.fields))):
            raise ValueError("QMD history pin differs from its cold coverage proof")
        object.__setattr__(self, "header", MappingProxyType(header))


def certify_history_product(reader, scope, fields, parents, *, expected_content_hash, feature_plan_cache=None):
    """Verify all normalized children and source pins before exposing windows."""
    if type(scope) is not c.HistoryScope or type(parents) is not c.HistoryParents:
        raise TypeError("QMD history needs exact typed source pins")
    c.selected_catalog(fields)
    c.require_hash(expected_content_hash)
    if scope.product_revision != c.product_revision(fields, parents.producer_code_hash,
            volume_window_seconds=parents.volume_window_seconds, feature_plan_hash=parents.feature_plan_hash):
        raise ValueError("QMD history revision differs from the declared source contract")
    permission_preflight(reader)
    storage_preflight(reader, tables=c.TABLES)
    if parents.feature_plan_hash:
        from .qmd_feature_source_plan import read_feature_source_plan,FeatureSourcePlanCache
        if feature_plan_cache is not None and type(feature_plan_cache) is not FeatureSourcePlanCache:
            raise TypeError('Feature source cache must be session-owned and parts-fenced')
        plan = (feature_plan_cache.read(reader,parents.feature_plan_hash) if feature_plan_cache is not None
                else read_feature_source_plan(reader, parents.feature_plan_hash))
        if (plan.market_plan_hash != parents.market_plan_hash or plan.session_date != scope.session_date
                or plan.first_bucket != scope.first_bucket or plan.stop_bucket != scope.stop_bucket):
            raise ValueError('Feature source plan differs from its history lineage')
    seals = read_seal(reader, scope)
    if len(seals) != 1:
        raise ValueError("QMD history product is not sealed")
    header = seals[0]
    expected = {**scope.prefix(), **parents.payload()}
    if (any(header[n] != value for n, value in expected.items())
            or header["content_hash"] != expected_content_hash
            or header["content_hash"] != c.content_hash({n: v for n, v in header.items()
                if n not in {"content_hash", "certified_at"}})
            or header["elapsed_clock_count"] != scope.clock_count
            or header["clock_row_count"] != scope.clock_count
            or header["field_count"] != len(fields)
            or not len(fields) <= header["value_row_count"] <= len(fields) * scope.clock_count):
        raise ValueError("QMD history coverage differs from the pinned product")
    predicate = scope_sql(scope)
    bound = len(c.MARKET_FIELDS) + 1
    field_rows = _rows(reader, f"SELECT * FROM q_live.{c.FIELD.name} WHERE {predicate} ORDER BY field_id LIMIT {bound}", bound)
    field_rows = [c.restore_record(c.FIELD, row) for row in field_rows]
    expected_fields = [{**scope.prefix(), "field_id": f.field_id, "field_name": f.name, "unit": f.unit} for f in fields]
    if field_rows != expected_fields:
        raise ValueError("QMD history field catalog differs")
    clocks, values = [], []
    for first in range(scope.first_bucket, scope.stop_bucket, 1024):
        stop = min(first + 1024, scope.stop_bucket)
        rows = _rows(reader, f"SELECT * FROM q_live.{c.CLOCK.name} WHERE {predicate} "
            f"AND bucket_index>={first} AND bucket_index<{stop} ORDER BY bucket_index LIMIT 1025", 1025)
        if len(rows) != stop - first:
            raise ValueError("QMD history completed clocks are missing or duplicate")
        rows = [c.restore_record(c.CLOCK, row) for row in rows]
        if any(any(row[n] != v for n, v in scope.prefix().items()) for row in rows):
            raise ValueError("QMD history clock scope differs")
        clocks.append(pl.DataFrame([{n: r[n] for n, _ in c.CLOCK.columns[len(c.PREFIX):]} for r in rows],
            schema={"bucket_index": pl.UInt32, "source_known": pl.UInt8, "observed": pl.UInt8,
                "extremes_valid": pl.UInt8, "reference_close": pl.Float64, "share_volume": pl.Float64,
                "execution_notional": pl.Float64, "rolling_volume": pl.Float64}))
    for field in fields:
        first = 0
        while first < scope.clock_count:
            rows = _rows(reader, f"SELECT * FROM q_live.{c.VALUE.name} WHERE {predicate} "
                f"AND field_id={field.field_id} AND first_ordinal>={first} AND first_ordinal<{scope.clock_count} "
                "ORDER BY first_ordinal LIMIT 1024", 1024)
            rows = [c.restore_record(c.VALUE, row) for row in rows]
            if any(any(row[n] != v for n, v in scope.prefix().items()) for row in rows):
                raise ValueError("QMD history feature scope differs")
            if rows:
                values.append(pl.DataFrame([{n: r[n] for n, _ in c.VALUE.columns[len(c.PREFIX):]} for r in rows],
                    schema={"field_id": pl.UInt32, "first_ordinal": pl.UInt32,
                        "stop_ordinal": pl.UInt32, "value": pl.Float32, "value_bits": pl.UInt32, "valid": pl.UInt8}))
                next_first = rows[-1]["first_ordinal"] + 1
                if next_first <= first:
                    raise ValueError("QMD history interval pagination did not advance")
                first = next_first
            if len(rows) < 1024:
                break
    if fields and not values:
        raise ValueError("QMD history feature intervals are missing")
    prepared = c.PreparedHistory(scope, fields, parents.volume_window_seconds, pl.concat(clocks),
        pl.concat(values) if values else pl.DataFrame(schema=c.VALUE_FRAME_SCHEMA))
    for table, count, key in ((c.FIELD, len(fields), "field_id"),
            (c.CLOCK, prepared.clocks.height, "bucket_index"),
            (c.VALUE, prepared.values.height, "(field_id,first_ordinal)")):
        summary = _rows(reader, f"SELECT count() AS row_count,uniqExact({key}) AS unique_keys "
            f"FROM q_live.{table.name} WHERE {predicate}", 1)
        if (len(summary) != 1 or int(summary[0]["row_count"]) != count
                or int(summary[0]["unique_keys"]) != count):
            raise ValueError("QMD history children contain extra or duplicate keys")
    if c.history_seal(prepared, parents, certified_at=header["certified_at"]) != header:
        raise ValueError("QMD history child hashes or arithmetic differ")
    if read_seal(reader, scope) != seals:
        raise ValueError("QMD history seal changed during certification")
    return CertifiedHistoryProduct(prepared, parents, MappingProxyType(header))


async def certify_history_product_async(reader_factory, scope, fields, parents, **pins):
    def work():
        reader = reader_factory()
        try:
            return certify_history_product(reader, scope, fields, parents, **pins)
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
class HistoryPinRequest:
    """Explicit central product identity; no inferred revision or private bars."""
    scope: c.HistoryScope
    fields: tuple[c.HistoryField, ...]
    parents: c.HistoryParents
    expected_content_hash: str

    def __post_init__(self):
        if type(self.scope) is not c.HistoryScope or type(self.parents) is not c.HistoryParents:
            raise TypeError('History pin request requires native source contracts')
        c.selected_catalog(self.fields)
        c.require_hash(self.expected_content_hash)
        if self.scope.product_revision != c.product_revision(self.fields,
                self.parents.producer_code_hash, volume_window_seconds=self.parents.volume_window_seconds,
                feature_plan_hash=self.parents.feature_plan_hash):
            raise ValueError('History pin request differs from its producer revision')


async def certify_history_pins_async(reader_factory, requests, *, concurrency):
    """Cold certify a complete input packet with fixed workers and pin-only retention.

    Each worker owns its SELECT-only client and native certification. Full
    frames are released in that worker before crossing the event-loop boundary.
    Cancellation drains existing readers; no producer or publication is invoked.
    """
    from .qmd_columnar_history_transport import _owned_worker
    from .qmd_feature_source_plan import FeatureSourcePlanCache
    feature_plan_cache=FeatureSourcePlanCache()
    if (not callable(reader_factory) or type(requests) is not tuple
            or not 1 <= len(requests) <= 25000 or type(concurrency) is not int
            or not 1 <= concurrency <= 6
            or any(type(request) is not HistoryPinRequest for request in requests)):
        raise ValueError('History pin packet requires bounded explicit native requests')
    identities = [request.scope.listing_id for request in requests]
    if len(set(identities)) != len(identities):
        raise ValueError('History pin packet repeats a listing')
    origin = lambda request: (request.scope.build_id, request.scope.session_date,
        request.scope.first_bucket, request.scope.stop_bucket, request.scope.product_revision,
        request.parents.market_plan_hash, request.parents.opening_reference_hash)
    if any(origin(request) != origin(requests[0]) for request in requests):
        raise ValueError('History pin packet differs in its certified session or origin')
    iterator, result = iter(requests), {}
    async def worker():
        owned = []
        def open_reader():
            owned.append(reader_factory())
        try:
            # Retain ownership even if cancellation arrives during construction.
            await _owned_worker(open_reader)
            while (request := next(iterator, None)) is not None:
                def certify():
                    return certify_history_product(owned[0], request.scope, request.fields,
                        request.parents, expected_content_hash=request.expected_content_hash,
                        feature_plan_cache=feature_plan_cache).pin()
                pin = await _owned_worker(certify)
                result[request.scope.listing_id] = pin
        finally:
            if owned:
                await _owned_worker(owned[0].close)
    async with asyncio.TaskGroup() as tasks:
        for _ in range(min(concurrency, len(requests))):
            tasks.create_task(worker())
    return MappingProxyType({identity: result[identity] for identity in sorted(result)})
