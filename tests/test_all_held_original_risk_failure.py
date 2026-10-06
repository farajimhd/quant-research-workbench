"""Causal and exact-price boundaries of the separately declared held extension."""
from dataclasses import replace
import pytest
from src.trading_runtime.all_held_original_risk_failure import AllHeldOriginalRiskPolicy,all_held_original_risk_failure
from src.trading_runtime.early_original_risk_failure import EarlyOriginalRiskPolicy,early_original_risk_failure
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput

POLICY=AllHeldOriginalRiskPolicy()


def value(*,held=100,boundary=65_000):
    return FollowThroughFailureInput(boundary,held,10.,8.,boundary,95_000,True,1.,2.,9.5,9.6,1_000_000,3.,False)


@pytest.mark.parametrize('held,boundary',[(100,10_000),(100,60_000),(100,65_000),(100,19_800_000),
    (43_200_100,43_210_000),(43_200_100,43_265_000),(43_200_100,57_600_000)])
def test_wholly_post_held_at_and_beyond_first_minute_in_both_extended_sessions(held,boundary):
    observed=all_held_original_risk_failure(value(held=held,boundary=boundary),policy=POLICY)
    assert observed and observed.boundary_ms==boundary and observed.first_held_boundary_ms==held
    assert observed.reference_ask==10. and observed.initial_stop==8.


@pytest.mark.parametrize('held,boundary',[(100,5_000),(100,19_805_000),(100,43_205_000),
    (19_800_000,19_810_000),(40_000_000,43_210_000)])
def test_partial_and_foreign_session_windows_do_not_exit(held,boundary):
    assert all_held_original_risk_failure(value(held=held,boundary=boundary),policy=POLICY) is None


@pytest.mark.parametrize('field,changed',[
    ('completed_five_second_boundary_ms',70_000),('completed_five_second_boundary_ms',None),
    ('completed_five_second_close_int',95_001),('completed_five_second_close_int',None),
    ('bid',9.5001),('bid',None),('price_valid',False),('macd_line',2.),('macd_signal',None),
    ('quote_age_us',1_000_001),('quote_age_us',True),('position_quantity',0.),('pending_exit',True)])
def test_missing_future_weak_or_pending_evidence_consumes_no_exit(field,changed):
    assert all_held_original_risk_failure(replace(value(),**{field:changed}),policy=POLICY) is None


def test_elapsed_time_alone_never_exits():
    v=replace(value(boundary=3_600_000),completed_five_second_close_int=100_000,bid=10.,ask=10.1)
    assert all_held_original_risk_failure(v,policy=POLICY) is None


def test_separate_policy_never_widens_legacy_first_minute_cap():
    legacy=EarlyOriginalRiskPolicy('ah-first-minute-quarter-original-risk@1',None,(1,4))
    v=value(held=43_200_100,boundary=43_265_000)
    assert early_original_risk_failure(v,policy=legacy) is None
    assert all_held_original_risk_failure(v,policy=POLICY) is not None
    with pytest.raises(ValueError):replace(legacy,eligibility_ms=65_000)


@pytest.mark.parametrize('change',[dict(policy_id='foreign'),dict(premarket_fraction=(1,2)),
    dict(afterhours_fraction=(True,4)),dict(premarket_fraction=[1,4])])
def test_closed_policy_rejects_aliases_and_changed_fractions(change):
    with pytest.raises(ValueError):replace(POLICY,**change)
