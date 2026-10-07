"""Exact V7 source validation using the producer canonical content order.

Transport ordinal order remains intact for strategy geometry. Only hashing uses
(valid_from_ms, level_id), matching the coverage publisher and producer certifier.
All original scalar, count, clock, causal, identity and content checks remain.
"""
from .fixed_structural_lot_entry import (
    CertifiedV7IntervalUnit, _validate_children, market_day_boundary,
    V7LevelInterval, clock_hash, interval_hash, UUID,
    CertifiedV7IntervalPlan, date,
)

VALIDATOR_RULE = 'fixed-structural-lot-interval-validator@2'


def _validate_interval_ticker_children(intervals, *, session_date, ticker):
    index = intervals._tickers.index(ticker)
    unit = intervals.coverage[index]
    if type(unit) is not CertifiedV7IntervalUnit:
        raise ValueError('Exact fixed structural lot source unit required')
    seconds, rows = (intervals.valid_seconds[index][1], intervals.intervals[index][1])
    _validate_children(seconds, rows, origin_ms=int(market_day_boundary(session_date, 0).timestamp() * 1000))
    if any((type(row) is not V7LevelInterval or type(row.lower) is not float or type(row.upper) is not float or (type(row.confirmed_at_ms) is not int) or (type(row.historical) is not bool) or (type(row.valid_from_ms) is not int) or (type(row.valid_to_ms) is not int) or (type(row.ordinal) is not int) or (type(row.level_id) is not str) or (type(row.role) is not str) or (type(row.transition_from) is not str) for row in rows)):
        raise ValueError('Fixed structural lot geometry scalar types differ')
    if type(unit.clock_count) is not int or type(unit.interval_count) is not int or unit.clock_count != len(seconds) or (unit.interval_count != len(rows)) or (unit.clock_hash != clock_hash(seconds)) or (unit.interval_hash != interval_hash(tuple(sorted(rows, key=lambda row: (row.valid_from_ms, row.level_id))))):
        raise ValueError('Fixed structural lot source content differs')
    for value in (intervals.token, unit.source_checkpoint_hash, unit.decoded_seed_hash, unit.seed_source_plan_hash, unit.split_evidence_hash, unit.clock_hash, unit.interval_hash):
        if type(value) is not str or len(value) != 64 or any((c not in '0123456789abcdef' for c in value)):
            raise ValueError('Fixed structural lot source identity differs')
    if type(intervals.source_build_id) is not str or not 1 <= len(intervals.source_build_id) <= 256:
        raise ValueError('Fixed structural lot producer build differs')
    for value in (unit.attempt_id, unit.bars_attempt_id):
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError('Fixed structural lot producer attempt differs')
    return unit


def validate_fixed_structural_lot_interval_ticker(intervals, *, session_date, ticker):
    """Strict ad-hoc ticker validation; no caller flag can bypass plan validation."""
    if type(intervals) is not CertifiedV7IntervalPlan or type(session_date) is not date:
        raise ValueError('Exact fixed structural lot interval plan/session required')
    intervals.__post_init__()
    return _validate_interval_ticker_children(intervals, session_date=session_date, ticker=ticker)


def validate_fixed_structural_lot_interval_plan(intervals, *, session_date):
    """Validate the whole aligned plan once, then every owned child once."""
    if (type(intervals) is not CertifiedV7IntervalPlan or type(session_date) is not date
            or intervals.session_date != session_date.isoformat()):
        raise ValueError('Exact complete fixed structural lot interval plan/session required')
    intervals.__post_init__()
    return tuple(_validate_interval_ticker_children(intervals,
        session_date=session_date, ticker=ticker) for ticker in intervals._tickers)
