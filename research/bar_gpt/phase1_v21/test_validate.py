import torch
from ..phase1_v20.test_model import build
from ..phase1_v7.full_data import Batch
from .validate import Accumulator


def test_accumulator_reset_and_counts():
    torch.set_num_threads(2);model,_,_=build();model.eval()
    raw=torch.ones(2,64,18);valid=torch.ones_like(raw,dtype=torch.bool)
    times=1_700_000_000_000_000+torch.arange(64).expand(2,-1)*1_000_000
    batch=Batch(raw,times,raw,valid,raw,torch.full((2,),1_000_000,dtype=torch.int64))
    pos=torch.arange(16).expand(2,-1);metric=Accumulator('cpu')
    metric.add(model,batch,pos);expected=[t.clone() for t in metric.buffers()]
    assert metric.raw['masked'].windows==2
    assert metric.raw['masked'].raw[...,0].sum()==2*48*18
    assert metric.raw['all_visible'].raw[...,0].sum()==2*64*18
    metric.zero();assert all(t.count_nonzero()==0 for t in metric.buffers())
    assert all(m.windows==0 for m in metric.raw.values())
    metric.add(model,batch,pos)
    for a,b in zip(metric.buffers(),expected):torch.testing.assert_close(a,b,rtol=0,atol=0)
