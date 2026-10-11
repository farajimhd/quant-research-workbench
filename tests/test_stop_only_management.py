from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as S

import pytest

from src.trading_runtime.order_management import OrderManagementState as State
from src.trading_runtime.stop_only_management import residual_rights


def source(identity, quantity, at, closed=0):
    return S(group_id=identity, created_at=at, intent=S(action="enter_long"),
        filled_quantity=quantity, filled_by_broker_order={"stop": closed},
        broker_order_roles={"stop": "protective_stop"})


def reduction(filled=6., cancelled=False, quantity=10.):
    return S(intent=S(action="reduce_long"), filled_quantity=filled, remaining_quantity=4.5,
        state=State.CANCELLED if cancelled else State.PARTIALLY_FILLED,
        broker_order_ids=["sell"], orders=[S(quantity=quantity)],
        broker_order_request_indexes={"sell": 0}, broker_order_roles={"sell": "managed_exit"},
        filled_by_broker_order={"sell": filled}, terminal_broker_order_ids={"sell"} if cancelled else set())


def test_actual_fills_and_executable_pending_budget_allocate_survivors_deterministically():
    at = datetime(2026, 10, 12, tzinfo=UTC)
    first, second = source("first", 10., at, closed=2.), source("second", 15., at + timedelta(seconds=1))
    managed = reduction()
    manager = S(_groups_by_assignment={("account", "assignment"): [second, managed, first]})
    assert residual_rights(manager, "account", "assignment") == {"first": 0., "second": 13.}
    managed.state = State.CANCELLED
    managed.terminal_broker_order_ids.add("sell")
    assert residual_rights(manager, "account", "assignment") == {"first": 2., "second": 15.}


def test_closed_episode_does_not_consume_new_acquisition_protection():
    at = datetime(2026, 10, 12, tzinfo=UTC)
    manager = S(_groups_by_assignment={("account", "assignment"): [
        source("old", 25., at, closed=13.), reduction(filled=12., cancelled=True, quantity=12.),
        source("new", 10., at + timedelta(seconds=100))]})
    assert residual_rights(manager, "account", "assignment") == {"old": 0., "new": 10.}


def test_excess_managed_capacity_is_rejected_instead_of_truncated():
    at = datetime(2026, 10, 12, tzinfo=UTC)
    manager = S(_groups_by_assignment={("account", "assignment"): [
        source("entry", 5., at), reduction(filled=6., cancelled=True)]})
    with pytest.raises(ValueError, match="exceeds processed acquisition rights"):
        residual_rights(manager, "account", "assignment")
