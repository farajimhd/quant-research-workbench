"""Shared elapsed-share-volume intervals over certified canonical bar sources.

Intervals cover every declared completed second, including explicit zero runs.
This product owns no price, feature, liquidity, strategy or execution state.
"""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import math
import re
from uuid import UUID

import numpy as np
import polars as pl

from . import columnar_history_product as history
from .causal_volume_history import elapsed_volume_history
from .opening_price_reference import content_hash, require_hash
from src.trading_runtime.arte_journal_schema import TableContract


REVISION = 'qmd-certified-elapsed-share-volume-interval-v1'
PREFIX = history.PREFIX
ORDER = history.ORDER
INTERVAL = TableContract('qmd_volume_interval_v1', PREFIX + (
    ('first_ordinal', 'UInt32'), ('stop_ordinal', 'UInt32'), ('rolling_volume', 'Float64')),
    'toYYYYMM(session_date)', ORDER + ',first_ordinal', database='q_live')
COVERAGE = TableContract('qmd_volume_coverage_v1', PREFIX + (
    ('source_bar_attempt_id', 'UUID'), ('source_bar_unit_hash', 'FixedString(64)'),
    ('opening_reference_hash', 'FixedString(64)'), ('market_plan_hash', 'FixedString(64)'),
    ('producer_code_hash', 'FixedString(64)'), ('volume_window_seconds', 'UInt32'),
    ('elapsed_clock_count', 'UInt32'), ('interval_row_count', 'UInt32'),
    ('interval_content_hash', 'FixedString(64)'), ('content_hash', 'FixedString(64)'),
    ('certified_at', "DateTime64(6, 'UTC')")), 'toYYYYMM(session_date)', ORDER, database='q_live')
TABLES = (INTERVAL, COVERAGE)


def plan_required_grants(reader='program_market_input_reader', publisher='program_history_publisher'):
    """Return only the exact new-product grant delta; never execute SQL."""
    if any(type(user) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', user) is None
           for user in (reader, publisher)) or reader == publisher:
        raise ValueError('Volume grants require isolated exact reader/publisher principals')
    return tuple([f'GRANT SELECT ON q_live.{table.name} TO {reader}' for table in TABLES]
        + [f'GRANT {privilege} ON q_live.{table.name} TO {publisher}'
           for table in TABLES for privilege in ('SELECT', 'INSERT')])


@dataclass(frozen=True, slots=True)
class VolumeRun:
    first_ordinal: int
    stop_ordinal: int
    rolling_volume: float

    def __post_init__(self):
        if (type(self.first_ordinal) is not int or type(self.stop_ordinal) is not int
                or not 0 <= self.first_ordinal < self.stop_ordinal <= 57600
                or type(self.rolling_volume) is not float
                or not math.isfinite(self.rolling_volume) or not 0 <= self.rolling_volume <= 2 ** 53):
            raise ValueError('Volume run requires immutable typed elapsed share values')


def interval_runs(rows):
    return tuple(VolumeRun(r['first_ordinal'], r['stop_ordinal'], r['rolling_volume']) for r in rows)


def run_records(scope, runs):
    return ({**scope.prefix(), 'first_ordinal': r.first_ordinal, 'stop_ordinal': r.stop_ordinal,
             'rolling_volume': r.rolling_volume} for r in runs)


def product_revision(code_hash, *, volume_window_seconds):
    require_hash(code_hash)
    if type(volume_window_seconds) is not int or not 1 <= volume_window_seconds <= 3600:
        raise ValueError('Volume product requires an explicit elapsed-second window')
    return content_hash(dict(algorithm=REVISION, producer_code_hash=code_hash,
        volume_window_seconds=volume_window_seconds, value_type='Float64',
        activity_basis='canonical_bar_share_volume', window_basis='completed_elapsed_seconds',
        coverage_basis='full_certified_bar_unit'))


@dataclass(frozen=True)
class VolumeParents:
    source_bar_attempt_id: str
    source_bar_unit_hash: str
    opening_reference_hash: str
    market_plan_hash: str
    producer_code_hash: str
    volume_window_seconds: int

    def __post_init__(self):
        if type(self.source_bar_attempt_id) is not str or str(UUID(self.source_bar_attempt_id)) != self.source_bar_attempt_id:
            raise ValueError('Volume parent requires a canonical bar attempt')
        for key in ('source_bar_unit_hash', 'opening_reference_hash', 'market_plan_hash', 'producer_code_hash'):
            require_hash(getattr(self, key))
        product_revision(self.producer_code_hash, volume_window_seconds=self.volume_window_seconds)

    def payload(self):
        return asdict(self)


def restore_record(table, row):
    if table not in TABLES or set(row) != {n for n, _ in table.columns}:
        raise ValueError('Volume record differs from its normalized scalar schema')
    result = {}
    for name, kind in table.columns:
        value = row[name]
        if kind.startswith('UInt'):
            if type(value) is str and re.fullmatch(r'0|[1-9][0-9]*', value):
                value = int(value)
            if type(value) is not int or not 0 <= value < 2 ** int(kind[4:]):
                raise ValueError('Invalid volume unsigned scalar')
        elif kind == 'Float64':
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2 ** 53:
                raise ValueError('Invalid volume share scalar')
            value = float(value)
        elif kind == 'Date':
            value = date.fromisoformat(value) if type(value) is str else value
            if type(value) is not date:
                raise ValueError('Invalid volume session date')
        elif kind.startswith('DateTime64'):
            if type(value) is str:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
            if type(value) is not datetime or value.tzinfo is None:
                raise ValueError('Volume certification requires an aware timestamp')
            value = value.astimezone(timezone.utc)
        elif kind == 'UUID':
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError('Invalid volume attempt')
        elif kind == 'FixedString(64)':
            require_hash(value)
        elif type(value) is not str or not 1 <= len(value) <= 256:
            raise ValueError('Invalid volume text identity')
        result[name] = value
    return result


def records_hash(rows):
    digest = sha256()
    for row in rows:
        digest.update(bytes.fromhex(content_hash(restore_record(INTERVAL, row))))
    return digest.hexdigest()


def prepare_intervals(scope, bars, parents):
    """Producer transform; absent clocks become zero only after bar certification."""
    if (type(scope) is not history.HistoryScope or type(parents) is not VolumeParents
            or scope.product_revision != product_revision(parents.producer_code_hash,
                volume_window_seconds=parents.volume_window_seconds)
            or not isinstance(bars, pl.DataFrame) or set(bars.columns) != {'bucket_index', 'volume'}
            or bars.height > scope.clock_count or not bars.schema['bucket_index'].is_integer()
            or not bars.schema['volume'].is_numeric()
            or bars.select((pl.col('bucket_index').is_null() | pl.col('bucket_index').is_duplicated()
                | ~pl.col('bucket_index').is_between(scope.first_bucket, scope.stop_bucket - 1)
                | pl.col('volume').is_null() | ~pl.col('volume').is_finite()
                | (pl.col('volume') < 0) | (pl.col('volume') > 2 ** 53)).any()).item()):
        raise ValueError('Volume preparation differs from its exact certified bar scope')
    grid = pl.DataFrame({'bucket_index': pl.Series(range(scope.first_bucket, scope.stop_bucket), dtype=pl.UInt32)})
    dense = grid.join(bars.with_columns(pl.col('bucket_index').cast(pl.UInt32)), on='bucket_index',
        how='left', validate='1:1').with_columns(pl.col('volume').fill_null(0.).cast(pl.Float64),
            pl.lit(scope.listing_id).alias('listing_id'), pl.lit(scope.session_date).alias('session_date'))
    dense = dense.with_columns(((pl.col('bucket_index').cast(pl.Int64) + 1) * 1_000_000
        + history.session_clock_anchor_us(scope)).cast(pl.Datetime('us', 'UTC')).alias('boundary_at'))
    dense = dense.with_columns(pl.col('boundary_at').alias('available_at'))
    values = elapsed_volume_history(dense, parents.volume_window_seconds)['rolling_volume'].to_numpy()
    changed = np.r_[True, values[1:].view(np.uint64) != values[:-1].view(np.uint64)]
    starts = np.flatnonzero(changed)
    stops = np.r_[starts[1:], scope.clock_count]
    return tuple(restore_record(INTERVAL, {**scope.prefix(), 'first_ordinal': int(first),
        'stop_ordinal': int(stop), 'rolling_volume': float(values[first])})
        for first, stop in zip(starts, stops))


def validate_intervals(scope, rows, *, first_ordinal=0, stop_ordinal=None):
    stop_ordinal = scope.clock_count if stop_ordinal is None else stop_ordinal
    if not 0 <= first_ordinal < stop_ordinal <= scope.clock_count or not rows:
        raise ValueError('Volume interval coverage is missing or out of scope')
    previous = None
    for raw in rows:
        row = restore_record(INTERVAL, raw)
        first, stop = row['first_ordinal'], row['stop_ordinal']
        if (any(row[k] != v for k, v in scope.prefix().items())
                or not 0 <= first < stop <= scope.clock_count
                or stop <= first_ordinal or first >= stop_ordinal
                or previous is None and first > first_ordinal
                or previous is not None and first != previous):
            raise ValueError('Volume intervals have missing, duplicate or conflicting coverage')
        previous = stop
    if previous < stop_ordinal:
        raise ValueError('Volume intervals do not cover every completed clock')


def seal(scope, parents, rows, *, certified_at):
    validate_intervals(scope, rows)
    header = {**scope.prefix(), **parents.payload(), 'elapsed_clock_count': scope.clock_count,
              'interval_row_count': len(rows), 'interval_content_hash': records_hash(rows)}
    header['content_hash'] = content_hash(header)
    header['certified_at'] = certified_at
    return restore_record(COVERAGE, header)


def expand_window(scope, rows, *, first_ordinal, stop_ordinal):
    if (type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 < stop_ordinal - first_ordinal <= 4096):
        raise ValueError('Volume window exceeds its bounded completed clock envelope')
    validate_intervals(scope, rows, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal)
    intervals = pl.DataFrame({'first': [r['first_ordinal'] for r in rows],
        'stop': [r['stop_ordinal'] for r in rows], 'rolling_volume': [r['rolling_volume'] for r in rows]})
    result = pl.DataFrame({'observed_ordinal': pl.Series(range(first_ordinal, stop_ordinal), dtype=pl.Int64)})
    result = result.join_asof(intervals, left_on='observed_ordinal', right_on='first', check_sortedness=False)
    return result.select(pl.lit(scope.listing_id).alias('listing_id'),
        ((pl.col('observed_ordinal') + scope.first_bucket + 1) * 1_000_000
            + history.session_clock_anchor_us(scope)).cast(pl.Datetime('us', 'UTC')).alias('boundary_at'),
        pl.col('rolling_volume')).with_columns(pl.col('boundary_at').alias('available_at'))
