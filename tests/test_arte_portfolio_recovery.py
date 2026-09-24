from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.trading_runtime import arte_portfolio_recovery as recovery
from src.trading_runtime.arte_portfolio_admission import TypedPortfolioAdmissionAuthority
from src.trading_runtime.arte_portfolio_sync import TypedPortfolioSyncAuthority
from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
from src.trading_runtime.portfolio import (
    PortfolioAllocationLot, PortfolioManagementEngine, PortfolioReservation, PortfolioSyncState,
    profiles_for_runtime,
)
from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient, _state
from tests.test_portfolio_management import ledger, position, summary


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


def test_engine_typed_seam_skips_sqlite_and_blocks_admission(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    recovered = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)

    class NoSQLite:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected SQLite access: {name}")

    engine = PortfolioManagementEngine(
        (profile,), journal=NoSQLite(), run_id="live-run", strategy_id="strategy-a",
        strategy_revision=1, typed_recovery=recovered)
    assert engine.reservations == recovered.reservations
    assert engine.allocations == recovered.allocations
    assert engine.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED
    with pytest.raises(RuntimeError, match="durable admission writer"):
        asyncio.run(engine.approve(None, account_id="account-id"))
    with pytest.raises(RuntimeError, match="cannot overwrite SQLite"):
        engine._persist_state(engine.states["account-id"])
    with pytest.raises(ValueError, match="differs from the engine run"):
        PortfolioManagementEngine(
            (profile,), journal=NoSQLite(), run_id="different", strategy_id="strategy-a",
            strategy_revision=1, typed_recovery=recovered)


def test_typed_admission_waits_for_receipt_and_poison_on_uncertain_commit(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    recovered = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)

    class NoSQLite:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected SQLite access: {name}")

    engine = PortfolioManagementEngine(
        (profile,), journal=NoSQLite(), run_id="live-run", strategy_id="strategy-a",
        strategy_revision=1, typed_recovery=recovered)
    state = engine.states["account-id"]
    state.sync_state = PortfolioSyncState.SYNCHRONIZED
    state.summary = object()
    state.ledger = object()
    state.component_watermarks = {"summary": AT}
    ready = asyncio.Event()
    release = asyncio.Event()
    fail = False
    fail_before_publication = False

    class Authority(TypedPortfolioAdmissionAuthority):
        def __init__(self):
            pass

        @asynccontextmanager
        async def claim(self, run_id, account_id, group_ids):
            assert (run_id, account_id, group_ids) == ("live-run", "account-id", ())
            yield {"owner_id": "worker", "epoch": 1, "resource_id": "account-id"}

        def claim_is_current(self, lease):
            return True

        def next_revision(self, run_id, account_id, lease):
            return 8

        async def publish(self, records, captured, lease):
            assert {row[0] for row in records} == {"portfolio_decision", "portfolio_reservation"}
            assert captured.state_revision == 8 and len(captured.reservations) == 2
            ready.set()
            await release.wait()
            if fail:
                raise OSError("commit failed")
            return SimpleNamespace(run_id="live-run", account_id="account-id",
                                   state_revision=8, snapshot_hash="a" * 64)

    def staged_approval(intent, state):
        @dataclass(frozen=True)
        class Approved:
            metadata: dict

        item = replace(engine.reservations["reservation-1"], reservation_id="reservation-2")
        engine.reservations[item.reservation_id] = item
        engine._record("portfolio_reservation", item.reservation_id, "account-id", {"event": "created"})
        engine._record("portfolio_decision", "decision-2", "account-id", {"event": "approved"})
        engine._persist_state(state)
        if fail_before_publication:
            raise ValueError("projection rejected")
        return SimpleNamespace(status="approved"), Approved(metadata={"seed": "value"})

    monkeypatch.setattr(engine, "_approve_locked", staged_approval)

    async def run_success():
        task = asyncio.create_task(engine.prepare_typed_admission(
            None, account_id="account-id", authority=Authority()))
        await ready.wait()
        assert not task.done()
        release.set()
        return await task

    _, approved = asyncio.run(run_success())
    assert approved.metadata["typed_admission_snapshot_hash"] == "a" * 64
    assert "reservation-2" in engine.reservations
    engine.reservations.pop("reservation-2")
    fail_before_publication = True
    with pytest.raises(ValueError, match="projection rejected"):
        asyncio.run(engine.prepare_typed_admission(
            None, account_id="account-id", authority=Authority()))
    assert "reservation-2" not in engine.reservations
    assert not engine._typed_admission_poisoned
    fail_before_publication = False
    ready = asyncio.Event()
    release = asyncio.Event()
    fail = True

    async def run_failure():
        task = asyncio.create_task(engine.prepare_typed_admission(
            None, account_id="account-id", authority=Authority()))
        await ready.wait()
        release.set()
        with pytest.raises(OSError, match="commit failed"):
            await task

    asyncio.run(run_failure())
    assert "reservation-2" in engine.reservations
    assert engine._typed_admission_poisoned
    assert engine.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED
    with pytest.raises(RuntimeError, match="verified recovery authority"):
        asyncio.run(engine.synchronize_typed_snapshot(
            "account-id", summary=summary("account-id"), ledger=ledger("account-id"),
            positions=[], open_orders=[], authority=Authority()))
    with pytest.raises(RuntimeError, match="idle recovered engine"):
        asyncio.run(engine.prepare_typed_admission(
            None, account_id="account-id", authority=Authority()))


def test_typed_broker_sync_exposes_synchronized_only_after_receipt(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    restored = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)

    class NoSQLite:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected SQLite access: {name}")

    engine = PortfolioManagementEngine(
        (profile,), journal=NoSQLite(), run_id="live-run", strategy_id="strategy-a",
        strategy_revision=1, typed_recovery=restored)
    ready = asyncio.Event()
    release = asyncio.Event()

    class Authority(TypedPortfolioSyncAuthority):
        def __init__(self):
            pass

        @asynccontextmanager
        async def claim(self, run_id, account_id):
            assert (run_id, account_id) == ("live-run", "account-id")
            yield "lease"

        def claim_is_current(self, lease):
            return True

        def next_revision(self, run_id, account_id, lease):
            return 8

        async def publish(self, captured, lease):
            assert captured.sync_state == "synchronized"
            assert captured.state_revision == 8
            assert not captured.reconciliation
            ready.set()
            await release.wait()
            return SimpleNamespace(run_id="live-run", account_id="account-id",
                                   state_revision=8, snapshot_hash="a" * 64)

    async def run():
        task = asyncio.create_task(engine.synchronize_typed_snapshot(
            "account-id", summary=summary("account-id"), ledger=ledger("account-id"),
            positions=[position("account-id", "AAA", 2, 5.25)],
            open_orders=[], authority=Authority()))
        await ready.wait()
        assert not task.done()
        assert engine.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED
        release.set()
        await task

    asyncio.run(run())
    assert engine.states["account-id"].sync_state == PortfolioSyncState.SYNCHRONIZED
    assert not engine.differences

    class BrokenBroker:
        async def live_orders(self):
            raise OSError("broker unavailable")

    with pytest.raises(OSError, match="broker unavailable"):
        asyncio.run(engine.synchronize_typed_broker(BrokenBroker(), authority=Authority()))
    assert engine.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED


def test_uncertain_typed_broker_sync_blocks_until_cold_recovery(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    restored = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    engine = PortfolioManagementEngine(
        (profile,), journal=object(), run_id="live-run", strategy_id="strategy-a",
        strategy_revision=1, typed_recovery=restored)

    class Authority(TypedPortfolioSyncAuthority):
        def __init__(self):
            pass

        @asynccontextmanager
        async def claim(self, run_id, account_id):
            yield "lease"

        def claim_is_current(self, lease):
            return True

        def next_revision(self, run_id, account_id, lease):
            return 8

        async def publish(self, captured, lease):
            raise OSError("receipt unavailable")

    with pytest.raises(OSError, match="receipt unavailable"):
        asyncio.run(engine.synchronize_typed_snapshot(
            "account-id", summary=summary("account-id"), ledger=ledger("account-id"),
            positions=[position("account-id", "AAA", 2, 5.25)],
            open_orders=[], authority=Authority()))
    assert engine._typed_admission_poisoned
    assert engine.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED


def test_typed_broker_sync_rejects_unmodeled_reconciliation_before_publish(monkeypatch) -> None:
    client, profile = _client(monkeypatch)
    restored = recovery.recover_portfolio_engine_state(
        client, run_id="live-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    engine = PortfolioManagementEngine(
        (profile,), journal=object(), run_id="live-run", strategy_id="strategy-a",
        strategy_revision=1, typed_recovery=restored)

    class Authority(TypedPortfolioSyncAuthority):
        def __init__(self):
            pass

        @asynccontextmanager
        async def claim(self, run_id, account_id):
            yield "lease"

        def claim_is_current(self, lease):
            return True

        def next_revision(self, run_id, account_id, lease):
            return 8

        async def publish(self, captured, lease):
            pytest.fail("unmodeled reconciliation must not be published")

    with pytest.raises(RuntimeError, match="unmodeled staged journal events"):
        asyncio.run(engine.synchronize_typed_snapshot(
            "account-id", summary=summary("account-id"), ledger=ledger("account-id"),
            positions=[], open_orders=[], authority=Authority()))
    assert engine.states["account-id"].sync_state == PortfolioSyncState.ENTRIES_BLOCKED
    assert not engine.differences
    assert not engine._typed_admission_poisoned
