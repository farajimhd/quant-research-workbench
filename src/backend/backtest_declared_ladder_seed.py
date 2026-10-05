"""Declared ladder requires the canonical immediately preceding NYSE checkpoint."""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import re
from zoneinfo import ZoneInfo
from pandas import Timestamp, isna

from src.market_engine.derived_trade_policy import POLICY
from .structural_v7_seed import CertifiedSeedPlan


@lru_cache(maxsize=128)
def _previous_session(day):
    from src.data_provider.calendar import mcal, MARKET_CALENDAR
    schedule = mcal.get_calendar(MARKET_CALENDAR).schedule(
        start_date=(day - timedelta(days=32)).isoformat(), end_date=day.isoformat())
    sessions = tuple(stamp.date() for stamp in schedule.index)
    if not sessions or sessions[-1] != day or len(sessions) < 2:
        raise ValueError('Declared ladder target lacks a preceding NYSE session')
    return sessions[-2]


def verify_declared_ladder_seed_plan(market, seeds):
    """Strengthen an existing SELECT certificate without substituting empty data."""
    if (type(seeds) is not CertifiedSeedPlan or seeds.provisional is not False
            or seeds.build_id != market.build_id):
        raise ValueError('Declared ladder prior seed must be canonical and nonprovisional')
    scope = {(unit.session_date, unit.ticker) for unit in market.units if unit.stage == 'bars'}
    if not scope or len(seeds.units) != len(scope):
        raise ValueError('Declared ladder prior seed lacks complete frozen bars scope')
    observed = set()
    for unit in seeds.units:
        target, ticker = str(unit['backtest_session']), str(unit['ticker'])
        key = (target, ticker)
        if key in observed or key not in scope:
            raise ValueError('Declared ladder prior seed duplicates or changes frozen scope')
        observed.add(key)
        day = date.fromisoformat(target)
        if unit['session_date'] != _previous_session(day).isoformat():
            raise ValueError('Declared ladder seed is not the exact prior NYSE session')
        for field in ('source_checkpoint_hash', 'source_plan_hash'):
            value = unit[field]
            if (type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                    or value == '0' * 64):
                raise ValueError('Declared ladder prior checkpoint has invalid ' + field)
        counts = (unit['level_count'], unit['observation_count'])
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError('Declared ladder seed counts are invalid')
        if any(counts) and unit['input_policy'] != POLICY:
            raise ValueError('Declared ladder nonempty prior checkpoint is not canonical')
        if not any(counts) and unit['input_policy'] not in ('', POLICY):
            raise ValueError('Declared ladder empty prior checkpoint has unsupported input policy')
        available = Timestamp(unit['available_at'])
        if isna(available):
            raise ValueError('Declared ladder prior checkpoint availability is missing')
        # Coverage DateTime64 is explicitly UTC; its transport can omit offset.
        if available.tzinfo is None:
            available = available.tz_localize('UTC')
        cutoff = datetime.combine(day, time(4), tzinfo=ZoneInfo('America/New_York'))
        if available > Timestamp(cutoff):
            raise ValueError('Declared ladder prior checkpoint was unavailable at session open')
    if observed != scope:
        raise ValueError('Declared ladder prior seed omits frozen bars scope')
    return seeds
