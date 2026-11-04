from concurrent.futures import ThreadPoolExecutor
import numpy as np
import polars as pl
import pytest
from research.vectorized_backtest.v66.shard_math import scalar_features
from research.vectorized_backtest.v66.shard_data import SessionData


def test_parallel_scalar_feature_parity():
    clocks=np.arange(14401,14601,dtype=np.int64)
    frames=[]
    for ticker in range(4):
        price=10+ticker+np.sin(np.arange(len(clocks))*.1)
        frames.append(pl.DataFrame(dict(time_us=clocks*1000000,open=price,high=price+.1,low=price-.1,close=price,volume=np.full(len(clocks),100.),trade_count=np.ones(len(clocks)),notional=price*100,price_valid=np.array([int(i%7!=0) for i in range(len(clocks))]),extremes_valid=np.array([int(i%11!=0) for i in range(len(clocks))]))))
    def prepare(frame):return scalar_features(frame,clocks,np.arange(0,len(clocks),3),0)
    expected=[prepare(frame) for frame in frames]
    with ThreadPoolExecutor(max_workers=4) as pool:actual=list(pool.map(prepare,frames))
    for a,b in zip(actual,expected):
        for left,right in zip(a,b):
            if isinstance(left,dict):
                for key in left:np.testing.assert_array_equal(left[key],right[key])
            else:np.testing.assert_array_equal(left,right)


def test_bounded_feature_workers():
    with pytest.raises(ValueError,match='bounded workers'):SessionData('unused',feature_workers=5)
