from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager

import pytest

from src.trading_runtime import arte_portfolio_sync as sync
from tests.test_arte_admission_fence import RUN, captured


class Writer:
    def __init__(self):
        self.future = Future()

    def submit_captured_portfolio_snapshot(self, image):
        assert image.run_id == RUN and image.account_id == "DU1"
        return self.future


class Keeper:
    current = True

    @asynccontextmanager
    async def claim_portfolio_snapshot(self, run_id, account_id):
        assert (run_id, account_id) == (RUN, "DU1")
        yield "lease"

    def portfolio_snapshot_claim_is_current(self, lease):
        assert lease == "lease"
        return self.current


def test_sync_receipt_requires_writer_then_cold_verified_snapshot(monkeypatch) -> None:
    writer = Writer()
    monkeypatch.setattr(sync, "load_portfolio_snapshot", lambda _client, **_identity: {
        "state_hash": "a" * 64, "state_revision": 1})
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=Keeper(),
        next_revision=lambda *_args: 1)

    async def run():
        async with authority.claim(RUN, "DU1") as lease:
            task = asyncio.create_task(authority.publish(captured(), lease))
            await asyncio.sleep(0)
            assert not task.done()
            writer.future.set_result("a" * 64)
            return await task

    receipt = asyncio.run(run())
    assert receipt.snapshot_hash == "a" * 64
    assert receipt.state_revision == 1


def test_sync_receipt_rejects_mismatched_readback(monkeypatch) -> None:
    writer = Writer()
    writer.future.set_result("a" * 64)
    monkeypatch.setattr(sync, "load_portfolio_snapshot", lambda _client, **_identity: {
        "state_hash": "b" * 64, "state_revision": 1})
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=Keeper(),
        next_revision=lambda *_args: 1)
    with pytest.raises(RuntimeError, match="differs from committed state"):
        asyncio.run(authority.publish(captured(), "lease"))
