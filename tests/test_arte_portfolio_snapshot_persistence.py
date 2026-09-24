from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import re

import pytest

from src.trading_runtime.arte_portfolio_snapshot import (
    load_latest_portfolio_snapshot, load_portfolio_snapshot,
    load_run_portfolio_snapshots, project_portfolio_snapshot, _snapshot_rows,
    prepare_portfolio_snapshot, publish_portfolio_snapshot,
    publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.portfolio import PortfolioReservation


class SnapshotClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.inserts: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            table = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(table)
            self.tables.setdefault(table, []).extend(
                json.loads(line) for line in sql.split("\n", 1)[1].splitlines()
            )
            return ""
        assert sql.startswith("SELECT ") and "FORMAT JSONEachRow" in sql
        table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        conditions = {name: value.strip("'") for name, value in re.findall(
            r"(run_id|account_id|state_revision)=('[^']*'|\d+)", sql)}
        run_id = conditions["run_id"]
        account_id = conditions["account_id"]
        if "ORDER BY state_revision DESC LIMIT 1" in sql:
            revisions = [row["state_revision"] for row in self.tables.get(table, [])
                         if row["run_id"] == run_id and row["account_id"] == account_id]
            return json.dumps({"state_revision": max(revisions)}) if revisions else ""
        revision = int(conditions["state_revision"])
        return "\n".join(json.dumps(row) for row in self.tables.get(table, [])
                         if row["run_id"] == run_id and row["account_id"] == account_id
                         and row["state_revision"] == revision)


def _state() -> dict:
    return {
        "account_key": "account-key", "control_mode": "enabled",
        "sync_state": "synchronized", "snapshot_id": "broker-1",
        "observed_at": datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        "stale_reason": "", "peak_net_liquidation": 1000.0,
        "realized_pnl_baseline": 0.0, "selected_policy": None,
        "disabled_strategy_allocations": ["strategy-a", "strategy-b"],
        "pending_operational_commands": [{
            "command_id": "resume_entries:1", "command": "resume_entries",
            "reason": "operator", "status": "pending",
        }],
        "pending_entry_requests": {}, "reservations": [], "allocations": [],
        "reconciliation": [],
    }


def _publish(client: SnapshotClient, state: dict | None = None) -> str:
    return publish_portfolio_snapshot(
        client, run_id="live-run", account_id="account-id", state_revision=1,
        snapshot_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        state=state or _state(),
    )


def test_snapshot_commit_fences_exact_typed_children_and_retries() -> None:
    client = SnapshotClient()
    assert load_portfolio_snapshot(client, run_id="live-run", account_id="account-id",
                                   state_revision=1) is None
    digest = _publish(client)
    loaded = load_portfolio_snapshot(client, run_id="live-run", account_id="account-id",
                                     state_revision=1)
    assert loaded is not None and loaded["state_hash"] == digest
    assert load_latest_portfolio_snapshot(
        client, run_id="live-run", account_id="account-id")["state_hash"] == digest
    assert len(loaded["families"]["trading_portfolio_disabled_strategy_v1"]) == 2
    assert loaded["state"]["observed_at"] == "2026-08-18T12:00:00.000000+00:00"
    assert project_portfolio_snapshot("account-id", loaded["state"]) == (
        project_portfolio_snapshot("account-id", _state()))
    before = list(client.inserts)
    assert _publish(client) == digest
    assert client.inserts == before
    assert client.inserts[-1] == "trading_portfolio_snapshot_commit_v1"


def test_snapshot_fence_rejects_child_tampering() -> None:
    client = SnapshotClient()
    _publish(client)
    client.tables["trading_portfolio_disabled_strategy_v1"][0]["strategy_id"] = "other"
    with pytest.raises(RuntimeError, match="content differs"):
        load_portfolio_snapshot(client, run_id="live-run", account_id="account-id",
                                state_revision=1)
    with pytest.raises(RuntimeError):
        _publish(client)
    with pytest.raises(RuntimeError, match="content differs"):
        load_latest_portfolio_snapshot(client, run_id="live-run", account_id="account-id")


def test_snapshot_round_trips_recovery_children_and_utc_time() -> None:
    state = _state()
    at = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    state["pending_entry_requests"] = {"intent-1": {
        "request_id": "intent-1", "ticker": "AAA", "assignment_id": "assignment-1",
        "requested_at": at.isoformat(), "last_validated_at": at.isoformat(),
        "reasons": ["broker_stale", "insufficient_cash"],
    }}
    state["reservations"] = [asdict(PortfolioReservation(
        reservation_id="reservation-1", decision_id="decision-1", intent_id="intent-1",
        account_key="account-key", account_id="account-id", strategy_id="strategy-a",
        assignment_id="assignment-1", ticker="AAA", action="enter_long",
        quantity=10, remaining_quantity=10, reference_price=5.25,
        reserved_notional=52.5, reserved_planned_risk=2.5, created_at=at,
    ))]
    client = SnapshotClient()
    _publish(client, state)
    loaded = load_portfolio_snapshot(client, run_id="live-run", account_id="account-id",
                                     state_revision=1)
    assert loaded is not None
    assert loaded["state"]["pending_entry_requests"]["intent-1"]["reasons"] == [
        "broker_stale", "insufficient_cash",
    ]
    assert loaded["state"]["reservations"][0]["created_at"] == (
        "2026-08-18T12:00:00.000000+00:00")
    assert project_portfolio_snapshot("account-id", loaded["state"]) == (
        project_portfolio_snapshot("account-id", state))


def test_latest_snapshot_rejects_stale_publication_and_corrupt_head() -> None:
    client = SnapshotClient()
    _publish(client)
    changed = _state()
    changed["peak_net_liquidation"] = 1100.0
    publish_portfolio_snapshot(
        client, run_id="live-run", account_id="account-id", state_revision=2,
        snapshot_at=datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc),
        state=changed,
    )
    assert load_latest_portfolio_snapshot(
        client, run_id="live-run", account_id="account-id")["state_revision"] == 2
    with pytest.raises(RuntimeError, match="older than the committed prefix"):
        _publish(client)
    for row in client.tables["trading_portfolio_snapshot_v1"]:
        if row["state_revision"] == 2:
            row["peak_net_liquidation"] = "999.000000000000000000"
    with pytest.raises(RuntimeError, match="content differs"):
        load_latest_portfolio_snapshot(client, run_id="live-run", account_id="account-id")


def test_unfenced_partial_snapshot_is_invisible_and_retry_completes() -> None:
    client = SnapshotClient()
    partial = _snapshot_rows("live-run", "account-id", 1, "2026-08-01",
                             project_portfolio_snapshot("account-id", _state()))
    client.tables["trading_portfolio_snapshot_v1"] = [partial["trading_portfolio_snapshot_v1"][0]]
    assert load_latest_portfolio_snapshot(client, run_id="live-run", account_id="account-id") is None
    digest = _publish(client)
    assert load_latest_portfolio_snapshot(
        client, run_id="live-run", account_id="account-id")["state_hash"] == digest
    assert client.inserts[0] != "trading_portfolio_snapshot_v1"


def test_duplicate_fence_and_missing_child_fail_recovery() -> None:
    client = SnapshotClient()
    _publish(client)
    client.tables["trading_portfolio_snapshot_commit_v1"].append(
        dict(client.tables["trading_portfolio_snapshot_commit_v1"][0]))
    with pytest.raises(RuntimeError, match="duplicate commit fences"):
        load_latest_portfolio_snapshot(client, run_id="live-run", account_id="account-id")
    client.tables["trading_portfolio_snapshot_commit_v1"].pop()
    client.tables["trading_portfolio_disabled_strategy_v1"].pop()
    with pytest.raises(RuntimeError, match="fence count"):
        load_latest_portfolio_snapshot(client, run_id="live-run", account_id="account-id")


def test_run_recovery_requires_every_pinned_account(monkeypatch) -> None:
    from src.trading_runtime import arte_journal_writer

    client = SnapshotClient()
    _publish(client)
    monkeypatch.setattr(arte_journal_writer, "load_typed_run_context",
                        lambda _client, _run_id: {"account_ids": ("account-id", "missing")})
    with pytest.raises(RuntimeError, match="missing"):
        load_run_portfolio_snapshots(client, run_id="live-run")
    monkeypatch.setattr(arte_journal_writer, "load_typed_run_context",
                        lambda _client, _run_id: {"account_ids": ("account-id",)})
    recovered = load_run_portfolio_snapshots(client, run_id="live-run")
    assert set(recovered) == {"account-id"}
    assert recovered["account-id"]["state"]["account_key"] == "account-key"


def test_prepared_snapshot_is_immutable_before_background_publication() -> None:
    state = _state()
    prepared = prepare_portfolio_snapshot(
        run_id="live-run", account_id="account-id", state_revision=1,
        snapshot_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc), state=state,
    )
    state["peak_net_liquidation"] = 999999.0
    state["disabled_strategy_allocations"].append("late-strategy")
    state["pending_operational_commands"][0]["reason"] = "changed"
    assert prepared.rows.account["peak_net_liquidation"] == "1000.000000000000000000"
    assert len(prepared.rows.disabled_strategies) == 2
    assert prepared.rows.commands[0]["reason"] == "operator"
    with pytest.raises(TypeError):
        prepared.rows.account["peak_net_liquidation"] = "0"
    client = SnapshotClient()
    publish_prepared_portfolio_snapshot(client, prepared)
    loaded = load_latest_portfolio_snapshot(client, run_id="live-run", account_id="account-id")
    assert loaded is not None
    assert loaded["state"]["peak_net_liquidation"] == 1000.0
