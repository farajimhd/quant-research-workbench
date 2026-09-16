"""Bounded causal quote ranges for confirming completed candle bases.

This measures observed positive-size NBBO midpoints, not executable returns or
continuous liquidity. Sparse quotes are not interpolated. Current tradability
and quote freshness remain separate acquisition requirements.
"""
from collections import deque
from math import floor, isfinite

from src.market_engine.events import QuoteEvent

CONTRACT = 'completed-nbbo-range-v1'
RETENTION_SECONDS = 64


def _number(value):
    return type(value) in (int, float) and isfinite(value)


class QuoteGeometryTracker:
    def __init__(self, saved=None):
        if saved is not None and saved.get('contract') != CONTRACT:
            raise ValueError('Unknown quote geometry checkpoint contract')
        saved = saved or {}
        self.buckets = deque(dict(b) for b in saved.get('buckets', []))
        self.coverage_start = saved.get('coverage_start')
        self.last_time = saved.get('last_time', -1.)
        self.rejected = saved.get('rejected', 0)
        self._cache_clock = None
        self._cache = ()

    def checkpoint(self):
        return dict(contract=CONTRACT, buckets=[dict(b) for b in self.buckets],
                    coverage_start=self.coverage_start, last_time=self.last_time,
                    rejected=self.rejected)

    def observe(self, event):
        now = event.ts.timestamp()
        if now < self.last_time:
            self.rejected += 1
            return
        if self.coverage_start is None:
            self.coverage_start = now
        self.last_time = now
        index = floor(now)
        if self._cache_clock is not None and index < self._cache_clock:
            self._cache_clock = None
        while self.buckets and self.buckets[0]['index'] < index-RETENTION_SECONDS:
            self.buckets.popleft()
        if not isinstance(event, QuoteEvent):
            return
        values = (event.bid_price, event.ask_price, event.bid_size, event.ask_size)
        if not all(_number(v) for v in values) or not (
                0 < event.bid_price <= event.ask_price and min(event.bid_size, event.ask_size) > 0):
            return
        midpoint = event.bid_price+(event.ask_price-event.bid_price)/2
        if not self.buckets or self.buckets[-1]['index'] != index:
            self.buckets.append(dict(index=index, high=midpoint, low=midpoint, count=0,
                                     first=now, last=now))
        b = self.buckets[-1]
        b.update(high=max(b['high'], midpoint), low=min(b['low'], midpoint),
                 count=b['count']+1, last=now)

    def snapshot(self, at):
        now = at.timestamp()
        clock = floor(now)
        # Cache only completed seconds; the current bucket remains mutable.
        if self._cache_clock != clock:
            self._cache = tuple(dict(b) for b in self.buckets
                                if clock-RETENTION_SECONDS <= b['index'] < clock)
            self._cache_clock = clock
        return dict(contract=CONTRACT, observed_at=now, coverage_start=self.coverage_start,
                    retained_from=clock-RETENTION_SECONDS, source_at=self.last_time,
                    rejected_out_of_order=self.rejected, buckets=self._cache)


def confirm_base(geometry, base, *, now, minimum_fraction):
    """Compare percentage ranges over exactly the prior base's [start, end)."""
    result = dict(contract=CONTRACT, passed=False, minimum_fraction=minimum_fraction,
                  base=dict(base or {}), observed_at=now)

    def fail(reason):
        return dict(result, reason=reason)

    if not geometry or geometry.get('contract') != CONTRACT:
        return fail('quote_history_missing')
    observed = geometry.get('observed_at')
    source = geometry.get('source_at')
    if not _number(observed) or not 0 <= now-observed <= 1 or not _number(source) or source > now:
        return fail('quote_history_clock_invalid')
    base = base or {}
    start, end, low, high = (base.get(k) for k in ('start', 'end', 'low', 'high'))
    if (not all(_number(v) for v in (start, end, low, high))
            or start != floor(start) or end != floor(end)
            or not start < end <= floor(now)-1 or not 0 < low < high):
        return fail('base_interval_invalid')
    coverage, retained = geometry.get('coverage_start'), geometry.get('retained_from')
    if not all(_number(v) for v in (coverage, retained)) or start < max(coverage, retained):
        return fail('quote_history_truncated')
    buckets = geometry.get('buckets', ())
    if not isinstance(buckets, (list, tuple)) or any(
            not isinstance(b, dict) or not _number(b.get('index')) for b in buckets):
        return fail('quote_history_invalid')
    rows = [b for b in buckets if start <= b['index'] < end]
    if any(not all(_number(b.get(k)) for k in ('high','low','first','last','count'))
           or b['index'] != floor(b['index']) or not 0 < b['low'] <= b['high']
           or not b['index'] <= b['first'] <= b['last'] < b['index']+1
           or b['count'] <= 0 or b['count'] != floor(b['count']) for b in rows):
        return fail('quote_history_invalid')
    if any(left['index'] >= right['index'] for left,right in zip(rows,rows[1:])):
        return fail('quote_history_invalid')
    if sum(b['count'] for b in rows) < 2:
        return fail('quote_history_insufficient')
    quote_high, quote_low = max(b['high'] for b in rows), min(b['low'] for b in rows)
    candle_range, quote_range = high/low-1, quote_high/quote_low-1
    ratio = quote_range/candle_range
    result.update(quote_high=quote_high, quote_low=quote_low, candle_range_pct=100*candle_range,
                  quote_range_pct=100*quote_range, range_fraction=ratio,
                  quote_count=sum(b['count'] for b in rows), quote_seconds=len(rows),
                  first_quote_at=rows[0]['first'], last_quote_at=rows[-1]['last'])
    return dict(result, passed=ratio >= minimum_fraction,
                reason='confirmed' if ratio >= minimum_fraction else 'range_not_confirmed')
