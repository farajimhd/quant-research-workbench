from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from src.trading_runtime.arte_portfolio_snapshot import project_portfolio_snapshot
from src.trading_runtime.portfolio import PortfolioPolicy, PortfolioReservation


def _state() -> dict:
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    return {
        "account_key": "account-a", "control_mode": "entries_paused",
        "sync_state": "synchronized", "snapshot_id": "broker-snapshot-1",
        "observed_at": at, "stale_reason": "", "peak_net_liquidation": 12345.5,
        "realized_pnl_baseline": None,
        "selected_policy": {**asdict(PortfolioPolicy(policy_id="selected")),
                            "identity": "selected@1"},
        "disabled_strategy_allocations": ["strategy-a"],
        "pending_operational_commands": [{
            "command_id": "resume_entries:1", "command": "resume_entries",
            "reason": "operator", "status": "pending",
        }],
        "pending_entry_requests": {"intent-1": {
            "request_id": "intent-1", "ticker": "AAA", "assignment_id": "assignment-1",
            "requested_at": at.isoformat(), "last_validated_at": at.isoformat(),
            "reasons": ["broker_stale", "insufficient_cash"],
        }},
        "reservations": [asdict(PortfolioReservation(
            reservation_id="reservation-1", decision_id="decision-1",
            intent_id="intent-1", account_key="account-a", account_id="account-id",
            strategy_id="strategy-a", assignment_id="assignment-1", ticker="AAA",
            action="enter_long", quantity=10, remaining_quantity=10,
            reference_price=5.25, reserved_notional=52.5,
            reserved_planned_risk=2.5, created_at=at,
        ))],
        "allocations": [], "reconciliation": [],
    }


def test_portfolio_recovery_projects_only_scalar_typed_rows() -> None:
    rows = project_portfolio_snapshot("account-id", _state())
    assert len(rows.reservations) == len(rows.commands) == len(rows.requests) == 1
    assert len(rows.request_reasons) == 2
    assert rows.commands[0]["ordinal"] == 0
    assert rows.account["selected_policy_hash"] is not None
    assert rows.reservations[0]["reserved_notional"] == "52.500000000000000000"
    for family in (rows.account, *rows.disabled_strategies, *rows.commands,
                   *rows.requests, *rows.request_reasons, *rows.reservations):
        assert all(not isinstance(value, (dict, list, tuple)) for value in family.values())


@pytest.mark.parametrize("mutation", [
    lambda state: state.update({"opaque": {"x": 1}}),
    lambda state: state["pending_operational_commands"][0].update({"detail": {"x": 1}}),
    lambda state: state["reservations"][0].update({"extra": "unmodeled"}),
    lambda state: state["reservations"][0].update({"account_key": "other-account"}),
    lambda state: state["pending_entry_requests"]["intent-1"].update({"extra": 1}),
])
def test_portfolio_recovery_rejects_unmodeled_nested_data(mutation) -> None:
    state = _state()
    mutation(state)
    with pytest.raises(ValueError, match="unmodeled|another account"):
        project_portfolio_snapshot("account-id", state)
