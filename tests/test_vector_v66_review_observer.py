from types import SimpleNamespace
import numpy as np
import torch
import pytest
from research.vectorized_backtest.v66.genome import sample,STAGES
from research.vectorized_backtest.v66.candidate_investigation import Observer as Reference
from research.vectorized_backtest.v66.review_observer import Observer


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_batched_observer_matches_reference_counts_with_missing_data(device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('CUDA required')
    torch.manual_seed(78);members=sample(np.random.default_rng(78),3)
    counts=torch.tensor([[len(m.networks[s].nodes) for s in STAGES] for m in members])
    offsets=np.cumsum(np.r_[0,counts.numpy().ravel()[:-1]])
    slots=torch.tensor([[0,1,2,-1,3],[3,2,1,0,-1],[0,3,2,1,-1],[1,2,3,0,-1]])
    bids=torch.ones((4,4))*10;asks=bids+.02
    data=SimpleNamespace(tickers=['A','B','C','D'],tensors={'bid':bids,'ask':asks},host_tensors={'bid':bids,'ask':asks})
    work=torch.randn((4,int(counts.sum()),5));known=torch.rand(work.shape)>.3;work[0,0,0]=float('inf')
    engine=SimpleNamespace(base=SimpleNamespace(members=members,offsets_cpu=offsets,counts=counts),
        data=data,slots=slots,slots_cpu=slots.numpy(),work=work,known=known)
    signals=torch.randint(0,32,(4,3,5),dtype=torch.int16);abstain=((signals&1)!=0)&(torch.rand(signals.shape)>.5)
    signals=torch.where(abstain,signals&~1,signals)
    prices=torch.tensor([10.02,10.,9.,12.,9.]).expand(4,3,5,5).clone();prices[0,0,0,2]=float('nan')
    packet=(signals,torch.ones((4,3,5,3)),prices,torch.zeros_like(signals),abstain)
    engine.slots=slots.to(device);engine.work=work.to(device);engine.known=known.to(device);engine.base.counts=counts.to(device)
    engine.data.tensors={k:v.to(device) for k,v in data.tensors.items()};packet=tuple(v.to(device) for v in packet)
    indices=torch.arange(4,device=device)
    reference=Reference();reference.capture(engine,indices,packet)
    observer=Observer();observer.capture(engine,indices,packet);actual=observer.finish()
    for key,row in reference.rows.items():
        assert {k:actual[key][k] for k in row}==row
        assert len(actual[key]['bounded_traces'])<=7
        assert len(actual[key]['trace_columns'])==23
