import asyncio
from dataclasses import replace
from threading import Event

import pytest

from src.backend import program_financial_recovery as module
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.portfolio import PortfolioSyncState
from tests.test_arte_portfolio_recovery import _client, AT


@pytest.mark.parametrize("movement", [None, "before", "after"])
def test_program_financial_read_uses_normalized_snapshot_and_closes(monkeypatch, movement):
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 7, "batch", "cursor", "running", ("batch",))
    calls = []
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True), raising=False)

    def head(reader, run_id):
        assert reader is client and run_id == prefix.run_id
        calls.append(True)
        changed = movement == "before" or (movement == "after" and len(calls) == 2)
        return replace(prefix, last_sequence=8) if changed else prefix

    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    async def run():
        return await module.recover_program_portfolio_async(lambda: client, prefix,
            profiles=(profile,), cutoff_at=AT)
    if movement:
        with pytest.raises(ValueError, match="prefix changed"):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert result.revisions == {"account-id": 7}
        assert result.states["account-id"].sync_state is PortfolioSyncState.ENTRIES_BLOCKED
        assert result.last_filled_by_reservation == {"reservation-1": 2}
    assert closed == [True]


def test_cancelled_program_financial_read_drains_owned_client(monkeypatch):
    client, profile = _client(monkeypatch)
    profile = replace(profile, mode="backtest")
    prefix = V4CommittedPrefix("live-run", 7, "batch", "cursor", "running", ("batch",))
    entered, release, closed = Event(), Event(), Event()
    monkeypatch.setattr(client, "close", closed.set, raising=False)
    def head(reader, run_id):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Synthetic reader was not released")
        return prefix
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    async def run():
        task = asyncio.create_task(module.recover_program_portfolio_async(lambda: client,
            prefix, profiles=(profile,), cutoff_at=AT))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not closed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    asyncio.run(run())
