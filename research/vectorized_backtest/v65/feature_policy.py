"""Searchable feature authority; raw execution data is never modified."""
import numpy as np
from research.vectorized_backtest.v65.features import CATALOG
from .authority import VERSION, BLOCKED, ALLOWED, feature_index
SHARES=tuple(i for i,f in enumerate(CATALOG) if f.name in ('log_float_shares','log_shares_outstanding'))

def transform_projected(values,columns=None):
    # The absolute bank stores tanh(log1p(log1p(shares))/4). Decode only
    # these two columns, then bucket to powers of ten before either channel.
    import torch
    columns=list(range(len(CATALOG))) if columns is None else columns
    c=len(columns);out=values.clone()
    if values.ndim==3:
        # Preserve scalar-clock CUDA rounding only for the two share channels.
        # Compact extraction avoids cloning the full feature bank per candle.
        shares=[i for i in SHARES if i in columns]
        indices=[bank*c+columns.index(i) for bank in range(3) for i in shares]
        if shares:
            compact=torch.stack([values[...,i] for i in indices],-1)
            transformed=torch.stack([transform_projected(row,shares) for row in compact])
            for j,i in enumerate(indices):out[...,i]=transformed[...,j]
        return out
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
