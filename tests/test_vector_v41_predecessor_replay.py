import pytest
from research.vectorized_backtest.v41.predecessor_replay import leaders


def test_top20_uses_only_eligible_training_objective_and_stable_serial_ties():
    scores=dict(ids=list(range(30,0,-1)),scores=[7.]*30,eligible=[True]*30)
    first=leaders(scores,[0.]*30)
    assert [scores['ids'][i] for i in first]==list(range(1,21))
    assert leaders(scores,[999999.]*30)==first
    scores['eligible'][-1]=False
    assert [scores['ids'][i] for i in leaders(scores,[0.]*30)]==list(range(2,22))


def test_insufficient_eligible_candidates_fail_closed():
    with pytest.raises(ValueError,match='Fewer'):
        leaders(dict(ids=list(range(20)),scores=[1.]*20,eligible=[False]+[True]*19),[0.]*20)
