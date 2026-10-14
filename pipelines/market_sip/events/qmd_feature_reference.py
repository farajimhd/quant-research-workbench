"""Point-in-time structural and security features for a pinned listing."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

import numpy as np

from pipelines.market_sip.events.market_day_sql import literal
from src.backend.qmd_columnar_history import _rows
POLICY='live_market_ssd'
def query(client,sql):return _rows(client,sql,25000)
from src.backend.structural_v7_seed import load_seed
from src.backend.structural_v7_seed import _validate_coverage

STRUCTURAL = ('structural_levels_v7', 'structural_level_observations_v7',
              'structural_level_coverage_v7')
VERSION = 'arte-prior-structural-qlive-reference-v2'


def opening(day: date) -> str:
    value = datetime.combine(day, time(4), ZoneInfo('America/New_York')).astimezone(timezone.utc)
    return value.strftime('%Y-%m-%d %H:%M:%S.%f') + '000'


def storage_check(client) -> None:
    names = ','.join(literal(name) for name in STRUCTURAL)
    rows = query(client, f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({names})")
    if {row['name'] for row in rows} != set(STRUCTURAL) or any(row['storage_policy'] != POLICY for row in rows):
        raise ValueError('Required arte structural tables are absent or misplaced')
    parts = query(client, f"SELECT table,disk_name FROM system.parts WHERE database='arte' AND active AND table IN ({names}) GROUP BY table,disk_name")
    if any(row['disk_name'] != POLICY for row in parts):
        raise ValueError('Structural V7 parts are outside live_market_ssd')


def _identity(listing: dict) -> str:
    keys = ('symbol_id', 'listing_id', 'security_id')
    if any(not listing.get(key) for key in keys):
        raise ValueError('Pinned listing lacks security identity')
    return ' AND '.join(f'{key}={literal(listing[key])}' for key in keys)


def _read_reference(client, day: date, listing: dict) -> tuple[dict, list[dict], dict, dict]:
    """Use prior-session V7 and q_live records known before 04:00 ET."""
    ticker = str(listing['ticker'])
    cutoff = f"toDateTime64({literal(opening(day))},9,'UTC')"
    identity = _identity(listing)
    coverage = query(client, 'SELECT * '
        'FROM arte.structural_level_coverage_v7 FINAL '
        f'WHERE ticker={literal(ticker)} AND available_at<={cutoff} '
        'ORDER BY available_at DESC LIMIT 1')
    if len(coverage) != 1 or coverage[0]['state'] not in ('complete','empty') or str(coverage[0]['session_date']) >= str(day):
        raise ValueError(f'No certified prior-session structural V7 coverage: {day} {ticker}')
    certificate = coverage[0]
    seed = load_seed(client,ticker=ticker,session=day,coverage=certificate)
    floats = query(client, 'SELECT effective_date,free_float,shares_outstanding,inserted_at,source_content_sha256 '
        'FROM q_live.market_security_float_v1 FINAL '
        f'WHERE {identity} AND effective_date<=toDate({literal(day)}) AND inserted_at<={cutoff} '
        'ORDER BY effective_date DESC,inserted_at DESC LIMIT 1')
    splits = query(client, 'SELECT execution_date,split_from,split_to,inserted_at,source_content_sha256 '
        'FROM q_live.market_stock_split_v1 FINAL '
        f'WHERE {identity} AND execution_date<=toDate({literal(day)}) AND inserted_at<={cutoff} '
        'ORDER BY execution_date DESC,inserted_at DESC')
    fundamental = fundamentals(day, floats, splits)
    by_day = {}
    split_conflicts = set()
    for row in splits:
        key = str(row['execution_date'])
        previous = by_day.get(key)
        if previous and not math.isclose(
                float(previous['split_to'])/float(previous['split_from']),
                float(row['split_to'])/float(row['split_from']),rel_tol=1e-12,abs_tol=0.):
            split_conflicts.add(key)
        if previous is None:
            by_day[key] = row
    if any(key > str(seed['session']) for key in split_conflicts):
        raise ValueError(f'Conflicting q_live split ratios after V7 seed: {ticker} '
            + ', '.join(sorted(key for key in split_conflicts if key > str(seed['session']))))
    evidence = {'coverage':certificate,'float':floats[0] if floats else None,
                'splits':splits,'split_conflict_dates':sorted(split_conflicts),'contract':VERSION}
    evidence['hash'] = sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()
    stream_splits = [by_day[key] for key in sorted(by_day) if key > str(seed['session'])]
    return seed, stream_splits, fundamental, evidence


def fundamentals(day: date, floats: list[dict], splits: list[dict]) -> dict:
    row = floats[0] if floats else {}
    def quantity(name):
        value = row.get(name)
        if value is None:
            return 0., 0.
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f'Invalid {name} in q_live reference data')
        return float(np.log1p(number)), 1.
    float_value,float_present = quantity('free_float')
    shares,shares_present = quantity('shares_outstanding')
    factors_by_day = {}
    for split in splits:
        split_day = date.fromisoformat(str(split['execution_date']))
        before,after = float(split['split_from']),float(split['split_to'])
        if not all(math.isfinite(x) and x > 0 for x in (before,after)):
            raise ValueError('Invalid q_live split ratio')
        factor = after/before
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('Invalid q_live split ratio')
        factors_by_day.setdefault(split_day,[]).append(factor)
    certain_split = uncertain_split = certain_reverse = uncertain_reverse = None
    for split_day,factors in factors_by_day.items():
        changed = [not math.isclose(factor,1.,rel_tol=1e-12,abs_tol=0.) for factor in factors]
        reverse = [factor < 1. for factor in factors]
        if all(changed):
            certain_split = split_day if certain_split is None else max(certain_split,split_day)
        elif any(changed):
            uncertain_split = split_day if uncertain_split is None else max(uncertain_split,split_day)
        if all(reverse):
            certain_reverse = split_day if certain_reverse is None else max(certain_reverse,split_day)
        elif any(reverse):
            uncertain_reverse = split_day if uncertain_reverse is None else max(uncertain_reverse,split_day)
    def age(value):
        return (float(np.log1p((day-value).days)),1.) if value else (0.,0.)
    split_age,split_present = age(certain_split) if uncertain_split is None or (
        certain_split is not None and certain_split > uncertain_split) else (0.,0.)
    reverse_age,reverse_present = age(certain_reverse) if uncertain_reverse is None or (
        certain_reverse is not None and certain_reverse > uncertain_reverse) else (0.,0.)
    return dict(log_float_shares=float_value,float_present=float_present,
        log_shares_outstanding=shares,shares_present=shares_present,
        log_days_since_split=split_age,split_present=split_present,
        log_days_since_reverse_split=reverse_age,reverse_split_present=reverse_present)


def read_reference(client,day,listing):
    """Missing prior structural coverage is explicit; corrupt coverage fails."""
    try:
        return _read_reference(client,day,listing)
    except ValueError as error:
        if 'No certified prior-session structural V7 coverage' not in str(error):raise
    cutoff="toDateTime64("+literal(opening(day))+",9,'UTC')"
    coverage=query(client,'SELECT state,session_date FROM arte.structural_level_coverage_v7 FINAL '
        f"WHERE ticker={literal(listing['ticker'])} AND available_at<={cutoff} ORDER BY available_at DESC LIMIT 1")
    if coverage:raise ValueError('Existing structural coverage is invalid, not missing')
    identity=_identity(listing)
    floats=query(client,'SELECT effective_date,free_float,shares_outstanding,inserted_at,source_content_sha256 '
        f'FROM q_live.market_security_float_v1 FINAL WHERE {identity} AND effective_date<=toDate({literal(day)}) '
        f'AND inserted_at<={cutoff} ORDER BY effective_date DESC,inserted_at DESC LIMIT 1')
    splits=query(client,'SELECT execution_date,split_from,split_to,inserted_at,source_content_sha256 '
        f'FROM q_live.market_stock_split_v1 FINAL WHERE {identity} AND execution_date<=toDate({literal(day)}) '
        f'AND inserted_at<={cutoff} ORDER BY execution_date DESC,inserted_at DESC LIMIT 10001')
    if len(splits)>10000:raise ValueError('Split reference exceeds its bounded history')
    evidence=dict(ticker=listing['ticker'],day=str(day),float=floats[0] if floats else None,
        splits=splits,v7_coverage='absent',contract='qmd-missing-prior-structure-masked-v1')
    evidence['hash']=sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()
    return None,[],fundamentals(day,floats,splits),evidence
