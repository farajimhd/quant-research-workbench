import json
from types import SimpleNamespace
import numpy as np
import polars as pl
import pytest


def test_catalog_excludes_fundamentals_and_preserves_legacy_ids():
    from research.vectorized_backtest.v64.features import CATALOG as old
    from research.vectorized_backtest.v65.features import PRE_SHARDS, SHARDS
    from research.vectorized_backtest.v65.authority import FEATURES, ALLOWED, GROUPS
    from research.vectorized_backtest.v65.shard_features import EXCLUDED
    assert [f.name for f in PRE_SHARDS]==[f.name for f in old]
    assert len(SHARDS)==1812
    assert not EXCLUDED & {FEATURES[i].name for i in ALLOWED}
    assert {i for group in GROUPS for i in group}==set(ALLOWED)
    assert not any(FEATURES[i].name.startswith('v7.') for i in ALLOWED)


def test_window_encoding_preserves_masks_and_relative_prices():
    from research.vectorized_backtest.v65.shard_features import encode
    values=np.array([[[10.,20.]],[[10.,-1.]],[[10.,20.]]])
    result=encode(values,np.ones_like(values,bool),('close','volume'),np.array([5.,5.,0.]))
    np.testing.assert_allclose(result[0],[1.,np.log1p(20)])
    assert np.isnan(result[1,1]) and np.isnan(result[2,0])
    assert np.isfinite(result[2,1])


def test_execution_page_straddling_cutoff_has_no_overlap(tmp_path):
    from research.market_shards.v1.contract import origin_us,sha
    from research.vectorized_backtest.v65.order_tail import Tail
    day='2026-01-05';origin=origin_us(day);cutoff=origin+34200_000000
    folder=tmp_path/'bars'/day;folder.mkdir(parents=True)
    frame=pl.DataFrame(dict(ticker=['AAA']*2,time_us=[cutoff,cutoff+1000000],
        mark=[10.,11.],bid=[9.,10.],ask=[10.,11.],quote_us=[cutoff,cutoff+1000000],
        bid_size=[20.,20.],ask_size=[30.,30.],book_valid=[True,True]))
    files={}
    for begin in range(14400,72000,3600):
        path=folder/f'execution-{begin}.parquet'
        (frame if begin==32400 else frame.head(0)).write_parquet(path);files[path.name]=sha(path)
    (folder/'complete.json').write_text(json.dumps(dict(files=files)))
    item=SimpleNamespace(shard_session=SimpleNamespace(root=tmp_path,day=day),
        arrays={'clocks':np.array([cutoff//1000000])},tickers=['AAA'])
    decision=Tail(folder,item,decision=True)
    page=decision.load(len(decision.receipt['pages'])-1)
    assert page['clocks'][-1]*1000000==cutoff
    assert page['ask'][-1,0]==10.
    item.execution_quote_seed=decision.last
    tail=Tail(folder,item);page=tail.load(0)
    assert page['clocks'][0]*1000000==cutoff+1000000
    assert page['ask'][0,0]==11.


def test_scalar_features_are_future_invariant():
    from research.vectorized_backtest.v65.shard_math import scalar_features
    clocks=np.arange(14401,14501,dtype=np.int64)
    close=10.+np.arange(100)*.01
    def frame(close):
        return pl.DataFrame(dict(time_us=clocks*1000000,open=close,high=close+.1,
            low=close-.1,close=close,volume=np.ones(100),trade_count=np.ones(100),
            notional=close,price_valid=np.ones(100,dtype=np.uint8),extremes_valid=np.ones(100,dtype=np.uint8)))
    before=scalar_features(frame(close),clocks,np.arange(50),0)
    changed=close.copy();changed[50:]*=10
    after=scalar_features(frame(changed),clocks,np.arange(50),0)
    for i in range(3):np.testing.assert_equal(before[i],after[i])


def test_shard_resident_gather_preserves_column_order_and_absence():
    import torch
    from research.vectorized_backtest.v65.resident_features import ResidentFeatures
    from research.vectorized_backtest.v65.features import PRE_SHARDS
    bank=object.__new__(ResidentFeatures);bank.device=torch.device('cpu');bank.column_plans={}
    bank.tensors={'shards':torch.tensor([[1.,float('nan')],[3.,4.]])}
    rows=torch.tensor([[1,-1,0]]);columns=[len(PRE_SHARDS)+1,len(PRE_SHARDS)]
    values,valid=bank._values(rows,rows,torch.zeros_like(rows),columns)
    assert values.tolist()==[[[4.,3.],[0.,0.],[0.,1.]]]
    assert valid.tolist()==[[[True,True],[False,False],[False,True]]]


@pytest.mark.parametrize('size',[1,10,300,19800])
def test_linear_swing_history_is_exactly_equal_to_v64(size):
    from research.vectorized_backtest.v64.certified_history_bank import calculate as old
    from research.vectorized_backtest.v65.certified_history_bank import calculate as new
    rng=np.random.default_rng(71)
    close=np.round(10+rng.normal(0,.1,size),2)
    observed=rng.random(size)>.02
    high=close+.1;low=close-.1;notional=rng.integers(0,10000,size).astype(float)
    low[rng.random(size)<.01]=np.nan
    np.testing.assert_equal(old(close,high,low,observed,notional),new(close,high,low,observed,notional))


def test_prefetch_reuses_pageable_session_without_rebuilding(monkeypatch,tmp_path):
    from research.vectorized_backtest.v65 import session_lanes
    worker=object.__new__(session_lanes.Worker)
    item=object();worker.host={'2026-01-05':item};worker.config={'output':str(tmp_path)}
    worker.read_host=lambda day:pytest.fail('Cached session was rebuilt')
    monkeypatch.setattr(session_lanes,'prepare_next',lambda data,path,config:data)
    assert worker.read_ahead('2026-01-05') is item
