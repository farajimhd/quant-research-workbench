from types import SimpleNamespace
import numpy as np
import torch
from research.vectorized_backtest.v6.torch_backtest.sparse_replay import SparseInputs,KEY_STRIDE
from research.vectorized_backtest.v6.torch_backtest.evolution import STAGES
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v6.torch_backtest.program import Node,Program,Op,TorchPrograms


def test_lookup_retains_departed_identity_and_expires_capacity_and_quotes():
    x=SparseInputs.__new__(SparseInputs)
    x.tensors={'market_keys':torch.tensor([2,4,KEY_STRIDE+3])}
    x.market={k:torch.tensor(v) for k,v in dict(mark=[2.,3.,100.],volume=[8.,9.,7.],notional=[16.,27.,700.],trade_count=[2.,3.,2.],
        high=[2.,3.,100.],low=[2.,3.,100.],fill_price=[2.,3.,100.],vwap=[2.,3.,100.],bid=[1.9,2.9,99.],ask=[2.1,3.1,101.],
        quote_us=[2000000.,4000000.,3000000.],feature_row=[0,1,2],observed=[True]*3).items()}
    result=x.lookup(torch.tensor([[0,1,-1],[0,1,2]]),5)
    assert result['known'].tolist()==[[True,True,False],[True,True,False]]
    assert not result['volume'].any() and not result['observed'].any()
    assert result['quote_valid'].tolist()==[[True,False,False],[True,False,False]]
    assert result['mark'][0,:2].tolist()==[3.,100.]
    before=x.lookup(torch.tensor([0,1]),1)
    assert not before['known'].any() and torch.isnan(before['mark']).all()


def test_sparse_chunks_match_whole_listing_temporal_programs():
    x=SparseInputs.__new__(SparseInputs);x.device=torch.device('cpu');x.offsets=[0,131,278]
    features=torch.randn(278,len(CATALOG),generator=torch.Generator().manual_seed(7))
    valid=torch.ones_like(features,dtype=torch.bool);valid[120,8]=False
    x.arrays={'feature_keys':np.arange(278)};x.tensors={'features':features,'feature_valid':valid}
    programs=[Program((Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=120),Node(Op.GREATER,a=0,b=1)),2),
        Program((Node(Op.FEATURE,feature=8),Node(Op.LAG,a=0,window=60),Node(Op.GREATER,a=0,b=1)),2)]
    individuals=[SimpleNamespace(programs=lambda p=p:{s:p for s in STAGES}) for p in programs]
    actual,_=x.compile(individuals,chunk_candles=33,listing_batch=3)
    expected=[]
    for a,b in zip(x.offsets[:-1],x.offsets[1:]):
        value,known=TorchPrograms(programs,CATALOG)(features[a:b],valid[a:b])
        expected.append(((value!=0)&known).to(torch.uint8)*sum(1<<i for i in range(len(STAGES))))
    assert torch.equal(actual,torch.cat(expected,dim=1))
    subset,_=x.compile(individuals,chunk_candles=33,listing_batch=3,listing_ids=[1])
    assert not subset[:,:131].any()
    assert torch.equal(subset[:,131:],actual[:,131:])
