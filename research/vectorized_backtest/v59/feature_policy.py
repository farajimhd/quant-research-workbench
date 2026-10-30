"""Searchable feature authority; raw execution data is never modified."""
import numpy as np
from research.vectorized_backtest.v42.features import CATALOG
VERSION='identity-reduced-v1'
REMOVED={'log_days_since_split','split_present','log_days_since_reverse_split','reverse_split_present','split_this_session','reverse_split_this_session'}
BLOCKED=frozenset(i for i,f in enumerate(CATALOG) if f.name in REMOVED or f.unit in ('price','log_price'))
ALLOWED=tuple(i for i in range(len(CATALOG)) if i not in BLOCKED)
SHARES=tuple(i for i,f in enumerate(CATALOG) if f.name in ('log_float_shares','log_shares_outstanding'))

def feature_index(rng):
    from research.vectorized_backtest.v27.search_space import feature_index as original
    while True:
        i=int(original(rng))
        if i not in BLOCKED:return i

def transform_projected(values):
    # The absolute bank stores tanh(log1p(log1p(shares))/4). Decode only
    # these two columns, then bucket to powers of ten before either channel.
    import torch
    c=len(CATALOG);out=values.clone()
    for i in SHARES:
        logged=torch.expm1(4*torch.atanh(values[...,i].clamp(-.999999,.999999)))
        bucket=torch.floor(logged/np.log(10.))
        valid=values[...,2*c+i]>0
        absolute=torch.tanh(bucket/10.)
        count=valid.sum().clamp_min(1)
        mean=torch.where(valid,bucket,0.).sum()/count
        delta=bucket-mean
        sd=(torch.where(valid,delta.square(),0.).sum()/count).sqrt().clamp_min(1e-6)
        out[...,i]=torch.where(valid,absolute,0.)
        out[...,c+i]=torch.where(valid,torch.tanh(delta/(2*sd)),0.)
    return out
