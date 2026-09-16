"""Causal cumulative share volume against QMD's aligned prior-session baseline."""
from datetime import datetime
from math import floor, isfinite

from src.market_engine.events import TradeEvent

CONTRACT = 'session-relative-volume-1'
BASELINE_CONTRACT = 'session-relative-volume-baseline-1'


def number(value):
    return type(value) in (int, float) and isfinite(value)


class SessionVolumeTracker:
    """Retain cumulative volume plus the unfinished second, never future prints.

    The caller must provide a certified stream covering session_start. This
    tracker cannot infer missing history from the arrival of the first trade.
    """
    def __init__(self, session_start, saved=None):
        self.start = session_start.timestamp()
        if saved and (saved.get('contract') != CONTRACT or saved.get('start') != self.start):
            raise ValueError('Session volume checkpoint identity mismatch')
        saved = saved or {}
        self.total = saved.get('total', 0.)
        self.second = saved.get('second', None)
        self.open_volume = saved.get('open_volume', 0.)
        self.last_time = saved.get('last_time', None)
        self.invalid = saved.get('invalid', False)

    def checkpoint(self):
        return dict(contract=CONTRACT, start=self.start, total=self.total,
                    second=self.second, open_volume=self.open_volume,
                    last_time=self.last_time, invalid=self.invalid)

    def observe(self, event):
        stamp = event.ts.timestamp()
        if stamp < self.start:
            return
        if self.last_time is not None and stamp < self.last_time:
            self.invalid = True
            return
        self.last_time = stamp
        second = floor(stamp)
        if second != self.second:
            self.second, self.open_volume = second, 0.
        if not isinstance(event, TradeEvent):
            return
        eligible = event.raw.get('volume_eligible')
        if type(eligible) is not bool or not number(event.size) or event.size < 0:
            self.invalid = True
        elif eligible:
            self.total += event.size
            self.open_volume += event.size

    def snapshot(self, at, baseline, ticker):
        now = at.timestamp()
        boundary = floor(now)
        result = dict(contract=CONTRACT, observed_at=now, effective_at=boundary,
                      ready=False, baseline_hash=baseline.get('content_hash'))
        if self.invalid or (self.last_time is not None and self.last_time > now):
            return dict(result, reason='invalid_or_future_volume_history')
        if baseline.get('contract') != BASELINE_CONTRACT or not baseline.get('content_hash'):
            return dict(result, reason='baseline_contract_missing')
        start = datetime.fromisoformat(baseline['session_start'])
        if start.tzinfo is None or start.timestamp() != self.start:
            return dict(result, reason='baseline_session_mismatch')
        index = int(boundary-self.start)
        profile = baseline.get('profiles', {}).get(ticker)
        if profile is None or not 0 <= index < len(profile):
            return dict(result, reason='baseline_unavailable')
        denominator = profile[index]
        numerator = self.total - (self.open_volume if self.second == boundary else 0.)
        if not number(denominator) or denominator <= 0:
            return dict(result, reason='baseline_nonpositive_or_missing', volume=numerator)
        if not number(numerator) or numerator < 0:
            return dict(result, reason='invalid_volume')
        return dict(result, ready=True, reason='measured', volume=numerator,
                    baseline_volume=denominator, ratio=numerator/denominator)


def confirm(evidence, *, now, minimum_ratio):
    evidence = dict(evidence or {})
    result = dict(evidence, minimum_ratio=minimum_ratio, passed=False)
    stamp, boundary, ratio = (evidence.get(k) for k in ('observed_at', 'effective_at', 'ratio'))
    if (evidence.get('contract') != CONTRACT or not number(stamp)
            or not 0 <= now-stamp <= 1 or boundary != floor(now)):
        return dict(result, reason='relative_volume_missing_or_stale')
    if evidence.get('ready') is not True or not number(ratio) or ratio < 0:
        return dict(result, reason=evidence.get('reason') or 'relative_volume_unavailable')
    return dict(result, passed=ratio >= minimum_ratio,
                reason='confirmed' if ratio >= minimum_ratio else 'relative_volume_below_minimum')
