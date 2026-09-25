"""Cold V2 terminal portfolio anchors, using only fake ClickHouse rows."""
import pytest

from src.backend import backtest_terminal_v2_accounts as recovery
from src.backend.backtest_terminal_v2_fence import project_terminal_v2_commit
from src.trading_runtime.arte_journal_writer import typed_row
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_terminal_v2_fence import (
    AT, ATTEMPT, BATCH, RUN, _suffix,
)
from tests.test_backtest_terminal_v2_publication import FakeStorage


class ReadClient(FakeStorage):
    def execute(self, sql):
        table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        rows = self.tables[table]
        if table == "trading_event_v1" and "AND sequence=" in sql:
            sequence = int(sql.split("AND sequence=", 1)[1].split(" ", 1)[0])
            rows = [row for row in rows if row["sequence"] == sequence]
        return "\n".join(canonical_json(row) for row in rows)


def _fixture(monkeypatch):
    prefix, events, transitions, accounts, positions = _suffix()
    seal = project_terminal_v2_commit(
        prefix, attempt_id=ATTEMPT, batch_id=BATCH, account_ids=("DU1",),
        source_cursor="start", status="completed", committed_at=AT,
        events=events, transitions=transitions, accounts=accounts,
        positions=positions)
    client = ReadClient()
    client.tables.update({
        "trading_event_v1": list(events),
        "trading_run_transition_v1": list(transitions),
        "trading_backtest_account_snapshot_v2": list(accounts),
        "trading_backtest_position_snapshot_v2": list(positions),
        "trading_backtest_terminal_commit_v2": [seal],
    })
    anchor = typed_row("trading_backtest_snapshot_anchor_v1", {
        "run_id": RUN, "anchor_month": "2026-08-01", "account_id": "DU1",
        "state_revision": seal["last_sequence"], "batch_id": BATCH,
        "last_sequence": seal["last_sequence"], "snapshot_hash": "a" * 64,
        "anchored_at": AT,
    })
    client.tables["trading_backtest_snapshot_anchor_v1"] = [
        {**anchor, "anchored_at": "2026-08-18 14:00:00.000000"}]
    context = {"mode": "backtest", "account_ids": ("DU1",)}
    snapshot = {"state_hash": "a" * 64,
                "state_revision": seal["last_sequence"],
                "snapshot_at": AT.isoformat(), "state": {"cash": "100"}}
    monkeypatch.setattr(recovery, "load_committed_prefix", lambda *_, **__: prefix)
    monkeypatch.setattr(recovery, "load_typed_run_context", lambda *_: context)
    monkeypatch.setattr(recovery, "load_portfolio_snapshot", lambda *_, **__: snapshot)
    return client, snapshot


def test_v2_cold_recovery_binds_portfolio_to_terminal_seal(monkeypatch):
    client, snapshot = _fixture(monkeypatch)
    assert recovery.load_terminal_v2_portfolio_accounts(client, run_id=RUN) == {
        "DU1": snapshot}


def test_v2_cold_recovery_rejects_tampered_or_missing_anchor(monkeypatch):
    client, _ = _fixture(monkeypatch)
    anchor = client.tables["trading_backtest_snapshot_anchor_v1"][0]
    anchor["snapshot_hash"] = "b" * 64
    with pytest.raises(RuntimeError, match="row hash differs"):
        recovery.load_terminal_v2_portfolio_accounts(client, run_id=RUN)
    client.tables["trading_backtest_snapshot_anchor_v1"] = []
    with pytest.raises(RuntimeError, match="pinned account count"):
        recovery.load_terminal_v2_portfolio_accounts(client, run_id=RUN)


def test_v2_cold_recovery_rejects_other_terminal_batch_or_snapshot(monkeypatch):
    client, _ = _fixture(monkeypatch)
    anchor = client.tables["trading_backtest_snapshot_anchor_v1"][0]
    anchor = typed_row("trading_backtest_snapshot_anchor_v1", {
        **{key: value for key, value in anchor.items() if key != "content_hash"},
        "batch_id": "00000000-0000-0000-0000-000000000b99",
        "anchored_at": AT,
    })
    client.tables["trading_backtest_snapshot_anchor_v1"] = [
        {**anchor, "anchored_at": "2026-08-18 14:00:00.000000"}]
    with pytest.raises(RuntimeError, match="differs from terminal sequence"):
        recovery.load_terminal_v2_portfolio_accounts(client, run_id=RUN)
    client, _ = _fixture(monkeypatch)
    monkeypatch.setattr(recovery, "load_portfolio_snapshot", lambda *_, **__: None)
    with pytest.raises(RuntimeError, match="committed portfolio state"):
        recovery.load_terminal_v2_portfolio_accounts(client, run_id=RUN)
