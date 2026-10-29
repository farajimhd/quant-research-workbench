"""Sparse-history-aware concentration penalties; retain all losses and drawdown."""
import numpy as np

VERSION='concentration-v1'
COEFFICIENT=.5


def concentration(best,count,coefficient=COEFFICIENT):
    if not np.isfinite(coefficient) or not 0<=coefficient<=1:raise ValueError('Invalid concentration coefficient')
    return coefficient*np.maximum(best,0.)*(1.-1./np.maximum(count,1))

def ticker_costs(profits,listings):
    profits=np.asarray(profits,float);listings=np.asarray(listings)
    if len(profits)!=len(listings) or not np.isfinite(profits).all():raise ValueError('Aligned finite ticker episode ledger required')
    tickers,index=np.unique(listings,return_inverse=True)
    totals=np.bincount(index,weights=profits,minlength=len(tickers))
    losses=np.bincount(index,weights=np.maximum(-profits,0),minlength=len(tickers))
    best=np.zeros(len(tickers));np.maximum.at(best,index,profits)
    return tickers,totals-losses-concentration(best,np.bincount(index,minlength=len(tickers)))

def ticker_component(metrics):
    if len(metrics['episode_profits'])!=len(metrics['episode_listings']):raise ValueError('Candidate ticker ledger mismatch')
    return np.asarray([ticker_costs(p,t)[1].sum() for p,t in zip(metrics['episode_profits'],metrics['episode_listings'])])

def combine(panels):
    if not panels:raise ValueError('At least one session required')
    keys=('net_pnl','gross_losses','drawdown','closed_positions','winning_positions','ticker_component')
    out={k:np.sum([p[k] for p in panels],axis=0) for k in keys}
    nets=np.asarray([p['net_pnl'] for p in panels]);best=nets.argmax(axis=0);cols=np.arange(nets.shape[1])
    costs=nets-np.asarray([p['gross_losses'] for p in panels])-np.asarray([p['drawdown'] for p in panels])
    # One profitable session retains its profit; all session drawdowns still count.
    out['session_component']=costs.sum(axis=0)-concentration(nets[best,cols],(nets>0).sum(axis=0))
    out['score']=out['ticker_component']+out['session_component']
    out['eligible']=np.logical_and.reduce([p['eligible'] for p in panels])
    out['maximum_position_risk']=np.maximum.reduce([p['maximum_position_risk'] for p in panels])
    return out
