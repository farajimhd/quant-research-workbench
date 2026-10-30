"""Mark-to-market account statistics from actual persistent-order fills."""
import torch


def account(before, after, old_orders, orders, aggregate, mark, complete, record):
    q,avg,stop,_,_,_,_,realized,episode=after.unbind(-1)
    price=torch.where(torch.isfinite(mark),mark,torch.zeros_like(mark))
    equity=(realized+q*(price-avg)).sum(-1)
    peak=torch.maximum(aggregate[...,0],equity)
    dd=torch.maximum(aggregate[...,1],peak-equity)
    closed=(before[...,0]>1.e-12)&(q<=1.e-12)
    profit=realized-before[...,8]
    worst=torch.minimum(aggregate[...,8],torch.where(closed,profit,float('inf')).amin(-1))
    risk=q*(avg-stop).clamp_min(0)
    distance=torch.where(q>0,(avg-stop).clamp_min(0)/avg.clamp_min(1.e-12),0.)
    filled=orders[...,14]>old_orders[...,14]
    # Completed records retain the final totals when the active kind resets.
    new_order=orders[...,12]>old_orders[...,12]
    delta_qty=torch.where(new_order,orders[...,4],orders[...,4]-old_orders[...,4])
    delta_notional=torch.where(new_order,orders[...,5],orders[...,5]-old_orders[...,5])
    first=filled & (orders[...,4]==delta_qty)
    kind=record[...,0]
    return torch.stack((peak,dd,aggregate[...,2]+(q*price).sum(-1),aggregate[...,3]+risk.sum(-1),
        aggregate[...,4]+(first&(kind==1)).sum(-1),aggregate[...,5]+(first&(kind==2)).sum(-1),
        aggregate[...,6]+(first&(kind==3)).sum(-1),aggregate[...,7]+(~(q>1.e-12).any(-1)),
        worst,aggregate[...,9]+closed.sum(-1),aggregate[...,10]+(closed&(profit>0)).sum(-1),
        aggregate[...,11]+torch.where(filled,delta_notional,0.).sum(-1),
        torch.maximum(aggregate[...,12],risk.amax(-1)),torch.maximum(aggregate[...,13],distance.amax(-1)),
        aggregate[...,14],aggregate[...,15]),-1)
