"""Reporting-only closed position episodes, including fees and partial fills."""
import math
import numpy as np


def episode_pnls(fills):
    fills=np.asarray(fills,dtype=np.float64)
    if fills.ndim!=2 or fills.shape[1]!=9 or not np.isfinite(fills).all():raise ValueError('Invalid fill ledger')
    if not len(fills):return np.empty(0,dtype=np.float64)
    if not np.isin(fills[:,3],[-1.,1.]).all() or (fills[:,4]<=0).any():raise ValueError('Invalid fill side/quantity')
    order=np.lexsort((np.arange(len(fills)),fills[:,2],fills[:,1]))
    rows=fills[order];keys=rows[:,1:3]
    starts=np.r_[True,(keys[1:]!=keys[:-1]).any(1)]
    group=np.cumsum(starts)-1;offsets=np.flatnonzero(starts)
    cumulative=np.cumsum(rows[:,3]*rows[:,4])
    bases=np.r_[0.,cumulative[offsets[1:]-1]]
    quantities=cumulative-bases[group]
    if (quantities<0).any():raise ValueError('Position episode sold unavailable shares')
    episode_starts=np.r_[True,(quantities[:-1]==0)|starts[1:]]
    begin=np.flatnonzero(episode_starts)
    end=np.r_[begin[1:]-1,len(rows)-1]
    cash=-rows[:,3]*rows[:,4]*rows[:,5]-rows[:,6]
    sums=np.add.reduceat(cash,begin)
    return sums[quantities[end]==0]


def tail_summary(values,fraction=.2):
    values=np.asarray(values,dtype=np.float64)
    if values.ndim!=1 or not np.isfinite(values).all() or not 0<fraction<=1:raise ValueError('Invalid position tail')
    count=math.ceil(len(values)*fraction)
    return dict(position_tail_mean_pnl=float(np.sort(values)[:count].mean()) if count else None,
        position_tail_count=count,position_tail_fraction=fraction,position_tail_closed_count=len(values))
