import numpy as np
import pytest

from tests.test_native_candidate_feature_scope import source, parent, Source  # noqa: F401
from src.backend.backtest_native_participation_ranking import PreparedParticipationScores
from src.backend.backtest_participation_acquisition_order import prepare_participation_acquisition_order
from src.trading_runtime.participation_acquisition_policy import ParticipationAcquisitionPolicy
from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy


def policy():
    return ParticipationAcquisitionPolicy(
        ParticipationRankingPolicy(((60000, 1.),), .5, .5, 1., 1, 1000),
        'eligible_unranked_last')


def frozen(values, dtype):
    return np.frombuffer(np.asarray(values, dtype=dtype).tobytes(), dtype=dtype)


def scores_for(source):
    available = source.eligible_mask.copy()
    available[0] = False
    values = np.arange(len(available), dtype=np.float64)
    values[~available] = np.nan
    return PreparedParticipationScores(frozen(values, np.float64), frozen(available, bool),
        frozen(source.eligible_mask, bool))


def test_complete_order_keeps_unknown_candidates_and_original_admission(source):
    before = source.eligible_mask.copy()
    scores = scores_for(source)
    plan = prepare_participation_acquisition_order(source, scores, policy=policy())
    keys = source.parent.momentum.keys
    expected = sorted(range(len(keys)), key=lambda i: (
        keys[i][1], not scores.available[i],
        -scores.scores[i] if scores.available[i] else 0, keys[i][0]))
    assert sorted(keys, key=lambda key: plan.rank(*key)) == [keys[i] for i in expected]
    assert len(plan.ranks) == len(keys)
    np.testing.assert_array_equal(source.eligible_mask, before)
    with pytest.raises(TypeError):
        plan.ranks[keys[0]] = 999
    with pytest.raises(ValueError, match='outside the prepared'):
        plan.rank('UNKNOWN', 100)


@pytest.mark.parametrize('mutation', ['mutable', 'omitted', 'mask', 'score'])
def test_unbound_or_corrupted_scores_fail_closed(source, mutation):
    original = scores_for(source)
    values, available, mandatory = original
    if mutation == 'mutable':
        values = values.copy()
    elif mutation == 'omitted':
        values = values[:-1]
    elif mutation == 'mask':
        mandatory = frozen(~mandatory, bool)
    else:
        values = values.copy()
        values[0] = 999  # Unavailable must not become a fabricated ranking.
        values = frozen(values, np.float64)
    with pytest.raises(ValueError):
        prepare_participation_acquisition_order(source,
            PreparedParticipationScores(values, available, mandatory), policy=policy())
