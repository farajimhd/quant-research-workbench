"""Strategy25 risk refinement preserves causal clocks and after-hours behavior."""
from dataclasses import replace
import pytest
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_early_followthrough_failure import early_followthrough_failure
from src.trading_runtime.strategy_premarket_quarter_risk_failure import premarket_quarter_risk_failure


def evidence():
    return FollowThroughFailureInput(45000,31100,10.,9.,45000,97000,
        True,.01,.02,9.69,9.71,20,100.,False)


def test_premarket_negative_macd_and_quarter_risk_exit_rejects_parent_half_rule():
    assert premarket_quarter_risk_failure(evidence()) is not None
    assert early_followthrough_failure(evidence()) is None
    assert premarket_quarter_risk_failure(replace(evidence(),macd_line=.03)) is None
    assert premarket_quarter_risk_failure(replace(evidence(),macd_line=.02)) is None
    assert premarket_quarter_risk_failure(replace(evidence(),completed_five_second_close_int=97501)) is None
    assert premarket_quarter_risk_failure(replace(evidence(),bid=9.751,ask=9.76)) is None


@pytest.mark.parametrize('price',[94000,95000,97000,110000])
@pytest.mark.parametrize('line',[.01,.02,.03])
def test_afterhours_is_exactly_parent_first_minute_rule(price,line):
    value=replace(evidence(),boundary_ms=43_225_000,first_held_boundary_ms=43_211_100,
        completed_five_second_boundary_ms=43_225_000,completed_five_second_close_int=price,
        bid=price/10000-.01,ask=price/10000+.01,macd_line=line)
    assert premarket_quarter_risk_failure(value)==early_followthrough_failure(value)


@pytest.mark.parametrize('updates',[
    {'first_held_boundary_ms':41100},{'boundary_ms':45100},
    {'completed_five_second_boundary_ms':40000},{'price_valid':False},
    {'macd_line':None},{'macd_signal':float('nan')},{'bid':None},
    {'quote_age_us':1000001},{'quote_age_us':-1},{'pending_exit':True},
    {'position_quantity':0.},
])
def test_missing_stale_forming_and_fill_bucket_never_invents_failure(updates):
    assert premarket_quarter_risk_failure(replace(evidence(),**updates)) is None


def test_actual_first_held_clock_inclusive_window_and_price_threshold():
    value=replace(evidence(),boundary_ms=90000,first_held_boundary_ms=30000,
        completed_five_second_boundary_ms=90000,completed_five_second_close_int=97500,
        bid=9.75,ask=9.76)
    assert premarket_quarter_risk_failure(value) is not None
    assert premarket_quarter_risk_failure(replace(value,boundary_ms=95000,
        completed_five_second_boundary_ms=95000)) is None


def test_future_tail_cannot_change_earlier_causal_decisions():
    rows=(evidence(),replace(evidence(),boundary_ms=50000,completed_five_second_boundary_ms=50000))
    expected=tuple(premarket_quarter_risk_failure(a) for a in rows)
    tail=replace(evidence(),boundary_ms=55000,completed_five_second_boundary_ms=55000,macd_line=.05)
    assert tuple(premarket_quarter_risk_failure(a) for a in (*rows,tail))[:2]==expected


def test_corrupt_original_position_authority_rejected_outside_eligibility():
    with pytest.raises(ValueError,match='causal position'):
        premarket_quarter_risk_failure(replace(evidence(),boundary_ms=100000,
            first_held_boundary_ms=30000,initial_stop=10.))
