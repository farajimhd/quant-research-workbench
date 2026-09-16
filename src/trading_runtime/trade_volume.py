"""Completed 5s/55s share-volume activity from producer-certified trade flags.

Price eligibility and volume eligibility are different contracts. A trade that
cannot update last price can still contribute shares to candle volume.
"""
from collections import deque
from math import floor, isfinite

from src.market_engine.events import TradeEvent

CONTRACT = 'completed-trade-volume-5s-55s-v1'


def number(value):
    return type(value) in (int, float) and isfinite(value)


class TradeVolumeTracker:
    def __init__(self, saved=None):
        if saved is not None and saved.get('contract') != CONTRACT:
            raise ValueError('Unknown trade volume checkpoint contract')
        saved = saved or {}
        self.buckets = deque(dict(b) for b in saved.get('buckets', []))
        self.coverage_start = saved.get('coverage_start')
        self.last_time = saved.get('last_time', -1.)
        self.rejected = saved.get('rejected', 0)
        self.last_rejected_at = saved.get('last_rejected_at')

    def checkpoint(self):
        return dict(contract=CONTRACT, buckets=[dict(b) for b in self.buckets],
                    coverage_start=self.coverage_start, last_time=self.last_time,
                    rejected=self.rejected, last_rejected_at=self.last_rejected_at)

    def observe(self, event):
        now = event.ts.timestamp()
        if now < self.last_time:
            self.rejected += 1
            self.last_rejected_at = self.last_time
            return
        if self.coverage_start is None:
            self.coverage_start = now
        self.last_time = now
        index = floor(now)
        while self.buckets and self.buckets[0]['index'] < index-60:
            self.buckets.popleft()
        if not isinstance(event, TradeEvent):
            return
        if not self.buckets or self.buckets[-1]['index'] != index:
            self.buckets.append(dict(index=index, volume=0., trades=0, invalid=0))
        b = self.buckets[-1]
        eligible = event.raw.get('volume_eligible')
        if type(eligible) is not bool or not number(event.size) or event.size < 0:
            b['invalid'] += 1
        elif eligible:
            b['volume'] += event.size
            b['trades'] += 1

    def snapshot(self, at):
        now = at.timestamp()
        end = floor(now)
        result = dict(contract=CONTRACT, observed_at=now, end=end, ready=False,
                      coverage_start=self.coverage_start, source_at=self.last_time,
                      rejected_out_of_order=self.rejected)
        if self.last_time > now:
            return dict(result, reason='future_source')
        if self.last_rejected_at is not None and now-self.last_rejected_at <= 60:
            return dict(result, reason='out_of_order_history')
        if self.coverage_start is None or self.coverage_start > end-60:
            return dict(result, reason='insufficient_history')
        rows = [b for b in self.buckets if end-60 <= b['index'] < end]
        if any(b['invalid'] for b in rows):
            return dict(result, reason='volume_eligibility_or_size_invalid')
        recent = sum(b['volume'] for b in rows if b['index'] >= end-5)
        prior = sum(b['volume'] for b in rows if b['index'] < end-5)
        if not number(recent) or not number(prior):
            return dict(result, reason='volume_totals_invalid')
        ratio = 11*recent/prior if prior > 0 else None
        return dict(result, ready=ratio is not None, ratio=ratio, recent_5s=recent,
                    prior_55s=prior, observed_trade_seconds=len(rows),
                    reason='measured' if ratio is not None else 'prior_volume_missing')


def confirm(evidence, *, now, minimum_ratio):
    evidence = dict(evidence or {})
    result = dict(evidence, minimum_ratio=minimum_ratio, passed=False)
    observed, end, ratio = (evidence.get(k) for k in ('observed_at','end','ratio'))
    if (evidence.get('contract') != CONTRACT or not number(observed)
            or not 0 <= now-observed <= 1 or end != floor(now)
            or not number(evidence.get('source_at')) or evidence['source_at'] > now):
        return dict(result, reason='volume_history_missing_or_stale')
    if evidence.get('ready') is not True or not number(ratio) or ratio < 0:
        return dict(result, reason=evidence.get('reason') or 'volume_history_invalid')
    return dict(result, passed=ratio >= minimum_ratio,
                reason='confirmed' if ratio >= minimum_ratio else 'volume_rate_below_minimum')
