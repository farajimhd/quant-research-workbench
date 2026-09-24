from __future__ import annotations

import json

import pytest

from src.trading_runtime import arte_terminal_recovery_audit as audit
from src.trading_runtime.arte_journal_writer import CommittedPrefix
from src.trading_runtime.arte_journal_reader import TypedJournalEvent


RUN = "backtest:run-1"
BATCH = "00000000-0000-0000-0000-000000000001"
PREFIX = CommittedPrefix(RUN, 2, BATCH, "end", "completed", (BATCH,))
CONTEXT = {"mode": "backtest", "account_ids": ("DU1", "DU2")}


class Client:
    def __init__(self, accounts=("DU1", "DU2")):
        self.accounts = accounts
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        assert sql.startswith("SELECT account_id FROM arte.trading_backtest_snapshot_anchor_v1 ")
        assert "LIMIT 3" in sql and "FORMAT JSONEachRow" in sql
        return "\n".join(json.dumps({"account_id": item}) for item in self.accounts)


def _install(monkeypatch, *, pages=None, snapshots=None):
    monkeypatch.setattr(audit, "load_committed_prefix", lambda _client, _run: PREFIX)
    monkeypatch.setattr(audit, "load_typed_run_context", lambda _client, _run: CONTEXT)
    seen = []

    def page(_client, _prefix, *, after_sequence, limit):
        seen.append((after_sequence, limit))
        if pages is not None:
            return pages(after_sequence)
        return (TypedJournalEvent({"sequence": after_sequence + 1}, None, None),)

    monkeypatch.setattr(audit, "load_typed_event_page", page)
    loaded = []

    def snapshot(_client, _prefix, *, account_id):
        loaded.append(account_id)
        if snapshots is not None:
            return snapshots(account_id)
        return {"account_id": account_id, "state_hash": "a" * 64}

    monkeypatch.setattr(audit, "load_terminal_backtest_snapshot", snapshot)
    return seen, loaded


def test_whole_run_audit_checks_every_event_join_and_pinned_snapshot(monkeypatch):
    seen, loaded = _install(monkeypatch)
    client = Client()
    rows = audit.audit_terminal_backtest_recovery(client, RUN, page_size=1)
    assert set(rows) == {"DU1", "DU2"}
    assert seen == [(0, 1), (1, 1)]
    assert loaded == ["DU1", "DU2"]
    assert len(client.queries) == 1


@pytest.mark.parametrize("anchors", [("DU1",), ("DU1", "DU1"),
                                       ("DU1", "DU2", "FOREIGN")])
def test_missing_duplicate_or_foreign_anchor_fails_before_snapshot(monkeypatch, anchors):
    _seen, loaded = _install(monkeypatch)
    with pytest.raises(RuntimeError, match="pinned account membership"):
        audit.audit_terminal_backtest_recovery(Client(anchors), RUN)
    assert loaded == []


def test_missing_event_detail_page_fails_before_snapshot(monkeypatch):
    _seen, loaded = _install(monkeypatch, pages=lambda after: () if after == 1 else (
        TypedJournalEvent({"sequence": 1}, None, None),))
    with pytest.raises(RuntimeError, match="ended early"):
        audit.audit_terminal_backtest_recovery(Client(), RUN, page_size=1)
    assert loaded == []


def test_changed_prefix_fails_after_snapshot_reads(monkeypatch):
    _seen, loaded = _install(monkeypatch)
    calls = 0

    def prefix(_client, _run):
        nonlocal calls
        calls += 1
        return PREFIX if calls == 1 else None

    monkeypatch.setattr(audit, "load_committed_prefix", prefix)
    with pytest.raises(RuntimeError, match="authority changed"):
        audit.audit_terminal_backtest_recovery(Client(), RUN)
    assert loaded == ["DU1", "DU2"]
