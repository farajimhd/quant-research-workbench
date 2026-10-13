"""Producer checkpoints in the existing normalized market-day certificate tables.

These unfinished producer receipts are never consumer admission evidence.
Consumers still require the complete native inventory and final Keeper fence.
No SQLite, disk ledger, schema creation or market calculation is owned here.
"""
from dataclasses import dataclass, field
from datetime import date
import json
import re
from types import MappingProxyType
from threading import Lock

from pipelines.market_sip.events.market_day_sql import literal
from src.trading_runtime.arte_market_day_certification import (
    STAGES, _COLUMNS, _typed_readback_row,
)
from src.trading_runtime.arte_market_day_keeper import BuildClaim, MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient, _placement


UNIT = 'market_day_stage_certificate_v1'
SEED = 'market_day_seed_v1'


def _scope(build_id, scopes):
    if (type(build_id) is not str or re.fullmatch(r'[0-9a-f]{64}(?:-[0-9a-f]{12})?', build_id) is None
            or type(scopes) is not tuple or not 1 <= len(scopes) <= 1000000):
        raise ValueError('Market-day checkpoints require a bounded immutable build scope')
    normalized = []
    for scope in scopes:
        if (type(scope) is not tuple or len(scope) != 2
                or type(scope[0]) is not str or date.fromisoformat(scope[0]).isoformat() != scope[0]
                or type(scope[1]) is not str or re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,31}', scope[1]) is None):
            raise ValueError('Market-day checkpoint scope is invalid')
        normalized.append(scope)
    if len(set(normalized)) != len(normalized):
        raise ValueError('Market-day checkpoint scope is duplicate')
    return tuple(sorted(normalized))


def _read(reader, name, build_id, scopes):
    if name not in (UNIT, SEED) or not 1 <= len(scopes) <= 256:
        raise ValueError('Market-day checkpoint read packet is invalid')
    bound = len(scopes) * (len(STAGES) if name == UNIT else 1)
    keys = ','.join(f'(toDate({literal(day)}),{literal(ticker)})' for day, ticker in scopes)
    text = reader.execute(f"SELECT {','.join(_COLUMNS[name])} FROM arte.{name} "
        f"WHERE build_id={literal(build_id)} AND (session_date,ticker) IN ({keys}) "
        f"ORDER BY session_date,ticker{' ,stage' if name == UNIT else ''} LIMIT {bound + 1} FORMAT JSONEachRow")
    if not isinstance(text, str) or len(text.encode()) > 8 * 1024 * 1024:
        raise ValueError('Market-day checkpoint response exceeds its byte bound')
    rows = [_typed_readback_row(name, json.loads(line)) for line in text.splitlines() if line.strip()]
    if len(rows) > bound:
        raise ValueError('Market-day checkpoint rows exceed the complete scope bound')
    return rows


@dataclass(frozen=True)
class CompletedMarketDayLedger:
    build_id: str
    scopes: tuple
    units: object
    seeds: object
    _scope_keys: frozenset = field(init=False, repr=False)

    def __post_init__(self):
        scopes = _scope(self.build_id, self.scopes)
        allowed = set(scopes)
        units, seeds = {}, {}
        for name, rows, target in ((UNIT, self.units, units), (SEED, self.seeds, seeds)):
            for supplied in rows:
                row = _typed_readback_row(name, supplied)
                key = (row['session_date'], row['ticker'])
                if (row['build_id'] != self.build_id or key not in allowed
                        or (name == UNIT and row['stage'] not in STAGES)):
                    raise ValueError('Market-day checkpoint differs from its planned build scope')
                if name == UNIT:
                    key += (row['stage'],)
                if key in target:
                    raise ValueError('Market-day checkpoint has duplicate receipt keys')
                target[key] = MappingProxyType(row)
        object.__setattr__(self, 'scopes', scopes)
        object.__setattr__(self, '_scope_keys', frozenset(scopes))
        object.__setattr__(self, 'units', MappingProxyType(units))
        object.__setattr__(self, 'seeds', MappingProxyType(seeds))

    def _require_scope(self, build_id, day, ticker):
        if build_id != self.build_id or (day, ticker) not in self._scope_keys:
            raise ValueError('Market-day checkpoint lookup is outside its immutable scope')

    def unit(self, build_id, day, ticker, stage):
        self._require_scope(build_id, day, ticker)
        if stage not in STAGES:
            raise ValueError('Unknown market-day checkpoint stage')
        row = self.units.get((day, ticker, stage))
        return None if row is None else {**row, 'status': 'complete'}

    def seed(self, build_id, day, ticker):
        self._require_scope(build_id, day, ticker)
        row = self.seeds.get((day, ticker))
        return None if row is None else dict(row)


async def load_completed_market_day_ledger(reader_factory, build_id, scopes):
    """Own bounded certificate reads off-loop; never open a writer or producer."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    scopes = _scope(build_id, scopes)
    if not callable(reader_factory):
        raise ValueError('Market-day checkpoints require an explicit reader factory')
    def load():
        reader = reader_factory()
        try:
            if reader.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Market-day checkpoint loader requires SELECT-only transport')
            _placement(reader)
            units, seeds = [], []
            for offset in range(0, len(scopes), 256):
                packet = scopes[offset:offset + 256]
                units.extend(_read(reader, UNIT, build_id, packet))
                seeds.extend(_read(reader, SEED, build_id, packet))
            return CompletedMarketDayLedger(build_id, scopes, tuple(units), tuple(seeds))
        finally:
            reader.close()
    return await _owned_worker(load)


class MarketDayCheckpointWriter:
    """Append verified completed receipts with native producer ownership fencing."""
    def __init__(self, reader, writer, keeper, claim, scopes):
        if (type(writer) is not MarketDayCertificateClient or type(keeper) is not MarketDayKeeperAuthority
                or type(claim) is not BuildClaim):
            raise TypeError('Market-day checkpoint writer requires native typed and Keeper authorities')
        self.scopes = _scope(claim.build_id, scopes)
        self._scope_keys = frozenset(self.scopes)
        self._commit_lock = Lock()
        self._storage_checked = False
        self.reader, self.writer, self.keeper, self.claim = reader, writer, keeper, claim

    def _owned(self):
        if not self.keeper.current(self.claim):
            raise RuntimeError('Market-day checkpoint producer ownership is stale')

    async def commit(self, name, supplied):
        """Reconcile uncertain acknowledgement by exact readback, never blind retry."""
        from src.backend.qmd_columnar_history_transport import _owned_worker
        if name not in (UNIT, SEED):
            raise ValueError('Checkpoint writer accepts only completed stage and seed receipts')
        row = _typed_readback_row(name, supplied)
        scope = (row['session_date'], row['ticker'])
        if (row['build_id'] != self.claim.build_id or scope not in self._scope_keys
                or (name == UNIT and row['stage'] not in STAGES)):
            raise ValueError('Checkpoint write differs from its immutable producer scope')
        def write_unlocked():
            self._owned()
            if not self._storage_checked:
                # Audit before starting this producer's writer lane. The final
                # native publisher separately verifies all actual part placement.
                _placement(self.reader)
                self._storage_checked = True
            def exact_rows():
                rows = _read(self.reader, name, self.claim.build_id, (scope,))
                return [r for r in rows if name != UNIT or r['stage'] == row['stage']]
            existing = exact_rows()
            if existing:
                if existing != [row]:
                    raise RuntimeError('Market-day checkpoint conflicts with existing receipt')
                self._owned()
                return 'skipped_verified'
            self._owned()
            failure = None
            try:
                self.writer.insert_typed_rows(name, (row,))
            except Exception as error:
                failure = error
            observed = exact_rows()
            self._owned()
            if observed != [row]:
                if failure is not None:
                    raise failure
                raise RuntimeError('Market-day checkpoint lacks exact completed receipt readback')
            return 'published_verified'
        def write():
            with self._commit_lock:
                return write_unlocked()
        return await _owned_worker(write)
