"""Causal, position-owned stop/target tests for draft Strategy 1."""
import pytest

from src.trading_runtime.strategy_one_position import (
    ResistanceBreak, advance_protection, confirm_protection_transition,
    open_protection, ordered_protection_amendments,
)


def level(identity, center):
    return {"unified_level_id": str(identity), "lower": center - .01,
            "upper": center + .01, "side": "resistance", "role": "resistance"}


def opening(**changes):
    args = dict(now_ms=30_100, bid=10., ask=10.01, tick=.01,
                low_boundary_ms=30_000, low_int=97_000,
                low_price_valid=True, low_extremes_valid=True,
                overhead_levels=[level(index, 10 + index * .1)
                                 for index in range(1, 8)])
    return open_protection(**{**args, **changes})


def advancing(state, **changes):
    args = dict(now_ms=31_000, bid=10., ask=10.01, tick=.01,
                low_boundary_ms=30_000, low_int=97_000,
                low_price_valid=True, low_extremes_valid=True,
                breaks=(), overhead_levels=[level(index, 10 + index * .1)
                                            for index in range(1, 8)],
                price_bearing_bar=True)
    return advance_protection(state, **{**args, **changes})


def test_entry_requires_completed_low_and_original_third_overhead_target():
    opened = opening()
    assert opened.state.stop == 9.69
    assert opened.state.target == 10.3
    assert opened.target_amendment["ordinal"] == 3
    assert opening(low_boundary_ms=60_000) is None
    assert opening(low_price_valid=False) is None
    assert opening(overhead_levels=[level(1, 10.1), level(2, 10.2)]) is None


def test_three_distinct_breaks_win_over_simultaneous_higher_low():
    opened = opening()
    breaks = [ResistanceBreak(31_000, level(index, center))
              for index, center in ((1, 9.8), (2, 9.9), (3, 10.1))]
    result = advancing(opened.state, low_int=99_500, breaks=breaks)
    assert result.stop_amendment["source"] == "three_resistance_step_stop"
    assert result.state.stop == 9.78
    assert result.state.applied_groups == 1
    assert len(result.state.accepted_ids) == 3
    assert advancing(result.state, now_ms=31_100, low_int=99_500,
                     breaks=()).state.stop == 9.94


def test_duplicate_break_does_not_earn_second_group_or_target_downgrade():
    opened = opening()
    first = advancing(opened.state, breaks=[
        ResistanceBreak(31_000, level(index, center))
        for index, center in ((1, 9.8), (2, 9.9), (3, 10.1))])
    repeated = advancing(first.state, now_ms=32_000, breaks=[
        ResistanceBreak(32_000, level(1, 9.8))])
    assert repeated.state.applied_groups == 1
    assert len(repeated.state.accepted_ids) == 3
    assert repeated.state.target == opened.state.target


def test_quote_only_boundary_does_not_rerank_target():
    opened = opening()
    result = advancing(opened.state, price_bearing_bar=False,
                       overhead_levels=[level(index, 11 + index * .1)
                                        for index in range(1, 8)])
    assert result.target_amendment is None
    assert result.state.target == opened.state.target


def test_sparse_bid_jump_cannot_cross_unfilled_target_with_stop():
    opened = opening()
    result = advancing(opened.state, bid=10.5, ask=10.51,
                       low_int=105_000, price_bearing_bar=False)
    assert result.stop_amendment is None
    assert result.state.stop < result.state.target


def test_future_break_is_rejected_without_mutating_prior_state():
    opened = opening()
    with pytest.raises(ValueError, match="completed 1s"):
        advancing(opened.state, breaks=[ResistanceBreak(32_000, level(1, 9.8))])
    assert opened.state.accepted_ids == frozenset()


def test_same_boundary_breaks_have_deterministic_price_order():
    opened = opening()
    events = [ResistanceBreak(31_000, level(index, center))
              for index, center in ((3, 10.1), (1, 9.8), (2, 9.9))]
    forward = advancing(opened.state, breaks=events)
    reverse = advancing(opened.state, breaks=list(reversed(events)))
    assert forward == reverse
    assert [row.unified_level_id for row in forward.state.earned_group] == [
        "1", "2", "3"]


def test_duplicate_break_witnesses_are_deterministic_and_conflicts_fail_closed():
    opened = opening()
    duplicate = ResistanceBreak(31_000, level(1, 9.8))
    result = advancing(opened.state, breaks=(duplicate, duplicate))
    assert len(result.state.accepted_ids) == 1
    assert len(result.state.pending_group) == 1
    with pytest.raises(ValueError, match="conflicting geometry"):
        advancing(opened.state, breaks=(
            duplicate, ResistanceBreak(31_000, level(1, 9.9))))


def test_only_latest_triple_is_retained_and_idle_boundary_reuses_state():
    opened = opening()
    first = advancing(opened.state, breaks=[
        ResistanceBreak(31_000, level(index, center))
        for index, center in ((1, 9.8), (2, 9.85), (3, 9.9))])
    second = advancing(first.state, now_ms=32_000, bid=10.1, ask=10.11, breaks=[
        ResistanceBreak(32_000, level(index, center))
        for index, center in ((4, 9.95), (5, 10.), (6, 10.05))])
    assert second.state.earned_groups == second.state.applied_groups == 2
    assert second.state.stop == 9.93
    assert [row.unified_level_id for row in second.state.earned_group] == [
        "4", "5", "6"]
    idle = advancing(second.state, now_ms=32_100, bid=10.1, ask=10.11,
                     price_bearing_bar=False)
    assert idle.state.accepted_ids is second.state.accepted_ids
    assert idle.state.earned_group is second.state.earned_group
    assert idle.state.pending_group is second.state.pending_group


def test_rejected_stop_keeps_break_evidence_but_retries_unapplied_group():
    opened = opening()
    transition = advancing(opened.state, breaks=[
        ResistanceBreak(31_000, level(index, center))
        for index, center in ((1, 9.8), (2, 9.9), (3, 10.1))])
    rejected = confirm_protection_transition(
        opened.state, transition, target_confirmed=False,
        stop_confirmed=False)
    assert rejected.stop == opened.state.stop
    assert rejected.accepted_ids == transition.state.accepted_ids
    assert rejected.earned_groups == 1 and rejected.applied_groups == 0
    retry = advancing(rejected, now_ms=31_100, breaks=())
    assert retry.stop_amendment["source"] == "three_resistance_step_stop"
    confirmed = confirm_protection_transition(
        rejected, retry, target_confirmed=False, stop_confirmed=True)
    assert confirmed.stop == retry.state.stop
    assert confirmed.applied_groups == 1


def test_target_precedes_stop_and_unacknowledged_target_cannot_license_crossing():
    opened = opening()
    transition = advancing(opened.state, bid=10.5, ask=10.51,
                           low_int=105_000, overhead_levels=[
                               level(index, 11 + index * .1)
                               for index in range(1, 8)])
    assert [action for action, _ in ordered_protection_amendments(transition)] == [
        "replace_profit_target", "replace_protective_stop"]
    with pytest.raises(RuntimeError, match="cross working target"):
        confirm_protection_transition(
            opened.state, transition, target_confirmed=False,
            stop_confirmed=True)
