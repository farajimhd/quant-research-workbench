"""Shared producer parity against the versioned search's independent callers."""
from dataclasses import replace
from datetime import date
import numpy as np
import polars as pl
import pytest

from src.market_engine import columnar_history_product as product
from src.market_engine import qmd_feature_catalog as shared
from src.market_engine import causal_context_features as math
from src.market_engine.causal_market_context import prepare_market_context
from src.market_engine.qmd_feature_projection import project_listing
from src.market_engine.candle_feature_projection import FUNDAMENTAL_NAMES
from src.market_engine.feature_source_plan import FeatureDependency, FeatureSourcePlan, TABLES
from research.vectorized_backtest.v18 import features as oracle
from research.vectorized_backtest.v18 import enriched_math as oracle_math


def candles(n=400):
    ix=np.arange(n,dtype=np.int64)
    close=100000+ix*3+(ix%7)*5
    return pl.DataFrame(dict(resolution_ms=np.full(n,1000),bucket_index=14400+ix,
        open_int=close-1,high_int=close+10,low_int=close-10,close_int=close,
        notional=np.full(n,100.),volume=np.full(n,10.),trade_count=np.full(n,2),
        price_valid=np.ones(n,dtype=np.uint8),extremes_valid=np.ones(n,dtype=np.uint8)))


def scope(n=400):
    fields=product.MARKET_FIELDS
    revision=product.product_revision(fields,'a'*64,volume_window_seconds=30,feature_plan_hash='b'*64)
    return product.HistoryScope('build',date(2026,7,30),'listing','ABC',
        '00000000-0000-0000-0000-000000000001',revision,14400,14400+n)


def peer(bars,s):
    origin=product.session_clock_anchor_us(s)//1000000
    return bars.select(pl.Series('second',origin+bars['bucket_index'].to_numpy()+1),
        (pl.col('close_int')/10000.).alias('close'),'notional','price_valid')


def test_complete_catalog_preserves_all_957_ids_units_bounds_and_masks():
    assert [vars(f) for f in shared.CATALOG]==[vars(f) for f in oracle.CATALOG]
    assert len(shared.CATALOG)==957
    assert product.CATALOG==tuple(f for f in product.MARKET_FIELDS if 149<=f.field_id<753)
    assert all(product.BY_NAME[f.name]==f for f in product.CATALOG)
    with pytest.raises(ValueError,match='source plan'):
        product.product_revision((product.BY_NAME['enriched.return.5s'],),'a'*64,volume_window_seconds=30)
    from src.trading_runtime.columnar_rule_program import InputField
    for f in shared.CATALOG:
        InputField(f.name,f.unit,'q_live.qmd_causal_history_value_v1','a'*64,1000)
    for name in ('enriched.position_age','enriched.fill_price','portfolio.unrealized_pnl'):
        with pytest.raises(ValueError,match='execution-derived'):
            InputField(name,'ratio','q_live.qmd_causal_history_value_v1','a'*64,1000)


def test_own_features_preserve_exact_float32_math_and_future_prefix():
    bars=candles();s=scope();p=peer(bars,s)
    f=bars.select(p['second'],*(pl.col(n+'_int').truediv(10000.).alias(n) for n in ('open','high','low','close')),
        'volume','trade_count','price_valid','extremes_valid')
    targets=p['second'].to_numpy();origin=int(targets[0])-1
    actual=math.own(f,targets,origin)
    expected=oracle_math.own(f,targets,origin)
    np.testing.assert_array_equal(actual.view(np.uint32),expected.view(np.uint32))
    np.testing.assert_array_equal(math.own(f.head(200),targets[:200],origin),actual[:200])
    assert np.isnan(actual[0,0])  # No manufactured early return history.


def test_market_context_uses_full_census_ties_missing_and_causal_prefix():
    bars=candles();s=scope();p=peer(bars,s);first=int(p['second'][0]);stop=int(p['second'][-1])+1
    frames={'ABC':p,'XYZ':p}
    actual=prepare_market_context(frames,universe=('ABC','MISSING','XYZ'),unavailable=('MISSING',),
        first_second=first,stop_second=stop,maximum_bytes=10000000)
    ret=oracle_math.returns(p['second'].to_numpy(),p['close'].to_numpy(),np.ones(len(p),bool),p['second'].to_numpy(),15)
    common,pct=oracle_math.market_summary(np.stack((ret,np.full(len(ret),np.nan),ret)),3)
    np.testing.assert_array_equal(actual['ABC'].select(math.CONTEXT_NAMES[:5]).to_numpy(),common)
    np.testing.assert_array_equal(actual['ABC'][math.CONTEXT_NAMES[6]].to_numpy(),pct[0])
    assert actual['ABC']['context.15s.coverage'][-1]==np.float32(2/3)
    assert actual['ABC']['context.15s.return_percentile'][-1]==.5
    prefix=prepare_market_context({k:v.head(200) for k,v in frames.items()},
        universe=('ABC','MISSING','XYZ'),unavailable=('MISSING',),first_second=first,
        stop_second=first+200,maximum_bytes=10000000)
    assert actual['ABC'].head(200).equals(prefix['ABC'])
    with pytest.raises(MemoryError):
        prepare_market_context(frames,universe=('ABC','XYZ'),unavailable=(),
            first_second=first,stop_second=stop,maximum_bytes=1)
    with pytest.raises(ValueError,match='census'):
        prepare_market_context(frames,universe=('ABC',),unavailable=(),
            first_second=first,stop_second=stop,maximum_bytes=10000000)


def project(n=400):
    bars=candles(n);s=scope(n);p=peer(bars,s)
    activity=pl.DataFrame({'second':p['second'],'volume':np.full(n,7.),'notional':np.full(n,70.),'trade_count':np.full(n,2.)})
    liquidity=pl.DataFrame({'bucket_index':bars['bucket_index'],'execution_notional':np.full(n,70.)})
    dense=product.prepare_dense_inputs(s,bars,liquidity)
    contexts=prepare_market_context({'ABC':p,'XYZ':p},universe=('ABC','XYZ'),unavailable=(),
        first_second=int(p['second'][0]),stop_second=int(p['second'][-1])+1,maximum_bytes=10000000)
    indicators=bars.select('bucket_index').with_columns(*(pl.lit(1.).alias(v) for v in
        ('macd_line','macd_signal','rsi_14','atr_14','ema_7','ema_26')))
    quotes=pl.DataFrame({'second':p['second'],'bid':np.full(n,9.99),'ask':np.full(n,10.01),
        'quote_us':p['second'].to_numpy()*1000000-1})
    prior=pl.DataFrame(schema={'bucket_index':pl.Int64,'volume':pl.Float64})
    out=project_listing(s,dense,bars=bars,indicators=indicators,prior_bars=prior,
        seed=None,splits=[],fundamentals={k:0. for k in FUNDAMENTAL_NAMES},quotes=quotes,
        activity=activity,continuous_history=((s.session_date,bars,1.),),context=contexts['ABC'],
        opening_splits=[],prior_volume_day=date(2026,7,29))
    return s,bars,liquidity,out


def test_all_fields_flow_through_existing_normalized_product_and_windows():
    s,bars,liquidity,frame=project()
    prepared=product.prepare_product(s,bars,liquidity,product.MARKET_FIELDS,
        volume_window_seconds=30,producer_code_hash='a'*64,feature_frame=frame,feature_plan_hash='b'*64)
    view=product.expand_values(prepared,first_ordinal=250,stop_ordinal=260,evaluation_block='complete-origin')
    assert all(name in view.columns for name in shared.BY_NAME)
    for name in ('enriched.return.5s','indicator.rsi.14','log_close','market.bid_relative'):
        np.testing.assert_array_equal(view[name].to_numpy(),frame[name].to_numpy()[250:260])
        assert view[name+'__valid'].all()
    assert not view['v7.below.1.center_distance_rel__valid'].any()
    assert not view['log_float_shares__valid'].any()
    shorter=project(260)[3]
    for name in shared.BY_NAME:
        np.testing.assert_array_equal(frame[name].to_numpy()[:260],shorter[name].to_numpy())
    with pytest.raises(ValueError,match='frame'):
        product.prepare_product(s,bars,liquidity,product.MARKET_FIELDS,volume_window_seconds=30,
            producer_code_hash='a'*64,feature_frame=frame.tail(10),feature_plan_hash='b'*64)


def test_feature_plan_is_scalar_shared_and_changes_product_identity():
    dep=FeatureDependency('bars','arte','bars_v1',date(2026,7,30),'ABC','attempt',3,'c'*64)
    plan=FeatureSourcePlan('d'*64,'e'*64,date(2026,7,30),14400,14800,2,(dep,))
    changed=replace(plan,dependencies=(replace(dep,content_hash='f'*64),))
    assert changed.plan_hash!=plan.plan_hash
    for table in TABLES:
        assert table.database=='q_live'
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert not any(v in table.ddl().lower() for v in ('json','blob','payload','array('))
    assert product.product_revision(product.MARKET_FIELDS,'a'*64,volume_window_seconds=30,
        feature_plan_hash=plan.plan_hash)!=product.product_revision(product.MARKET_FIELDS,'a'*64,
        volume_window_seconds=30,feature_plan_hash=changed.plan_hash)
