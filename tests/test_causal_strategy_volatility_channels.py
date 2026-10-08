import polars as pl
import pytest
from polars.testing import assert_frame_equal
from research.causal_strategy_features.v2.features import completed_channels
from tests.test_causal_strategy_feature_channels import observations


@pytest.mark.parametrize('timeframe', [1,5,15])
def test_future_prices_do_not_change_past_volatility_channels(timeframe):
    frame = observations()
    expected = completed_channels(frame.filter(pl.col('decision_minute')<=17),
                                  timeframe_minutes=timeframe, lookback_bars=2)
    changed = frame.with_columns(*[
        pl.when(pl.col('decision_minute')>17).then(pl.col(name)*10)
          .otherwise(pl.col(name)).alias(name)
        for name in ('close_int','high_int','low_int')
    ])
    actual = completed_channels(changed,timeframe_minutes=timeframe,lookback_bars=2)
    assert_frame_equal(expected,actual.filter(pl.col('available_minute')<=17))


def test_current_return_excluded_from_volatility_baseline():
    frame = observations()
    normal = completed_channels(frame,timeframe_minutes=1,lookback_bars=3)
    changed = frame.with_columns(*[
        pl.when(pl.col('minute')==20).then(pl.col(name)*2)
          .otherwise(pl.col(name)).alias(name)
        for name in ('close_int','high_int','low_int')
    ])
    shock = completed_channels(changed,timeframe_minutes=1,lookback_bars=3)
    assert normal.filter(pl.col('bar_id')==20)['prior_return_volatility'][0] == shock.filter(pl.col('bar_id')==20)['prior_return_volatility'][0]
    assert normal.filter(pl.col('bar_id')==20)['return_volatility_units'][0] != shock.filter(pl.col('bar_id')==20)['return_volatility_units'][0]


def test_zero_volatility_and_gaps_remain_unavailable():
    flat = observations().with_columns(pl.lit(100).alias('close_int'),
                                      pl.lit(102).alias('high_int'),pl.lit(98).alias('low_int'))
    result = completed_channels(flat,timeframe_minutes=1)
    assert result['return_volatility_units'].null_count() == result.height
    result = completed_channels(observations().filter(pl.col('minute')!=18),
                                timeframe_minutes=1,lookback_bars=3)
    assert result.filter(pl.col('bar_id')==20)['return_volatility_units'][0] is None
