"""Independent one-unit hindsight entry opportunities with a 90-second exit horizon."""
from collections import Counter, deque
from datetime import datetime
from math import isfinite

import numpy as np


class ActionGrid:
    def __init__(self, start, end, *, quote_age_seconds=1.):
        self.next = int(start)
        self.end = int(end)
        self.age = quote_age_seconds
        self.last = float('-inf')
        self.quote = None
        self.mark = None
        self.trades = deque()
        self.volume = 0.
        self.rows = []
        self.counts = Counter()

    def _emit(self):
        t = self.next
        while self.trades and self.trades[0][0] <= t-10:
            self.volume -= self.trades.popleft()[1]
        q = self.quote
        fresh = q is not None and 0 <= t-q['at'] <= self.age
        self.rows.append(dict(time=t,mark=self.mark,quote=q if fresh else None,
                              volume_10s=max(0.,self.volume),trades_10s=len(self.trades)))
        if not fresh:self.counts['seconds_without_fresh_quote'] += 1
        self.next += 1

    def observe(self, row):
        t = datetime.fromisoformat(row['ts'].replace('Z','+00:00')).timestamp()
        if not isfinite(t) or t < self.last:raise ValueError('Unordered canonical events')
        self.last = t
        while self.next < t and self.next <= self.end:self._emit()
        self.counts['events'] += 1
        if self.counts['events'] > 10_000_000:raise ValueError('Action research event budget exceeded')
        if row['kind']=='trade':
            price,size=float(row.get('price') or 0),float(row.get('size') or 0)
            if not all(isfinite(v) and v>0 for v in (price,size)) or (row.get('raw') or {}).get('price_eligible') is False:
                self.counts['invalid_trades'] += 1
                return
            self.trades.append((t,size));self.volume += size
            while self.trades and self.trades[0][0] <= t-10:
                self.volume -= self.trades.popleft()[1]
        elif row['kind']=='quote':
            b,a,bs,az=[float(row.get(k) or 0) for k in ('bid_price','ask_price','bid_size','ask_size')]
            if not all(isfinite(v) and v>0 for v in (b,a,bs,az)) or b>a:
                self.quote=None;self.counts['invalid_quotes'] += 1
                return
            self.mark=(a+b)/2
            self.quote=dict(at=t,bid=b,ask=a,bid_size=bs,ask_size=az)
        else:raise ValueError('Unsupported canonical event kind')

    def finish(self):
        while self.next <= self.end:self._emit()
        return self.rows


MAX_HOLD_SECONDS = 90


def solve_actions(rows, *, decision_end=None, cost_bps=0., max_spread_bps=150.):
    """Absolute gross return from entry now, never advantage over another policy.

    Exits use fresh quoted bids for longs and asks for shorts, strictly after
    entry and at most 90 seconds later. Equal best prices choose the first exit.
    Overlapping entry opportunities are independent and must not be summed.
    """
    if not rows or len(rows)>7291 or any(rows[i]['time']-rows[i-1]['time']!=1 for i in range(1,len(rows))):
        raise ValueError('Expected a bounded contiguous 1-second grid')
    if not all(isfinite(v) for v in (cost_bps,max_spread_bps)) or not (0<=cost_bps<=100 and 0<=max_spread_bps<=1000):
        raise ValueError('Invalid opportunity settings')
    decision_end=rows[-1]['time'] if decision_end is None else decision_end
    times=np.array([r['time'] for r in rows])
    bids=np.array([r['quote']['bid'] if r['quote'] and r['quote']['bid_size']>=1 else -np.inf for r in rows])
    asks=np.array([r['quote']['ask'] if r['quote'] and r['quote']['ask_size']>=1 else np.inf for r in rows])
    labels=[]
    for i,row in enumerate(rows):
        if row['time']>decision_end:break
        label=dict(time=row['time'],action='unavailable',profit=None,hold_seconds=None,
                   exit_time=None,long=None,short=None,reason=None,available_at=row['time']+MAX_HOLD_SECONDS)
        labels.append(label)
        if i+MAX_HOLD_SECONDS>=len(rows):
            label['reason']='incomplete_90s_horizon';continue
        q=row['quote']
        if not q:
            label['reason']='no_fresh_entry_quote';continue
        if row['trades_10s']<3 or (q['ask']-q['bid'])/((q['ask']+q['bid'])/2)*10000>max_spread_bps:
            label['reason']='entry_liquidity_or_spread_filter';continue
        candidates=[]
        for side in ('long','short'):
            if q['ask_size' if side=='long' else 'bid_size']<1:continue
            values=(bids if side=='long' else asks)[i+1:i+MAX_HOLD_SECONDS+1]
            offset=int(np.argmax(values) if side=='long' else np.argmin(values))
            exit_price=float(values[offset])
            if not isfinite(exit_price):continue
            j=i+1+offset;entry=q['ask' if side=='long' else 'bid']
            gross=exit_price-entry if side=='long' else entry-exit_price
            outcome=dict(entry_price=entry,exit_price=exit_price,exit_time=int(times[j]),
                         hold_seconds=j-i,gross_profit=gross,net_profit=gross-(entry+exit_price)*cost_bps/10000)
            label[side]=outcome
            candidates.append((gross,-outcome['hold_seconds'],side=='long',side,outcome))
        if not candidates:
            label['reason']='no_eligible_exit_quote';continue
        gross,_,_,side,best=max(candidates)
        if gross<=1e-12:
            label.update(action='wait',profit=0.,hold_seconds=0)
        else:
            label.update(action='buy' if side=='long' else 'sell',profit=gross,
                         hold_seconds=best['hold_seconds'],exit_time=best['exit_time'])
    return dict(algorithm='hindsight-best-exit-90s-v3',hindsight_only=True,position_size=1,
                max_hold_seconds=MAX_HOLD_SECONDS,value_unit='USD gross profit per share',
                entry_rule='Long at current ask; short at current bid. One unit.',
                exit_rule='Best fresh quoted bid/ask 1-90 seconds after entry; earliest equal best exit.',
                labels=labels,counts=dict(Counter(x['action'] for x in labels),seconds=len(labels)),
                short_availability='Hypothetical: borrow availability and costs not established')
