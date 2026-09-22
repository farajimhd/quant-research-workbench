"""Run-scoped QMD streaming state over certified, immutable prepared inputs.

Loading arrays is not observation: only rows through the requested cutoff enter
the shared V7 kernel. Inactive states spill losslessly to local runtime storage.
"""
from collections import OrderedDict
from math import prod
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import time

import numpy as np
import psutil

from .streaming_level_book import StreamingLevelBook
from .v7_snapshot_transport import Encoder


def retained_bytes(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(retained_bytes(k, seen) + retained_bytes(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set)):
        size += sum(retained_bytes(v, seen) for v in value)
    return size


def source_clock_identity(token):
    """Window-independent certified source identity; counts vary with the window."""
    head, tail = token.split(':Archive:', 1)
    build, _count, continuation, updated = head.split(':', 3)
    _window, policy = tail.split(':split-sha256:', 1)
    return build, continuation, updated, policy


def certified_causal_bar_authority(row):
    """Accept only the QMD closed-bar profiles used by V7 causal seconds."""
    token = row.get('revision_token', '')
    source_policy = (':structure-input-v1:archive-sip-condition:recent-participant-aware:' in token
        or ':execution-clock-v1:' in token)
    return (row.get('authority') in ('qmd_history_prepared_closed_bars',
                                    'qmd_history_derived', 'qmd_history_derived_bundle')
        and row.get('calculation_revision') in ('qmd-derived-v58', 'qmd-derived-v59-0405-et')
        and row.get('complete_for_history') is True
        and bool(row.get('source_plan_hash'))
        and source_policy)


class PreparedStream:
    def __init__(self, service, path, day, catalog_hash, *, max_bytes=4 * 1024**3, required_end=None):
        from .v7_qmd import session_bounds, stamp
        if service.catalog.fingerprint != catalog_hash:
            raise ValueError('Prepared V7 catalog identity changed')
        self.service = service
        self.path = Path(path).resolve()
        self.day = day
        self.begin, self.end = session_bounds(day)
        self.required_end = stamp(required_end) if required_end else self.end
        if not self.begin <= self.required_end <= self.end:
            raise ValueError('Prepared V7 requested end is outside the session')
        self.file_identity = self._file_identity()
        self.max_bytes = max_bytes
        self.bytes = 0
        from .v7_resident_states import ResidentStates
        self.states = ResidentStates(self.path.parent)
        self.process = psutil.Process()
        self.memory_checked_at = 0.
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.db.execute('PRAGMA query_only=ON')

    def _file_identity(self):
        from .v7_preparation_cache import identity
        return identity(self.path), identity(str(self.path) + '-wal')

    def close(self):
        self.db.close()
        self.states.clear()

    def prepare(self, tickers, expected_revisions=None):
        from .v7_qmd import stamp
        if self.file_identity != self._file_identity():
            raise ValueError('Prepared V7 source changed during warm-up')
        result = []
        for ticker in tickers:
            expected=(expected_revisions or {}).get(ticker)
            if ticker in self.states:
                if self.states[ticker]['expected_revision'] != expected:
                    raise ValueError('Prepared V7 resume source identity changed')
                result.append(self.states[ticker]['receipt'])
                continue
            record = self.db.execute("SELECT authority_json FROM strategy_frame_streams WHERE ticker=? AND timeframe='1s'", (ticker,)).fetchone()
            if record is None:
                raise ValueError(f'No complete prepared 1s stream for {ticker}')
            authority = json.loads(record[0])
            chunks = authority.get('chunks', [authority])
            if not authority.get('complete_for_history') or not chunks or any(
                not certified_causal_bar_authority(row) for row in chunks
            ):
                raise ValueError(f'{ticker}: prepared bars lack the certified V7 causal source-clock contract')
            if authority.get('start') and (stamp(authority['start']) > self.begin or stamp(authority['end']) < self.required_end):
                raise ValueError(f'{ticker}: prepared stream does not cover the requested causal prefix')
            cache = self.service.preparation_cache
            bar_key = cache.key(['prepared-bars-v1', str(self.path), self.file_identity, ticker, self.day, authority])
            bars = cache.get(bar_key)
            bars_reused = bars is not None
            if bars is None:
                values = []
                for micros, encoded in self.db.execute("SELECT as_of_us,bar_json FROM strategy_frames WHERE ticker=? AND timeframe='1s' ORDER BY as_of_us,sequence", (ticker,)):
                    bar = json.loads(encoded)
                    second = micros / 1_000_000
                    if not self.begin.timestamp() < second <= self.end.timestamp():
                        continue
                    if bar.get('sym') != ticker or bar.get('timeframe') != '1s' or stamp(bar['bar_end']).timestamp() != second or second != int(second):
                        raise ValueError('Prepared V7 bar identity/timestamp mismatch')
                    row = [second, *(float(bar[k]) for k in ('open', 'high', 'low', 'close', 'volume'))]
                    if values and second <= values[-1][0]:
                        raise ValueError('Duplicate or unordered prepared V7 bar')
                    if not np.isfinite(row).all() or row[3] <= 0 or row[5] < 0 or row[3] > min(row[1], row[4]) or row[2] < max(row[1], row[4]):
                        raise ValueError('Invalid prepared V7 OHLCV')
                    values.append(row)
                bars = np.asarray(values, dtype=np.float64).reshape((-1, 6))
                from .v7_preparation_cache import identity
                if self.file_identity != self._file_identity():
                    raise ValueError('Prepared V7 source changed during validation')
                cache.put(bar_key, bars, {str(self.path): identity(self.path),
                    str(self.path) + '-wal': identity(str(self.path) + '-wal')})
            bars.flags.writeable = False
            if expected is not None:
                try:
                    compatible=all(source_clock_identity(row['revision_token']) == source_clock_identity(expected['token']) for row in chunks)
                except (KeyError,ValueError):
                    compatible=False
                if not compatible:
                    # Lookback windows can include earlier splits, producing a
                    # different split digest even when today's bars are equal.
                    # Prove exact current-session equality before reusing them.
                    canonical,audit=self.service.source.seconds(ticker,self.day,self.begin.timestamp(),self.end.timestamp(),'history')
                    if audit.get('source_revision') != expected:
                        raise ValueError('Prepared V7 canonical source changed since the restart checkpoint')
                    available_end=stamp(authority['end']).timestamp() if authority.get('end') else self.end.timestamp()
                    reference=np.asarray([[row[k] for k in ('t','open','high','low','close','volume')]
                        for row in canonical if row['t']<=available_end],dtype=np.float64).reshape((-1,6))
                    if not np.array_equal(reference,bars):
                        raise ValueError('Prepared V7 bars differ from the resumed canonical source')
            prior, provenance = self.service._seed(ticker, self.day, self.begin.timestamp(), 'history')
            if prior['available_at'] > self.begin.timestamp():
                raise ValueError('Prepared V7 checkpoint is not available at the session opening')
            splits = self.service.source.splits(ticker, prior['session'], self.day, self.begin)
            from .filtered_v7_history import kernel
            seed_key = cache.key(['opening-engine-v1', self.service.catalog.fingerprint, kernel(),
                ticker, self.day, prior['checkpoint_hash'], splits])
            engine = cache.get(seed_key)
            seed_reused = engine is not None
            if engine is None:
                engine = StreamingLevelBook(prior, ticker=ticker, session=self.day,
                    start=self.begin.timestamp(), end=self.end.timestamp(),
                    split_factor=prod(float(s['split_from'])/float(s['split_to']) for s in splits), split_evidence=splits)
                cache.put(seed_key, engine, {})
            if engine.bars_processed != 0:
                raise ValueError('Cached V7 seed contains playback state')
            size = retained_bytes(engine.__dict__) + bars.nbytes
            if size > self.max_bytes:
                raise ValueError('Prepared V7 working set exceeds the explicit resident memory budget; no eviction fallback')
            receipt = dict(ticker=ticker, checkpoint_hash=prior['checkpoint_hash'],
                input_hash=hashlib.sha256(bars.tobytes()).hexdigest(), bars=len(bars), retained_bytes=size,
                prepared_bars_reused=bars_reused, opening_seed_reused=seed_reused)
            self.states[ticker] = dict(engine=engine, bars=bars, offset=0, cutoff=self.begin.timestamp(),
                provenance={k:v for k,v in provenance.items() if k!='source_plan'},
                authority=authority, receipt=receipt, encoder=Encoder(), projection=None, revision=None,
                snapshots=OrderedDict(), failure=None,expected_revision=expected)
            self.bytes += size
            result.append(receipt)
        return result

    def snapshot(self, ticker, as_of, base_version=None):
        from .v7_qmd import projection, stamp, BOOK_ID, VERSION
        at = stamp(as_of) if isinstance(as_of, str) else as_of
        if at.tzinfo is None:
            raise ValueError('Prepared V7 cutoff requires a timezone')
        cutoff = int(at.timestamp())
        state = self.states[ticker]
        if cutoff in state['snapshots']:
            return state['encoder'].encode(state['snapshots'][cutoff],base_version,compact=True)
        if state['failure']:
            raise ValueError(state['failure'])
        if not state['cutoff'] <= cutoff <= self.required_end.timestamp():
            raise ValueError('Prepared V7 stream cannot rewind or cross the session boundary')
        engine, bars = state['engine'], state['bars']
        stop = int(np.searchsorted(bars[:, 0], cutoff, side='right'))
        try:
            for values in bars[state['offset']:stop]:
                engine.update(dict(zip(('t','open','high','low','close','volume'), map(float, values))), observed_at=cutoff)
        except Exception as exc:
            state['failure'] = f'Prepared V7 input failed; rebuild the stream before advancing: {exc}'
            raise
        state['offset'] = stop
        state['cutoff'] = cutoff
        revision = getattr(engine, '_projection_revision', 0)
        changed = state['projection'] is None or state['revision'] != revision
        if changed:
            state['projection'] = projection(engine, cutoff, state['provenance'], False)['unified_levels']
            state['revision'] = revision
        value = engine.snapshot(cutoff, include_segments=False)
        value.pop('segments', None)
        levels = state['projection']
        value.update(unified_levels=levels, qmd_structure_unified_levels=levels,
            qmd_level_book_version=VERSION, provenance=state['provenance'], book_id=BOOK_ID,
            source_mode='history', source_audit=dict(source='qmd-prepared-causal-seconds',
                input_hash=state['receipt']['input_hash'], consumed_through=cutoff,
                source_revision=dict(authority='qmd-prepared-causal-seconds-v1',request_complete=True,
                    input_hash=state['receipt']['input_hash'],prepared_authority=state['authority'],
                    **({'verified_resume_revision':state['expected_revision']} if state['expected_revision'] is not None else {})),
                late_trade_policy='qmd-completed-seconds-excludes-delayed-reports'))
        state['snapshots'][cutoff]=value
        while len(state['snapshots'])>32:state['snapshots'].popitem(last=False)
        packet = state['encoder'].encode(value, base_version, compact=True)
        now=time.monotonic()
        if now-self.memory_checked_at>=1:
            self.memory_checked_at=now
            if self.process.memory_info().rss > self.max_bytes:
                state['snapshots'].pop(cutoff, None)
                state['failure'] = 'Prepared V7 worker exceeded the resident memory budget'
                raise ValueError(state['failure'])
        return packet
