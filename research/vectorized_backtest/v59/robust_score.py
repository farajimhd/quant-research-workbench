"""Logarithmic profit targets; losses and account drawdown remain uncapped."""
import numpy as np
VERSION='log-profit-target-v1'
COEFFICIENT=.1
TICKER_TARGET=500.
SESSION_TARGET=1000.

def reward(profit,target,coefficient=COEFFICIENT):
    p=np.asarray(profit,dtype=np.float64)
    if not np.isfinite(p).all() or not np.isfinite(target) or target<=0 or not 0<=coefficient<=1:
        raise ValueError('Finite profit, positive target and bounded coefficient required')
    return np.minimum(p,target)+coefficient*target*np.log1p(np.maximum(p-target,0.)/target)

def ticker_costs(profits,listings):
    profits=np.asarray(profits,float);listings=np.asarray(listings)
    if len(profits)!=len(listings) or not np.isfinite(profits).all():raise ValueError('Aligned finite ticker ledger required')
    tickers,index=np.unique(listings,return_inverse=True)
    totals=np.bincount(index,weights=profits,minlength=len(tickers))
    return tickers,reward(totals,TICKER_TARGET)

def ticker_ledgers(metrics):
    for i,(profits,listings) in enumerate(zip(metrics['episode_profits'],metrics['episode_listings'])):
        yield (list(profits)+list(metrics.get('open_episode_realized',[[]]*len(metrics['net_pnl']))[i]),
               list(listings)+list(metrics.get('open_episode_listings',[[]]*len(metrics['net_pnl']))[i]))

def ticker_component(metrics):
    result=[]
    for i,(profits,listings) in enumerate(ticker_ledgers(metrics)):
        if not np.isclose(sum(profits),metrics['net_pnl'][i],rtol=1e-10,atol=1e-7):
            raise ValueError('Ticker-date cashflows do not reconcile to session net profit')
        result.append(ticker_costs(profits,listings)[1].sum())
    return np.asarray(result)

def combine(panels):
    if not panels:raise ValueError('At least one session required')
    keys=('net_pnl','gross_losses','drawdown','closed_positions','winning_positions','ticker_component')
    out={k:np.sum([p[k] for p in panels],axis=0) for k in keys}
    nets=np.asarray([p['net_pnl'] for p in panels])
    out['session_component']=reward(nets,SESSION_TARGET).sum(axis=0)
    out['score']=.5*out['ticker_component']+.5*out['session_component']-out['drawdown']
    out['uncapped_score']=out['net_pnl']-out['drawdown']
    out['profitable_sessions']=(nets>0).sum(axis=0)
    out['session_targets_met']=(nets>=SESSION_TARGET).sum(axis=0)
    out['best_session_net']=nets.max(axis=0)
    out['net_excluding_best_session']=out['net_pnl']-out['best_session_net']
    out['eligible']=np.logical_and.reduce([p['eligible'] for p in panels])
    out['maximum_position_risk']=np.maximum.reduce([p['maximum_position_risk'] for p in panels])
    return out
