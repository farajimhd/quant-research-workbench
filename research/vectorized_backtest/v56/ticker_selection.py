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
    width=len(item.listing_ids);result=[]
    for i,pnl in enumerate(metrics['ticker_pnl']):
        losses=np.zeros(width)
        listings=np.asarray(metrics['episode_listings'][i],int)
        profits=np.asarray(metrics['episode_profits'][i],float)
        if len(listings)!=len(profits) or (listings<0).any() or (listings>=width).any():raise ValueError('Ticker ledger alignment changed')
        np.add.at(losses,listings,np.maximum(-profits,0))
        dd=np.asarray(metrics['ticker_drawdown'][i],float)[:width]
        if not np.isfinite(dd).all() or (dd<0).any():raise ValueError('Invalid ticker equity drawdown')
        result.append({str(item.receipt['listings'][int(item.listing_ids[j])]['listing_id']):float(pnl.get(item.receipt['listings'][int(item.listing_ids[j])]['listing_id'],pnl.get(str(item.receipt['listings'][int(item.listing_ids[j])]['listing_id']),0.))-losses[j]-2*dd[j]) for j in np.unique(listings)})
    return result

def specialist_pools(ordered,valid,active,ids):
    if len(ordered)!=1:raise ValueError('V56 specialist policy requires one session')
    rows=ordered[0]['ticker_score']
    if len(rows)!=len(ids):raise ValueError('Ticker score alignment changed')
    pools={}
    for i,row in enumerate(rows):
        if not valid[i] or not active[i]:continue
        for ticker,score in row.items():
            if not np.isfinite(score):raise ValueError('Nonfinite ticker score')
            pools.setdefault(str(ticker),[]).append((i,float(score)))
    return {k:[i for i,v in sorted(pools[k],key=lambda x:(-x[1],ids[x[0]]))] for k in sorted(pools)}
