"""Logarithmic profit targets with blocked-request penalties; drawdown is diagnostic."""
import numpy as np
from .supervision import VERSION,SETTINGS
COEFFICIENT=.1
TICKER_TARGET=2000.
SESSION_TARGET=5000.

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
        yield (np.concatenate((profits,metrics.get('open_episode_realized',[[]]*len(metrics['net_pnl']))[i])),
               np.concatenate((np.asarray(listings,np.int64),np.asarray(metrics.get('open_episode_listings',[[]]*len(metrics['net_pnl']))[i],np.int64))))

def ticker_component(metrics):
    rewards=np.asarray(metrics['label_ticker_reward'],dtype=float)
    if not np.isfinite(rewards).all() or not np.allclose(rewards.sum(1),metrics['label_reward'],rtol=1e-10,atol=1e-7):
        raise ValueError('Supervised ticker rewards do not reconcile')
    if np.any(np.asarray(metrics['label_reward'])>np.asarray(metrics['net_pnl'])+1e-7):
        raise ValueError('Label credit exceeds actual net profit')
    return reward(rewards,TICKER_TARGET).sum(1)


def combine(panels):
    if not panels:raise ValueError('At least one session required')
    keys=('net_pnl','gross_losses','drawdown','closed_positions','winning_positions','ticker_component','blocked_requests','label_reward','label_penalty','label_entry_errors','label_exit_errors','label_late_notional_seconds',
          'label_opportunities','label_captured','label_missed','label_missed_penalty','label_capture_bonus')
    out={k:np.sum([p[k] for p in panels],axis=0) for k in keys}
    nets=np.asarray([p['label_reward'] for p in panels])
    out['session_component']=reward(nets,SESSION_TARGET).sum(axis=0)
    out['blocked_penalty']=5.*out['blocked_requests']
    base=.5*out['ticker_component']+.5*out['session_component']-out['blocked_penalty']-out['label_penalty']
    out['absolute_score']=base-out['label_missed_penalty']
    out['abstention_score']=-SETTINGS['missed_pair_penalty']*out['label_opportunities']
    # A candidate-independent baseline shift preserves pairwise costs while
    # keeping concentration/diversity descriptors meaningful across sessions.
    out['score']=base+out['label_capture_bonus']
    if not np.allclose(out['score'],out['absolute_score']-out['abstention_score'],rtol=1e-10,atol=1e-7):
        raise ValueError('Opportunity score does not reconcile to abstention')
    out['uncapped_score']=out['label_reward']-out['label_penalty']-out['blocked_penalty']+out['label_capture_bonus']
    out['profitable_sessions']=(nets>0).sum(axis=0)
    out['session_targets_met']=(nets>=SESSION_TARGET).sum(axis=0)
    out['best_session_net']=np.asarray([p['net_pnl'] for p in panels]).max(axis=0)
    out['net_excluding_best_session']=out['net_pnl']-out['best_session_net']
    out['eligible']=np.logical_and.reduce([p['eligible'] for p in panels])
    out['parent_eligible']=out['eligible']&(out['closed_positions']>0)
    admission_keys={k for p in panels for k in p if k.startswith('admission_')}
    if any(not admission_keys.issubset(p) for p in panels):raise ValueError('Incomplete admission evidence across sessions')
    out.update({k:np.sum([p[k] for p in panels],axis=0) for k in sorted(admission_keys)})
    out['maximum_position_risk']=np.maximum.reduce([p['maximum_position_risk'] for p in panels])
    return out
