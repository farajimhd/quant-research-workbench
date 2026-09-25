from __future__ import annotations

import asyncio
import re
from concurrent.futures import Future
from contextlib import asynccontextmanager
from datetime import date
from dataclasses import replace
from dataclasses import asdict
from threading import Event

import pytest

from src.trading_runtime import arte_portfolio_sync as sync
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_projection import project_portfolio_reconciliation_records
from src.trading_runtime.arte_journal_writer import _wire_row, typed_row
from tests.test_arte_admission_fence import RUN, captured
from tests.test_arte_admission_fence import ATTEMPT, BATCH, ZERO
from src.trading_runtime.portfolio import PortfolioReconciliationDifference


class Writer:
    def __init__(self):
        self.future = Future()
        self.submitted = Event()

    def submit_portfolio_sync(self, batch, image):
        assert image.run_id == RUN and image.account_id == "DU1"
        assert len(batch.portfolio_reconciliation_events) == 1
        self.submitted.set()
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

    def __init__(self):
        self.receipt = None

    @asynccontextmanager
    async def claim_portfolio_snapshot(self, run_id, account_id):
        assert (run_id, account_id) == (RUN, "DU1")
        yield "lease"

    def portfolio_snapshot_claim_is_current(self, lease):
        assert lease == "lease"
        return self.current

    def attest_portfolio_snapshot_receipt(
        self, lease, run_id, account_id, revision, batch_id, snapshot_hash,
        *, marker_hash=None, fence_hash=None,
    ):
        if not self.portfolio_snapshot_claim_is_current(lease):
            raise RuntimeError("Keeper CAS rejected stale owner")
        proof = sync.KeeperSyncAttestation(
            run_id, account_id, revision, batch_id, snapshot_hash, "owner-a", 7,
            marker_hash, fence_hash)
        if self.receipt is not None and self.receipt != proof:
            raise RuntimeError("Keeper CAS receipt conflicts")
        self.receipt = proof
        return proof

    def load_portfolio_snapshot_receipt(self, run_id, account_id, revision):
        return self.receipt


def test_v2_sync_cold_audit_binds_marker_fence_and_enumerable_head(monkeypatch) -> None:
    from hashlib import sha256
    from test_keeper_ownership import _Client, _Store
    from src.trading_runtime.journal_contract import canonical_json
    from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator

    run, account, revision = "run-v2", "DU1", 1
    marker = {"batch_id": BATCH, "snapshot_id": "broker-snapshot"}
    fence = {"batch_id": BATCH, "snapshot_hash": "a" * 64}
    coordinator = KeeperOwnershipCoordinator(_Client(_Store(), 11))
    def attest():
        async def work():
            async with coordinator.claim_portfolio_snapshot(run, account) as lease:
                coordinator.attest_portfolio_snapshot_receipt(
                    lease, run, account, revision, BATCH, fence["snapshot_hash"],
                    marker_hash=sha256(canonical_json(marker).encode()).hexdigest(),
                    fence_hash=sha256(canonical_json(fence).encode()).hexdigest())
        asyncio.run(work())
    attest()
    monkeypatch.setattr(sync, "_stored_marker",
                        lambda *_args: dict(marker))
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync",
                        lambda *_args, **_kwargs: dict(fence))
    monkeypatch.setattr(sync, "load_attested_portfolio_sync",
                        lambda *_args, **_kwargs: dict(fence))
    def rows(_client, query):
        if "SELECT account_id,state_revision" not in query:
            raise AssertionError(query)
        return [] if "AND (account_id,state_revision)" in query else [
            {"account_id": account, "state_revision": revision}]
    monkeypatch.setattr(sync, "_rows", rows)
    class Barrier:
        def assert_fenced(self, run_id):
            assert run_id == run
    assert sync.audit_attested_portfolio_sync_transitions(
        object(), coordinator, run, quiescence=Barrier(), page_size=1) == 1
    marker["snapshot_id"] = "tampered"
    with pytest.raises(RuntimeError, match="matching V2 Keeper proof"):
        sync.audit_attested_portfolio_sync_transitions(
            object(), coordinator, run, quiescence=Barrier(), page_size=1)
    marker["snapshot_id"] = "broker-snapshot"
    async def orphan():
        async with coordinator.claim_portfolio_snapshot(run, account) as lease:
            coordinator.attest_portfolio_snapshot_receipt(
                lease, run, account, 2, BATCH, fence["snapshot_hash"],
                marker_hash="b" * 64, fence_hash="c" * 64)
    asyncio.run(orphan())
    with pytest.raises(RuntimeError, match="run proof head differs"):
        sync.audit_attested_portfolio_sync_transitions(
            object(), coordinator, run, quiescence=Barrier(), page_size=1)


def test_sync_receipt_requires_writer_then_cold_verified_snapshot(monkeypatch) -> None:
    writer = Writer()
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda _client, **_identity: {
        "snapshot_hash": "a" * 64, "state_revision": 1, "batch_id": BATCH})
    monkeypatch.setattr(sync, "_transition_hashes", lambda *_args:
                        ("b" * 64, "c" * 64, {"snapshot_hash": "a" * 64,
                         "state_revision": 1, "batch_id": BATCH}))
    monkeypatch.setattr(sync, "load_attested_portfolio_sync_transition",
                        lambda *_args, **_kwargs: {"snapshot_hash": "a" * 64})
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
    assert authority._keeper.receipt.marker_hash == "b" * 64
    assert authority._keeper.receipt.fence_hash == "c" * 64


def test_sync_cold_readback_does_not_block_event_loop(monkeypatch) -> None:
    writer = Writer()
    writer.future.set_result("a" * 64)
    entered = Event()
    release = Event()

    def slow_readback(_client, **_identity):
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test readback was not released")
        return {"snapshot_hash": "a" * 64, "state_revision": 1,
                "batch_id": BATCH}

    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", slow_readback)
    monkeypatch.setattr(sync, "_transition_hashes", lambda *_args:
                        ("b" * 64, "c" * 64, {"snapshot_hash": "a" * 64,
                         "state_revision": 1, "batch_id": BATCH}))
    monkeypatch.setattr(sync, "load_attested_portfolio_sync_transition",
                        lambda *_args, **_kwargs: {"snapshot_hash": "a" * 64})
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=Keeper(),
        next_revision=lambda *_args: 1, batch_identity=identity)

    async def run():
        task = asyncio.create_task(authority.publish(records(), captured(), "lease"))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
            assert not task.done()
        finally:
            release.set()
        return await task

    assert asyncio.run(run()).snapshot_hash == "a" * 64


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


def test_sync_lost_claim_during_identity_never_enters_writer() -> None:
    keeper = Keeper()
    class RejectWriter:
        def submit_portfolio_sync(self, *_args):
            pytest.fail("stale claim reached the writer")
    def losing_identity(*args):
        keeper.current = False
        return identity(*args)
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=RejectWriter(), keeper=keeper,
        next_revision=lambda *_args: 1, batch_identity=losing_identity)
    with pytest.raises(RuntimeError, match="lost its claim before enqueue"):
        asyncio.run(authority.publish(records(), captured(), "lease"))


def test_sync_lost_claim_while_writer_runs_never_returns_receipt(monkeypatch) -> None:
    keeper = Keeper()
    writer = Writer()
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda *_args, **_kwargs:
                        pytest.fail("stale claim read a release receipt"))
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=keeper,
        next_revision=lambda *_args: 1, batch_identity=identity)
    async def run():
        task = asyncio.create_task(authority.publish(records(), captured(), "lease"))
        assert await asyncio.to_thread(writer.submitted.wait, 1)
        keeper.current = False
        writer.future.set_result("a" * 64)
        await task
    with pytest.raises(RuntimeError, match="lost its claim while publishing"):
        asyncio.run(run())


def test_cold_sync_requires_keeper_attestation_not_just_ch_fence(monkeypatch) -> None:
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda *_args, **_kwargs: {
        "run_id": RUN, "account_id": "DU1", "state_revision": 1,
        "batch_id": BATCH, "snapshot_hash": "a" * 64})
    keeper = Keeper()
    with pytest.raises(RuntimeError, match="no matching Keeper CAS attestation"):
        sync.load_attested_portfolio_sync(
            object(), keeper, run_id=RUN, account_id="DU1", state_revision=1)
    keeper.attest_portfolio_snapshot_receipt(
        "lease", RUN, "DU1", 1, BATCH, "a" * 64)
    # A historical proof remains valid after the owner loses its live lease;
    # it does not by itself authorize present-tense broker admission.
    keeper.current = False
    assert sync.load_attested_portfolio_sync(
        object(), keeper, run_id=RUN, account_id="DU1", state_revision=1)
    keeper.receipt = replace(keeper.receipt, batch_id=ZERO)
    with pytest.raises(RuntimeError, match="no matching Keeper CAS attestation"):
        sync.load_attested_portfolio_sync(
            object(), keeper, run_id=RUN, account_id="DU1", state_revision=1)


def test_stale_owner_cannot_attest_late_ch_fence(monkeypatch) -> None:
    keeper = Keeper()
    writer = Writer()
    def read_fence(_client, **_identity):
        keeper.current = False
        return {"snapshot_hash": "a" * 64, "state_revision": 1, "batch_id": BATCH}
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", read_fence)
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=keeper,
        next_revision=lambda *_args: 1, batch_identity=identity)
    writer.future.set_result("a" * 64)
    with pytest.raises(RuntimeError, match="differs from committed state"):
        asyncio.run(authority.publish(records(), captured(), "lease"))
    assert keeper.receipt is None
    with pytest.raises(RuntimeError, match="no matching Keeper CAS attestation"):
        sync.load_attested_portfolio_sync(
            object(), keeper, run_id=RUN, account_id="DU1", state_revision=1)


def test_keeper_cas_rejects_lease_loss_after_ch_readback(monkeypatch) -> None:
    keeper = Keeper()
    writer = Writer()
    writer.future.set_result("a" * 64)
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda *_args, **_kwargs: {
        "snapshot_hash": "a" * 64, "state_revision": 1, "batch_id": BATCH})
    monkeypatch.setattr(sync, "_transition_hashes", lambda *_args:
                        ("b" * 64, "c" * 64, {"snapshot_hash": "a" * 64,
                         "state_revision": 1, "batch_id": BATCH}))
    def expire_before_cas(*args, **kwargs):
        keeper.current = False
        raise RuntimeError("Keeper CAS rejected stale owner")
    keeper.attest_portfolio_snapshot_receipt = expire_before_cas
    authority = sync.TypedPortfolioSyncAuthority(
        client=object(), writer=writer, keeper=keeper,
        next_revision=lambda *_args: 1, batch_identity=identity)
    with pytest.raises(RuntimeError, match="Keeper CAS rejected stale owner"):
        asyncio.run(authority.publish(records(), captured(), "lease"))
    assert keeper.receipt is None


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
    monkeypatch.setattr(sync, "_publish_marker", lambda _client, _marker:
                        order.append("marker"))
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
    assert order == ["marker", "event", "snapshot", "fence"]


def test_combined_sync_writer_rejects_event_difference_tamper(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    tampered = replace(batch, portfolio_reconciliation_events=({
        **batch.portfolio_reconciliation_events[0], "difference_hash": "b" * 64},))
    monkeypatch.setattr(sync, "publish_typed_batch", lambda *_args:
                        pytest.fail("tampered event reached persistence"))
    monkeypatch.setattr(sync, "_publish_marker", lambda *_args:
                        pytest.fail("tampered event reached marker publication"))
    with pytest.raises(ValueError, match="does not bind"):
        sync.publish_fenced_portfolio_sync(object(), tampered, image)


def test_sync_snapshot_marker_is_idempotent_and_rejects_conflict(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    stored = []
    monkeypatch.setattr(sync, "_rows", lambda _client, _sql: list(stored))
    def insert(_client, name, rows, _token):
        assert name == sync._MARKER
        stored.extend(_wire_row(name, row) for row in rows)
    monkeypatch.setattr(sync, "_insert", insert)
    marker = sync._marker(batch, image)
    sync._publish_marker(object(), marker)
    sync._publish_marker(object(), marker)
    assert len(stored) == 1
    changed = sync._marker(batch, replace(image, broker_snapshot_id="other-snapshot"))
    with pytest.raises(RuntimeError, match="revision conflicts"):
        sync._publish_marker(object(), changed)


def test_sync_fence_cold_read_rejects_tampered_event_or_snapshot(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    digest = "a" * 64
    fence = _wire_row(sync._FENCE, sync._fence(batch, image, digest))
    detail = _wire_row("trading_portfolio_reconciliation_event_v1",
                       typed_row("trading_portfolio_reconciliation_event_v1",
                                 batch.portfolio_reconciliation_events[0]))
    event = _wire_row("trading_event_v1",
                      typed_row("trading_event_v1", batch.events[0]))
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    commit = {"attempt_id": ATTEMPT, "first_sequence": 1,
              "last_sequence": 1, "event_count": 1,
              "event_hash": sha256(
                  canonical_json(sync._identity([event])).encode()).hexdigest(),
              "portfolio_reconciliation_event_count": 1,
              "portfolio_reconciliation_event_hash": sha256(
                  canonical_json(sync._identity([detail])).encode()).hexdigest()}
    actual = {"event": event, "detail": detail, "snapshot_hash": digest}

    def rows(_client, sql):
        if "FROM arte.trading_portfolio_sync_fence_v1" in sql:
            return [fence]
        if "FROM arte.trading_commit_v1" in sql:
            return [commit]
        if "FROM arte.trading_event_v1" in sql:
            return [] if actual["event"] is None else [actual["event"]]
        if "FROM arte.trading_portfolio_reconciliation_event_v1" in sql:
            return [actual["detail"]]
        if "FROM arte.trading_portfolio_sync_snapshot_marker_v1" in sql:
            return []  # Previously committed sync receipts remain compatible.
        raise AssertionError(sql)

    monkeypatch.setattr(sync, "_rows", rows)
    monkeypatch.setattr(sync, "load_portfolio_snapshot", lambda *_args, **_kwargs: {
        "state_hash": actual["snapshot_hash"],
        "state": {"snapshot_id": "broker-snapshot", "account_key": "primary",
                  "reconciliation": []},
    })
    assert sync.load_fenced_portfolio_sync(
        object(), run_id=RUN, account_id="DU1", state_revision=1)
    assert sync.load_fenced_portfolio_sync(
        object(), run_id=RUN, account_id="DU1", state_revision=1,
        _provisional_fence=sync._fence(batch, image, digest))
    actual["event"] = None
    with pytest.raises(RuntimeError, match="lacks its causal journal event"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)
    actual["event"] = {**event, "entity_id": "foreign-account"}
    with pytest.raises(RuntimeError, match="causal journal event is not sealed"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)
    actual["event"] = _wire_row("trading_event_v1", typed_row("trading_event_v1", {
        **batch.events[0], "record_id": "00000000-0000-0000-0000-000000000099"}))
    commit["event_hash"] = sha256(
        canonical_json(sync._identity([actual["event"]])).encode()).hexdigest()
    with pytest.raises(RuntimeError, match="reconciliation detail is not sealed"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)
    actual["event"] = event
    commit["event_hash"] = sha256(
        canonical_json(sync._identity([event])).encode()).hexdigest()
    actual["detail"] = {**detail, "difference_hash": "b" * 64}
    with pytest.raises(RuntimeError, match="differs from reconciliation detail"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)
    actual["detail"] = detail
    actual["snapshot_hash"] = "b" * 64
    with pytest.raises(RuntimeError, match="differs from recovery snapshot"):
        sync.load_fenced_portfolio_sync(
            object(), run_id=RUN, account_id="DU1", state_revision=1)


def test_startup_sync_audit_keysets_all_events_and_fences(monkeypatch) -> None:
    batch_ids = [f"00000000-0000-0000-0000-{number:012x}"
                 for number in range(1, 261)]
    loaded = []

    def rows(_client, sql):
        if "GROUP BY batch_id" in sql:
            cursor = re.search(r"batch_id>toUUID\('([^']+)'\)", sql)
            available = [batch_id for batch_id in batch_ids
                         if cursor is None or batch_id > cursor.group(1)]
            return [dict(batch_id=batch_id, row_count=1,
                         account_id="DU1", max_account_id="DU1")
                    for batch_id in available[:256]]
        if "FROM arte.trading_portfolio_sync_fence_v1" in sql:
            return [{"account_id": "DU1", "state_revision": 1}]
        raise AssertionError(sql)

    monkeypatch.setattr(sync, "_rows", rows)
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync",
                        lambda _client, **_identity: loaded.append(_identity) or {
                            "batch_id": batch_ids[(len(loaded) - 1) % len(batch_ids)]})
    sync.verify_no_incomplete_portfolio_syncs(object(), RUN)
    assert len(loaded) == 780
    assert loaded[0]["run_id"] == RUN


@pytest.mark.parametrize("failure", ["orphan_event", "orphan_fence", "orphan_marker", "duplicate"])
def test_startup_sync_audit_rejects_partial_or_duplicate_transitions(
    monkeypatch, failure,
) -> None:
    def rows(_client, sql):
        if "GROUP BY batch_id" in sql:
            event_source = "FROM arte.trading_event_v1" in sql
            if failure in {"orphan_fence", "orphan_marker"} and event_source:
                return []
            if failure == "orphan_marker" and "FROM arte.trading_portfolio_sync_fence_v1" in sql:
                return []
            if failure == "orphan_fence" and "FROM arte.trading_portfolio_sync_snapshot_marker_v1" in sql:
                return []
            return [dict(batch_id=BATCH,
                         row_count=2 if failure == "duplicate" else 1,
                         account_id="DU1", max_account_id="DU1")]
        if "FROM arte.trading_portfolio_sync_fence_v1" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(sync, "_rows", rows)
    with pytest.raises(RuntimeError, match="duplicate|lacks one matching"):
        sync.verify_no_incomplete_portfolio_syncs(object(), RUN)


def test_live_writer_startup_runs_sync_audit(monkeypatch) -> None:
    from src.trading_runtime import arte_admission_fence as admission
    checked = []
    monkeypatch.setattr(writer_module, "_rows", lambda *_args: [{"run_id": RUN}])
    monkeypatch.setattr(writer_module, "load_typed_run_context",
                        lambda *_args: {"mode": "live"})
    monkeypatch.setattr(admission, "verify_no_incomplete_admissions",
                        lambda *_args: checked.append("admission"))
    monkeypatch.setattr(sync, "verify_no_incomplete_portfolio_syncs",
                        lambda *_args: checked.append("sync"))
    writer_module._verify_run_identity(object(), RUN)
    assert checked == ["admission", "sync"]


def test_cold_sync_resume_seals_only_complete_committed_sources(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    marker = sync._canonical_typed_content(
        sync._MARKER, {key: value for key, value in sync._marker(batch, image).items()
                       if key != "content_hash"})
    monkeypatch.setattr(sync, "_stored_marker", lambda *_args: marker)
    monkeypatch.setattr(sync, "load_committed_prefix", lambda *_args: object())
    monkeypatch.setattr(sync, "load_portfolio_snapshot", lambda *_args, **_kwargs: {
        "state_hash": "a" * 64})
    def rows(_client, sql):
        if "FROM arte.trading_commit_v1" in sql:
            return [{"attempt_id": ATTEMPT, "first_sequence": 1, "last_sequence": 1}]
        if "FROM arte.trading_portfolio_reconciliation_event_v1" in sql:
            return [{"difference_hash": sync.portfolio_reconciliation_hash(image),
                     "snapshot_id": image.broker_snapshot_id}]
        raise AssertionError(sql)
    monkeypatch.setattr(sync, "_rows", rows)
    published = {}
    keeper = Keeper()
    def load(_client, **kwargs):
        provisional = kwargs.get("_provisional_fence")
        if provisional is not None:
            assert provisional["batch_id"] == BATCH
            assert provisional["snapshot_hash"] == "a" * 64
            return {"batch_id": BATCH, "snapshot_hash": "a" * 64}
        return published.get("fence")
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", load)
    def insert(_client, name, rows, _token):
        assert name == sync._FENCE
        published["fence"] = {"batch_id": rows[0]["batch_id"],
                              "snapshot_hash": rows[0]["snapshot_hash"]}
    monkeypatch.setattr(sync, "_insert", insert)
    result = asyncio.run(sync.resume_complete_portfolio_sync(
        object(), keeper, run_id=RUN, account_id="DU1", state_revision=1))
    assert result["batch_id"] == BATCH
    assert keeper.receipt is not None
    assert sync.load_attested_portfolio_sync(
        object(), keeper, run_id=RUN, account_id="DU1", state_revision=1) == result


@pytest.mark.parametrize("proof_state", ["missing", "matching", "conflicting", "legacy"])
def test_cold_sync_resume_existing_fence_requires_exact_keeper_proof(
    monkeypatch, proof_state,
) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    marker = sync._canonical_typed_content(
        sync._MARKER, {key: value for key, value in sync._marker(batch, image).items()
                       if key != "content_hash"})
    keeper = Keeper()
    fence = {"batch_id": BATCH, "snapshot_hash": "a" * 64}
    if proof_state != "missing":
        from hashlib import sha256
        from src.trading_runtime.journal_contract import canonical_json
        marker_hash = sha256(canonical_json(marker).encode()).hexdigest()
        fence_hash = sha256(canonical_json(fence).encode()).hexdigest()
        keeper.receipt = sync.KeeperSyncAttestation(
            RUN, "DU1", 1, BATCH,
            "b" * 64 if proof_state == "conflicting" else "a" * 64,
            "historical-owner", 3,
            None if proof_state == "legacy" else marker_hash,
            None if proof_state == "legacy" else fence_hash)
    monkeypatch.setattr(sync, "_stored_marker", lambda *_args: marker)
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda *_args, **_kwargs: fence)
    monkeypatch.setattr(sync, "_insert", lambda *_args:
                        pytest.fail("existing fence must not be republished"))
    if proof_state != "missing":
        monkeypatch.setattr(keeper, "attest_portfolio_snapshot_receipt", lambda *_args:
                            pytest.fail("existing proof must not be rewritten"))
    if proof_state in {"conflicting", "legacy"}:
        with pytest.raises(RuntimeError, match=(
                "no matching Keeper CAS attestation" if proof_state == "conflicting"
                else "matching V2 Keeper proof")):
            asyncio.run(sync.resume_complete_portfolio_sync(
                object(), keeper, run_id=RUN, account_id="DU1", state_revision=1))
    else:
        assert asyncio.run(sync.resume_complete_portfolio_sync(
            object(), keeper, run_id=RUN, account_id="DU1",
            state_revision=1)) == fence
        assert keeper.receipt is not None


def test_cold_sync_resume_keeps_marker_only_transition_blocked(monkeypatch) -> None:
    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    marker = sync._canonical_typed_content(
        sync._MARKER, {key: value for key, value in sync._marker(batch, image).items()
                       if key != "content_hash"})
    monkeypatch.setattr(sync, "_stored_marker", lambda *_args: marker)
    monkeypatch.setattr(sync, "load_fenced_portfolio_sync", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sync, "load_committed_prefix", lambda *_args: None)
    monkeypatch.setattr(sync, "_insert", lambda *_args:
                        pytest.fail("marker-only repair cannot write a fence"))
    with pytest.raises(RuntimeError, match="committed event prefix"):
        asyncio.run(sync.resume_complete_portfolio_sync(
            object(), Keeper(), run_id=RUN, account_id="DU1", state_revision=1))


def test_cold_sync_resume_rejects_stale_keeper_claim_before_read(monkeypatch) -> None:
    keeper = Keeper()
    keeper.current = False
    monkeypatch.setattr(sync, "_stored_marker", lambda *_args:
                        pytest.fail("stale claim cannot inspect or repair marker"))
    with pytest.raises(RuntimeError, match="current Keeper claim"):
        asyncio.run(sync.resume_complete_portfolio_sync(
            object(), keeper, run_id=RUN, account_id="DU1", state_revision=1))


def test_cold_sync_resume_keeps_event_loop_responsive_during_keeper_check(monkeypatch) -> None:
    from threading import Event

    entered, release = Event(), Event()
    class BlockingKeeper(Keeper):
        def portfolio_snapshot_claim_is_current(self, lease):
            entered.set()
            assert release.wait(2)
            return super().portfolio_snapshot_claim_is_current(lease)

    monkeypatch.setattr(sync, "_stored_marker", lambda *_args: None)
    async def run():
        task = asyncio.create_task(sync.resume_complete_portfolio_sync(
            object(), BlockingKeeper(), run_id=RUN, account_id="DU1",
            state_revision=1))
        assert await asyncio.to_thread(entered.wait, 1)
        heartbeat = []
        asyncio.get_running_loop().call_soon(heartbeat.append, "responsive")
        await asyncio.sleep(0)
        assert heartbeat == ["responsive"] and not task.done()
        release.set()
        with pytest.raises(RuntimeError, match="prepared marker"):
            await task
    asyncio.run(run())
