import pytest
import torch
from research.vectorized_backtest.v5.torch_backtest.gate_compiler import FeatureResident
from research.vectorized_backtest.v5.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v5.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v5.torch_backtest.evolution import Individual, STAGES
from research.vectorized_backtest.v5.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v5.torch_backtest.program import Node, Op, TorchPrograms


def test_batched_gates_match_independent_full_history_with_sparse_clocks():
    class Bank:
        def listing(self, identity, previous=None):
            clocks=torch.arange(1,310)[::identity+1]*1_000_000
            features=torch.zeros(len(clocks),len(CATALOG))
            features[:,8]=torch.sin(torch.arange(len(clocks))*.3+identity)
            valid=torch.ones_like(features,dtype=torch.bool)
            valid[12::23,8]=False
            return clocks,features,valid
    space=StrategySpace()
    individuals=[]
    for window in (3,120):
        nodes=[Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=window),Node(Op.GREATER,a=0,b=1)]
        individuals.append(Individual(space.default.tolist(),{s:[(nodes,2)] for s in STAGES},{s:[] for s in STAGES}))
    tape=synthetic_tape(seconds=320,listings=4)
    resident=FeatureResident(Bank(),range(3),device='cpu')
    expected=torch.zeros(len(tape.clocks),2,len(tape.tickers),dtype=torch.uint8)
    boundaries=tape.clocks*1_000_000
    for ticker,(clocks,features,valid) in enumerate(resident.rows):
        index=torch.searchsorted(boundaries,clocks)
        for bit,stage in enumerate(STAGES):
            values,known=TorchPrograms([v.programs()[stage] for v in individuals],CATALOG)(features,valid)
            expected[index,:,ticker]|=((values!=0)&known).T.to(torch.uint8)*(1<<bit)
    for batch_size in (1,2,32):
        actual,_=resident.compile(individuals,tape,chunk_candles=41,listing_batch=batch_size)
        assert torch.equal(actual,expected)
    pointer=actual.data_ptr()
    actual.fill_(255)  # Stale previous-population bits must be cleared.
    reused,_=resident.compile(individuals,tape,chunk_candles=41,out=actual)
    assert reused is actual and reused.data_ptr()==pointer
    assert torch.equal(reused,expected)
    with pytest.raises(ValueError,match='Reusable packed'):
        resident.compile(individuals,tape,out=actual[:,:1])
    with pytest.raises(ValueError,match='Reusable packed'):
        resident.compile(individuals,tape,packed=False,out=actual)
    unpacked,_=resident.compile(individuals,tape,chunk_candles=41,packed=False)
    for bit,stage in enumerate(STAGES):
        assert torch.equal(unpacked[stage],(expected&(1<<bit))!=0)
    with pytest.raises(MemoryError,match='workspace'):
        resident.compile(individuals,tape,workspace_gib=1e-9)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA buffer residency required')
def test_cuda_reuses_unindexed_device_gate_buffer():
    class Bank:
        def listing(self,identity,previous=None):
            return torch.arange(1,21)*1_000_000,torch.ones(20,len(CATALOG)),torch.ones(20,len(CATALOG),dtype=torch.bool)
    space=StrategySpace();nodes=[Node(Op.CONSTANT,value=1,unit='bool')]
    individual=Individual(space.default.tolist(),{s:[(nodes,0)] for s in STAGES},{s:[] for s in STAGES})
    tape=synthetic_tape(seconds=20,device='cuda')
    resident=FeatureResident(Bank(),range(2),device='cuda')
    expected,_=resident.compile([individual],tape)
    buffer=torch.full_like(expected,255);pointer=buffer.data_ptr()
    actual,_=resident.compile([individual],tape,out=buffer)
    assert actual is buffer and actual.data_ptr()==pointer
    assert torch.equal(actual,expected)
