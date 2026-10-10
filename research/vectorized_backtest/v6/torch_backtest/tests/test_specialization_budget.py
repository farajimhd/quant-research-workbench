import pytest
from research.vectorized_backtest.v6.torch_backtest.runtime import specialization_budget


def test_budget_is_derived_from_approved_full_schedule():
    assert specialization_budget(1024,128,32,30)==8192
    assert specialization_budget(128,128,1,8)==128
    assert specialization_budget(1025,128,32,30)==16384


@pytest.mark.parametrize('values',[(0,128,32,30),(1024,0,32,30),(1024,128,True,30),(1024,128,1000,30)])
def test_reject_unbounded_or_invalid_budget(values):
    with pytest.raises(ValueError):specialization_budget(*values)
