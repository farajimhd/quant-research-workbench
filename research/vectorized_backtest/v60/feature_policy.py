"""Searchable feature authority; raw execution data is never modified."""
import numpy as np
from research.vectorized_backtest.v60.features import CATALOG
from .authority import VERSION, BLOCKED, ALLOWED, feature_index
SHARES=tuple(i for i,f in enumerate(CATALOG) if f.name in ('log_float_shares','log_shares_outstanding'))

def transform_projected(values,columns=None):
    # The absolute bank stores tanh(log1p(log1p(shares))/4). Decode only
    # these two columns, then bucket to powers of ten before either channel.
    import torch
    columns=list(range(len(CATALOG))) if columns is None else columns
    c=len(columns);out=values.clone()
    for canonical in SHARES:
        if canonical not in columns:continue
        i=columns.index(canonical)
        logged=torch.expm1(4*torch.atanh(values[...,i].clamp(-.999999,.999999)))
        bucket=torch.floor(logged/np.log(10.))
        valid=values[...,2*c+i]>0
        absolute=torch.tanh(bucket/10.)
        count=valid.sum(-1,keepdim=True).clamp_min(1)
        mean=torch.where(valid,bucket,0.).sum(-1,keepdim=True)/count
        delta=bucket-mean
        sd=(torch.where(valid,delta.square(),0.).sum(-1,keepdim=True)/count).sqrt().clamp_min(1e-6)
        out[...,i]=torch.where(valid,absolute,0.)
        out[...,c+i]=torch.where(valid,torch.tanh(delta/(2*sd)),0.)
    return out
