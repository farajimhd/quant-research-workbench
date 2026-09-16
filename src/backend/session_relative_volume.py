"""Bounded, run-owned QMD RVOL baseline artifacts; never substitute stale dates."""
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite, isnan
import mmap
import os
import operator
from pathlib import Path
import re
import struct
from threading import RLock
from uuid import uuid4

from src.backend.qmd_gateway_client import qmd_history_post_json, qmd_historical_source_revision
from src.trading_runtime.session_relative_volume import BASELINE_CONTRACT

HASH_CONTRACT = 'typed-json-sha256-1'
PROJECTION_CONTRACT = 'session-relative-volume-batch-projection-1'
REVISION_FIELDS = ('token', 'source_plan_hash', 'calculation_revision', 'corporate_action_revision')


def baseline_content_hash(value):
    """Hash JSON types, independent of whitespace and floating-point spelling.

    Tags: n=null, b=boolean+ASCII 0/1, i=decimal integer, f=IEEE754 BE,
    s=UTF8 string, a=array, o=object. Integer/string lengths and array/object
    counts are unsigned 64-bit BE. Object keys sort lexically and use s tags.
    Only the root content_hash is replaced with an empty string.
    """
    digest = sha256()
    def length(n):
        digest.update(struct.pack('>Q', n))
    def emit(item):
        if item is None:
            digest.update(b'n')
        elif type(item) is bool:
            digest.update(b'b1' if item else b'b0')
        elif type(item) is int:
            if not -(1 << 63) <= item < (1 << 64):
                raise ValueError('RVOL hash integer outside JSON producer range')
            data = str(item).encode('ascii')
            digest.update(b'i'); length(len(data)); digest.update(data)
        elif type(item) is float:
            if not isfinite(item):
                raise ValueError('RVOL hash requires finite numbers')
            digest.update(b'f'); digest.update(struct.pack('>d', item))
        elif type(item) is str:
            data = item.encode('utf-8')
            digest.update(b's'); length(len(data)); digest.update(data)
        elif type(item) is list:
            digest.update(b'a'); length(len(item))
            for child in item:
                emit(child)
        elif type(item) is dict and all(type(k) is str for k in item):
            digest.update(b'o'); length(len(item))
            for key in sorted(item):
                emit(key); emit(item[key])
        else:
            raise ValueError('Unsupported RVOL hash value')
    emit(dict(value, content_hash=''))
    return 'sha256:' + digest.hexdigest()


def _validate_baselines(value, tickers, session):
    if value.get('contract') != BASELINE_CONTRACT or value.get('session_date') != str(session):
        raise ValueError('RVOL baseline contract/session mismatch')
    dates = [date.fromisoformat(s) for s in value.get('sessions', [])]
    if len(dates) != 20 or dates != sorted(set(dates)) or dates[-1] >= date.fromisoformat(str(session)):
        raise ValueError('RVOL requires 20 distinct prior sessions')
    if any(day.weekday() >= 5 for day in dates):
        raise ValueError('RVOL prior sessions include a weekend')
    day = dates[0]
    while day < date.fromisoformat(str(session)):
        if day.weekday() < 5 and day not in dates:
            raise ValueError('RVOL missing weekday requires closed-session proof')
        day += timedelta(days=1)
    stamp = datetime.fromisoformat(value['session_start'])
    from zoneinfo import ZoneInfo
    local = stamp.astimezone(ZoneInfo('America/New_York'))
    if stamp.tzinfo is None or str(local.date()) != str(session) or local.hour != 4 or local.minute or local.second or local.microsecond:
        raise ValueError('RVOL session boundary mismatch')
    revision = value.get('source_revision') or {}
    if revision.get('complete_for_history') is not True or revision.get('request_complete') is not True:
        raise ValueError('RVOL source revision is incomplete')
    if any(not isinstance(revision.get(k), str) or not revision[k] for k in ('token', 'source_plan_hash')):
        raise ValueError('RVOL source revision identity missing')
    if value.get('boundary_seconds') != 1 or not value.get('content_hash'):
        raise ValueError('RVOL baseline identity missing')
    if (value.get('content_hash_contract') != HASH_CONTRACT
            or value['content_hash'] != baseline_content_hash(value)):
        raise ValueError('RVOL baseline content hash changed or unsupported')
    if not 1 <= len(tickers) <= 16 or set(value.get('profiles', {})) != set(tickers):
        raise ValueError('RVOL baseline ticker/profile mismatch')
    for ticker in tickers:
        profile = value['profiles'][ticker]
        if not isinstance(profile, list) or len(profile) != 57601:
            raise ValueError('RVOL baseline ticker/profile mismatch')
        if profile[0] is not None:
            raise ValueError('RVOL opening boundary must have no denominator')
        previous = 0.
        for x in profile:
            if x is None:
                if previous > 0:
                    raise ValueError('RVOL cumulative profile regressed')
            elif type(x) not in (int, float) or not isfinite(x) or x <= 0 or x < previous:
                raise ValueError('Invalid RVOL cumulative baseline')
            else:
                previous = x
    return value


def _source_query(value, tickers):
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('America/New_York')
    return dict(start=datetime.combine(date.fromisoformat(value['sessions'][0]),
                                      datetime.min.time().replace(hour=4), zone).isoformat(),
                end=datetime.combine(date.fromisoformat(value['sessions'][-1]),
                                    datetime.min.time().replace(hour=20), zone).isoformat(),
                tickers=sorted(tickers))


def validate_baseline(value, ticker, session):
    _validate_baselines(value, [ticker], session)
    provenance = value.get('batch_provenance')
    if provenance is not None:
        tickers = provenance.get('tickers', [])
        revision = provenance.get('source_revision', {})
        if (provenance.get('contract') != PROJECTION_CONTRACT
                or not isinstance(tickers, list) or not 1 <= len(tickers) <= 16
                or tickers != sorted(set(tickers)) or ticker not in tickers
                or any(not re.fullmatch(r'[A-Z0-9._-]{1,32}', item) for item in tickers)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', provenance.get('content_hash', ''))
                or provenance.get('source_query') != _source_query(value, tickers)
                or revision.get('complete_for_history') is not True
                or revision.get('request_complete') is not True
                or any(not isinstance(revision.get(k), str) or not revision[k] for k in REVISION_FIELDS)
                or any(revision[k] != value['source_revision'][k] for k in ('token', 'source_plan_hash'))):
            raise ValueError('RVOL batch projection provenance mismatch')
    return value


def _write_artifact(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class _MappedProfile(Sequence):
    """Read-only doubles; NaN encodes the validated JSON null denominator.

    Virtual address space is 460,808 bytes per ticker. OS page residency is
    demand-managed; indexing performs no Python file reads or JSON parsing.
    """
    def __init__(self, path):
        self._path = path
        with path.open('rb') as stream:
            if os.fstat(stream.fileno()).st_size != 57601 * 8:
                raise ValueError('RVOL packed profile has invalid length')
            self._mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)

    def __len__(self):
        return 57601

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(len(self))))
        index = operator.index(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        value = struct.unpack_from('>d', self._mapping, index * 8)[0]
        return None if isnan(value) else value

    def close(self):
        self._mapping.close()
        self._path.unlink(missing_ok=True)


class BaselineStore:
    """Run-owned mappings; prepare asynchronously, read cached views synchronously.

    No profile eviction: current acquisition evidence never disappears because
    another ticker was prepared. Fetches hydrate at most 16 JSON profiles;
    shared/run artifact reads and mmap conversion handle one ticker at a time.
    close() releases every mapping; mappings are never checkpoint authority.
    """
    def __init__(self, directory, session, expected=None, *, allowed_tickers=None, shared_directory=None):
        self.directory = Path(directory)
        self.session = str(session)
        self.identities = dict(expected or {})
        self.cache = {}
        self.allowed_tickers = None if allowed_tickers is None else frozenset(allowed_tickers)
        self._lock = RLock()
        self._closed = False
        self._mapping_id = uuid4().hex
        self.shared_directory = None if shared_directory is None else Path(shared_directory) / self.session
        self.status = {}

    def _check_ticker(self, ticker):
        if not isinstance(ticker, str) or not re.fullmatch(r'[A-Z0-9._-]{1,32}', ticker):
            raise ValueError('Invalid RVOL ticker')
        if self.allowed_tickers is not None and ticker not in self.allowed_tickers:
            raise ValueError('RVOL ticker is outside the prepared population')

    def prepare_many(self, tickers, progress=None, stopped=None):
        """Synchronous preflight: serial batches <=16, no network during playback.

        progress(completed,total) executes on the calling worker. status exposes
        restored/shared/fetched counts. A stop raises InterruptedError between
        bounded units, preserving completed run-owned artifacts for restart.
        """
        with self._lock:
            if self._closed:
                raise ValueError('RVOL baseline store is closed')
            tickers = sorted(set(tickers))
            for ticker in tickers:
                self._check_ticker(ticker)
            self.status = dict(total=len(tickers), completed=0, restored=0, shared=0, fetched=0, stage='preparing')
            def checkpoint():
                if stopped is not None and stopped():
                    self.status['stage'] = 'stopped'
                    raise InterruptedError('RVOL baseline preparation stopped')
            def completed(kind):
                self.status[kind] += 1
                self.status['completed'] += 1
                if progress is not None:
                    progress(self.status['completed'], len(tickers))
            if progress is not None:
                progress(0, len(tickers))
            # Revalidate each original shared batch once, at its original scope.
            revisions = {}
            missing = []
            for ticker in tickers:
                checkpoint()
                path = self.directory / (ticker+'.json')
                if ticker in self.cache or path.exists() or ticker in self.identities:
                    self._prepare(ticker)  # Missing pinned artifacts fail, never refetch.
                    completed('restored')
                elif self._restore_shared(ticker, revisions):
                    self._prepare(ticker)
                    completed('shared')
                else:
                    missing.append(ticker)
            for offset in range(0, len(missing), 16):
                checkpoint()
                batch = missing[offset:offset+16]
                self.status['stage'] = 'fetching'
                value = qmd_history_post_json('/features/session-relative-volume-baseline',
                    {'session_date': self.session, 'tickers': batch}, timeout=900)
                _validate_baselines(value, batch, self.session)
                query = _source_query(value, batch)
                revision = qmd_historical_source_revision(**query)
                if (revision.get('complete_for_history') is not True or revision.get('request_complete') is not True
                        or any(not isinstance(revision.get(k), str) or not revision[k] for k in REVISION_FIELDS)
                        or any(revision[k] != value['source_revision'][k] for k in ('token', 'source_plan_hash'))):
                    raise ValueError('RVOL batch source revision changed or incomplete')
                provenance = dict(contract=PROJECTION_CONTRACT, content_hash=value['content_hash'],
                                  tickers=batch, source_query=query, source_revision=revision)
                for ticker in batch:
                    checkpoint()
                    # Keep original source scope/counts; explicitly identify this
                    # single-profile projection instead of pretending QMD hashed it.
                    projected = dict(value, profiles={ticker:value['profiles'][ticker]},
                                     batch_provenance=provenance, content_hash='')
                    projected['content_hash'] = baseline_content_hash(projected)
                    validate_baseline(projected, ticker, self.session)
                    raw = json.dumps(projected, separators=(',', ':'), sort_keys=True).encode()
                    _write_artifact(self.directory / (ticker+'.json'), raw)
                    if self.shared_directory is not None:
                        _write_artifact(self.shared_directory / (ticker+'.json'), raw)
                    self._prepare(ticker)
                    completed('fetched')
                # Release the batch before issuing the next request.
                del value, projected, raw
            self.status['stage'] = 'ready'
            return dict(self.status)

    def _restore_shared(self, ticker, revisions):
        if self.shared_directory is None:
            return False
        path = self.shared_directory / (ticker+'.json')
        if not path.exists():
            return False
        try:
            raw = path.read_bytes()
            value = validate_baseline(json.loads(raw), ticker, self.session)
            provenance = value.get('batch_provenance')
            if provenance is None:
                return False  # Legacy lacks full calculation/corporate authority.
            key = json.dumps(provenance, sort_keys=True, separators=(',', ':'))
            if key not in revisions:
                revision = qmd_historical_source_revision(**provenance['source_query'])
                revisions[key] = (revision.get('complete_for_history') is True
                    and revision.get('request_complete') is True
                    and all(revision.get(k) == provenance['source_revision'][k] for k in REVISION_FIELDS))
            if not revisions[key]:
                return False
        except (ValueError, KeyError, TypeError):
            return False
        _write_artifact(self.directory / (ticker+'.json'), raw)
        return True

    def cached(self, ticker):
        """Memory-only lookup; a miss must be prepared off the event loop."""
        if self._closed:
            raise ValueError('RVOL baseline store is closed')
        if ticker not in self.cache:
            raise ValueError('RVOL baseline is not prepared')
        return self.cache[ticker]

    def get(self, ticker):
        """Prepare once, normally via asyncio.to_thread; never use on a hot miss."""
        with self._lock:
            if self._closed:
                raise ValueError('RVOL baseline store is closed')
            return self._prepare(ticker)

    def _prepare(self, ticker):
        self._check_ticker(ticker)
        if ticker in self.cache:
            return self.cache[ticker]
        path = self.directory / (ticker+'.json')
        if path.exists():
            raw = path.read_bytes()
            value = validate_baseline(json.loads(raw), ticker, self.session)
        else:
            if ticker in self.identities:
                raise ValueError('Checkpoint RVOL artifact is missing')
            value = validate_baseline(qmd_history_post_json('/features/session-relative-volume-baseline',
                {'session_date': self.session, 'tickers': [ticker]}, timeout=900), ticker, self.session)
            raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
            _write_artifact(path, raw)
        identity = sha256(raw).hexdigest()
        if ticker in self.identities and self.identities[ticker] != identity:
            raise ValueError('Checkpoint RVOL artifact changed')
        # Rebuild from validated, pinned JSON on every store lifetime. A stale
        # or modified packed file is never accepted as independent authority.
        packed = bytearray(57601 * 8)
        for index, item in enumerate(value['profiles'][ticker]):
            struct.pack_into('>d', packed, index * 8, float('nan') if item is None else item)
        packed_path = self.directory / (ticker + '.' + identity + '.' + self._mapping_id + '.f64')
        temporary = packed_path.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            stream.write(packed)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(packed_path)
        profile = _MappedProfile(packed_path)
        compact = {key: item for key, item in value.items() if key != 'profiles'}
        compact['profiles'] = {ticker: profile}
        self.identities[ticker] = identity
        self.cache[ticker] = compact
        return compact

    def close(self):
        with self._lock:
            for value in self.cache.values():
                for profile in value['profiles'].values():
                    profile.close()
            self.cache.clear()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
