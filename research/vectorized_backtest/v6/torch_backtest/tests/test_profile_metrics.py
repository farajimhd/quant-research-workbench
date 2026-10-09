"""Sealed invalid scores stay invalid during numerical parity audits."""
import pytest
from research.vectorized_backtest.v6.torch_backtest.profile_execution import assert_metric_equal


def test_serialized_invalid_scores_preserve_pattern():
    assert_metric_equal([None,-1.,0.],[None,-1.,0.])


def test_invalid_score_cannot_become_finite():
    with pytest.raises(ValueError,match='pattern changed'):
        assert_metric_equal([0.,-1.],[None,-1.])


def test_finite_score_change_is_detected():
    with pytest.raises(AssertionError):
        assert_metric_equal([None,3.],[None,2.])
