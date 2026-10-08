import polars as pl
import pytest
from polars.testing import assert_frame_equal

from research.causal_strategy_features.v3.native_bars import IDENTITY
from research.causal_strategy_features.v4.decisions import decision_channels
from research.causal_strategy_features.v4.native_bars import native_channels
from tests.test_causal_native_volatility_channels import source


def channels():
    return native_channels(source(),resolutions_ms=(60000,),through_day_boundary_ms=600000,
                           lookback_bars=3,volatility_lookback_bars=3)


def clocks(times):
    return source().select(IDENTITY).unique().join(pl.DataFrame({'decision_day_ms':times}),how='cross')


def align(features,decisions,age=59999,interval=100):
    return decision_channels(features,decisions,decision_interval_ms=interval,max_age_ms=age)


def test_forming_future_bar_and_label_mutation_cannot_change_decision():
    decisions=clocks([359900,300000,300100]).with_columns(pl.lit(999).alias('future_reward'))
    before=channels()
    changed=before.with_columns(pl.when(pl.col('available_day_boundary_ms')>=360000)
        .then(999).otherwise(pl.col('close_return')).alias('close_return'),
        pl.lit(999).alias('future_gain'))
    actual=align(changed,decisions)
    assert_frame_equal(actual,align(before,decisions))
    assert actual['available_day_boundary_ms'].to_list()==[300000]*3
    assert actual['feature_age_ms'].to_list()==[59900,0,100]
    assert 'future_gain' not in actual.columns and 'future_reward' not in actual.columns


def test_missing_stale_and_foreign_attempt_do_not_borrow_features():
    decisions=clocks([100,60000,120000,180000])
    features=channels().filter(pl.col('bucket_index')!=1)
    out=align(features,decisions,age=59999)
    assert out['feature_row_available'].to_list()==[False,True,False,True]
    foreign=decisions.with_columns(pl.lit('other').alias('attempt_id'))
    assert align(features,foreign)['feature_row_available'].sum()==0


def test_decision_interval_parameter_does_not_change_source_availability():
    decision=clocks([300000])
    assert_frame_equal(align(channels(),decision,interval=100),
                       align(channels(),decision,interval=60000))
    with pytest.raises(ValueError,match='clock'):
        align(channels(),clocks([300100]),interval=60000)


def test_duplicate_and_false_availability_fail_closed():
    with pytest.raises(ValueError,match='Duplicate'):
        align(channels(),clocks([300000,300000]))
    bad=channels().with_columns((pl.col('available_day_boundary_ms')-1).alias('available_day_boundary_ms'))
    with pytest.raises(ValueError,match='clock'):
        align(bad,clocks([300000]))
