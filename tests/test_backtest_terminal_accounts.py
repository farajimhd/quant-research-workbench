from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from src.backend.backtest_terminal_accounts import (
    capture_terminal_backtest_accounts, load_terminal_backtest_accounts,
)
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import CommittedPrefix
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_writer import captured


RUN = "backtest:terminal-1"
AT = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
ATTEMPT = "00000000-0000-0000-0000-000000000a01"
BATCH = "00000000-0000-0000-0000-000000000a02"
ZERO = "00000000-0000-0000-0000-000000000000"


def _batch():
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000a03", RUN, 7,
        AT, AT, "lifecycle", "run", RUN, "",
        {"status": "completed", "processed_events": 123},
    )
    return project_journal_record(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="terminal",
    )


class FakePortfolio:
    run_id = RUN

    def __init__(self):
        self.calls = []

    def capture_recovery_snapshot(self, account_id, *, state_revision, snapshot_at):
        self.calls.append((account_id, state_revision, snapshot_at))
        return replace(captured(), run_id=RUN, account_id=account_id,
                       account_key=account_id, state_revision=state_revision,
                       snapshot_at=snapshot_at)


def test_terminal_capture_uses_one_event_revision_for_every_pinned_account():
    portfolio = FakePortfolio()
    batch = _batch()
    rows = capture_terminal_backtest_accounts(
        portfolio, batch, account_ids=("DU2", "DU1"))
    assert tuple(row.account_id for row in rows) == ("DU1", "DU2")
    assert all(row.state_revision == batch.last_sequence == 7 for row in rows)
    assert all(row.snapshot_at == AT for row in rows)
    assert portfolio.calls == [("DU1", 7, AT), ("DU2", 7, AT)]


def test_terminal_capture_rejects_missing_or_changed_identity():
    batch = _batch()
    portfolio = FakePortfolio()
    with pytest.raises(ValueError, match="pinned lifecycle/account set"):
        capture_terminal_backtest_accounts(portfolio, batch,
                                           account_ids=("DU1", "DU1"))
    portfolio.run_id = "other-run"
    with pytest.raises(ValueError, match="pinned lifecycle/account set"):
        capture_terminal_backtest_accounts(portfolio, batch,
                                           account_ids=("DU1",))


def test_terminal_readback_requires_each_account_on_same_prefix(monkeypatch):
    prefix = CommittedPrefix(RUN, 7, BATCH, "terminal", "completed", (BATCH,))
    seen = []

    def load(_client, actual, *, account_id):
        assert actual == prefix
        seen.append(account_id)
        if account_id == "DU2":
            raise RuntimeError("Backtest account lacks one terminal snapshot anchor")
        return {"account_id": account_id, "state_revision": 7}

    monkeypatch.setattr("src.backend.backtest_terminal_accounts.load_terminal_backtest_snapshot", load)
    with pytest.raises(RuntimeError, match="lacks one terminal snapshot anchor"):
        load_terminal_backtest_accounts(object(), prefix,
                                        account_ids=("DU2", "DU1"))
    assert seen == ["DU1", "DU2"]

    seen.clear()
    result = load_terminal_backtest_accounts(object(), prefix,
                                             account_ids=("DU1",))
    assert result == {"DU1": {"account_id": "DU1", "state_revision": 7}}
    assert seen == ["DU1"]
