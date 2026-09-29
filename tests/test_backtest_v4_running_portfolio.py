from datetime import date, timezone

import pytest

from src.backend import backtest_v4_running_portfolio as subject
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


RUN = "00000000-0000-0000-0000-000000000a01"
BATCH = "00000000-0000-0000-0000-000000000a02"
DAY = date(2026, 8, 18)
PREFIX = V4CommittedPrefix(RUN, 7, BATCH, "2026-08-18:300000",
                           "running", (BATCH,))


def _install(monkeypatch, *, snapshot=True, moved=False):
    reads = []

    def prefix(_client, _run_id):
        reads.append("prefix")
        return (V4CommittedPrefix(RUN, 8, BATCH, PREFIX.source_cursor,
                                  "running", (BATCH,))
                if moved and len(reads) > 2 else PREFIX)

    monkeypatch.setattr(subject, "load_verified_v4_prefix", prefix)
    monkeypatch.setattr(subject, "load_latest_backtest_cursor",
                        lambda _client, _prefix: {
                            "run_id": RUN, "event_sequence": 7,
                            "batch_id": BATCH, "session_date": DAY.isoformat(),
                            "boundary_ms": 300_000})

    def account(_client, *, run_id, account_id, state_revision):
        reads.append("account")
        if not snapshot:
            return None
        return {
            "state_revision": state_revision,
            "snapshot_at": market_day_boundary(DAY, 300_000)
            .astimezone(timezone.utc).isoformat(),
            "families": {"trading_portfolio_snapshot_v1": ({
                "run_id": run_id, "account_id": account_id,
                "state_revision": state_revision},)},
        }

    monkeypatch.setattr(subject, "load_portfolio_snapshot", account)
    return reads


def test_running_portfolio_cold_reader_joins_one_exact_cursor(monkeypatch):
    reads = _install(monkeypatch)
    prefix, images = subject.load_v4_running_portfolio_images(
        object(), run_id=RUN, account_ids=("DU1",))
    assert prefix == PREFIX
    assert tuple(images) == ("DU1",)
    assert reads == ["prefix", "account", "prefix"]


def test_running_portfolio_cold_reader_rejects_missing_image(monkeypatch):
    _install(monkeypatch, snapshot=False)
    with pytest.raises(RuntimeError, match="lacks exact account image"):
        subject.load_v4_running_portfolio_images(
            object(), run_id=RUN, account_ids=("DU1",))


def test_running_portfolio_cold_reader_rejects_moved_prefix(monkeypatch):
    _install(monkeypatch, moved=True)
    with pytest.raises(RuntimeError, match="prefix moved"):
        subject.load_v4_running_portfolio_images(
            object(), run_id=RUN, account_ids=("DU1",))
