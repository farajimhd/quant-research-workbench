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
    from .robust_score import ticker_costs,ticker_ledgers
    result=[]
    for profits,listings in ticker_ledgers(metrics):
        tickers,values=ticker_costs(profits,listings)
        # Attribute blocked requests by ticker when constructing specialist ranks.
        blocked=metrics.get('ticker_blocked_requests')
        if blocked is None:raise ValueError('Ticker penalty attribution required')
        index=len(result)
        extra=np.asarray(blocked[index],dtype=float)
        tickers=np.union1d(tickers,np.flatnonzero(extra))
        original=dict(zip(*ticker_costs(profits,listings)))
        values=np.asarray([original.get(j,0.)-5.*extra[int(j)] for j in tickers])
        if any(j<0 or j>=len(item.listing_ids) for j in tickers):raise ValueError('Ticker ledger alignment changed')
        result.append({str(item.receipt['listings'][int(item.listing_ids[int(j)])]['listing_id']):float(v) for j,v in zip(tickers,values)})
    return result

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
