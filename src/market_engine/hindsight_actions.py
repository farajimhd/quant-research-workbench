"""Independent one-unit hindsight entry opportunities at base MACD hindsight targets."""
from collections import Counter, deque
from datetime import datetime
from math import isfinite

from bisect import bisect_right


class ActionGrid:
    def __init__(self, start, end, *, quote_age_seconds=1., target_times=()):
        self.target_times = sorted(set(target_times))
        self.target_index = 0
        self.target_quotes = {}
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

    def _targets_until(self, t):
        while self.target_index < len(self.target_times) and self.target_times[self.target_index] < t:
            target = self.target_times[self.target_index]
            q = self.quote
            self.target_quotes[target] = q if q is not None and 0 <= target-q['at'] <= self.age else None
            self.target_index += 1

    def observe(self, row):
        t = datetime.fromisoformat(row['ts'].replace('Z','+00:00')).timestamp()
        if not isfinite(t) or t < self.last:raise ValueError('Unordered canonical events')
        self.last = t
        self._targets_until(t)
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
        self._targets_until(float('inf'))
        while self.next <= self.end:self._emit()
        return self.rows


def solve_actions(rows, *, targets, target_quotes, decision_end=None, cost_bps=0., max_spread_bps=150.):
    """Value entry now at the next base MACD hindsight exit for each direction.

    Target timestamps are exact, not rounded to the one-second decision grid.
    Never skip an unavailable target in favor of a later, more profitable move.
    """
    if not rows or len(rows)>7201 or any(rows[i]['time']-rows[i-1]['time']!=1 for i in range(1,len(rows))):
        raise ValueError('Expected a bounded contiguous 1-second grid')
    if not all(isfinite(v) for v in (cost_bps,max_spread_bps)) or not (0<=cost_bps<=100 and 0<=max_spread_bps<=1000):
        raise ValueError('Invalid opportunity settings')
    by_side={side:sorted((p for p in targets if p['direction']==side),key=lambda p:p['exit_time']) for side in ('long','short')}
    times={side:[p['exit_time'] for p in group] for side,group in by_side.items()}
    decision_end=rows[-1]['time'] if decision_end is None else decision_end
    labels=[]
    for row in rows:
        t=row['time']
        if t>decision_end:break
        label=dict(time=t,action='unavailable',profit=None,hold_seconds=None,exit_time=None,
                   long=None,short=None,reason=None,available_at=None,targets={},action_reasons={})
        labels.append(label)
        for side in ('long','short'):
            index=bisect_right(times[side],t)
            if index<len(by_side[side]):label['targets'][side]=by_side[side][index]
            else:label['action_reasons'][side]='no_future_macd_target'
        available=[p['label_available_at'] for p in label['targets'].values()]
        label['available_at']=max(available) if available else None
        q=row['quote']
        if not q:
            label['reason']='no_fresh_entry_quote';continue
        if row['trades_10s']<3 or (q['ask']-q['bid'])/((q['ask']+q['bid'])/2)*10000>max_spread_bps:
            label['reason']='entry_liquidity_or_spread_filter';continue
        candidates=[]
        for side,target in label['targets'].items():
            if q['ask_size' if side=='long' else 'bid_size']<1:
                label['action_reasons'][side]='insufficient_entry_depth';continue
            exit_quote=target_quotes.get(target['exit_time'])
            if not exit_quote or exit_quote['bid_size' if side=='long' else 'ask_size']<1:
                label['action_reasons'][side]='no_fresh_quote_at_macd_target';continue
            entry=q['ask' if side=='long' else 'bid']
            exit_price=exit_quote['bid' if side=='long' else 'ask']
            gross=exit_price-entry if side=='long' else entry-exit_price
            outcome=dict(entry_price=entry,exit_price=exit_price,exit_time=target['exit_time'],
                         hold_seconds=target['exit_time']-t,gross_profit=gross,
                         net_profit=gross-(entry+exit_price)*cost_bps/10000,
                         entry_quote_time=q['at'],exit_quote_time=exit_quote['at'])
            label[side]=outcome
            candidates.append((gross,-outcome['hold_seconds'],side=='long',side,outcome))
        if not candidates:
            label['reason']='no_valued_macd_target';continue
        gross,_,_,side,best=max(candidates)
        if gross<=1e-12:label.update(action='wait',profit=0.,hold_seconds=0)
        else:label.update(action='buy' if side=='long' else 'sell',profit=gross,
                          hold_seconds=best['hold_seconds'],exit_time=best['exit_time'])
    return dict(algorithm='hindsight-macd-target-values-v5',hindsight_only=True,position_size=1,
                horizon='next_base_macd_exit_per_direction',value_unit='USD gross profit per share',
                entry_rule='Long at current ask; short at current bid. One unit.',
                exit_rule='Fresh bid/ask at the next base MACD hindsight exit for each direction; exact target timestamps.',
                labels=labels,counts=dict(Counter(x['action'] for x in labels),seconds=len(labels)),
                short_availability='Hypothetical: borrow availability and costs not established')
