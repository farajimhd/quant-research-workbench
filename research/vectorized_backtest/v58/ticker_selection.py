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
    from .robust_score import ticker_costs
    result=[]
    for profits,listings in zip(metrics['episode_profits'],metrics['episode_listings']):
        tickers,values=ticker_costs(profits,listings)
        if any(j<0 or j>=len(item.listing_ids) for j in tickers):raise ValueError('Ticker ledger alignment changed')
        result.append({str(item.receipt['listings'][int(item.listing_ids[int(j)])]['listing_id']):float(v) for j,v in zip(tickers,values)})
    return result

def specialist_pools(ordered,valid,active,ids):
    rows=[{} for _ in ids]
    for session in ordered:
        if len(session['ticker_score'])!=len(ids):raise ValueError('Ticker score alignment changed')
        for i,values in enumerate(session['ticker_score']):
            for ticker,score in values.items():
                if not np.isfinite(score):raise ValueError('Nonfinite ticker score')
                key=str(ticker);rows[i][key]=rows[i].get(key,0.)+score
    pools={}
    for i,row in enumerate(rows):
        if not valid[i] or not active[i]:continue
        for ticker,score in row.items():
            if not np.isfinite(score):raise ValueError('Nonfinite ticker score')
            pools.setdefault(str(ticker),[]).append((i,float(score)))
    return {k:[i for i,v in sorted(pools[k],key=lambda x:(-x[1],ids[x[0]]))] for k in sorted(pools)}
