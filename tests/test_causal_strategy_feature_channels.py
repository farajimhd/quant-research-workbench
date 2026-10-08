"""Research feature causality; no native strategy or profit claim."""
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from research.causal_strategy_features.v1.features import completed_channels


def observations():
    return pl.DataFrame({
        'ticker': ['X']*40, 'minute': list(range(40)),
        'decision_minute': list(range(1,41)),
        'close_int': list(range(100,140)), 'high_int': list(range(102,142)),
        'low_int': list(range(98,138)), 'execution_volume': [10.]*40,
        'execution_notional': [1000.]*40, 'trade_count': [5]*40,
        'valid_rows': [2]*40, 'bucket_rows': [2]*40,
    })


@pytest.mark.parametrize('timeframe', [1,5,15])
def test_prefix_invariance_and_future_label_isolation(timeframe):
    source = observations()
    prefix = source.filter(pl.col('decision_minute')<=17)
    expected = completed_channels(prefix,timeframe_minutes=timeframe)
    changed = source.with_columns(*[
        pl.when(pl.col('decision_minute')>17).then(pl.col(name)*3)
        .otherwise(pl.col(name)).alias(name)
        for name in ('close_int','high_int','low_int','execution_volume','execution_notional','trade_count')
    ], pl.lit(999999.).alias('future_profit_label'))
    actual = completed_channels(changed,timeframe_minutes=timeframe).filter(pl.col('available_minute')<=17)
    assert_frame_equal(actual,expected)


def test_current_volume_not_in_trailing_denominator():
    source = observations().with_columns(
        pl.when(pl.col('minute')==20).then(1000.).otherwise(pl.col('execution_volume')).alias('execution_volume'))
    actual = completed_channels(source,timeframe_minutes=1,lookback_bars=3)
    assert actual.filter(pl.col('bar_id')==20)['relative_volume'][0] == 100.


def test_gap_does_not_create_candle_or_consecutive_return():
    source = observations().filter(pl.col('minute')!=13)
    actual = completed_channels(source,timeframe_minutes=5)
    assert actual.filter(pl.col('available_minute')==15).is_empty()
    assert actual.filter(pl.col('available_minute')==20)['return'][0] is None


def test_duplicate_source_rejected():
    source = observations()
    with pytest.raises(ValueError,match='Duplicate'):
        completed_channels(pl.concat([source,source.head(1)]),timeframe_minutes=1)


@pytest.mark.parametrize('parameter,value', [('timeframe_minutes',True),('timeframe_minutes',0),
                                           ('lookback_bars',True),('lookback_bars',0)])
def test_parameter_contract(parameter,value):
    arguments = {'timeframe_minutes':1,'lookback_bars':5,parameter:value}
    with pytest.raises(ValueError): completed_channels(observations(),**arguments)

