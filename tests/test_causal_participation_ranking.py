from dataclasses import replace
from math import log1p

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY
from research.causal_strategy_features.v7.ranking import (
    ParticipationRankingPolicy, opportunity_scores,
)


def policy():
    return ParticipationRankingPolicy(((30000, 1.), (60000, 2.)), .5, .5, 1., 2, 100)


def packet():
    channel = dict(feature_row_available=True, candle_available=True,
                   available_day_boundary_ms=60000, relative_execution_volume=3.,
                   relative_trade_count=3., high_return=.1, low_return=0.,
                   upper_wick_return=.02)
    values = {c: ['scope'] for c in DECISION_IDENTITY}
    values.update(decision_day_ms=[60000], channels_30000ms=[channel],
                  channels_60000ms=[channel])
    return pl.DataFrame(values)


def test_explicit_formula_and_future_labels_are_projected_away():
    expected = opportunity_scores(packet(), policy=policy())
    assert expected['opportunity_score'][0] == pytest.approx(log1p(3)-.2)
    assert expected['known_resolution_count'][0] == 2
    assert_frame_equal(expected, opportunity_scores(
        packet().with_columns(pl.lit(999.).alias('future_profit')), policy=policy()))


def test_missing_resolution_requires_declared_partial_coverage():
    frame = packet().with_columns(pl.col('channels_30000ms').struct.with_fields(
        pl.lit(False).alias('feature_row_available')))
    strict = opportunity_scores(frame, policy=policy())
    assert strict['known_resolution_count'][0] == 1
    assert strict['opportunity_score'][0] is None
    partial = opportunity_scores(frame, policy=replace(policy(), minimum_known_resolutions=1))
    assert partial['score_available'][0]


def test_future_channel_clock_rejected():
    frame = packet().with_columns(pl.col('channels_30000ms').struct.with_fields(
        pl.lit(60001).alias('available_day_boundary_ms')))
    with pytest.raises(ValueError, match='future'):
        opportunity_scores(frame, policy=policy())


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1.])
def test_invalid_participation_is_unavailable(value):
    frame = packet().with_columns(pl.col('channels_30000ms').struct.with_fields(
        pl.lit(value).alias('relative_execution_volume')))
    result = opportunity_scores(frame, policy=policy())
    assert result['score_30000ms'][0] is None
    assert not result['score_available'][0]


@pytest.mark.parametrize('change', [dict(volume_weight=True), dict(wick_penalty=-1.),
    dict(resolution_weights=((60000, 1.), (30000, 1.))), dict(minimum_known_resolutions=0)])
def test_invalid_declarations_rejected(change):
    with pytest.raises(ValueError):
        replace(policy(), **change)


def test_duplicate_identity_and_declared_bound_rejected():
    with pytest.raises(ValueError, match='duplicate'):
        opportunity_scores(pl.concat([packet(), packet()]), policy=policy())
    with pytest.raises(ValueError, match='bound'):
        opportunity_scores(pl.concat([packet(), packet()]), policy=replace(policy(), max_rows=1))
