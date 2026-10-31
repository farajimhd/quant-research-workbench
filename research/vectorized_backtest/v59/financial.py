"""Exact closed and unliquidated realized cashflow attribution; no fabricated fills."""
import numpy as np


def ticker_totals(metrics,item):
    results=[]
    n=len(metrics['net_pnl']);width=len(item.listing_ids)
    for i in range(n):
        listings=np.asarray(metrics['episode_listings'][i]+metrics['open_episode_listings'][i],dtype=np.int64)
        profits=np.asarray(metrics['episode_profits'][i]+metrics['open_episode_realized'][i],dtype=np.float64)
        if listings.shape!=profits.shape or (listings<0).any() or (listings>=width).any():
            raise ValueError('Position cashflow attribution changed')
        totals=np.bincount(listings,weights=profits,minlength=width)
        if not np.isclose(totals.sum(),metrics['net_pnl'][i],rtol=1e-10,atol=1e-7):
            raise ValueError(f'Completed plus open realized cashflows do not reconcile: candidate_index={i}, net_pnl={metrics["net_pnl"][i]:.17g}, position_cashflows={totals.sum():.17g}, difference={totals.sum()-metrics["net_pnl"][i]:.17g}')
        used=np.unique(listings)
        results.append({item.receipt['listings'][int(item.listing_ids[j])]['listing_id']:float(totals[j]) for j in used})
    return results
