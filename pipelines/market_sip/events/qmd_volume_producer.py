"""Operator-owned QMD volume publication; canonical bars are the sole input.

Shared opening proof is checked once per bounded batch, not once per listing.
No liquidity preparation, strategy state, acquisition or historical fallback.
"""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from itertools import islice
from hashlib import sha256
from pathlib import Path
import math
from uuid import uuid4

import polars as pl

from .market_day_sql import literal
from .opening_price_reference_producer import CertifiedOpeningInput, source_storage_preflight
from src.backend.qmd_opening_reference import CertifiedOpeningReference, certify_opening_reference
from src.backend.backtest_market_data import _verify_market_batch
from src.backend.qmd_columnar_history import _rows
from src.market_engine import qmd_volume_product as v
from src.market_engine.columnar_history_product import HistoryScope
from src.market_engine.typed_product_insert import insert_product_rows
from src.trading_runtime.arte_journal_schema import storage_preflight


def algorithm_hash():
    from src.market_engine import causal_volume_history, qmd_volume_product, columnar_history_product, opening_price_reference
    modules = (causal_volume_history, qmd_volume_product, columnar_history_product, opening_price_reference)
    return v.content_hash({**{m.__name__: sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules},
                           __name__: sha256(Path(__file__).read_bytes()).hexdigest()})


@dataclass(frozen=True)
class CertifiedVolumeSource:
    opening_source: CertifiedOpeningInput
    opening_reference: CertifiedOpeningReference
    ticker: str
    first_bucket: int
    stop_bucket: int
    volume_window_seconds: int

    def __post_init__(self):
        if (type(self.opening_source) is not CertifiedOpeningInput
                or type(self.opening_reference) is not CertifiedOpeningReference
                or len(self.opening_source.market.sessions) != 1
                or self.opening_reference.scope != self.opening_source.scope(
                    self.opening_reference.scope.producer_code_hash)):
            raise ValueError('Volume source requires its exact native opening population')
        listings = [r for r in self.opening_source.listings if r.ticker == self.ticker]
        units = [u for u in self.opening_source.market.units if u.stage == 'bars'
                 and u.ticker == self.ticker and u.session_date == self.opening_source.market.sessions[0]]
        if (len(listings) != 1 or len(units) != 1
                or units[0].build_id != self.opening_source.market.build_id
                or 1000 not in self.opening_source.market.required_resolutions_ms
                or self.opening_reference.rows_by_listing.get(listings[0].listing_id, {}).get('ticker') != self.ticker):
            raise ValueError('Volume source lacks its exact certified canonical bar/listing unit')
        self.scope('00000000-0000-0000-0000-000000000001', '0' * 64)

    @property
    def bar_unit(self):
        return next(u for u in self.opening_source.market.units
                    if u.stage == 'bars' and u.ticker == self.ticker
                    and u.session_date == self.opening_source.market.sessions[0])

    @property
    def listing_id(self):
        return next(r.listing_id for r in self.opening_source.listings if r.ticker == self.ticker)

    def scope(self, attempt_id, code_hash):
        return HistoryScope(self.opening_source.market.build_id,
            date.fromisoformat(self.opening_source.market.sessions[0]), self.listing_id, self.ticker,
            attempt_id, v.product_revision(code_hash, volume_window_seconds=self.volume_window_seconds),
            self.first_bucket, self.stop_bucket)

    def parents(self, code_hash):
        return v.VolumeParents(self.bar_unit.attempt_id, v.content_hash(asdict(self.bar_unit)),
            self.opening_reference.content_hash, self.opening_source.market.token, code_hash,
            self.volume_window_seconds)


def verify_volume_sources(reader, sources, *, ownership=None):
    """Verify common population once and every exact source unit in bounded batches."""
    if (type(sources) is not tuple or not 1 <= len(sources) <= 25000
            or any(type(s) is not CertifiedVolumeSource for s in sources)
            or len({s.listing_id for s in sources}) != len(sources)):
        raise ValueError('Volume verification requires unique native source units')
    first = sources[0]
    if any(s.opening_source != first.opening_source or s.opening_reference != first.opening_reference
           for s in sources):
        raise ValueError('Volume source batch crosses a certified session/population')
    if ownership is not None:
        ownership.assert_owner()
    source_storage_preflight(reader, listing_basis=first.opening_source.listing_basis)
    first.opening_source.verify(reader)
    opening = certify_opening_reference(reader, first.opening_reference.scope,
                                        expected_content_hash=first.opening_reference.content_hash)
    if opening != first.opening_reference:
        raise ValueError('Volume opening source changed')
    for offset in range(0, len(sources), 128):
        if ownership is not None:
            ownership.assert_owner()
        units = tuple(s.bar_unit for s in sources[offset:offset + 128])
        _verify_market_batch(first.opening_source.market, 'bars', 'bars_v1',
                             first.bar_unit.session_date, units, reader)


def read_volume_source_packet(reader, sources):
    """Read at most 32 audited listings x 1024 elapsed seconds per query."""
    if (type(sources) is not tuple or not 1 <= len(sources) <= 32
            or any(type(s) is not CertifiedVolumeSource for s in sources)
            or len({s.ticker for s in sources}) != len(sources)
            or any((s.opening_source, s.first_bucket, s.stop_bucket) !=
                   (sources[0].opening_source, sources[0].first_bucket, sources[0].stop_bucket) for s in sources)):
        raise ValueError('Volume source read requires one bounded canonical packet')
    first = sources[0]
    tickers = {s.ticker for s in sources}
    pairs = ','.join(f'({literal(s.ticker)},toUUID({literal(s.bar_unit.attempt_id)}))' for s in sources)
    parts = []
    for begin in range(first.first_bucket, first.stop_bucket, 1024):
        stop = min(begin + 1024, first.stop_bucket)
        bound = len(sources) * (stop - begin)
        where = (f'build_id={literal(first.opening_source.market.build_id)} '
            f'AND session_date=toDate({literal(first.bar_unit.session_date)}) '
            f'AND (ticker,attempt_id) IN ({pairs}) AND resolution_ms=1000 '
            f'AND bucket_index>={begin} AND bucket_index<{stop}')
        rows = _rows(reader, f'SELECT ticker,bucket_index,volume FROM arte.bars_v1 '
            f'WHERE {where} ORDER BY ticker,bucket_index LIMIT {bound + 1}', bound + 1)
        if len(rows) > bound:
            raise ValueError('Volume source packet exceeds exact clock coverage')
        for row in rows:
            if set(row) != {'ticker', 'bucket_index', 'volume'} or row['ticker'] not in tickers:
                raise ValueError('Volume source packet escaped its canonical identity')
            bucket = row['bucket_index']
            if type(bucket) is str and bucket.isascii() and bucket.isdecimal():
                bucket = int(bucket)
            if type(bucket) is not int or not begin <= bucket < stop:
                raise ValueError('Volume source clock escaped its admitted packet')
            if (type(row['volume']) not in (int, float) or not math.isfinite(row['volume'])
                    or not 0 <= row['volume'] <= 2 ** 53):
                raise ValueError('Volume source has invalid canonical shares')
            row['bucket_index'] = bucket
        parts.append(pl.DataFrame(rows, schema={'ticker': pl.String, 'bucket_index': pl.UInt32, 'volume': pl.Float64}))
    frame = pl.concat(parts)
    if frame.select(pl.struct('ticker', 'bucket_index').is_duplicated().any()).item():
        raise ValueError('Volume source packet has duplicate clocks')
    groups = frame.partition_by('ticker', as_dict=True)
    empty = frame.clear().drop('ticker')
    return {s.ticker: groups[(s.ticker,)].drop('ticker') if (s.ticker,) in groups else empty for s in sources}


def publish_volume_batch(reader, writer, sources, *, ownership, code_hash,
                         maximum_retained_bytes=256 * 1024 * 1024, progress=None, stop_requested=None,
                         reconcile_requests=()):
    """Publish one admitted population in <=32-source packets; seal each unit last.

    Any existing unsealed child or ambiguous write fails closed. No automatic
    retry, overwrite, deletion or cross-build volume reuse is performed.
    """
    from src.backend.qmd_volume_reader import (
        permission_preflight, read_seal, scope_sql, read_intervals, VolumePinRequest,
    )
    v.require_hash(code_hash)
    if (type(sources) is not tuple or not 1 <= len(sources) <= 25000
            or reader is writer or not getattr(reader, 'base_url', None)
            or reader.base_url != getattr(writer, 'base_url', None)
            or type(maximum_retained_bytes) is not int or maximum_retained_bytes < 1
            or progress is not None and not callable(progress)
            or stop_requested is not None and not callable(stop_requested)
            or type(reconcile_requests) is not tuple
            or any(type(r) is not VolumePinRequest for r in reconcile_requests)):
        raise ValueError('Volume publication requires bounded sources and separate same-endpoint clients')
    reconciliations = {r.scope.listing_id: r for r in reconcile_requests}
    by_listing = {s.listing_id: s for s in sources}
    if (len(reconciliations) != len(reconcile_requests)
            or not set(reconciliations) <= set(by_listing)
            or any(r.source != by_listing[listing] or r.parents != by_listing[listing].parents(code_hash)
                   for listing, r in reconciliations.items())):
        raise ValueError('Volume reconciliation differs from its explicit source attempt/hash')
    if permission_preflight(reader) == permission_preflight(writer, writable=True):
        raise ValueError('Volume publication requires isolated reader/writer principals')
    storage_preflight(reader, tables=v.TABLES)
    verify_volume_sources(reader, sources, ownership=ownership)
    projected = []
    for offset in range(0, len(sources), 32):
        if stop_requested is not None and stop_requested():
            raise InterruptedError('Volume publication stopped before a source packet')
        packet = sources[offset:offset + 32]
        bars = read_volume_source_packet(reader, packet)
        for source in packet:
            ownership.assert_owner()
            probe = source.scope('00000000-0000-0000-0000-000000000001', code_hash)
            existing = read_seal(reader, probe)
            parents = source.parents(code_hash)
            reconciliation = reconciliations.get(source.listing_id)
            if existing:
                if reconciliation is not None and (existing['derivation_attempt_id'] != reconciliation.scope.attempt_id
                        or existing['content_hash'] != reconciliation.expected_content_hash):
                    raise ValueError('Volume reconciliation differs from existing coverage')
                scope = source.scope(existing['derivation_attempt_id'], code_hash)
                rows = v.prepare_intervals(scope, bars[source.ticker], parents)
                if v.seal(scope, parents, rows, certified_at=existing['certified_at']) != existing:
                    raise ValueError('Existing volume product differs from its canonical source')
                actual = read_intervals(reader, scope)
                if actual != rows:
                    raise ValueError('Existing volume children differ from their source projection')
                projected.append((source, scope, parents, existing, 'skipped_verified'))
                continue
            attempts = _rows(reader, f'SELECT DISTINCT derivation_attempt_id FROM q_live.{v.INTERVAL.name} '
                f'WHERE {scope_sql(probe, attempt=False)} LIMIT 2', 2)
            if attempts and (reconciliation is None or any(
                    a['derivation_attempt_id'] != reconciliation.scope.attempt_id for a in attempts)):
                raise ValueError('Unsealed volume attempt requires explicit operator reconciliation')
            if reconciliation is not None and not attempts:
                raise ValueError('Volume reconciliation has no persisted attempt to verify')
            scope = reconciliation.scope if reconciliation is not None else source.scope(str(uuid4()), code_hash)
            rows = v.prepare_intervals(scope, bars[source.ticker], parents)
            header = v.seal(scope, parents, rows, certified_at=datetime.now(timezone.utc))
            if reconciliation is not None and header['content_hash'] != reconciliation.expected_content_hash:
                raise ValueError('Volume reconciliation differs from its immutable projected content hash')
            if progress is not None:
                # Caller may durably retain this exact attempt/hash before the
                # first INSERT, enabling explicit reconciliation after failure.
                progress(dict(stage='volume_projected', active=1, queued=len(sources)-len(projected)-1,
                    completed=0, skipped=0, retried=0, failed=0, scope=scope,
                    expected_content_hash=header['content_hash']))
            if reconciliation is None:
                iterator = iter(rows)
                while chunk := tuple(islice(iterator, 4096)):
                    ownership.assert_owner()
                    insert_product_rows(writer, v.INTERVAL, chunk)
            actual = read_intervals(reader, scope)
            if actual != rows:
                raise ValueError('Volume child readback differs before coverage publication')
            projected.append((source, scope, parents, header, 'published'))
    # Recheck every source after children and before issuing ANY new seal.
    verify_volume_sources(reader, sources, ownership=ownership)
    receipts, retained_bytes = [], 0
    for source, scope, parents, header, status in projected:
        ownership.assert_owner()
        if status == 'published':
            if read_seal(reader, scope) is not None:
                raise ValueError('Volume coverage appeared during exclusive publication')
            insert_product_rows(writer, v.COVERAGE, (header,))
        acknowledged = read_seal(reader, scope)
        if acknowledged != header:
            raise ValueError('Volume coverage acknowledgement is ambiguous')
        rows = read_intervals(reader, scope)
        if len(rows) != header['interval_row_count'] or v.records_hash(rows) != header['interval_content_hash']:
            raise ValueError('Volume interval acknowledgement differs from the projected seal')
        from src.backend.qmd_volume_reader import CertifiedVolumePin
        pin = CertifiedVolumePin(scope, parents, header, source, v.interval_runs(rows))
        retained_bytes += pin.retained_interval_bytes
        if retained_bytes > maximum_retained_bytes:
            raise MemoryError('Published volume interval receipts exceed the admitted byte budget')
        receipts.append((status, pin))
        if progress is not None:
            progress(dict(stage='volume_sealed', active=0, queued=len(sources)-len(receipts),
                completed=sum(s == 'published' for s, _ in receipts),
                skipped=sum(s == 'skipped_verified' for s, _ in receipts), retried=0, failed=0,
                scope=scope, expected_content_hash=header['content_hash']))
    ownership.assert_owner()
    return tuple(receipts)
