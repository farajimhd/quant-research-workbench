import numpy as np
from research.rl_trading.v6.enriched_features import aggregate,timeframe_history,liquidity
import polars as pl
import torch
from research.rl_trading.v6.enriched_features import quote_features,market_listing


def scalar(prices):
    x=np.zeros((len(prices),37));x[:,:4]=np.log(np.array(prices))[:,None]
    x[:,6:8]=1;x[:,8:10]=np.log1p(10);x[:,35:37]=1
    return x


def test_completed_clock_buckets_and_empty_masks():
    # Prior final bucket, then current sparse observations. 5s endpoint itself
    # remains unavailable for a decision at exactly that endpoint.
    clocks=np.array([57600_000000,86401_000000,86403_000000,86411_000000])
    origins=np.array([0,86400_000000,86400_000000,86400_000000])
    table=aggregate(clocks,scalar([90,100,102,999]),origins,5)
    values,mask=timeframe_history(table,np.array([86405_000000,86406_000000,86416_000000]),0,86400_000000,5)
    assert mask[0,-1] and values[0,-1,3]==0  # Prior final bucket, not the current 5s candle.
    assert mask[1,-1] and not mask[2,-2]  # 04:00:05-10 interval contains no observations.
    changed=aggregate(clocks,scalar([90,100,102,1]),origins,5)
    other,_=timeframe_history(changed,np.array([86406_000000]),0,86400_000000,5)
    assert np.array_equal(values[1],other[0])


def test_liquidity_excludes_target_and_uses_elapsed_seconds():
    clocks=np.array([1,4,10])*1_000_000
    x=scalar([10,11,1000])
    a=liquidity(clocks,x,np.array([10_000000]))
    x[-1,8]=np.log1p(999999)
    b=liquidity(clocks,x,np.array([10_000000]))
    assert np.array_equal(a,b)
    assert a[0,7]==0  # No active seconds in [5,10); source4 is outside.
    assert np.isclose(a[0,11],2/15)


def test_quotes_exclude_uncompleted_bucket_and_expose_staleness():
    frame=pl.DataFrame(dict(bucket_end_us=[900000,1000000],quote_timestamp_us=[850000,950000],
        last_event_us=[880000,980000],bid_int=[100000,200000],ask_int=[100100,300000],
        bid_size=[10.,20.],ask_size=[10.,20.],quote_valid=[1,1],event_count=[1,1]))
    values=quote_features(frame,np.array([1000000,2100000]))
    assert values[0,4]==1 and values[0,0]<20
    assert values[1,4]==0 and values[1,1]>1 and values[1,0]==0


def test_market_returns_ignore_future_and_expose_missing_reference():
    clocks=np.array([1,17,18])*1_000000
    x=scalar([10,11,999])
    a=market_listing(clocks,x,np.array([18_000000]))
    x[-1,:4]=np.log(.01)
    assert np.array_equal(a,market_listing(clocks,x,np.array([18_000000])))
    assert a[0,1]==1 and np.isclose(a[0,0],np.log(1.1)) and a[0,3]==0


def test_enriched_branches_backpropagate_and_mask_empty_history():
    from research.rl_trading.v6.enriched_teacher import EnrichedTeacher
    from research.rl_trading.v6.transformer_search import TransformerSpec
    torch.set_num_threads(2)
    normalizers={n:dict(mean=[0.]*d,std=[1.]*d) for n,d in (('multi',16),('liquidity',30),('market',30))}
    model=EnrichedTeacher(TransformerSpec('patch',32,1,4,2,4),dict(mean=[0.]*147,std=[1.]*147),dict(mean=[0.]*35,std=[1.]*35),normalizers)
    raw=torch.zeros(2,120,147);present=torch.ones(2,120,dtype=torch.bool);held=torch.zeros(2,11)
    fields=dict(multi=torch.randn(2,3,120,16),multi_present=torch.ones(2,3,120,dtype=torch.bool),liquidity=torch.randn(2,30),market=torch.randn(2,30))
    output=model.representation(raw,present,held,**fields);model.heads.entry(output).sum().backward()
    for module in (model.multi_project,model.multi_attention,model.enriched_fuse):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in module.parameters())
    fields['multi_present'].fill_(False)
    a=model.representation(raw,present,held,**fields)
    fields['multi'].fill_(10000)
    assert torch.equal(a,model.representation(raw,present,held,**fields)) and torch.isfinite(a).all()
