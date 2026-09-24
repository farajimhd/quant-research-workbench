from __future__ import annotations

from datetime import date, datetime, timezone
from threading import Event
from uuid import uuid4

import pytest

from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime import arte_portfolio_snapshot as snapshot_module
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, JournalQueueFull, TypedJournalBatch,
)
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioAccountState, PortfolioReservation,
)


def _prepared(revision: int = 1):
    return snapshot_module.prepare_portfolio_snapshot(
        run_id="live-run", account_id="account-id", state_revision=revision,
        snapshot_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        state={
            "account_key": "key", "control_mode": "enabled",
            "sync_state": "synchronized", "snapshot_id": "broker-1",
            "observed_at": None, "stale_reason": "", "peak_net_liquidation": 1000,
            "realized_pnl_baseline": None, "selected_policy": None,
            "disabled_strategy_allocations": [], "pending_operational_commands": [],
            "pending_entry_requests": {}, "reservations": [], "allocations": [],
            "reconciliation": [],
        },
    )


def _writer(monkeypatch, *, capacity: int = 2) -> ArteJournalWriter:
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: None)
    return ArteJournalWriter(object(), run_id="live-run", capacity=capacity)


def test_snapshot_submission_is_nonwaiting_and_bounded(monkeypatch) -> None:
    entered, release = Event(), Event()

    def stalled(_client, prepared):
        entered.set()
        assert release.wait(5)
        return f"state-{prepared.state_revision}"

    monkeypatch.setattr(snapshot_module, "publish_prepared_portfolio_snapshot", stalled)
    writer = _writer(monkeypatch, capacity=1)
    try:
        first = writer.submit_portfolio_snapshot(_prepared())
        assert entered.wait(5)
        second = writer.submit_portfolio_snapshot(_prepared(2))
        with pytest.raises(JournalQueueFull):
            writer.submit_portfolio_snapshot(_prepared(3))
        assert not first.done() and not second.done()
        release.set()
        assert first.result(timeout=5) == "state-1"
        assert second.result(timeout=5) == "state-2"
    finally:
        release.set()
        writer.close()


def test_snapshot_uses_same_ordered_lane_as_journal_batches(monkeypatch) -> None:
    published = []
    monkeypatch.setattr(writer_module, "publish_typed_batch",
                        lambda _client, _batch: published.append("journal") or "batch")
    monkeypatch.setattr(snapshot_module, "publish_prepared_portfolio_snapshot",
                        lambda _client, _prepared: published.append("snapshot") or "state")
    writer = _writer(monkeypatch)
    try:
        batch = TypedJournalBatch(
            "live-run", date(2026, 8, 1), str(uuid4()), str(uuid4()),
            "00000000-0000-0000-0000-000000000000", 1, 1,
            "cursor-1", "running", (),
        )
        journal_receipt = writer.submit(batch)
        snapshot_receipt = writer.submit_portfolio_snapshot(_prepared())
        assert journal_receipt.result(timeout=5) == "batch"
        assert snapshot_receipt.result(timeout=5) == "state"
        assert published == ["journal", "snapshot"]
    finally:
        writer.close()


def test_snapshot_persistence_failure_poisons_ordered_lane(monkeypatch) -> None:
    entered, release = Event(), Event()

    def failed(_client, _prepared):
        entered.set()
        assert release.wait(5)
        raise OSError("ClickHouse unavailable")

    monkeypatch.setattr(snapshot_module, "publish_prepared_portfolio_snapshot", failed)
    writer = _writer(monkeypatch)
    try:
        first = writer.submit_portfolio_snapshot(_prepared())
        assert entered.wait(5)
        second = writer.submit_portfolio_snapshot(_prepared(2))
        release.set()
        with pytest.raises(OSError, match="ClickHouse unavailable"):
            first.result(timeout=5)
        with pytest.raises(RuntimeError, match="failed earlier"):
            second.result(timeout=5)
        with pytest.raises(RuntimeError, match="failed"):
            writer.submit_portfolio_snapshot(_prepared(3))
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="did not drain durably"):
            writer.close()


def test_actor_capture_normalizes_only_on_worker_and_ignores_later_mutation(monkeypatch) -> None:
    profile = PortfolioAccountProfile(
        account_key="key", account_id="account-id", mode="live",
        account_class="cash", policy=snapshot_module.PortfolioPolicy(),
    )
    state = PortfolioAccountState(profile=profile)
    state.pending_entry_requests["intent-1"] = {
        "request_id": "intent-1", "ticker": "AAA", "assignment_id": "a",
        "requested_at": "2026-08-18T12:00:00+00:00",
        "last_validated_at": "2026-08-18T12:00:00+00:00",
        "reasons": ["broker_stale"],
    }
    at = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    hold = PortfolioReservation(
        reservation_id="r1", decision_id="d1", intent_id="intent-1",
        account_key="key", account_id="account-id", strategy_id="s",
        assignment_id="a", ticker="AAA", action="enter_long", quantity=1,
        remaining_quantity=1, reference_price=5, reserved_notional=5,
        reserved_planned_risk=1, created_at=at,
    )
    captured = snapshot_module.capture_portfolio_snapshot(
        run_id="live-run", state_revision=1, snapshot_at=at, state=state,
        reservations=(hold,), allocations=(), reconciliation=(),
    )
    state.pending_entry_requests["intent-1"]["reasons"].append("late_reason")
    state.pending_entry_requests.clear()
    seen = []

    def publish(_client, prepared):
        seen.append(prepared)
        return "state-hash"

    monkeypatch.setattr(snapshot_module, "publish_prepared_portfolio_snapshot", publish)
    writer = _writer(monkeypatch)
    try:
        assert writer.submit_captured_portfolio_snapshot(captured).result(timeout=5) == "state-hash"
        assert len(seen) == 1
        assert seen[0].rows.request_reasons[0]["reason"] == "broker_stale"
        assert len(seen[0].rows.reservations) == 1
    finally:
        writer.close()
