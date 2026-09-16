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

from src.backend.qmd_gateway_client import qmd_history_post_json
from src.trading_runtime.session_relative_volume import BASELINE_CONTRACT

HASH_CONTRACT = 'typed-json-sha256-1'


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


def validate_baseline(value, ticker, session):
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
    profile = value.get('profiles', {}).get(ticker)
    if set(value.get('profiles', {})) != {ticker} or not isinstance(profile, list) or len(profile) != 57601:
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
    another ticker was prepared. Only one JSON profile is hydrated at a time.
    close() releases every mapping; mappings are never checkpoint authority.
    """
    def __init__(self, directory, session, expected=None, *, allowed_tickers=None):
        self.directory = Path(directory)
        self.session = str(session)
        self.identities = dict(expected or {})
        self.cache = {}
        self.allowed_tickers = None if allowed_tickers is None else frozenset(allowed_tickers)
        self._lock = RLock()
        self._closed = False
        self._mapping_id = uuid4().hex

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
        if not re.fullmatch(r'[A-Z0-9._-]{1,32}', ticker):
            raise ValueError('Invalid RVOL ticker')
        if self.allowed_tickers is not None and ticker not in self.allowed_tickers:
            raise ValueError('RVOL ticker is outside the prepared population')
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
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(raw)
            temporary.replace(path)
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
