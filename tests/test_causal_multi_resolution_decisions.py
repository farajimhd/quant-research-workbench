import polars as pl
import pytest
from polars.testing import assert_frame_equal

from research.causal_strategy_features.v4.native_bars import native_channels
from research.causal_strategy_features.v5.decisions import multi_resolution_decisions
from tests.test_causal_native_volatility_channels import source


def inputs():
    raw = pl.concat([source(), source().with_columns(
        pl.lit(300000, dtype=source().schema['resolution_ms']).alias('resolution_ms'))])
    features = native_channels(raw, resolutions_ms=(60000, 300000),
                               through_day_boundary_ms=3000000, lookback_bars=3,
                               volatility_lookback_bars=3)
    identity = ['build_id', 'session_date', 'ticker', 'attempt_id']
    decisions = raw.select(identity).unique().join(
        pl.DataFrame({'decision_day_ms': [600000, 359900, 300000]}), how='cross')
    return features, decisions


def run(features, decisions, freshness=((60000, 59999), (300000, 299999)), interval=100):
    return multi_resolution_decisions(features, decisions, decision_interval_ms=interval,
                                      freshness_by_resolution=freshness)


def test_one_clock_preserves_order_and_independent_completed_resolutions():
    features, decisions = inputs()
    out = run(features, decisions)
    assert out['decision_day_ms'].to_list() == [600000, 359900, 300000]
    assert out['channels_60000ms'].struct.field('available_day_boundary_ms').to_list() == [600000, 300000, 300000]
    assert out['channels_300000ms'].struct.field('available_day_boundary_ms').to_list() == [600000, 300000, 300000]
    assert out.height == decisions.height


def test_per_resolution_freshness_and_missing_attempt_fail_closed():
    features, decisions = inputs()
    out = run(features, decisions, freshness=((60000, 0), (300000, 299999)))
    assert out['channels_60000ms'].struct.field('feature_row_available').to_list() == [True, False, True]
    assert out['channels_60000ms'].struct.field('close_int').to_list()[1] is None
    assert out['channels_300000ms'].struct.field('feature_row_available').to_list() == [True]*3
    foreign = decisions.with_columns(pl.lit('other-attempt').alias('attempt_id'))
    for r in (60000, 300000):
        assert run(features, foreign)[f'channels_{r}ms'].struct.field('feature_row_available').sum() == 0


def test_future_values_and_reward_columns_cannot_enter_channels():
    features, decisions = inputs()
    decisions = decisions.filter(pl.col('decision_day_ms') <= 359900)
    changed = features.with_columns(
        pl.when(pl.col('available_day_boundary_ms') > 359900).then(999999)
          .otherwise(pl.col('close_int')).alias('close_int'),
        pl.lit(999999).alias('future_reward'))
    actual = run(changed, decisions.with_columns(pl.lit(999).alias('future_label')))
    assert_frame_equal(actual, run(features, decisions))
    assert 'future_label' not in actual.columns
    assert 'future_reward' not in actual['channels_60000ms'].struct.fields


@pytest.mark.parametrize('freshness', [(), ((60000, True),), ((True, 0),),
    ((300000, 0), (60000, 0)), ((60000, 0), (60000, 1))])
def test_invalid_parameter_declarations_are_rejected(freshness):
    features, decisions = inputs()
    with pytest.raises(ValueError):
        run(features, decisions, freshness=freshness)


def test_undeclared_resolution_duplicate_decisions_and_unaligned_clock_rejected():
    features, decisions = inputs()
    with pytest.raises(ValueError, match='undeclared'):
        run(features, decisions, freshness=((60000, 0),))
    with pytest.raises(ValueError, match='duplicate'):
        run(features, pl.concat([decisions, decisions]))
    with pytest.raises(ValueError, match='clock'):
        run(features, decisions, interval=60000)


def test_missing_resolution_retains_all_decisions_and_null_channels():
    features, decisions = inputs()
    out = run(features.filter(pl.col('resolution_ms') == 60000), decisions)
    assert out.height == decisions.height
    assert out['channels_300000ms'].struct.field('feature_row_available').sum() == 0
    assert out['channels_300000ms'].struct.field('close_return').null_count() == out.height
    empty = run(features.head(0), decisions)
    assert empty['channels_60000ms'].struct.field('feature_row_available').sum() == 0
    assert run(features, decisions.head(0)).height == 0


def test_matrix_channels_equal_individual_causal_adapters():
    from research.causal_strategy_features.v4.decisions import decision_channels
    from research.causal_strategy_features.v5.decisions import CHANNEL_COLUMNS
    features, decisions = inputs()
    out = run(features, decisions)
    for r, age in ((60000, 59999), (300000, 299999)):
        expected = decision_channels(features.filter(pl.col('resolution_ms') == r),
            decisions.with_columns(pl.lit(r, dtype=features.schema['resolution_ms']).alias('resolution_ms')),
            decision_interval_ms=100, max_age_ms=age).select(CHANNEL_COLUMNS)
        assert_frame_equal(out.select(f'channels_{r}ms').unnest(f'channels_{r}ms'), expected)
