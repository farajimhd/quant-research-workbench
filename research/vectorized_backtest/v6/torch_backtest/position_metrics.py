"""Elapsed-seconds position statistics, reporting only."""
import torch


def duration_summary(samples,prefix='closed_hold'):
    names=('min_seconds','mean_seconds','median_seconds','p90_seconds','max_seconds')
    if not samples:return {prefix+'_'+name:None for name in names}
    values=torch.as_tensor(samples,dtype=torch.float64)
    if not torch.isfinite(values).all() or (values<0).any():
        raise ValueError('Position durations must be finite nonnegative elapsed seconds')
    stats=(values.min(),values.mean(),torch.quantile(values,.5),torch.quantile(values,.9),values.max())
    return {prefix+'_'+name:float(value) for name,value in zip(names,stats)}
