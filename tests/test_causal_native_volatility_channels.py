import polars as pl
import pytest
from polars.testing import assert_frame_equal

from research.causal_strategy_features.v4.native_bars import native_channels


def source():
    close = [100, 102, 101, 104, 103, 107, 105, 110, 108, 112]
    return pl.DataFrame(dict(build_id=['build']*10, session_date=['2026-08-04']*10,
        ticker=['X']*10, attempt_id=['source']*10, resolution_ms=[60000]*10,
        bucket_index=list(range(10)), open_int=[c-1 for c in close],
        high_int=[c+2 for c in close], low_int=[c-2 for c in close], close_int=close,
        execution_volume=[10.]*10, execution_notional=[1000.]*10,
        trade_count=list(range(10,20)), price_valid=[1]*10, extremes_valid=[1]*10))


def project(raw, boundary=600000):
    return native_channels(raw, resolutions_ms=(60000,), through_day_boundary_ms=boundary,
                           lookback_bars=3, volatility_lookback_bars=3)


def test_future_prices_participation_and_labels_do_not_change_prefix():
    raw=source()
    changed=raw.with_columns(*[pl.when(pl.col('bucket_index')>=5).then(pl.col(c)*10)
        .otherwise(pl.col(c)).alias(c) for c in ('open_int','high_int','low_int','close_int',
                                               'execution_volume','trade_count')],
        pl.lit(999).alias('future_reward'))
    assert_frame_equal(project(raw,300000),project(changed,300000))


def test_current_return_and_trades_excluded_from_own_baseline():
    raw=source(); before=project(raw).filter(pl.col('bucket_index')==7)
    changed=raw.with_columns(*[pl.when(pl.col('bucket_index')==7).then(pl.col(c)*2)
        .otherwise(pl.col(c)).alias(c) for c in ('open_int','high_int','low_int','close_int','trade_count')])
    after=project(changed).filter(pl.col('bucket_index')==7)
    assert before['prior_return_volatility'][0]==after['prior_return_volatility'][0]
    assert before['prior_trade_count_mean'][0]==after['prior_trade_count_mean'][0]
    assert after['relative_trade_count'][0]==before['relative_trade_count'][0]*2
    assert before['close_volatility_units'][0]!=after['close_volatility_units'][0]


def test_gap_and_new_attempt_have_no_shared_volatility():
    raw=source().filter(pl.col('bucket_index')!=4).with_columns(
        pl.when(pl.col('bucket_index')>=8).then(pl.lit('new')).otherwise(pl.col('attempt_id')).alias('attempt_id'))
    out=project(raw)
    assert out.height==9
    assert out.filter(pl.col('bucket_index').is_in([5,6,7,8,9]))['close_volatility_units'].null_count()==5


def test_flat_baseline_and_invalid_current_candle_stay_missing():
    flat=source().with_columns(pl.lit(100).alias('close_int'),pl.lit(100).alias('open_int'),
                              pl.lit(102).alias('high_int'),pl.lit(98).alias('low_int'))
    assert project(flat)['close_volatility_units'].null_count()==10
    invalid=project(source().with_columns(pl.when(pl.col('bucket_index')==7).then(0)
        .otherwise(pl.col('price_valid')).alias('price_valid'))).filter(pl.col('bucket_index')==7)
    assert invalid['close_volatility_units'][0] is None
    assert invalid['body_volatility_units'][0] is None


def test_baseline_parameter_rejects_bool_and_single_bar():
    for n in (True,1,0):
        with pytest.raises(ValueError,match='at least two'):
            native_channels(source(),resolutions_ms=(60000,),through_day_boundary_ms=600000,
                            volatility_lookback_bars=n)
