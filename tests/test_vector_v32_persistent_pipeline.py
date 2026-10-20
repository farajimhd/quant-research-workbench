from types import SimpleNamespace
import numpy as np
import pytest
import torch


def test_prepacked_population_is_exact_and_does_not_advance_rng(monkeypatch):
    import psutil
    from research.vectorized_backtest.v32.session_owner import prepare_population
    from research.vectorized_backtest.v32.genome import sample
    from research.vectorized_backtest.v32.lineage import program_key
    from research.vectorized_backtest.v32.network_pack import pack_networks
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=200*2**30))
    rng=np.random.default_rng(2020);members=sample(rng,10);state=rng.bit_generator.state
    keys=[program_key(m) for m in members];packet=prepare_population(members,set(keys[:2]),4)
    assert packet.keys==keys and packet.missing==list(range(2,10))
    assert rng.bit_generator.state==state
    for offset,(packed,seconds) in packet.batches.items():
        reference=pack_networks(members[offset+2:offset+6])
        for k,v in reference.items():
            if k=='values':
                for a,b in zip(v,packed[k]):np.testing.assert_array_equal(a,b)
            else:np.testing.assert_array_equal(v,packed[k])
        assert seconds>=0
    for original,slim in zip(members,packet.members):
        assert slim.policy==original.policy and slim.minimum_age==original.minimum_age
        assert not hasattr(slim,'networks')


class Bank:
    def __init__(self):
        self.values=torch.tensor(np.random.default_rng(2020).normal(size=(19,7,3)),dtype=torch.float32,device='cuda')
        self.values[2,3,0]=float('nan');self.values[:,:,1]=1.
        self.tensors=dict(source=torch.arange(19,device='cuda')[:,None].expand(-1,7),
            ids=torch.zeros((19,7),device='cuda',dtype=torch.int64),clocks=torch.arange(19,device='cuda'))
    def _values(self,rows,ids,clock,columns):
        v=self.values[rows,torch.arange(7,device='cuda')[:,None]]
        return v,torch.isfinite(v)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_shared_market_blocks_exact_projection_and_tail():
    from research.vectorized_backtest.v32.market_block import MarketBlock
    from research.vectorized_backtest.v32.network import NetworkPlan
    plan=object.__new__(NetworkPlan);plan.columns=[0,1,2]
    plan.logarithmic=torch.tensor([True,False,True],device='cuda')
    data=SimpleNamespace(device=torch.device('cuda'),listing_ids=np.arange(7),clocks=19,resident_features=Bank())
    cache=MarketBlock(plan,data,block=8);streams=[torch.cuda.Stream(),torch.cuda.Stream()]
    for start in (0,8,16):
        cache.enqueue(streams)
        for stream in streams:stream.synchronize()
        for i in range(start,start+8):
            clock=torch.tensor(min(i,17),device='cuda')
            expected=plan._project(data,clock);actual=cache.read(clock)
            for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=0)
    cache.reset();cache.enqueue(streams)
    for stream in streams:stream.synchronize()
    for a,b in zip(cache.read(torch.tensor(0,device='cuda')),plan._project(data,torch.tensor(0,device='cuda'))):
        torch.testing.assert_close(a,b,rtol=0,atol=0)
