import numpy as np
import pytest

from tests.test_backtest_strategy_first_price_source import authority, Bars
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
from src.backend.backtest_native_price_candidate_features import native_price_candidate_feature_decisions
from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
from src.backend.backtest_native_participation_ranking import PreparedParticipationScores
from src.backend.backtest_participation_acquisition_order import prepare_participation_acquisition_order
from src.trading_runtime.participation_acquisition_policy import ParticipationAcquisitionPolicy
from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy


def native_plan():
    market, parent = authority()
    return compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))


def test_full_native_population_preserves_source_keys_attempts_and_admission():
    plan = native_plan()
    before = plan.eligible_mask.copy()
    rows = native_price_candidate_feature_decisions(plan, max_rows=1000)
    assert list(zip(rows['ticker'], rows['decision_day_ms'])) == [
        (ticker, clock + SESSION_OPEN_OFFSET_MS) for ticker, clock in plan.momentum.keys]
    assert rows['attempt_id'].to_list() == list(plan.source.source_attempts)
    assert rows.height == len(plan.eligible_mask)
    np.testing.assert_array_equal(before, plan.eligible_mask)


@pytest.mark.parametrize('bound', [True, 0, -1, 1.])
def test_native_population_rejects_invalid_bounds(bound):
    with pytest.raises(ValueError, match='positive native feature row bound'):
        native_price_candidate_feature_decisions(native_plan(), max_rows=bound)


def test_native_population_cannot_borrow_foreign_attempts():
    plan = native_plan()
    object.__setattr__(plan.source, 'source_attempts', tuple('foreign' for _ in plan.source.source_attempts))
    with pytest.raises(ValueError, match='scope differs'):
        native_price_candidate_feature_decisions(plan, max_rows=1000)


def test_native_price_plan_can_prepare_complete_rank_without_dropping_unknowns():
    plan = native_plan()
    def frozen(values, dtype):
        return np.frombuffer(np.asarray(values, dtype=dtype).tobytes(), dtype=dtype)
    scores = PreparedParticipationScores(frozen([np.nan] * len(plan.eligible_mask), np.float64),
        frozen([False] * len(plan.eligible_mask), bool), frozen(plan.eligible_mask, bool))
    policy = ParticipationAcquisitionPolicy(
        ParticipationRankingPolicy(((60000, 1.),), .5, .5, 1., 1, 1000), 'eligible_unranked_last')
    prepared = prepare_participation_acquisition_order(plan, scores, policy=policy)
    assert prepared.source is plan and prepared.source_token == plan.token
    assert set(prepared.ranks) == set(plan.momentum.keys)
