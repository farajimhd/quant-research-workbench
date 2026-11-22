import torch
from .gap_audit import price_bands,CloseBands


def test_band_boundaries_and_denominator_counts():
    x=torch.tensor([0.,.01,.1,1.,5.,20.,100.],dtype=torch.float64)
    assert price_bands(x).tolist()==[0,0,1,2,3,4,5]
    raw=torch.ones(1,64,18,dtype=torch.float64);valid=torch.zeros_like(raw,dtype=torch.bool)
    raw[0,0,3]=0;raw[0,1,3]=1e-9;raw[0,2,3]=.5
    valid[0,:3,3]=True
    p=raw.clone();p[0,2,3]=.6
    metric=CloseBands();metric.add(p,raw,valid,torch.tensor([1000000]))
    result=metric.result();assert result['zero_denominators']==1 and result['tiny_positive_denominators']==1
    row=next(iter(result['rows'].values()))
    assert row['count']==1 and abs(row['mean_pct']-20)<1e-10 and abs(row['p90_pct']-20)<1e-10
