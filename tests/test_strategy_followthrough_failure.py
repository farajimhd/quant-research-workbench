"""Causal failure predicate: preserve large-move pullbacks and fail closed."""
from dataclasses import replace
import pytest
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput, followthrough_failure


def evidence():
    return FollowThroughFailureInput(45000, 31100, 10., 9., 45000, 94500,
        True, .01, .02, 9.44, 9.46, 20, 100., False)


def test_half_distance_failure_requires_both_price_and_completed_momentum():
    assert followthrough_failure(evidence()) is not None
    assert followthrough_failure(replace(evidence(), completed_five_second_close_int=98000, bid=9.79, ask=9.81)) is None
    assert followthrough_failure(replace(evidence(), macd_line=.03)) is None
    assert followthrough_failure(replace(evidence(), macd_line=.02)) is None
    assert followthrough_failure(replace(evidence(), bid=9.51, ask=9.52)) is None


@pytest.mark.parametrize('updates', [
    {'macd_line':None}, {'macd_signal':float('nan')}, {'bid':None},
    {'quote_age_us':1_000_001}, {'quote_age_us':-1}, {'quote_age_us':None},
    {'price_valid':False}, {'completed_five_second_close_int':None},
    {'completed_five_second_boundary_ms':50000}, {'completed_five_second_boundary_ms':40000},
    {'first_held_boundary_ms':41100}, {'boundary_ms':45100}, {'pending_exit':True},
    {'position_quantity':0.},
])
def test_missing_forming_stale_entry_bucket_and_duplicate_exit_inputs_do_not_trigger(updates):
    assert followthrough_failure(replace(evidence(), **updates)) is None


def test_complete_bucket_after_fill_and_exact_threshold_are_allowed():
    assert followthrough_failure(replace(evidence(), first_held_boundary_ms=40000,
        completed_five_second_close_int=95000, bid=9.5, ask=9.51)) is not None


@pytest.mark.parametrize('age', [0, 1001, 999999, 1000000])
def test_one_second_quote_freshness_uses_microsecond_units(age):
    assert followthrough_failure(replace(evidence(), quote_age_us=age)) is not None


def test_future_tail_cannot_change_a_completed_prefix_decision():
    prefix=(replace(evidence(), boundary_ms=35000,completed_five_second_boundary_ms=35000),evidence())
    expected=tuple(followthrough_failure(row) for row in prefix)
    extended=prefix+(replace(evidence(),boundary_ms=50000,completed_five_second_boundary_ms=50000,
        completed_five_second_close_int=110000,bid=11.,ask=11.01),)
    assert tuple(followthrough_failure(row) for row in extended)[:2]==expected


@pytest.mark.parametrize('updates',[{'boundary_ms':True},{'initial_stop':10.},
    {'reference_ask':float('inf')},{'position_quantity':-1.}])
def test_corrupt_position_authority_is_rejected(updates):
    with pytest.raises(ValueError,match='causal position'):
        followthrough_failure(replace(evidence(),**updates))
