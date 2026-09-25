"""Point-in-time structural and security features for a pinned listing."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

import numpy as np

from research.rl_trading.v1.arte_sql import POLICY, literal, query
from src.backend.structural_v7_seed import load_seed
from src.backend.structural_v7_seed import _validate_coverage

STRUCTURAL = ('structural_levels_v7', 'structural_level_observations_v7',
              'structural_level_coverage_v7')
VERSION = 'arte-prior-structural-qlive-reference-v1'


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


def missing_seeds(client, day: date, tickers: list[str]) -> list[str]:
    """Fail before shard allocation if any pinned listing lacks a prior V7 seed."""
    cutoff = f"toDateTime64({literal(opening(day))},9,'UTC')"
    rows = query(client, 'SELECT DISTINCT ticker FROM arte.structural_level_coverage_v7 FINAL '
        f'WHERE available_at<={cutoff} AND session_date<toDate({literal(day)}) '
        "AND state IN ('complete','empty')")
    available = {str(row['ticker']) for row in rows}
    return sorted(set(tickers)-available)


def nonempty_v7_population(client, day: date, tickers: list[str]) -> dict:
    """Pin the latest pre-open V7 certificate and exclude absent/empty seeds."""
    cutoff = f"toDateTime64({literal(opening(day))},9,'UTC')"
    rows = query(client, 'SELECT * FROM arte.structural_level_coverage_v7 FINAL '
        f'WHERE available_at<={cutoff} AND ticker IN (' +
        ','.join(literal(ticker) for ticker in tickers) + ') '
        'ORDER BY ticker,available_at DESC LIMIT 1 BY ticker')
    by_ticker = {str(row['ticker']): row for row in rows}
    if len(by_ticker) != len(rows) or not set(by_ticker) <= set(tickers):
        raise ValueError('Malformed V7 coverage population')
    included, excluded, certificates = [], {}, {}
    for ticker in tickers:
        row = by_ticker.get(ticker)
        if row is None:
            excluded[ticker] = 'missing_prior_coverage'
        elif row['state'] not in ('complete', 'empty') or str(row['session_date']) >= str(day):
            excluded[ticker] = 'uncertified_prior_coverage'
        elif int(row['level_count']) == 0:
            excluded[ticker] = 'empty_prior_levels'
        else:
            _validate_coverage(row,ticker=ticker,session=day)
            included.append(ticker)
            certificates[ticker] = {key:row[key] for key in (
                'session_date','available_at','source_checkpoint_hash','level_count',
                'observation_count','input_policy')}
    if not included:
        raise ValueError('No tickers have certified nonempty prior V7 levels')
    return dict(contract='nonempty-prior-v7-at-0400-v1',included=included,
        excluded=excluded,certificates=certificates)


def _identity(listing: dict) -> str:
    keys = ('symbol_id', 'listing_id', 'security_id')
    if any(not listing.get(key) for key in keys):
        raise ValueError('Pinned listing lacks security identity')
    return ' AND '.join(f'{key}={literal(listing[key])}' for key in keys)


def read_reference(client, day: date, listing: dict) -> tuple[dict, list[dict], dict, dict]:
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
    evidence = {'coverage':certificate,'float':floats[0] if floats else None,
                'splits':splits,'contract':VERSION}
    evidence['hash'] = sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()
    by_day = {}
    for row in splits:
        key = str(row['execution_date'])
        previous = by_day.get(key)
        if previous and (float(previous['split_from']),float(previous['split_to'])) != (
                float(row['split_from']),float(row['split_to'])):
            raise ValueError(f'Conflicting q_live split ratios: {ticker} {key}')
        if previous is None:
            by_day[key] = row
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
    seen = set()
    last_split = last_reverse = None
    for split in splits:
        split_day = date.fromisoformat(str(split['execution_date']))
        before,after = float(split['split_from']),float(split['split_to'])
        if not all(math.isfinite(x) and x > 0 for x in (before,after)):
            raise ValueError('Invalid q_live split ratio')
        if split_day in seen:
            continue
        seen.add(split_day)
        if before != after:
            last_split = split_day if last_split is None else max(last_split,split_day)
            if after < before:
                last_reverse = split_day if last_reverse is None else max(last_reverse,split_day)
    def age(value):
        return (float(np.log1p((day-value).days)),1.) if value else (0.,0.)
    split_age,split_present = age(last_split)
    reverse_age,reverse_present = age(last_reverse)
    return dict(log_float_shares=float_value,float_present=float_present,
        log_shares_outstanding=shares,shares_present=shares_present,
        log_days_since_split=split_age,split_present=split_present,
        log_days_since_reverse_split=reverse_age,reverse_split_present=reverse_present)
