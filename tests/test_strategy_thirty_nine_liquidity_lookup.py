"""Version-bound native lookup checks; no numbered executor admission."""
import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
from src.backend.backtest_strategy_liquidity_fade_loader import load_compiled_liquidity_fade_lookup
from test_liquidity_fade_columnar_lookup import fixture
from test_liquidity_fade_lookup_loader import Reader


def supplied_frame():
    witness,frame,args=fixture()
    return witness,frame.with_columns(pl.Series('trade_count',[100,100,60,40],dtype=pl.UInt64)),args


def test_additional_lookup_retains_native_values_and_older_numbers_keep_quarter_rate():
    witness,frame,args=supplied_frame()
    for number in (35,36,37,38):
        cache=CompiledLiquidityFadeLookup(frame,**args,strategy_number=number)
        assert cache.window_at('PLUG',witness.boundary_ms) is None
    cache=CompiledLiquidityFadeLookup(frame.reverse(),**args,strategy_number=39)
    window=cache.window_at('PLUG',witness.boundary_ms)
    assert window.boundary_ms==witness.completed_five_second_boundary_ms
    assert tuple(x.trade_count for x in window.candles)==(100,100,60,40)
    assert cache.market_plan_token==args['plan'].token and cache.strategy_number==39
    assert cache.window_at('PLUG',window.boundary_ms-100) is None
    assert cache.window_at('PLUG',window.boundary_ms+5000) is None


def test_missing_native_rows_and_later_nonfade_rows_do_not_carry_old_signal():
    witness,frame,args=supplied_frame()
    missing=frame.with_columns(pl.Series('trade_count',[100,None,60,40],dtype=pl.UInt64))
    assert CompiledLiquidityFadeLookup(missing,**args,strategy_number=39).window_at('PLUG',witness.boundary_ms) is None
    later=frame.tail(1).with_columns((pl.col('boundary_ms')+5000).alias('boundary_ms'),pl.lit(1000,dtype=pl.UInt64).alias('trade_count'))
    cache=CompiledLiquidityFadeLookup(pl.concat([frame,later]),**args,strategy_number=39)
    assert cache.window_at('PLUG',witness.boundary_ms) is not None
    assert cache.window_at('PLUG',witness.completed_five_second_boundary_ms+5000) is None


def test_one_read_compiles_half_rate_lookup_with_no_decision_time_queries():
    witness,frame,args=supplied_frame()
    reader=Reader(frame.with_columns(pl.col('session_date').str.to_date()).to_arrow().to_batches(max_chunksize=2))
    cache=load_compiled_liquidity_fade_lookup(reader,**args,strategy_number=39)
    assert len(reader.queries)==1 and reader.stream.closed
    for boundary in range(witness.completed_five_second_boundary_ms,witness.completed_five_second_boundary_ms+5000,100):
        assert cache.window_at('PLUG',boundary) is not None
    assert len(reader.queries)==1
    assert 'resolution_ms=5000' in reader.queries[0] and 'FROM arte.bars_v1' in reader.queries[0]


@pytest.mark.parametrize('number',[True,34,40,'39'])
def test_invalid_version_rejects_before_market_query(number):
    _,frame,args=supplied_frame();reader=Reader(())
    with pytest.raises(ValueError):load_compiled_liquidity_fade_lookup(reader,**args,strategy_number=number)
    assert not reader.queries
    with pytest.raises(ValueError):CompiledLiquidityFadeLookup(frame,**args,strategy_number=number)
