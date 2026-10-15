"""Native SELECT-only QMD volume certification and bounded async projection."""
import asyncio
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime
import re
import sys
from types import MappingProxyType

from pipelines.market_sip.events.market_day_sql import literal
from src.market_engine import qmd_volume_product as v
from src.market_engine.columnar_history_product import HistoryScope, completed_clock
from src.backend.qmd_columnar_history import _rows
from src.backend.qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_schema import storage_preflight


def permission_preflight(client, *, writable=False):
    from src.market_engine.columnar_history_product import TABLES as HISTORY_TABLES
    from src.market_engine.feature_source_plan import TABLES as FEATURE_TABLES
    required_writes = {('q_live', t.name) for t in v.TABLES}
    optional_groups = ({('q_live', t.name) for t in HISTORY_TABLES},
                       {('q_live', t.name) for t in FEATURE_TABLES})
    allowed_writes = required_writes | set().union(*optional_groups)
    user = client.execute('SELECT currentUser()').strip()
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', user) is None:
        raise ValueError('Volume product principal is invalid')
    reads, writes = set(), set()
    for line in client.execute('SHOW GRANTS FINAL').splitlines():
        match = re.fullmatch(r'GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*)\.'
            r'([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)', line.strip())
        if match is None or match[4] != user:
            raise ValueError('Volume product principal has delegated or unknown authority')
        for privilege in {p.strip() for p in match[1].split(',')}:
            target = match[2], match[3]
            if privilege == 'SELECT':
                reads.add(target)
            elif privilege == 'INSERT' and writable and target in allowed_writes:
                writes.add(target)
            else:
                raise ValueError('Volume product principal has unauthorized write/DDL authority')
    required = {('q_live', t.name) for t in v.TABLES} | {('system', n) for n in (
        'storage_policies', 'tables', 'columns', 'parts', 'data_skipping_indices')}
    if (not required <= reads or not writable and writes
            or writable and (not required_writes <= writes
                or any(bool(writes & group) and not group <= writes for group in optional_groups))):
        raise ValueError('Volume product principal grants are incomplete')
    return user


def scope_sql(scope, *, attempt=True):
    if type(scope) is not HistoryScope:
        raise TypeError('Volume reader requires an exact typed clock scope')
    result = (f'source_build_id={literal(scope.build_id)} '
        f'AND session_date=toDate({literal(scope.session_date.isoformat())}) '
        f'AND listing_id={literal(scope.listing_id)} AND ticker={literal(scope.ticker)} '
        f'AND product_revision={literal(scope.product_revision)} '
        f'AND first_bucket={scope.first_bucket} AND stop_bucket={scope.stop_bucket}')
    if attempt:
        result += f' AND derivation_attempt_id=toUUID({literal(scope.attempt_id)})'
    return result


def read_seal(reader, scope):
    rows = _rows(reader, f'SELECT * FROM q_live.{v.COVERAGE.name} '
        f'WHERE {scope_sql(scope, attempt=False)} LIMIT 2', 2)
    if len(rows) > 1:
        raise ValueError('Volume product has ambiguous coverage')
    return v.restore_record(v.COVERAGE, rows[0]) if rows else None


def read_intervals(reader, scope, *, first_ordinal=0, stop_ordinal=None):
    stop_ordinal = scope.clock_count if stop_ordinal is None else stop_ordinal
    if (type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal <= scope.clock_count):
        raise ValueError('Volume interval request exceeds its clock scope')
    result, cursor = [], -1
    while True:
        rows = _rows(reader, f'SELECT * FROM q_live.{v.INTERVAL.name} WHERE {scope_sql(scope)} '
            f'AND stop_ordinal>{first_ordinal} AND first_ordinal<{stop_ordinal} '
            f'AND first_ordinal>{cursor} ORDER BY first_ordinal LIMIT 4097', 4097)
        normalized = tuple(v.restore_record(v.INTERVAL, r) for r in rows)
        if not normalized:
            break
        if (normalized[0]['first_ordinal'] <= cursor
                or any(b['first_ordinal'] <= a['first_ordinal'] for a, b in zip(normalized, normalized[1:]))):
            raise ValueError('Volume interval source has duplicate or unordered keys')
        # Validate the lookahead too, so a repeated boundary key cannot hide
        # behind the cursor. Its unique identity is read again on the next page.
        normalized = normalized[:4096]
        result.extend(normalized)
        if len(result) > scope.clock_count:
            raise ValueError('Volume interval source exceeds its finite clock envelope')
        cursor = normalized[-1]['first_ordinal']
        if len(normalized) < 4096:
            break
    v.validate_intervals(scope, result, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal)
    return tuple(result)


@dataclass(frozen=True)
class VolumePinRequest:
    scope: HistoryScope
    parents: v.VolumeParents
    expected_content_hash: str
    source: object

    def __post_init__(self):
        from pipelines.market_sip.events.qmd_volume_producer import CertifiedVolumeSource
        v.require_hash(self.expected_content_hash)
        if (type(self.scope) is not HistoryScope or type(self.parents) is not v.VolumeParents
                or type(self.source) is not CertifiedVolumeSource
                or self.scope != self.source.scope(self.scope.attempt_id, self.parents.producer_code_hash)
                or self.parents != self.source.parents(self.parents.producer_code_hash)):
            raise ValueError('Volume request differs from its exact native source lineage')


@dataclass(frozen=True)
class CertifiedVolumePin:
    scope: HistoryScope
    parents: v.VolumeParents
    header: object
    source: object
    intervals: tuple[v.VolumeRun, ...]
    _starts: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self):
        header = v.restore_record(v.COVERAGE, dict(self.header))
        VolumePinRequest(self.scope, self.parents, header['content_hash'], self.source)
        if (any(header[k] != value for k, value in {**self.scope.prefix(), **self.parents.payload()}.items())
                or header['elapsed_clock_count'] != self.scope.clock_count
                or not 1 <= header['interval_row_count'] <= self.scope.clock_count
                or header['content_hash'] != v.content_hash({k: value for k, value in header.items()
                    if k not in {'content_hash', 'certified_at'}})):
            raise ValueError('Volume pin differs from its cold coverage proof')
        if (type(self.intervals) is not tuple or any(type(r) is not v.VolumeRun for r in self.intervals)
                or len(self.intervals) != header['interval_row_count']):
            raise ValueError('Volume pin requires its complete immutable interval snapshot')
        rows = tuple(v.run_records(self.scope, self.intervals))
        v.validate_intervals(self.scope, rows)
        if v.records_hash(rows) != header['interval_content_hash']:
            raise ValueError('Volume snapshot differs from its cold interval hash')
        object.__setattr__(self, 'header', MappingProxyType(header))
        object.__setattr__(self, '_starts', tuple(r.first_ordinal for r in self.intervals))

    @property
    def retained_interval_bytes(self):
        # Conservative object accounting includes scalar objects even when
        # Python happens to share a small integer across adjacent intervals.
        return sys.getsizeof(self.intervals) + sys.getsizeof(self._starts) + sum(sys.getsizeof(r)
            + sys.getsizeof(r.first_ordinal) + sys.getsizeof(r.stop_ordinal)
            + sys.getsizeof(r.rolling_volume) for r in self.intervals)


def _read_certified_pin(reader, request):
    header = read_seal(reader, request.scope)
    if header is None or header['content_hash'] != request.expected_content_hash:
        raise ValueError('Volume coverage is missing or differs from its expected content hash')
    rows = read_intervals(reader, request.scope)
    if len(rows) != header['interval_row_count'] or v.records_hash(rows) != header['interval_content_hash']:
        raise ValueError('Volume children differ from their sealed coverage')
    if read_seal(reader, request.scope) != header:
        raise ValueError('Volume coverage changed during cold certification')
    return CertifiedVolumePin(request.scope, request.parents, header, request.source, v.interval_runs(rows))


def certify_volume_product(reader, request):
    from pipelines.market_sip.events.qmd_volume_producer import verify_volume_sources
    if type(request) is not VolumePinRequest:
        raise TypeError('Volume certification requires an exact native request')
    permission_preflight(reader)
    storage_preflight(reader, tables=v.TABLES)
    verify_volume_sources(reader, (request.source,))
    pin = _read_certified_pin(reader, request)
    verify_volume_sources(reader, (request.source,))
    return pin


async def certify_volume_pins_async(reader_factory, requests, *, concurrency,
                                   maximum_retained_bytes=256 * 1024 * 1024):
    """One shared census proof, fixed readers, every exact source unit audited."""
    from pipelines.market_sip.events.qmd_volume_producer import verify_volume_sources
    if (not callable(reader_factory) or type(requests) is not tuple or not 1 <= len(requests) <= 25000
            or any(type(r) is not VolumePinRequest for r in requests)
            or len({r.scope.listing_id for r in requests}) != len(requests)
            or type(concurrency) is not int or not 1 <= concurrency <= 6
            or type(maximum_retained_bytes) is not int or maximum_retained_bytes < 1):
        raise ValueError('Volume certification requires bounded native reader admission')
    sources = tuple(r.source for r in requests)
    def verify():
        reader = reader_factory()
        try:
            permission_preflight(reader)
            storage_preflight(reader, tables=v.TABLES)
            verify_volume_sources(reader, sources)
        finally:
            reader.close()
    await _owned_worker(verify)
    pending, result, retained_bytes = iter(requests), {}, 0
    async def worker():
        nonlocal retained_bytes
        while (request := next(pending, None)) is not None:
            def read():
                reader = reader_factory()
                try:
                    return _read_certified_pin(reader, request)
                finally:
                    reader.close()
            pin = await _owned_worker(read)
            retained_bytes += pin.retained_interval_bytes
            if retained_bytes > maximum_retained_bytes:
                raise MemoryError('Certified volume interval population exceeds its admitted byte budget')
            result[pin.scope.listing_id] = pin
    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(requests))):
            group.create_task(worker())
    await _owned_worker(verify)
    return MappingProxyType(result)


def preflight_volume_window(pin, *, first_ordinal, stop_ordinal, as_of):
    """Validate a complete bounded window without expansion or network I/O."""
    if (type(pin) is not CertifiedVolumePin
            or type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal <= pin.scope.clock_count
            or stop_ordinal - first_ordinal > 4096
            or not isinstance(as_of, datetime) or as_of.tzinfo is None
            or completed_clock(pin.scope, pin.scope.first_bucket + stop_ordinal - 1) > as_of):
        raise ValueError('Volume window lacks its bounded certified completed scope')


def volume_window(pin, *, first_ordinal, stop_ordinal, as_of):
    """Expand immutable QMD-owned source intervals; never calculate volume here."""
    preflight_volume_window(pin, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, as_of=as_of)
    first = max(0, bisect_right(pin._starts, first_ordinal) - 1)
    stop = bisect_left(pin._starts, stop_ordinal)
    rows = tuple(v.run_records(pin.scope, pin.intervals[first:stop]))
    return v.expand_window(pin.scope, rows, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal)


async def read_volume_window_async(pin, *, first_ordinal, stop_ordinal, as_of, reader_factory=None):
    """Offload native snapshot projection; optional factory is never a fallback."""
    if (type(pin) is not CertifiedVolumePin or reader_factory is not None and not callable(reader_factory)
            or type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal <= pin.scope.clock_count
            or stop_ordinal - first_ordinal > 4096
            or not isinstance(as_of, datetime) or as_of.tzinfo is None
            or completed_clock(pin.scope, pin.scope.first_bucket + stop_ordinal - 1) > as_of):
        raise ValueError('Volume window lacks its bounded certified completed scope')
    def project():
        return volume_window(pin, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, as_of=as_of)
    return await _owned_worker(project)


async def verify_volume_pin_headers_async(reader_factory, pins):
    """Refresh immutable snapshot admission using bounded native coverage batches."""
    if (not callable(reader_factory) or type(pins) is not tuple or not pins
            or len(pins) > 25000 or any(type(p) is not CertifiedVolumePin for p in pins)
            or len({p.scope.listing_id for p in pins}) != len(pins)):
        raise ValueError('Volume snapshot refresh requires exact bounded cold pins')
    def verify():
        reader = reader_factory()
        try:
            permission_preflight(reader)
            storage_preflight(reader, tables=v.TABLES)
            for begin in range(0, len(pins), 32):
                packet = pins[begin:begin + 32]
                predicate = ' OR '.join(f'({scope_sql(p.scope, attempt=False)})' for p in packet)
                rows = _rows(reader, f'SELECT * FROM q_live.{v.COVERAGE.name} '
                    f'WHERE {predicate} ORDER BY listing_id LIMIT {len(packet) + 1}', len(packet) + 1)
                normalized = [v.restore_record(v.COVERAGE, r) for r in rows]
                expected = {p.scope.listing_id: dict(p.header) for p in packet}
                if (len(normalized) != len(packet) or len({r['listing_id'] for r in normalized}) != len(packet)
                        or any(expected.get(r['listing_id']) != r for r in normalized)):
                    raise ValueError('Volume snapshot coverage changed or became ambiguous')
        finally:
            reader.close()
    await _owned_worker(verify)
