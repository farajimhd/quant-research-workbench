"""Causal backtest continuation of a validated ClickHouse closing-book build.

Only compact level/state/observation columns are fetched, never SIP events.
Independent cursors serve strategy frames, trade events and chart review.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
from threading import RLock
from zoneinfo import ZoneInfo

import numpy as np
from src.trading_runtime.normalized_level_book import CONTRACT

from research.mlops.clickhouse import (ClickHouseHttpClient, default_clickhouse_url,
    default_clickhouse_user, default_clickhouse_password)

VERSION = 'clickhouse-closing-book-1'
ROOT = Path(r'D:\TradingML\runtimes\structure-validation')
NY = ZoneInfo('America/New_York')


def builds():
    result = []
    for path in sorted(ROOT.glob('*/report.json')):
        validation = path.with_name('validation.json')
        if not validation.is_file():
            continue
        report = json.loads(path.read_text())
        proof = json.loads(validation.read_text())
        if report.get('version') != VERSION or proof.get('status') != 'passed':
            continue
        if report.get('status') != 'built_pending_quality_acceptance':
            continue
        if proof.get('database') != report.get('database'):
            continue
        result.append({'id': report['database'], 'ticker': report['ticker'],
            'version': VERSION, 'start': report['requested_start'],
            'end': report['actual_end'], 'fingerprint': report['fingerprint'],
            'runtime': str(path.parent)})
    return result


def resolve(build_id):
    if not re.fullmatch(r'structure_book_[a-f0-9]{12}', build_id):
        raise ValueError('Invalid experimental book identifier')
    matches = [row for row in builds() if row['id'] == build_id]
    if len(matches) != 1:
        raise ValueError('Experimental book is missing or not validated')
    return matches[0]


def rows(sql):
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(),
        default_clickhouse_password(), timeout_seconds=60,
        default_query_params={'readonly': 1, 'max_threads': 2,
            'max_memory_usage': 536870912, 'max_result_rows': 100000,
            'max_result_bytes': 16000000, 'result_overflow_mode': 'throw'})
    return [json.loads(line) for line in client.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def micros(value):
    if value.tzinfo is None:
        raise ValueError('An aware as-of timestamp is required')
    delta = value.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


@lru_cache(maxsize=16)
def load_day(build_id, ticker, session, fingerprint):
    build = resolve(build_id)
    if build['fingerprint'] != fingerprint or ticker != build['ticker']:
        raise ValueError('Book source identity changed or ticker is not covered')
    manifest = json.loads((Path(build['runtime'])/'source_manifest.json').read_text())
    days, _, splits, _ = manifest
    dates = [row['source_date'] for row in days]
    if session not in dates:
        raise ValueError(f'Experimental book has no certified session {ticker} {session}')
    index = dates.index(session)
    prior = dates[index-1] if index else '1970-01-01'
    close = micros(datetime.combine(datetime.fromisoformat(session).date(), time(20), NY))
    data = rows(f"""SELECT toString(l.level_id) id,l.price AS price,l.lower AS lower,l.upper AS upper,l.tick AS tick,
        l.side,l.born_us,l.timeframe,p.state,c.confirmed_ordinal FROM {build_id}.levels AS l FINAL
        LEFT JOIN (SELECT level_id,state FROM {build_id}.history FINAL
          WHERE session_date='{prior}') p ON l.level_id=p.level_id
        INNER JOIN (SELECT price,confirmed_us,min(confirmed_ordinal) confirmed_ordinal
          FROM {build_id}.candidates FINAL WHERE may_found_level
          GROUP BY price,confirmed_us) c ON l.price=c.price AND l.born_us=c.confirmed_us
        WHERE l.born_us<={close} ORDER BY l.born_us,l.level_id""")
    observations = rows(f"SELECT known_us,price,prior_range FROM {build_id}.observations FINAL WHERE session_date='{session}' AND known_us<={close} ORDER BY known_us")
    factor = 1.
    for split in splits:
        if split['execution_date'] <= session:
            factor *= float(split['split_from'])/float(split['split_to'])
    return data, observations, factor


def transition(state, eligible, price, volatility, lower, upper, tick, known_us):
    """Vector form of the independently tested ClickHouse v1 transition."""
    i = np.flatnonzero(eligible)
    if not len(i):
        return
    s = state[i].copy()
    old_side, old_phase = s[:, 0].copy(), s[:, 1].copy()
    lo, hi = lower[i], upper[i]
    past = np.where(old_side > 0, price < lo-np.maximum(tick[i], volatility*.25),
                    price > hi+np.maximum(tick[i], volatility*.25))
    contact = (price >= lo) & (price <= hi)
    flip = np.where(old_side > 0, price < lo-tick[i], price > hi+tick[i])
    reject = np.where(old_side > 0, price > hi+tick[i], price < lo-tick[i])
    s[:, 2] = np.where((old_phase <= 1) & past, s[:, 2]+1, 0)
    s[:, 1] = np.select([old_phase <= 1, old_phase == 2],
        [np.where(past, np.where(s[:, 2] >= 2, 2, 1), 0), np.where(contact, 3, 2)],
        default=np.where(flip | reject, 0, 3))
    changed = (old_phase == 3) & flip
    s[changed, 0] *= -1
    s[changed, 9] = known_us
    broken = (old_phase == 1) & (s[:, 1] == 2)
    reset = broken | ((s[:, 7] != 0) & (s[:, 7] != s[:, 0]))
    finished = reset & (s[:, 6] != 0)
    s[finished, 3] += s[finished, 4]
    s[finished, 8] += 1
    s[reset, 4:7] = 0
    s[:, 7] = s[:, 0]
    active = (s[:, 1] <= 1) & ~broken
    returned = active & (s[:, 6] == 2) & contact
    s[returned, 3] += s[returned, 4]
    s[returned, 8] += 1
    s[returned, 4:7] = 0
    started = active & (s[:, 6] == 0) & contact & (volatility > 0)
    s[started, 5], s[started, 6] = volatility, 1
    reacting = active & (s[:, 6] != 0)
    distance = np.where(s[:, 0] > 0, price-hi, lo-price)
    excursion = np.maximum(distance, 0)/np.where(s[:, 5] > 0, s[:, 5], 1.)
    s[reacting, 4] = np.maximum(s[reacting, 4], excursion[reacting])
    s[reacting & (s[:, 4] >= 1), 6] = 2
    state[i] = s


class BookCursor:
    def __init__(self, build_id, ticker, fingerprint=None):
        self.build = resolve(build_id)
        if fingerprint is not None and fingerprint != self.build['fingerprint']:
            raise ValueError('Experimental book fingerprint changed')
        self.ticker = ticker
        self.session = None
        self.at = -1
        self.lock = RLock()

    def advance(self, cutoff):
        stamp = micros(cutoff)
        if not time(4) <= cutoff.astimezone(NY).time().replace(tzinfo=None) <= time(20):
            raise ValueError('Experimental continuation is available only from 04:00 through 20:00 New York')
        session = cutoff.astimezone(NY).date().isoformat()
        with self.lock:
            if session != self.session or stamp < self.at:
                self.levels, self.observations, self.factor = load_day(self.build['id'],
                    self.ticker, session, self.build['fingerprint'])
                self.born = np.array([int(r['born_us']) for r in self.levels], dtype=np.int64)
                self.confirmation_sequence = np.array([int(r['confirmed_ordinal']) for r in self.levels], dtype=np.int64)
                self.lower = np.array([r['lower'] for r in self.levels])
                self.upper = np.array([r['upper'] for r in self.levels])
                self.tick = np.array([r['tick'] for r in self.levels])
                seeds = []
                for r in self.levels:
                    s = r['state']
                    seeds.append([s[0],s[1],s[2],*s[3],s[4]] if s[0] else
                        [r['side'],0,0,0,0,0,0,0,0,int(r['born_us'])])
                self.state = np.array(seeds, dtype=float).reshape((-1, 10))
                self.index = 0
                self.session, self.at = session, -1
            while self.index < len(self.observations):
                observation = self.observations[self.index]
                known = int(observation['known_us'])
                if known > stamp:
                    break
                transition(self.state, self.born <= known-1000000, observation['price'],
                    observation['prior_range'], self.lower, self.upper, self.tick, known)
                self.index += 1
            self.at = stamp
            return self

    def snapshot(self, cutoff, sequence=None):
        with self.lock:
            self.advance(cutoff)
            visible = self.born <= self.at
            if sequence is not None:
                visible &= (self.born < self.at) | (self.confirmation_sequence <= sequence)
            # Visibility grows in causal birth/sequence order; the same count
            # at the same observation cursor denotes the same level set.
            cache_key = (self.session, self.index, int(visible.sum()))
            if getattr(self, '_snapshot_key', None) == cache_key:
                return self._snapshot_value
            output = self.project(np.flatnonzero(visible))
            self._snapshot_key, self._snapshot_value = cache_key, {'unified_levels': output}
            return self._snapshot_value

    def project(self, indices):
        output = []
        for i in indices:
            r, s = self.levels[i], self.state[i]
            output.append({'unified_level_id': r['id'], 'side': int(s[0]),
                'price': r['price']*self.factor, 'lower': r['lower']*self.factor,
                'upper': r['upper']*self.factor, 'prominence': float(np.log1p(s[3]+s[4])),
                'book_version': VERSION, 'timeframes': [r['timeframe']], 'sources': [],
                'created_at_ms': int(r['born_us'])//1000, 'confirmed_at_ms': int(s[9])//1000,
                'lifecycle': ['active','crossed','awaiting_retest','retest_contact'][int(s[1])],
                'pending_side': -int(s[0]) if s[1]>=2 else 0,
                'ticker_relative_quality_status': 'unavailable'})
        return output



def context(snapshot, price=0):
    levels = snapshot['unified_levels']
    result = {'qmd_structure_support_levels': [r for r in levels if r['side'] > 0],
              'qmd_structure_resistance_levels': [r for r in levels if r['side'] < 0]}
    for side in ['support', 'resistance']:
        candidates = [r for r in result[f'qmd_structure_{side}_levels']
                      if (r['price']<=price if side=='support' else r['price']>=price)]
        nearest = min(candidates, key=lambda r:abs(r['price']-price), default={})
        for field in ['price','lower','upper']:
            result[f'qmd_structure_{side}_{field}'] = nearest.get(field)
        result[f'qmd_structure_{side}_strength'] = None
        result[f'qmd_structure_{side}_confidence'] = None
    return result


def raw_chart_rows(build_id, ticker, start, end, fingerprint=None):
    if end < start or start.astimezone(NY).date() != end.astimezone(NY).date():
        raise ValueError('Experimental chart requires one session and a valid causal window')
    cursor = BookCursor(build_id, ticker, fingerprint)
    cursor.advance(start)
    timestamps = {micros(start)}
    timestamps.update(int(r['known_us']) for r in cursor.observations if micros(start)<int(r['known_us'])<=micros(end))
    timestamps.update(int(r['born_us']) for r in cursor.levels if micros(start)<int(r['born_us'])<=micros(end))
    previous, previous_visible, output = None, np.zeros(len(cursor.levels), dtype=bool), []
    for stamp in sorted(timestamps):
        at = datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=stamp)
        cursor.advance(at)
        visible = cursor.born <= stamp
        projected = cursor.state[:, [0,1,3,4,9]]
        changed = visible if previous is None else visible & ((projected != previous).any(axis=1) | ~previous_visible)
        if not changed.any() and output:
            continue
        levels = cursor.project(np.flatnonzero(changed))
        row = {'bar_start': at.isoformat(), 'bar_end': at.isoformat()}
        if previous is None:
            row['qmd_structure_unified_levels'] = levels
        else:
            removed = [{'unified_level_id': cursor.levels[i]['id'], 'side': int(previous[i,0])}
                for i in np.flatnonzero(previous_visible & ((projected[:,0] != previous[:,0]) | ~visible))]
            row['qmd_structure_unified_level_delta'] = {'upserts': levels, 'removed': removed}
        output.append(row)
        previous, previous_visible = projected.copy(), visible.copy()
    return output

@lru_cache(maxsize=32)
def session_normalization(build_id, ticker, session, fingerprint, ratio=1.0, *, contract=CONTRACT):
    from src.trading_runtime.normalized_level_book import calibration
    build = resolve(build_id)
    manifest = json.loads((Path(build['runtime'])/'source_manifest.json').read_text())
    dates = [row['source_date'] for row in manifest[0]]
    index = dates.index(session)
    if index == 0:
        raise ValueError('Normalized book requires a certified preceding session close')
    prior = dates[index-1]
    close_us = micros(datetime.combine(datetime.fromisoformat(prior).date(), time(16), NY))
    opening_us = micros(datetime.combine(datetime.fromisoformat(prior).date(), time(9,30), NY))
    prices = rows(f"SELECT price FROM {build_id}.observations FINAL WHERE session_date='{prior}' AND known_us>{opening_us} AND known_us<={close_us} ORDER BY known_us DESC LIMIT 1")
    if not prices:
        raise ValueError('Previous regular-session close is unavailable; normalization cannot proceed')
    at = datetime.combine(datetime.fromisoformat(session).date(), time(4), NY)
    seed = BookCursor(build_id, ticker, fingerprint)
    snapshot = seed.snapshot(at, -1)
    basis = calibration([r for r in snapshot['unified_levels'] if r['confirmed_at_ms'] < at.timestamp()*1000], float(prices[0]['price'])*seed.factor, ratio, contract=contract)
    return dict(basis, prior_session=prior, frozen_at=at.isoformat(), close_authority='last completed regular-session observation')


class NormalizedBookCursor(BookCursor):
    def snapshot(self, cutoff, sequence=None):
        from src.trading_runtime.normalized_level_book import transform
        with self.lock:
            raw = super().snapshot(cutoff, sequence)
            if getattr(self, '_normalized_raw', None) is raw:
                return self._normalized_value
            basis = session_normalization(self.build['id'], self.ticker, self.session, self.build['fingerprint'])
            self._normalized_value = transform(raw['unified_levels'], basis)
            self._normalized_raw = raw
            return self._normalized_value


def uncached_chart_rows(build_id, ticker, start, end, fingerprint=None):
    from src.trading_runtime.normalized_level_book import transform
    build = resolve(build_id)
    basis = session_normalization(build_id, ticker, start.astimezone(NY).date().isoformat(),
                                  fingerprint or build['fingerprint'])
    raw, previous, output = {}, None, []
    # Reuse the vectorized state-change projection. Rebuilding thousands of
    # unchanged raw rows at every observation needlessly slows chart review.
    for source in raw_chart_rows(build_id, ticker, start, end, fingerprint):
        if 'qmd_structure_unified_levels' in source:
            raw = {r['unified_level_id']: r for r in source['qmd_structure_unified_levels']}
        else:
            delta = source['qmd_structure_unified_level_delta']
            for row in delta['removed']:
                raw.pop(row['unified_level_id'], None)
            raw.update((r['unified_level_id'], r) for r in delta['upserts'])
        current = {r['unified_level_id']:r for r in transform(list(raw.values()), basis)['unified_levels']}
        row = {'bar_start': source['bar_start'], 'bar_end': source['bar_end']}
        if previous is None:
            row['qmd_structure_unified_levels'] = list(current.values())
        else:
            upserts = [r for key,r in current.items() if previous.get(key)!=r]
            removed = [dict(unified_level_id=key,side=r['side']) for key,r in previous.items() if key not in current]
            if not upserts and not removed:
                continue
            row['qmd_structure_unified_level_delta'] = dict(upserts=upserts,removed=removed)
        output.append(row)
        previous = current
    return output


def chart_rows(build_id, ticker, start, end, fingerprint=None, *, after=None, contract=CONTRACT):
    from src.backend.structure_chart_timeline import chart_rows as cached_rows
    return cached_rows(build_id, ticker, start, end, fingerprint, after=after, contract=contract)
