"""Bounded, shared, causal prefixes of structural chart history.

Each state transition is evaluated once per retained session/start/source.
The cursor only advances to the requested cutoff; rewinds slice the retained
prefix and never expose future rows or reset another reader's cursor.
"""
from bisect import bisect_right
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
import json

import numpy as np

from src.trading_runtime.normalized_level_book import CONTRACT, transform


class ChartTimeline:
    def __init__(self, build_id, ticker, start, fingerprint, contract=CONTRACT):
        self.identity = (build_id, ticker, start, fingerprint)
        self.contract = contract
        self.lock = RLock()
        self.cursor = None
        self.next_index = 0
        self.output = []
        self.output_stamps = []
        self.previous = None
        self.bytes = 0
        self.transitions = 0

    def rows(self, end, after=None):
        from src.backend.experimental_structure_book import BookCursor, NY, micros, session_normalization
        build_id, ticker, start, fingerprint = self.identity
        if end < start or start.astimezone(NY).date() != end.astimezone(NY).date():
            raise ValueError('Experimental chart requires one session and a valid causal window')
        with self.lock:
            if self.cursor is None:
                self.cursor = BookCursor(build_id, ticker, fingerprint)
                self.cursor.advance(start)
                self.basis = session_normalization(build_id, ticker, start.astimezone(NY).date().isoformat(), fingerprint, contract=self.contract)
                # Timestamps are source metadata, not evaluated future states.
                self.timestamps = sorted({micros(start),
                    *(int(r['known_us']) for r in self.cursor.observations if int(r['known_us']) > micros(start)),
                    *(int(r['born_us']) for r in self.cursor.levels if int(r['born_us']) > micros(start))})
                self.raw = {}
                self.raw_previous = None
                self.visible_previous = np.zeros(len(self.cursor.levels), dtype=bool)
            stop = bisect_right(self.timestamps, micros(end))
            while self.next_index < stop:
                stamp = self.timestamps[self.next_index]
                at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=stamp)
                self.cursor.advance(at)
                visible = self.cursor.born <= stamp
                projected = self.cursor.state[:, [0, 1, 3, 4, 9]]
                changed = visible if self.raw_previous is None else visible & (
                    (projected != self.raw_previous).any(axis=1) | ~self.visible_previous)
                self.next_index += 1
                self.transitions += 1
                if not changed.any() and self.output:
                    continue
                self.raw.update((r['unified_level_id'], r) for r in self.cursor.project(np.flatnonzero(changed)))
                current = {r['unified_level_id']: r for r in transform(list(self.raw.values()), self.basis)['unified_levels']}
                row = {'bar_start': at.isoformat(), 'bar_end': at.isoformat()}
                if self.previous is None:
                    row['qmd_structure_unified_levels'] = list(current.values())
                else:
                    upserts = [r for key, r in current.items() if self.previous.get(key) != r]
                    removed = [dict(unified_level_id=key, side=r['side']) for key, r in self.previous.items() if key not in current]
                    if upserts or removed:
                        row['qmd_structure_unified_level_delta'] = dict(upserts=upserts, removed=removed)
                self.raw_previous, self.visible_previous = projected.copy(), visible.copy()
                self.previous = current
                if len(row) > 2:
                    self.output.append(row)
                    self.output_stamps.append(stamp)
                    self.bytes += len(json.dumps(row, separators=(',', ':')))
            # Readers own their returned dictionaries; no cross-chart mutation.
            begin = bisect_right(self.output_stamps, micros(after)) if after is not None else 0
            return deepcopy(self.output[begin:bisect_right(self.output_stamps, micros(end))])


_CACHE = OrderedDict()
_LOCK = RLock()
MAX_SESSIONS = 4
MAX_BYTES = 64 * 1024 * 1024


def chart_rows(build_id, ticker, start, end, fingerprint=None, *, after=None, contract=CONTRACT):
    from src.backend.experimental_structure_book import resolve, micros
    build = resolve(build_id)
    if fingerprint is not None and fingerprint != build['fingerprint']:
        raise ValueError('Experimental book fingerprint changed')
    key = (build_id, ticker, micros(start), build['fingerprint'], contract)
    with _LOCK:
        timeline = _CACHE.get(key)
        if timeline is None:
            timeline = ChartTimeline(build_id, ticker, start, build['fingerprint'], contract)
            _CACHE[key] = timeline
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_SESSIONS:
            _CACHE.popitem(last=False)
    try:
        return timeline.rows(end, after)
    except Exception:
        with _LOCK:
            if _CACHE.get(key) is timeline:
                _CACHE.pop(key)
        raise
    finally:
        with _LOCK:
            # Oversized prefixes remain valid responses but are not retained.
            # Eviction affects performance only, never visible history.
            while _CACHE and sum(t.bytes for t in _CACHE.values()) > MAX_BYTES:
                _CACHE.popitem(last=False)
