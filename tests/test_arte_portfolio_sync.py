from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
from datetime import date
from dataclasses import replace
from dataclasses import asdict

import pytest

from src.trading_runtime import arte_portfolio_sync as sync
from src.trading_runtime.arte_journal_projection import project_portfolio_reconciliation_records
from src.trading_runtime.arte_journal_writer import _wire_row, typed_row
from tests.test_arte_admission_fence import RUN, captured
from tests.test_arte_admission_fence import ATTEMPT, BATCH, ZERO
from src.trading_runtime.portfolio import PortfolioReconciliationDifference


class Writer:
    def __init__(self):
        self.future = Future()

    def submit_portfolio_sync(self, batch, image):
        assert image.run_id == RUN and image.account_id == "DU1"
        assert len(batch.portfolio_reconciliation_events) == 1
        return self.future


def identity(*_args):
    return dict(run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
                batch_id=BATCH, prior_batch_id=ZERO, first_sequence=1,
                source_cursor="portfolio-sync-1")


def records():
    return (("portfolio_reconciliation", "primary", "DU1", {
        "event": "portfolio_reconciliation_completed",
        "snapshot_id": "broker-snapshot", "difference_count": 0,
        "differences": [],
    }),)


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
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda _client, **_identity: {
        "snapshot_hash": "a" * 64, "state_revision": 1, "batch_id": BATCH})
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=Keeper(),
        next_revision=lambda *_args: 1, batch_identity=identity)

    async def run():
        async with authority.claim(RUN, "DU1") as lease:
            task = asyncio.create_task(authority.publish(records(), captured(), lease))
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
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda _client, **_identity: {
        "snapshot_hash": "b" * 64, "state_revision": 1, "batch_id": BATCH})
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=Keeper(),
        next_revision=lambda *_args: 1, batch_identity=identity)
    with pytest.raises(RuntimeError, match="differs from committed state"):
        asyncio.run(authority.publish(records(), captured(), "lease"))


def test_sync_projection_rejects_dropped_or_tampered_reconciliation() -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    assert len(batch.events) == len(batch.portfolio_reconciliation_events) == 1
    with pytest.raises(ValueError, match="exactly one"):
        project_portfolio_reconciliation_records((), captured=image, **identity())
    changed = (("portfolio_reconciliation", "primary", "DU1", {
        **records()[0][3], "difference_count": 1}),)
    with pytest.raises(ValueError, match="differs"):
        project_portfolio_reconciliation_records(changed, captured=image, **identity())
    with pytest.raises(ValueError, match="exactly one"):
        project_portfolio_reconciliation_records(records() * 2, captured=image, **identity())


def test_sync_projection_binds_nonempty_difference_set() -> None:
    from tests.test_arte_admission_fence import AT
    difference = PortfolioReconciliationDifference("primary", "AAA", 2.0, 0.0, 2.0, AT)
    image = replace(captured(), reconciliation=(difference,))
    fact = (("portfolio_reconciliation", "primary", "DU1", {
        "event": "portfolio_reconciliation_completed",
        "snapshot_id": "broker-snapshot", "difference_count": 1,
        "differences": [asdict(difference)],
    }),)
    batch = project_portfolio_reconciliation_records(fact, captured=image, **identity())
    assert batch.portfolio_reconciliation_events[0]["difference_count"] == 1
    assert batch.portfolio_reconciliation_events[0]["difference_hash"] != sync.portfolio_reconciliation_hash(captured())
    changed = ((fact[0][0], fact[0][1], fact[0][2], {
        **fact[0][3], "differences": [{**asdict(difference), "broker_quantity": 3.0}]}),)
    with pytest.raises(ValueError, match="differs"):
        project_portfolio_reconciliation_records(changed, captured=image, **identity())


def test_combined_sync_writer_fences_after_event_and_snapshot(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    order = []
    fence = {}
    monkeypatch.setattr(sync, "publish_typed_batch", lambda _client, _batch:
                        order.append("event") or BATCH)
    monkeypatch.setattr(sync, "prepare_captured_portfolio_snapshot", lambda value: value)
    monkeypatch.setattr(sync, "publish_prepared_portfolio_snapshot", lambda _client, _image:
                        order.append("snapshot") or "a" * 64)
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda _client, **_identity:
                        fence.get("row"))
    def insert(_client, name, rows, _token):
        assert name == "trading_portfolio_sync_fence_v1"
        order.append("fence")
        fence["row"] = sync._canonical_typed_content(
            name, {key: value for key, value in rows[0].items()
                   if key != "content_hash"})
    monkeypatch.setattr(sync, "_insert", insert)
    assert sync.publish_fenced_portfolio_sync(object(), batch, image) == "a" * 64
    assert order == ["event", "snapshot", "fence"]


def test_combined_sync_writer_rejects_event_difference_tamper(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    tampered = replace(batch, portfolio_reconciliation_events=({
        **batch.portfolio_reconciliation_events[0], "difference_hash": "b" * 64},))
    monkeypatch.setattr(sync, "publish_typed_batch", lambda *_args:
                        pytest.fail("tampered event reached persistence"))
    with pytest.raises(ValueError, match="does not bind"):
        sync.publish_fenced_portfolio_sync(object(), tampered, image)


def test_sync_fence_cold_read_rejects_tampered_event_or_snapshot(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    digest = "a" * 64
    fence = _wire_row(sync._FENCE, sync._fence(batch, image, digest))
    detail = _wire_row("trading_portfolio_reconciliation_event_v1",
                       typed_row("trading_portfolio_reconciliation_event_v1",
                                 batch.portfolio_reconciliation_events[0]))
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    commit = {"attempt_id": ATTEMPT, "first_sequence": 1,
              "last_sequence": 1, "portfolio_reconciliation_event_count": 1,
              "portfolio_reconciliation_event_hash": sha256(
                  canonical_json(sync._identity([detail])).encode()).hexdigest()}
    actual = {"detail": detail, "snapshot_hash": digest}

    def rows(_client, sql):
        if "FROM arte.trading_portfolio_sync_fence_v1" in sql:
            return [fence]
        if "FROM arte.trading_commit_v1" in sql:
            return [commit]
        if "FROM arte.trading_portfolio_reconciliation_event_v1" in sql:
            return [actual["detail"]]
        raise AssertionError(sql)

    monkeypatch.setattr(sync, "_rows", rows)
    monkeypatch.setattr(sync, "load_portfolio_snapshot", lambda *_args, **_kwargs: {
        "state_hash": actual["snapshot_hash"],
        "state": {"snapshot_id": "broker-snapshot", "account_key": "primary",
                  "reconciliation": []},
    })
    assert sync.load_fenced_portfolio_sync(
        object(), run_id=RUN, account_id="DU1", state_revision=1)
    actual["detail"] = {**detail, "difference_hash": "b" * 64}
    with pytest.raises(RuntimeError, match="differs from reconciliation detail"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)
    actual["detail"] = detail
    actual["snapshot_hash"] = "b" * 64
    with pytest.raises(RuntimeError, match="differs from recovery snapshot"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)
