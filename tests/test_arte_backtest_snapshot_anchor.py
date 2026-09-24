from __future__ import annotations

import json

import pytest

from src.trading_runtime import arte_backtest_snapshot_anchor as anchor
from src.trading_runtime.arte_journal_writer import CommittedPrefix
from tests.test_arte_admission_fence import BATCH, RUN, captured


HASH = "a" * 64
PREFIX = CommittedPrefix(RUN, 1, BATCH, "terminal", "completed", (BATCH,))


class Client:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.inserts = 0

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte.trading_backtest_snapshot_anchor_v1"):
            self.inserts += 1
            self.rows.extend(json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
            return ""
        assert "FROM arte.trading_backtest_snapshot_anchor_v1" in sql
        return "\n".join(json.dumps(row) for row in self.rows)


def _install(monkeypatch):
    monkeypatch.setattr(anchor, "load_committed_prefix", lambda _client, _run: PREFIX)
    monkeypatch.setattr(anchor, "load_typed_run_context", lambda _client, _run: {
        "mode": "backtest", "account_ids": ("DU1",),
    })
    monkeypatch.setattr(anchor, "publish_prepared_portfolio_snapshot",
                        lambda _client, _prepared: HASH)
    monkeypatch.setattr(anchor, "load_portfolio_snapshot",
                        lambda _client, **_kwargs: {"state_hash": HASH})


def test_terminal_anchor_binds_one_snapshot_to_exact_prefix(monkeypatch) -> None:
    _install(monkeypatch)
    client = Client()
    with pytest.raises(RuntimeError, match="lacks one terminal"):
        anchor.load_terminal_backtest_snapshot(client, PREFIX, account_id="DU1")
    assert anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured()) == HASH
    assert client.inserts == 1
    assert anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured()) == HASH
    assert client.inserts == 1
    assert anchor.load_terminal_backtest_snapshot(client, PREFIX, account_id="DU1") == {
        "state_hash": HASH}
    with pytest.raises(RuntimeError, match="differs from terminal events"):
        anchor.load_terminal_backtest_snapshot(
            client, CommittedPrefix(RUN, 2, BATCH, "terminal", "completed", (BATCH,)),
            account_id="DU1")
    client.rows[0]["snapshot_hash"] = "b" * 64
    with pytest.raises(RuntimeError, match="differs from its hash"):
        anchor.load_terminal_backtest_snapshot(client, PREFIX, account_id="DU1")


def test_terminal_anchor_rejects_changed_prefix_before_snapshot_write(monkeypatch) -> None:
    _install(monkeypatch)
    client = Client()
    monkeypatch.setattr(anchor, "load_committed_prefix", lambda _client, _run: None)
    with pytest.raises(RuntimeError, match="current terminal prefix"):
        anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured())
    assert client.inserts == 0
