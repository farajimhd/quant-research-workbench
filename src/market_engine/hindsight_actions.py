"""Finite-inventory, full-information action labels. Never execution signals.

Quotes and trailing activity are sampled causally on a 1s grid. Backward dynamic
programming then uses the entire requested window to value feasible adjustments.
The reward is cash flow less transaction costs and quadratic inventory exposure
cost. Terminal inventory is zero. Short availability is deliberately not claimed.
"""
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


def action_name(before, after):
    if before==after:return 'hold' if before else 'stay_flat'
    if not after:return 'exit_long' if before>0 else 'exit_short'
    if not before:return 'enter_long' if after>0 else 'enter_short'
    return ('add_' if abs(after)>abs(before) else 'reduce_')+('long' if before>0 else 'short')


def solve_actions(rows, *, lot_shares=25, inventory_steps=4, max_notional=1000.,
                  cost_bps=5., max_spread_bps=150., participation=.05,
                  risk_bps_per_second=.01):
    if not rows or any(rows[i]['time']-rows[i-1]['time']!=1 for i in range(1,len(rows))):
        raise ValueError('Action labels require a contiguous 1-second grid')
    if not 1<=lot_shares<=1000 or not 1<=inventory_steps<=8 or len(rows)>7201:
        raise ValueError('Action research grid exceeds its bounded contract')
    if not all(isfinite(v) for v in (max_notional,cost_bps,max_spread_bps,participation,risk_bps_per_second)) or not (
            max_notional>0 and 0<=cost_bps<=100 and 0<=max_spread_bps<=1000 and 0<participation<=1 and 0<=risk_bps_per_second<=10):
        raise ValueError('Invalid action-value settings')
    inventory=np.arange(-inventory_steps,inventory_steps+1)*lot_shares
    n=len(inventory);zero=inventory_steps;count=len(rows)
    delta=inventory[None,:]-inventory[:,None]
    permitted=(np.abs(delta)<=lot_shares)&(inventory[:,None]*inventory[None,:]>=0)
    holding=delta==0
    increasing=np.abs(inventory[None,:])>np.abs(inventory[:,None])
    fee=cost_bps/10000
    next_value=np.full(n,-np.inf);next_value[zero]=0.
    advantages=np.full((count,n,n),np.nan)
    choices=np.zeros((count,n),dtype=np.int16)
    reachable=np.zeros((count,n),dtype=bool)
    for index in range(count-1,-1,-1):
        row=rows[index];quote=row['quote'];mark=row['mark'] or 0.
        allowed=holding.copy();cash=np.zeros((n,n))
        if quote:
            bid,ask=quote['bid'],quote['ask']
            capacity=np.where(delta>0,quote['ask_size'],quote['bid_size'])
            liquid=(np.abs(delta)<=capacity)&(np.abs(delta)<=row['volume_10s']*participation)
            admission=((ask-bid)/((ask+bid)/2)*10000<=max_spread_bps)&(row['trades_10s']>=3)
            exposure=np.abs(inventory[None,:])*np.where(inventory[None,:]>=0,ask,bid)<=max_notional
            allowed=holding|(permitted&liquid&(~increasing|(admission&exposure)))
            cash=np.where(delta>0,-delta*ask*(1+fee),-delta*bid*(1-fee))
        penalty=(inventory/(lot_shares*inventory_steps))**2*mark*(lot_shares*inventory_steps)*risk_bps_per_second/10000
        if index==count-1:penalty=np.zeros(n)
        values=np.where(allowed,cash+next_value[None,:]-penalty[None,:],-np.inf)
        # Hold wins ties, preventing arbitrary turnover when values are equal.
        best=np.argmax(values,axis=1)
        hold_values=np.diag(values)
        maxima=values[np.arange(n),best]
        ties=np.isfinite(hold_values)&(hold_values>=maxima-1e-10)
        best[ties]=np.arange(n)[ties]
        reachable[index]=np.isfinite(maxima)
        choices[index]=best
        baseline=np.where(np.isfinite(hold_values),hold_values,maxima)
        with np.errstate(invalid='ignore'):
            advantages[index]=np.where(np.isfinite(values),values-baseline[:,None],np.nan)
        next_value=maxima
    if not reachable[0,zero]:raise ValueError('No feasible flat-to-flat path')
    objective=float(next_value[zero]);state=zero;cash_total=0.;risk_total=0.
    path=[];moves=[];move=None
    for i,row in enumerate(rows):
        target=int(choices[i,state]);before,after=int(inventory[state]),int(inventory[target]);change=after-before
        cash=0.;price=None
        if change:
            quote=row['quote'];price=quote['ask'] if change>0 else quote['bid']
            cash=-change*price-abs(change)*price*fee
        cash_total+=cash
        risk=0. if i==count-1 else (after/(lot_shares*inventory_steps))**2*(row['mark'] or 0.)*(lot_shares*inventory_steps)*risk_bps_per_second/10000
        risk_total+=risk
        event=dict(time=row['time'],before=before,after=after,action=action_name(before,after),
                   price=price,cash_flow=cash,advantage=float(advantages[i,state,target]),state_index=state,
                   forced_terminal_unwind=bool(change and not np.isfinite(advantages[i,state,state])))
        path.append(event)
        if not before and after:
            move=dict(number=len(moves)+1,direction='long' if after>0 else 'short',
                      start_index=i,entry_time=row['time'],entry_price=price,cash_before=cash_total-cash)
        if before and not after:
            move.update(end_index=i,exit_time=row['time'],exit_price=price,net_cash=cash_total-move.pop('cash_before'))
            moves.append(move);move=None
        state=target
    if state!=zero or move is not None:raise AssertionError('Terminal inventory is not flat')
    if abs(cash_total-risk_total-objective)>1e-6:raise AssertionError('Action cash/risk reconciliation failed')
    return dict(algorithm='hindsight-inventory-action-values-v1',hindsight_only=True,
        value_unit='USD advantage over holding with optimal hindsight continuation',
        terminal_rule='Flat at selected window end; future labels unavailable until then',
        short_availability='Hypothetical: borrow availability and borrow costs not established',
        inventory=inventory.tolist(),grid=rows,
        values=[[[float(x) if np.isfinite(x) else None for x in vector] for vector in matrix] for matrix in advantages],
        path=path,moves=moves,net_cash=cash_total,risk_penalty=risk_total,objective=objective,
        spectrum=dict(negative=-1.,positive=1.,unit='USD advantage',clipped_colors=True),
        counts=dict(seconds=count,moves=len(moves),adjustments=sum(p['before']!=p['after'] for p in path),
                    infeasible_values=int(np.isnan(advantages).sum())))
