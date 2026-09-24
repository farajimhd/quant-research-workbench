from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from src.trading_runtime import arte_portfolio_recovery as recovery
from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
from src.trading_runtime.portfolio import (
    PortfolioAllocationLot, PortfolioReservation, PortfolioSyncState,
    profiles_for_runtime,
)
from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient, _state


AT = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


def _client(monkeypatch, *, sync_state: str = "synchronized",
            observed_at: datetime = AT, snapshot_at: datetime = AT):
    client = SnapshotClient()
    monkeypatch.setattr(recovery, "load_typed_run_context",
                        lambda _client, run_id: {"account_ids": ("account-id",)}
                        if run_id == "live-run" else {"account_ids": ()})
    state = _state()
    state["sync_state"] = sync_state
    state["observed_at"] = observed_at
    state["reservations"] = [asdict(PortfolioReservation(
        reservation_id="reservation-1", decision_id="decision-1", intent_id="intent-1",
        account_key="account-key", account_id="account-id", strategy_id="strategy-a",
        assignment_id="assignment-1", ticker="AAA", action="enter_long",
        quantity=10, remaining_quantity=8, reference_price=5.25,
        reserved_notional=52.5, reserved_planned_risk=2.5, created_at=AT,
        filled_quantity=2,
    ))]
    state["allocations"] = [asdict(PortfolioAllocationLot(
        allocation_id="allocation-1", account_key="account-key", account_id="account-id",
        strategy_id="strategy-a", strategy_revision=1, assignment_id="assignment-1",
        ticker="AAA", quantity=2, average_price=5.25, planned_risk=0.5,
        realized_pnl=0, source="fill", updated_at=AT,
    ))]
    publish_portfolio_snapshot(client, run_id="live-run", account_id="account-id",
                               state_revision=7, snapshot_at=snapshot_at, state=state)
    profile = profiles_for_runtime(("account-id",), mode="live")[0]
    profile = type(profile)(**{**asdict(profile), "account_key": "account-key",
                               "policy": profile.policy})
    return client, profile


def test_exact_revision_reconstructs_typed_engine_state(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    restored = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    assert restored.states["account-id"].profile is profile
    assert restored.states["account-id"].observed_at == AT
    assert restored.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED
    assert not restored.states["account-id"].synchronized
    assert restored.states["account-id"].summary is None
    assert restored.states["account-id"].stale_reason == (
        "Broker resynchronization required after journal recovery")
    assert restored.reservations["reservation-1"].created_at == AT
    assert restored.allocations["allocation-1"].updated_at == AT
    assert restored.last_filled_by_reservation == {"reservation-1": 2.0}


@pytest.mark.parametrize("persisted", ["disabled", "fully_blocked"])
def test_recovery_preserves_stronger_broker_admission_blocks(monkeypatch, persisted) -> None:
    client, profile = _client(monkeypatch, sync_state=persisted)
    restored = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    assert restored.states["account-id"].sync_state == PortfolioSyncState(persisted)
    assert not restored.states["account-id"].synchronized


def test_missing_revision_and_wrong_profile_fail_closed(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    with pytest.raises(RuntimeError, match="lacks account revision"):
        recovery.recover_portfolio_engine_state(
            client, run_id="live-run", profiles=(profile,),
            state_revisions={"account-id": 8}, cutoff_at=AT)
    wrong = type(profile)(**{**asdict(profile), "account_key": "wrong",
                             "policy": profile.policy})
    with pytest.raises(RuntimeError, match="account key differs"):
        recovery.recover_portfolio_engine_state(
            client, run_id="live-run", profiles=(wrong,),
            state_revisions={"account-id": 7}, cutoff_at=AT)


def test_future_domain_timestamp_and_missing_pinned_account_fail_closed(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    with pytest.raises(ValueError, match="beyond its cutoff"):
        recovery.recover_portfolio_engine_state(
            client, run_id="live-run", profiles=(profile,),
            state_revisions={"account-id": 7}, cutoff_at=AT - timedelta(seconds=1))
    delayed_client, delayed_profile = _client(
        monkeypatch, observed_at=AT - timedelta(seconds=1), snapshot_at=AT)
    with pytest.raises(ValueError, match="beyond its cutoff"):
        recovery.recover_portfolio_engine_state(
            delayed_client, run_id="live-run", profiles=(delayed_profile,),
            state_revisions={"account-id": 7},
            cutoff_at=AT - timedelta(milliseconds=500))
    monkeypatch.setattr(recovery, "load_typed_run_context",
                        lambda _client, _run_id: {"account_ids": ("account-id", "other")})
    with pytest.raises(RuntimeError, match="pinned run accounts"):
        recovery.recover_portfolio_engine_state(
            client, run_id="live-run", profiles=(profile,),
            state_revisions={"account-id": 7}, cutoff_at=AT)
