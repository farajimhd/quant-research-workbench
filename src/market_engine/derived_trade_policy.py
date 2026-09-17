"""Versioned derived-state cutoff; canonical trade records remain unchanged."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

POLICY = 'exclude-trades-before-0405-et-v1'
NY = ZoneInfo('America/New_York')


def eligible_trade_time(timestamp):
    return datetime.fromtimestamp(timestamp, NY).time() >= time(4, 5)


def eligible_completed_second(end):
    # A bar ending at 04:05 contains trades from 04:04:59 and is excluded.
    return eligible_trade_time(end-1)
