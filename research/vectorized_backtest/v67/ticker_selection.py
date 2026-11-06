"""Training-only ticker equity accounting and ranked specialist pools."""
import numpy as np
import torch

def update_equity(state,position,mark,active):
    price=torch.where(torch.isfinite(mark),mark,torch.zeros_like(mark))
    equity=position[...,7]+position[...,0]*(price-position[...,1])
    peak=torch.maximum(state[...,0],equity)
    dd=torch.maximum(state[...,1],peak-equity)
    state.copy_(torch.where(active[...,None],torch.stack((peak,dd),-1),state))

def ticker_scores(metrics,item):
    from .robust_score import reward,TICKER_TARGET
    rewards=np.asarray(metrics['label_ticker_reward'],float)
    penalties=np.asarray(metrics['label_ticker_penalty'],float)
    blocked=np.asarray(metrics['ticker_blocked_requests'],float)
    if rewards.shape!=penalties.shape or rewards.shape!=blocked.shape or rewards.shape[1]!=len(item.listing_ids):
        raise ValueError('Supervised specialist ticker attribution differs')
    values=reward(rewards,TICKER_TARGET)-penalties-5.*blocked
    return [{str(item.receipt['listings'][int(item.listing_ids[j])]['listing_id']):float(v)
             for j,v in enumerate(row) if v!=0.} for row in values]


def specialist_pools(ordered,valid,active,ids):
    n=len(ids)
    if any(len(s['ticker_score'])!=n for s in ordered):raise ValueError('Ticker score alignment changed')
    names=sorted({str(k) for s in ordered for row in s['ticker_score'] for k in row})
    lookup={k:i for i,k in enumerate(names)};totals=np.zeros((n,len(names)),np.float64);present=np.zeros_like(totals,dtype=bool)
    for session in ordered:
        records=[(i,lookup[str(k)],v) for i,row in enumerate(session['ticker_score']) for k,v in row.items()]
        if not records:continue
        rows,columns,values=map(np.asarray,zip(*records));rows=rows.astype(np.int64);columns=columns.astype(np.int64)
        if not np.isfinite(values).all():raise ValueError('Nonfinite ticker score')
        # add.at retains session/record addition order, including aliased keys.
        np.add.at(totals,(rows,columns),values);present[rows,columns]=True
    mask=np.asarray(valid,bool)&np.asarray(active,bool);pools={}
    for column,name in enumerate(names):
        rows=np.flatnonzero(mask&present[:,column]);scores=totals[rows,column]
        if not np.isfinite(scores).all():raise ValueError('Nonfinite ticker score')
        if len(rows):pools[name]=rows[np.lexsort((np.asarray(ids)[rows],-scores))].tolist()
    return pools
