from types import SimpleNamespace
import numpy as np
import polars as pl
from research.vectorized_backtest.v6.torch_backtest.compact_prepare import feature_rows, vector_validity, sparse_market
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CertifiedBank


def bank(clocks, factor):
    x=np.zeros((len(clocks),37),dtype=np.float32)
    x[:,3]=np.log(2.);x[:,35:37]=1;x[:,13]=1;x[:,12]=np.log1p(3.)
    return SimpleNamespace(clocks=np.asarray(clocks,dtype=np.int64),scalar=x,
        levels=np.zeros((len(clocks),2,5,11),dtype=np.float32),manifest={'offsets':{'x':(0,len(clocks))}},
        split_basis={'x':dict(rvol_price_factor=factor,history_price_factor=5.,split_this_session=1,reverse_split_this_session=1)})


def test_numpy_feature_history_exactly_matches_certified_reader():
    old=bank([1000000,2000000],2.);new=bank([3000000,4000000,5000000],3.)
    clocks,values=feature_rows(new,old,'x',4000000,4000000)
    a,b,c=CertifiedBank.listing(new,'x',previous=old,start_us=4000000,end_us=4000000)
    np.testing.assert_array_equal(clocks,a.numpy())
    np.testing.assert_array_equal(values,b.numpy())
    np.testing.assert_array_equal(vector_validity(values),c.numpy())


def test_sparse_market_carries_price_but_never_interval_capacity():
    bars=pl.DataFrame(dict(clock=[1,3],listing=[0,0],volume=[10.,0.],execution_volume=[10.,0.],
        execution_notional=[20.,0.],price_valid=[1,0],extremes_valid=[1,0],close=[2.,0.],high=[2.,0.],low=[2.,0.],trade_count=[1,0]))
    liquid=pl.DataFrame(dict(clock=[1,2,3],listing=[0,0,0],volume=[10.,0.,0.],notional=[20.,0.,0.],
        cumulative_volume=[10.,10.,10.],cumulative_notional=[20.,20.,20.],bid=[1.9,0.,0.],ask=[2.1,0.,0.],quote_us=[1000000,0,0]))
    rows=sparse_market(bars,liquid)
    assert rows['mark'].to_list()==[2.,2.,2.]
    assert rows['observed'].to_list()==[True,False,False]
    assert rows['volume'].to_list()==[10.,0.,0.]
    assert rows['high'].to_list()==[2.,None,None]
    assert rows['quote_us'].to_list()==[1000000]*3
