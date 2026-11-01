"""V21 ticker/session objectives. No per-trade sorting or performance eligibility."""
from dataclasses import dataclass, asdict
import math
import numpy as np

@dataclass(frozen=True)
class Objective:
    ticker_trim_fraction: float = .10
    session_trim_fraction: float = .10
    concentration_weight: float = .25
    drawdown_weight: float = .05
    complexity_dollars_per_node: float = 0.
    def validate(self):
        if any(not math.isfinite(x) or x<0 for x in asdict(self).values()):raise ValueError('Finite nonnegative objective required')
        if not 0<=self.ticker_trim_fraction<1 or not 0<=self.session_trim_fraction<1:raise ValueError('Invalid trim fraction')
        if self.complexity_dollars_per_node!=0:raise ValueError('V21 has no complexity penalty')
        return self


def positive_trim(panel,fraction):
    x=np.asarray(panel,dtype=np.float64)
    if x.ndim!=2 or not len(x) or not np.isfinite(x).all() or not 0<=fraction<1:raise ValueError('Finite session panel required')
    ranked=np.sort(np.maximum(x,0),axis=0)[::-1]
    weights=np.clip(fraction*len(x)-np.arange(len(x)),0,1)
    removed=(ranked*weights[:,None]).sum(0)
    return x.sum(0)-removed,removed


def ticker_summary(records,fraction=.1):
    """Ragged ticker totals -> one native segmented ordering; no position ledger."""
    sizes=np.fromiter((len(r) for r in records),dtype=np.int64,count=len(records))
    values=np.fromiter((v for r in records for v in r.values()),dtype=np.float64,count=int(sizes.sum()))
    if not np.isfinite(values).all():raise ValueError('Nonfinite ticker profit')
    owner=np.repeat(np.arange(len(records)),sizes)
    totals=np.bincount(owner,weights=values,minlength=len(records))
    order=np.lexsort((-values,owner));ranked=values[order]
    starts=np.cumsum(sizes)-sizes
    ordinal=np.arange(len(values))-starts[owner]
    weights=np.clip(fraction*sizes[owner]-ordinal,0,1)
    removed=np.bincount(owner,weights=np.maximum(ranked,0)*weights,minlength=len(records))
    return totals,removed,sizes


def local_panel(ordered,config=Objective()):
    config.validate()
    panel=lambda k:np.asarray([m[k] for m in ordered],dtype=np.float64)
    pnl=panel('net_pnl');closed=panel('closed_positions')
    if pnl.ndim!=2 or not pnl.size or not np.isfinite(pnl).all() or closed.shape!=pnl.shape:raise ValueError('Finite complete session panel required')
    records=[r for m in ordered for r in m['ticker_pnl']]
    if len(records)!=pnl.size:raise ValueError('Ticker candidate alignment changed')
    totals,removed,counts=ticker_summary(records,config.ticker_trim_fraction)
    removed=removed.reshape(pnl.shape);counts=counts.reshape(pnl.shape)
    if not np.allclose(totals.reshape(pnl.shape),pnl,rtol=1e-10,atol=1e-7):raise ValueError('Ticker P&L does not reconcile')
    dd=panel('drawdown');peak=panel('maximum_position_risk');distance=panel('maximum_stop_distance')
    safe=(peak<=300.+1e-7)&(distance<=.20+1e-12)&(peak>=0)&(distance>=0)
    valid=np.asarray([m['terminal_valid'] for m in ordered],dtype=bool)
    if any(a.shape!=pnl.shape or not np.isfinite(a).all() for a in (closed,dd,peak,distance)) or (closed<0).any() or (dd<0).any():raise ValueError('Malformed financial summaries')
    if valid.shape!=pnl.shape:raise ValueError('Terminal validity alignment changed')
    feasible=valid.all(0)&safe.all(0)
    score=pnl-config.concentration_weight*removed-config.drawdown_weight*dd
    return dict(pnl=pnl,closed=closed,trimmed=pnl-removed,ticker_trim_removed=removed,traded_tickers=counts,
        other_loss=np.zeros_like(pnl),values=score,eligible=np.broadcast_to(feasible,pnl.shape),drawdown=dd,
        risk_hours=panel('stop_risk_dollar_seconds')/3600,capital_hours=panel('capital_dollar_seconds')/3600,peak_risk=peak,risk_safe=safe)


def score(ordered,complexity,config=Objective()):
    local=local_panel(ordered,config);pnl=local['pnl'];days,count=pnl.shape
    if np.asarray(complexity).shape!=(count,):raise ValueError('Complexity alignment changed')
    total=pnl.sum(0);ticker_removed=local['ticker_trim_removed'].sum(0)
    _,session_removed=positive_trim(pnl,config.session_trim_fraction)
    deduction=config.concentration_weight*np.maximum(ticker_removed,session_removed)
    drawdown=config.drawdown_weight*local['drawdown'].sum(0)
    value=total-deduction-drawdown;feasible=local['eligible'].all(0)
    positive=np.maximum(pnl,0);gross=positive.sum(0);closed=local['closed'].sum(0)
    return dict(total_pnl=total,closed_positions=closed,score=value,cost=-value,feasible=feasible,global_eligible=feasible,
        trimmed_profit=total-deduction,concentration_deduction=deduction,mean_session_profit=pnl.mean(0),complexity=np.asarray(complexity,dtype=np.float64),
        profitable_active_fraction=np.divide((pnl>0).sum(0),(local['closed']>0).sum(0),out=np.zeros(count),where=(local['closed']>0).sum(0)>0),ticker_trim_removed=ticker_removed,
        global_session_trim_removed=session_removed,active_sessions=(local['closed']>0).sum(0),zero_trade_sessions=(local['closed']==0).sum(0),
        best_day_pnl=pnl.max(0),other_days_pnl=total-pnl.max(0),profitable_day_fraction=(pnl>0).mean(0),
        best_day_gross_positive_share=np.divide(positive.max(0),gross,out=np.zeros(count),where=gross>0),
        components=dict(net_profit=total,concentration_penalty=deduction,drawdown_penalty=drawdown),_local=local)


def session_scores(ordered,config=Objective(),prepared=None):
    local=prepared['_local'] if prepared is not None else local_panel(ordered,config)
    return local['values'],local['eligible']
