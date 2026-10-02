from dataclasses import replace
import pytest
from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure as zero_regime_failure
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_persistent_risk_failure import persistent_risk_failure


def observation(**changes):
    return replace(FollowThroughFailureInput(100_000,31_100,10.01,9.89,
        100_000,99_400,True,-.02,-.01,9.94,9.95,100_000,100,False), **changes)


@pytest.mark.parametrize('signal', [-.01,0,.01])
def test_late_zero_boundary_is_strict(signal):
    value=observation(macd_line=signal-.01,macd_signal=signal)
    assert persistent_risk_failure(value) is not None
    assert (zero_regime_failure(value) is not None)==(signal<0)


@pytest.mark.parametrize('held', [31_100,43_211_100])
def test_positive_signal_early_failure_is_preserved(held):
    boundary=(held+60_000)//5000*5000
    value=observation(first_held_boundary_ms=held,boundary_ms=boundary,
        completed_five_second_boundary_ms=boundary,macd_line=.01,macd_signal=.02)
    assert zero_regime_failure(value)==persistent_risk_failure(value)
    assert zero_regime_failure(value) is not None


@pytest.mark.parametrize('change', [{'pending_exit':True},{'quote_age_us':1_000_001},
    {'macd_line':None},{'price_valid':False},{'bid':9.96,'ask':9.97}])
def test_negative_regime_never_bypasses_parent_evidence(change):
    assert zero_regime_failure(observation(**change)) is None


@pytest.mark.parametrize('number', [30, 31, 32, 33, 34, 35])
def test_prepared_successor_factory_preserves_parent_late_zero_regime(number):
    from src.trading_runtime.strategy_followthrough_exit import validate_witness
    from src.trading_runtime.arte_followthrough_failure_v4 import validate_numbered_failure
    witness = zero_regime_failure(observation())
    validate_witness(witness, strategy_number=number)
    validate_numbered_failure(witness, number)
    # A persistent-risk witness with positive signal cannot be admitted as
    # the parent's late zero-regime exit for either number.
    positive = persistent_risk_failure(observation(macd_line=.01, macd_signal=.02))
    with pytest.raises(ValueError, match='pinned rule'):
        validate_witness(positive, strategy_number=number)
    with pytest.raises(ValueError, match='pinned rule'):
        validate_numbered_failure(positive, number)
