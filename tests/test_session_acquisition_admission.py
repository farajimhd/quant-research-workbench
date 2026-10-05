from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.session_acquisition_admission import (
    SessionAcquisitionPolicy, session_acquisition_lock,
)
from tests.test_squeeze_ladder_admission import snapshot
from tests.test_squeeze_ladder_protection import request


def lock(*groups, session="premarket", **changes):
    scope = dict(policy=SessionAcquisitionPolicy.ONCE_PER_EXTENDED_SESSION,
        owned_group_ids=frozenset({"group-1"}), session_date=date(2026, 8, 18),
        session=session, account_id="DU1", ticker="TEST",
        as_of=request().event_time + timedelta(hours=9))
    scope.update(changes)
    return session_acquisition_lock(tuple(groups), **scope)


@pytest.mark.parametrize("profile_name", ["unrelated-generic", "other-strategy"])
def test_declared_rule_accepts_any_profile_and_zero_fill_cancel(profile_name):
    group = snapshot(OrderManagementState.CANCELLED, accepted=True)
    profile = replace(group.intent.protection_profile, profile_id=profile_name)
    group = replace(group, intent=replace(group.intent, protection_profile=profile))
    assert group.filled_quantity == 0
    assert lock(group) == "accepted_batch_consumed_session"


@pytest.mark.parametrize("state", [OrderManagementState.REJECTED, OrderManagementState.CANCELLED])
def test_never_acknowledged_terminal_does_not_consume(state):
    assert lock(snapshot(state)) is None


@pytest.mark.parametrize("state", [OrderManagementState.OUTCOME_UNKNOWN,
    OrderManagementState.SUBMITTING, OrderManagementState.WORKING])
def test_unresolved_submission_blocks(state):
    assert lock(snapshot(state)) == "unresolved_submission"


def test_pm_and_ah_have_independent_locks():
    pm = snapshot(OrderManagementState.CANCELLED, accepted=True)
    assert lock(pm, session="afterhours") is None
    ah = replace(snapshot(OrderManagementState.CANCELLED, accepted=True,
        at=request().event_time + timedelta(hours=8)), group_id="ah-group")
    assert lock(pm, ah, session="afterhours",
        owned_group_ids=frozenset({"group-1", "ah-group"})) == "accepted_batch_consumed_session"


@pytest.mark.parametrize("change", [dict(group_id="foreign-strategy"),
    dict(account_id="foreign-account")])
def test_unrelated_ownership_is_ignored_even_if_future(change):
    group = replace(snapshot(OrderManagementState.CANCELLED, accepted=True),
        updated_at=request().event_time + timedelta(days=1), **change)
    membership = frozenset() if "group_id" in change else frozenset({"group-1"})
    assert lock(group, owned_group_ids=membership) is None


def test_unrelated_ticker_or_action_cannot_consume():
    group = snapshot(OrderManagementState.CANCELLED, accepted=True)
    assert lock(replace(group, intent=replace(group.intent, ticker="OTHER"))) is None
    assert lock(replace(group, intent=replace(group.intent, action="exit_long"))) is None
    assert lock(group, owned_group_ids=frozenset()) is None


@pytest.mark.parametrize("field", ["updated_at", "created_at"])
def test_owned_future_snapshot_fails_closed(field):
    group = snapshot(OrderManagementState.CANCELLED, accepted=True)
    with pytest.raises(ValueError, match="future"):
        lock(replace(group, **{field: request().event_time + timedelta(days=1)}))


def test_future_intent_and_naive_clocks_fail_closed():
    group = snapshot(OrderManagementState.CANCELLED, accepted=True)
    for at in (request().event_time + timedelta(days=1), request().event_time.replace(tzinfo=None)):
        with pytest.raises(ValueError, match="clocks"):
            lock(replace(group, intent=replace(group.intent, event_time=at)))


def test_inconsistent_broker_identity_and_duplicate_owned_groups_fail_closed():
    group = snapshot(OrderManagementState.CANCELLED, accepted=True)
    with pytest.raises(ValueError, match="ownership"):
        lock(replace(group, broker_order_ids=()))
    with pytest.raises(ValueError, match="duplicate"):
        lock(group, group)
    with pytest.raises(ValueError, match="acknowledgement"):
        lock(replace(snapshot(OrderManagementState.REJECTED), filled_quantity=1.))


def test_policy_must_be_explicit_and_empty_verified_history_allows_entry():
    assert lock(owned_group_ids=frozenset()) is None
    with pytest.raises(ValueError, match="policy"):
        lock(policy="once_per_extended_session")


def test_declared_group_missing_from_snapshot_prefix_fails_closed():
    with pytest.raises(ValueError, match="missing"):
        lock()


def test_acceptance_wins_over_another_unknown_submission():
    accepted = snapshot(OrderManagementState.CANCELLED, accepted=True)
    unknown = replace(snapshot(OrderManagementState.OUTCOME_UNKNOWN), group_id="group-2")
    assert lock(unknown, accepted,
        owned_group_ids=frozenset({"group-1", "group-2"})) == "accepted_batch_consumed_session"
