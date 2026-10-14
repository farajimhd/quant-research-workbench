import numpy as np
import polars as pl
import torch
import pytest
from research.vectorized_backtest.v18 import enriched_math as em
from research.vectorized_backtest.v18.features import CATALOG,LEGACY,EXTRA
from research.vectorized_backtest.v17.features import CATALOG as OLD
from research.vectorized_backtest.v18.program import Op,Node,Program,TorchPrograms


def frame(n=400):
    t=np.arange(1,n+1);c=np.exp(t*.0001)
    return pl.DataFrame(dict(second=t,open=c*.9999,high=c*1.001,low=c*.999,close=c,volume=np.ones(n)*10,
        notional=c*10,trade_count=np.ones(n,dtype=np.int64),price_valid=np.ones(n,dtype=np.int64),extremes_valid=np.ones(n,dtype=np.int64)))


def test_append_ids_and_clock_return():
    assert tuple(OLD)==LEGACY and tuple(CATALOG[:len(OLD)])==tuple(OLD)
    f=frame();v=em.own(f,np.array([300,301,320]),0)
    assert v.shape==(3,len(em.OWN_NAMES))
    assert np.allclose(v[:,0],.0005)
    changed=f.with_columns(pl.when(pl.col('second')>301).then(999.).otherwise(pl.col('close')).alias('close'))
    assert np.allclose(em.own(f,np.array([300,301]),0),em.own(changed,np.array([300,301]),0),equal_nan=True)
    assert np.isnan(em.returns(np.array([1,100]),np.array([1.,2.]),np.array([True,True]),np.array([100]),5)[0])
    assert np.isnan(em.returns(np.array([1,2]),np.array([1.,2.]),np.array([True,False]),np.array([2]),1)[0])


def test_market_ties_missing_and_coverage():
    a=np.array([[0.,np.nan],[0.,1.],[.1,np.nan]])
    common,pct=em.market_summary(a,4)
    assert common[0,4]==.75 and common[1,4]==.25
    assert np.allclose(pct[:,0],[.25,.25,1.])
    assert np.isnan(pct[:,1]).all() and np.isnan(common[1,:4]).all()


def test_completed_multitimeframe_and_unknown_extremes():
    f=frame(30);v=em.own(f,np.array([14]),0)
    ix=em.OWN_NAMES.index('candle.15s.body');assert np.isnan(v[0,ix])
    missing=f.with_columns(pl.when(pl.col('second')==15).then(0).otherwise(pl.col('extremes_valid')).alias('extremes_valid'))
    v=em.own(missing,np.array([15]),0);assert np.isnan(v[0,em.OWN_NAMES.index('candle.15s.range')])

def test_bounded_source_prefix_is_equivalent():
    f=frame(900);targets=np.array([600,700,899])
    full=em.own(f,targets,0);bounded=em.own(f.filter(pl.col('second')>=283),targets,0)
    assert np.allclose(full,bounded,equal_nan=True,atol=1e-7)

def test_fee_ratio_includes_minimum_order_commission():
    f=frame().with_columns(*(pl.col(n)*15 for n in ('open','high','low','close','notional')))
    v=em.own(f,np.array([400]),0);price=f['close'][-1];quantity=np.floor(1000/price)
    expected=v[0,em.OWN_NAMES.index('volatility.15s')]/(2*max(.005*quantity,1)/(quantity*price))
    assert np.isclose(v[0,em.OWN_NAMES.index('execution.volatility_fee_ratio')],expected)


def test_std_masks_validation_and_genetic_reachability():
    p=Program((Node(Op.FEATURE,feature=len(LEGACY)),Node(Op.STD,a=0,window=3)),1)
    p.validate(CATALOG,output_unit='ratio')
    x=torch.zeros(5,len(CATALOG));x[:,len(LEGACY)]=torch.tensor([1.,2.,3.,4.,5.]);m=torch.ones_like(x,dtype=torch.bool)
    v,k=TorchPrograms([p],CATALOG,'cpu',output_unit='ratio')(x,m)
    assert k.tolist()==[[False,False,True,True,True]] and torch.allclose(v[0,2:],torch.full((3,),np.sqrt(2/3)),atol=1e-6)
    m[2,len(LEGACY)]=False;_,k=TorchPrograms([p],CATALOG,'cpu',output_unit='ratio')(x,m);assert not k.any()
    from research.vectorized_backtest.v18.expressions import TEMPORAL,UNARY,numeric,exchange
    from research.vectorized_backtest.v18.search_space import mutation_feature
    assert Op.STD in TEMPORAL and Op.STD in UNARY
    rng=np.random.default_rng(8)
    assert any(any(n.op==Op.STD for n in numeric(rng,'ratio').nodes) for _ in range(2000))
    assert mutation_feature(rng,len(LEGACY))!=len(LEGACY)

def test_std_cannot_hide_an_implicit_constant_management_rule():
    from research.vectorized_backtest.v18.semantics import validate_rule,SemanticError
    p=Program((Node(Op.FEATURE,feature=len(LEGACY)),Node(Op.STD,a=0,window=1),
        Node(Op.CONSTANT,value=-.1),Node(Op.GREATER,a=1,b=2)),3)
    with pytest.raises(SemanticError,match='implicit_constant_signal'):validate_rule(p,'exit')


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_fused_std_parity():
    from research.vectorized_backtest.v18.fused_program import FusedPrograms
    ps=[Program((Node(Op.FEATURE,feature=0),Node(Op.STD,a=0,window=w)),1) for w in (1,3,20,60,120)]
    torch.manual_seed(4);x=torch.randn(3,120,len(CATALOG),device='cuda');m=torch.rand_like(x)>.02
    a,am=TorchPrograms(ps,CATALOG,'cuda',output_unit=CATALOG[0].unit)(x,m)
    b,bm=FusedPrograms(ps,CATALOG,'cuda',output_unit=CATALOG[0].unit)(x,m)
    assert torch.equal(am,bm);assert torch.allclose(a,b,atol=2e-6,rtol=2e-6)
