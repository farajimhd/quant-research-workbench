"""Verified typed broker-sync snapshot receipt, separate from live cutover."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
from itertools import zip_longest
import re
from typing import Any, Callable, Mapping

from src.trading_runtime.arte_journal_projection import (
    portfolio_reconciliation_hash, project_portfolio_reconciliation_records,
)
from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, _canonical_typed_content, _identity, _insert, _literal, _rows,
    load_committed_prefix, publish_typed_batch, typed_row,
)

from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, load_portfolio_snapshot,
    prepare_captured_portfolio_snapshot, publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.journal_contract import canonical_json

_FENCE = "trading_portfolio_sync_fence_v1"
_MARKER = "trading_portfolio_sync_snapshot_marker_v1"


def _sync_dispatch(client: Any) -> Any | None:
    dispatch = getattr(client, "typed_sync_insert_dispatch", None)
    if getattr(client, "typed_insert_strict", False) and dispatch is None:
        raise RuntimeError("Strict portfolio sync lacks durable dispatch")
    return dispatch


def _marker(batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot) -> dict[str, Any]:
    return typed_row(_MARKER, {
        "run_id": captured.run_id,
        "sync_month": captured.snapshot_at.astimezone(timezone.utc).date().replace(day=1).isoformat(),
        "account_id": captured.account_id, "state_revision": captured.state_revision,
        "batch_id": batch.batch_id, "snapshot_id": captured.broker_snapshot_id,
        "captured_at": captured.snapshot_at.astimezone(timezone.utc).isoformat(),
    })


def _stored_marker(client: Any, run_id: str, account_id: str,
                   state_revision: int) -> dict[str, Any] | None:
    rows = _rows(client, "SELECT * FROM arte.trading_portfolio_sync_snapshot_marker_v1 "
                 f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
                 f"AND state_revision={int(state_revision)} FORMAT JSONEachRow")
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("Portfolio sync snapshot marker has duplicate revision")
    row = rows[0]
    content = _canonical_typed_content(
        _MARKER, {key: value for key, value in row.items() if key != "content_hash"},
        stored_utc=True)
    if sha256(canonical_json(content).encode("utf-8")).hexdigest() != row["content_hash"]:
        raise RuntimeError("Portfolio sync snapshot marker content hash differs")
    return content


async def resume_complete_portfolio_sync(
    client: Any, keeper: Any, *, run_id: str, account_id: str,
    state_revision: int,
) -> dict[str, Any]:
    """Finish only a fully durable event and snapshot; never abort missing facts.

    This cold-start repair holds the account's Keeper claim. A marker-only
    transition remains blocked for operator reconciliation because an old
    ClickHouse INSERT can still arrive after a negative read.
    """
    async with keeper.claim_portfolio_snapshot(run_id, account_id) as lease:
        current = keeper.portfolio_snapshot_claim_is_current
        async def attested(fence: Mapping[str, Any]) -> dict[str, Any]:
            if not await asyncio.to_thread(current, lease):
                raise RuntimeError("Portfolio sync resume lost its Keeper claim before CAS")
            proof = await asyncio.to_thread(
                keeper.load_portfolio_snapshot_receipt,
                run_id, account_id, state_revision)
            marker_hash, fence_hash, exact = await asyncio.to_thread(
                _transition_hashes, client, run_id, account_id, state_revision)
            if exact != fence:
                raise RuntimeError("Portfolio sync resume fence changed before attestation")
            if proof is None:
                await asyncio.to_thread(
                    keeper.attest_portfolio_snapshot_receipt,
                    lease, run_id, account_id, state_revision,
                    str(fence["batch_id"]), str(fence["snapshot_hash"]),
                    marker_hash=marker_hash, fence_hash=fence_hash)
            verified = await asyncio.to_thread(
                load_attested_portfolio_sync_transition, client, keeper,
                run_id=run_id, account_id=account_id,
                state_revision=state_revision)
            if (verified is None or verified["batch_id"] != fence["batch_id"]
                    or verified["snapshot_hash"] != fence["snapshot_hash"]
                    or not await asyncio.to_thread(current, lease)):
                raise RuntimeError("Portfolio sync resume lacks an attested receipt")
            dispatch = _sync_dispatch(client)
            if dispatch is not None:
                durable_proof = await asyncio.to_thread(
                    keeper.load_portfolio_snapshot_receipt,
                    run_id, account_id, state_revision)
                if not await asyncio.to_thread(
                        dispatch.is_latest_compacted, run_id, durable_proof):
                    for table, row_hash in ((_MARKER, marker_hash), (_FENCE, fence_hash)):
                        await asyncio.to_thread(dispatch.seal_readback,
                            run_id=run_id, account_id=account_id,
                            revision=state_revision, table=table, row_hash=row_hash)
                    await asyncio.to_thread(dispatch.compact,
                        run_id=run_id, account_id=account_id, revision=state_revision,
                        marker_hash=marker_hash, fence_hash=fence_hash,
                        proof=durable_proof)
            return verified

        if not await asyncio.to_thread(current, lease):
            raise RuntimeError("Portfolio sync resume lacks a current Keeper claim")
        marker = await asyncio.to_thread(
            _stored_marker, client, run_id, account_id, state_revision)
        if marker is None:
            raise RuntimeError("Portfolio sync resume lacks its prepared marker")
        existing = await asyncio.to_thread(
            load_fenced_portfolio_sync,
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision)
        if existing is not None:
            if existing["batch_id"] != marker["batch_id"]:
                raise RuntimeError("Portfolio sync resume fence differs from marker")
            return await attested(existing)
        batch_id = str(marker["batch_id"])
        prefix = await asyncio.to_thread(load_committed_prefix, client, run_id)
        if prefix is None:
            raise RuntimeError("Portfolio sync resume lacks a committed event prefix")
        commits = await asyncio.to_thread(_rows, client,
            "SELECT attempt_id,first_sequence,last_sequence FROM arte.trading_commit_v1 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
        details = await asyncio.to_thread(_rows, client,
            "SELECT difference_hash,snapshot_id FROM arte.trading_portfolio_reconciliation_event_v1 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
        snapshot = await asyncio.to_thread(
            load_portfolio_snapshot,
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision)
        if (len(commits) != 1 or len(details) != 1 or snapshot is None
                or details[0]["snapshot_id"] != marker["snapshot_id"]):
            raise RuntimeError("Portfolio sync resume requires complete committed facts")
        fence = typed_row(_FENCE, {
            "run_id": run_id, "sync_month": marker["sync_month"],
            "account_id": account_id, "state_revision": state_revision,
            "attempt_id": commits[0]["attempt_id"], "batch_id": batch_id,
            "first_sequence": int(commits[0]["first_sequence"]),
            "last_sequence": int(commits[0]["last_sequence"]),
            "snapshot_hash": snapshot["state_hash"],
            "difference_hash": details[0]["difference_hash"],
            "captured_at": marker["captured_at"].replace(" ", "T") + "+00:00",
        })
        # Verify the exact sealed event/detail and complete normalized snapshot
        # before publishing the missing late fence.
        await asyncio.to_thread(
            load_fenced_portfolio_sync,
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision, _provisional_fence=fence)
        if not await asyncio.to_thread(current, lease):
            raise RuntimeError("Portfolio sync resume lost its Keeper claim")
        dispatch = _sync_dispatch(client)
        if dispatch is not None:
            await asyncio.to_thread(dispatch.reserve, run_id, account_id, state_revision)
        await asyncio.to_thread(
            _insert, client, _FENCE, (fence,),
            f"portfolio-sync:{run_id}:{account_id}:{state_revision}",
            **({"dispatch_sync_account_id": account_id,
                "dispatch_sync_revision": state_revision} if dispatch is not None else {}))
        verified = await asyncio.to_thread(
            load_fenced_portfolio_sync,
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision)
        if (verified is None or verified["batch_id"] != batch_id
                or not await asyncio.to_thread(current, lease)):
            raise RuntimeError("Portfolio sync resume fence did not become durable")
        if dispatch is not None:
            await asyncio.to_thread(dispatch.seal_readback,
                run_id=run_id, account_id=account_id, revision=state_revision,
                table=_FENCE, row_hash=fence["content_hash"])
        return await attested(verified)


def _publish_marker(client: Any, row: Mapping[str, Any]) -> None:
    expected = _canonical_typed_content(
        _MARKER, {key: value for key, value in row.items() if key != "content_hash"})
    run_id, account_id = str(row["run_id"]), str(row["account_id"])
    revision = int(row["state_revision"])
    prior = _stored_marker(client, run_id, account_id, revision)
    if prior is not None:
        if prior != expected:
            raise RuntimeError("Portfolio sync snapshot marker revision conflicts")
        dispatch = _sync_dispatch(client)
        if dispatch is not None:
            dispatch.seal_readback(run_id=run_id, account_id=account_id,
                revision=revision, table=_MARKER, row_hash=row["content_hash"])
        return
    dispatch = _sync_dispatch(client)
    _insert(client, _MARKER, (row,),
            f"portfolio-sync:{run_id}:{account_id}:{revision}:marker",
            **({"dispatch_sync_account_id": account_id,
                "dispatch_sync_revision": revision} if dispatch is not None else {}))
    if _stored_marker(client, run_id, account_id, revision) != expected:
        raise RuntimeError("Portfolio sync snapshot marker did not become durable")
    if dispatch is not None:
        dispatch.seal_readback(run_id=run_id, account_id=account_id,
            revision=revision, table=_MARKER, row_hash=row["content_hash"])


def _fence(batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot,
           digest: str) -> dict[str, Any]:
    return typed_row(_FENCE, {
        "run_id": captured.run_id,
        "sync_month": captured.snapshot_at.astimezone(timezone.utc).date().replace(day=1).isoformat(),
        "account_id": captured.account_id, "state_revision": captured.state_revision,
        "attempt_id": batch.attempt_id, "batch_id": batch.batch_id,
        "first_sequence": batch.first_sequence, "last_sequence": batch.last_sequence,
        "snapshot_hash": digest,
        "difference_hash": portfolio_reconciliation_hash(captured),
        "captured_at": captured.snapshot_at.astimezone(timezone.utc).isoformat(),
    })


def load_fenced_portfolio_sync(client: Any, *, run_id: str, account_id: str,
                               state_revision: int,
                               _provisional_fence: Mapping[str, Any] | None = None,
                               ) -> dict[str, Any] | None:
    rows = ([dict(_provisional_fence)] if _provisional_fence is not None else _rows(
        client, "SELECT * FROM arte.trading_portfolio_sync_fence_v1 "
        f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
        f"AND state_revision={int(state_revision)} FORMAT JSONEachRow"))
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("Portfolio sync fence has duplicate revision")
    row = rows[0]
    content = _canonical_typed_content(
        _FENCE, {key: value for key, value in row.items() if key != "content_hash"},
        stored_utc=_provisional_fence is None)
    if sha256(canonical_json(content).encode("utf-8")).hexdigest() != str(row["content_hash"]):
        raise RuntimeError("Portfolio sync fence content hash differs")
    if (content["run_id"] != run_id or content["account_id"] != account_id
            or int(content["state_revision"]) != state_revision):
        raise RuntimeError("Portfolio sync fence identity differs from request")
    commits = _rows(client, "SELECT attempt_id,first_sequence,last_sequence,"
                    "event_count,event_hash,"
                    "portfolio_reconciliation_event_count,portfolio_reconciliation_event_hash "
                    "FROM arte.trading_commit_v1 "
                    f"WHERE run_id={_literal(run_id)} AND batch_id=toUUID({_literal(str(content['batch_id']))}) "
                    "FORMAT JSONEachRow")
    if (len(commits) != 1 or str(commits[0]["attempt_id"]) != str(content["attempt_id"])
            or int(commits[0]["first_sequence"]) != int(content["first_sequence"])
            or int(commits[0]["last_sequence"]) != int(content["last_sequence"])
            or int(commits[0]["event_count"]) != 1
            or int(content["first_sequence"]) != int(content["last_sequence"])
            or int(commits[0]["portfolio_reconciliation_event_count"]) != 1):
        raise RuntimeError("Portfolio sync fence lacks committed reconciliation event")
    events = _rows(client, "SELECT * FROM arte.trading_event_v1 "
                   f"WHERE run_id={_literal(run_id)} AND batch_id=toUUID({_literal(str(content['batch_id']))}) "
                   "FORMAT JSONEachRow")
    if len(events) != 1:
        raise RuntimeError("Portfolio sync fence lacks its causal journal event")
    event = events[0]
    canonical_event = _canonical_typed_content(
        "trading_event_v1",
        {key: value for key, value in event.items() if key != "content_hash"},
        stored_utc=True)
    if (sha256(canonical_json(canonical_event).encode("utf-8")).hexdigest()
            != event["content_hash"]
            or sha256(canonical_json(_identity(events)).encode("utf-8")).hexdigest()
            != commits[0]["event_hash"]
            or event["category"] != "portfolio_management"
            or event["entity_type"] != "portfolio_reconciliation"
            or event["account_id"] != account_id
            or str(event["attempt_id"]) != str(content["attempt_id"])
            or int(event["sequence"]) != int(content["first_sequence"])):
        raise RuntimeError("Portfolio sync causal journal event is not sealed")
    details = _rows(client, "SELECT * FROM arte.trading_portfolio_reconciliation_event_v1 "
                    f"WHERE run_id={_literal(run_id)} AND batch_id=toUUID({_literal(str(content['batch_id']))}) "
                    "FORMAT JSONEachRow")
    if len(details) != 1 or str(details[0]["difference_hash"]) != content["difference_hash"]:
        raise RuntimeError("Portfolio sync fence differs from reconciliation detail")
    detail = details[0]
    canonical_detail = _canonical_typed_content(
        "trading_portfolio_reconciliation_event_v1",
        {key: value for key, value in detail.items() if key != "content_hash"},
        stored_utc=True)
    if (sha256(canonical_json(canonical_detail).encode("utf-8")).hexdigest()
            != detail["content_hash"]
            or sha256(canonical_json(_identity(details)).encode("utf-8")).hexdigest()
            != commits[0]["portfolio_reconciliation_event_hash"]
            or detail["account_id"] != account_id
            or str(detail["record_id"]) != str(event["record_id"])
            or detail["account_key"] != event["entity_id"]
            or canonical_detail["source_event_time"] != canonical_event["event_time"]):
        raise RuntimeError("Portfolio sync reconciliation detail is not sealed")
    snapshot = load_portfolio_snapshot(
        client, run_id=run_id, account_id=account_id, state_revision=state_revision)
    if snapshot is None or snapshot["state_hash"] != content["snapshot_hash"]:
        raise RuntimeError("Portfolio sync fence differs from recovery snapshot")
    marker = _stored_marker(client, run_id, account_id, state_revision)
    if (marker is not None and (
            marker["batch_id"] != content["batch_id"]
            or marker["snapshot_id"] != snapshot["state"]["snapshot_id"]
            or marker["captured_at"] != content["captured_at"])):
        raise RuntimeError("Portfolio sync marker differs from fenced snapshot")
    if (snapshot["state"]["snapshot_id"] != detail["snapshot_id"]
            or snapshot["state"]["account_key"] != detail["account_key"]
            or len(snapshot["state"]["reconciliation"]) != int(detail["difference_count"])
            or sha256(canonical_json(sorted(snapshot["state"]["reconciliation"],
                                      key=lambda row: (row["account_key"], row["ticker"]))).encode("utf-8")).hexdigest()
            != detail["difference_hash"]):
        raise RuntimeError("Portfolio sync event differs from normalized recovery differences")
    return content


def verify_no_incomplete_portfolio_syncs(client: Any, run_id: str) -> None:
    """Audit every live sync event and fence without a whole-run result limit.

    An event committed before its late fence is not a completed transition.
    Scan both directions so duplicate and fence-only transitions fail too.
    This is a startup guard, not a replacement for holding the writer claim.
    """
    if not run_id:
        raise ValueError("Portfolio sync audit requires a run identity")
    for source in ("trading_event_v1", _FENCE, _MARKER):
        cursor: str | None = None
        while True:
            predicate = (f" AND batch_id>toUUID({_literal(cursor)})"
                         if cursor is not None else "")
            category = (" AND category='portfolio_management' "
                        "AND entity_type='portfolio_reconciliation'"
                        if source == "trading_event_v1" else "")
            page = _rows(client,
                "SELECT toString(batch_id) AS batch_id,count() AS row_count,"
                "min(account_id) AS account_id,max(account_id) AS max_account_id "
                f"FROM arte.{source} WHERE run_id={_literal(run_id)}"
                f"{category}{predicate} GROUP BY batch_id ORDER BY batch_id "
                "LIMIT 256 FORMAT JSONEachRow")
            if not page:
                break
            for row in page:
                if (int(row["row_count"]) != 1
                        or not row["account_id"]
                        or row["account_id"] != row["max_account_id"]):
                    raise RuntimeError("Portfolio sync has duplicate or mixed batch rows")
                batch_id = str(row["batch_id"])
                fences = _rows(client,
                    "SELECT account_id,state_revision FROM arte.trading_portfolio_sync_fence_v1 "
                    f"WHERE run_id={_literal(run_id)} "
                    f"AND batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
                if len(fences) != 1 or fences[0]["account_id"] != row["account_id"]:
                    raise RuntimeError("Portfolio sync event lacks one matching late fence")
                verified = load_fenced_portfolio_sync(
                    client, run_id=run_id, account_id=row["account_id"],
                    state_revision=int(fences[0]["state_revision"]))
                if verified is None or str(verified["batch_id"]) != batch_id:
                    raise RuntimeError("Portfolio sync late fence differs from its event")
            cursor = str(page[-1]["batch_id"])
            if len(page) < 256:
                break


def publish_fenced_portfolio_sync(
    client: Any, batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot,
) -> str:
    """Worker-only late fence: event, snapshot, then verified transition."""
    if (len(batch.portfolio_reconciliation_events) != 1 or len(batch.events) != 1
            or batch.events[0]["account_id"] != captured.account_id
            or batch.portfolio_reconciliation_events[0]["difference_hash"]
            != portfolio_reconciliation_hash(captured)):
        raise ValueError("Portfolio sync batch does not bind captured differences")
    dispatch = _sync_dispatch(client)
    if dispatch is not None:
        dispatch.reserve(captured.run_id, captured.account_id,
                         captured.state_revision)
    _publish_marker(client, _marker(batch, captured))
    if publish_typed_batch(client, batch) != batch.batch_id:
        raise RuntimeError("Portfolio sync event batch identity changed")
    digest = publish_prepared_portfolio_snapshot(
        client, prepare_captured_portfolio_snapshot(captured))
    fence = _fence(batch, captured, digest)
    prior = load_fenced_portfolio_sync(
        client, run_id=captured.run_id, account_id=captured.account_id,
        state_revision=captured.state_revision)
    if prior is not None:
        expected = _canonical_typed_content(
            _FENCE, {key: value for key, value in fence.items() if key != "content_hash"})
        if prior != expected:
            raise RuntimeError("Portfolio sync revision has conflicting fence")
        if dispatch is not None:
            dispatch.seal_readback(run_id=captured.run_id,
                account_id=captured.account_id, revision=captured.state_revision,
                table=_FENCE, row_hash=fence["content_hash"])
        return digest
    _insert(client, _FENCE, (fence,),
            f"portfolio-sync:{captured.run_id}:{captured.account_id}:{captured.state_revision}",
            **({"dispatch_sync_account_id": captured.account_id,
                "dispatch_sync_revision": captured.state_revision} if dispatch is not None else {}))
    verified = load_fenced_portfolio_sync(
        client, run_id=captured.run_id, account_id=captured.account_id,
        state_revision=captured.state_revision)
    if verified is None or verified["snapshot_hash"] != digest:
        raise RuntimeError("Portfolio sync fence did not become durable")
    if dispatch is not None:
        dispatch.seal_readback(run_id=captured.run_id,
            account_id=captured.account_id, revision=captured.state_revision,
            table=_FENCE, row_hash=fence["content_hash"])
    return digest


@dataclass(frozen=True, slots=True)
class PortfolioSyncReceipt:
    run_id: str
    account_id: str
    state_revision: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class KeeperSyncAttestation:
    """Persistent Keeper CAS proof; no portfolio or market data lives here.

    The Keeper adapter must create this only in one transaction that verifies
    the current holder owner+epoch. Historical attestations remain valid after
    a newer owner takes the account; current ownership is checked separately.
    """

    run_id: str
    account_id: str
    state_revision: int
    batch_id: str
    snapshot_hash: str
    owner_id: str
    epoch: int
    marker_hash: str | None = None
    fence_hash: str | None = None

    def wire(self) -> bytes:
        from src.trading_runtime.keeper_ownership import _sync_receipt_bytes
        return _sync_receipt_bytes(
            self.run_id, self.account_id, self.state_revision, self.batch_id,
            self.snapshot_hash, self.owner_id, self.epoch,
            marker_hash=self.marker_hash, fence_hash=self.fence_hash)


@dataclass(frozen=True, slots=True)
class KeeperSyncTransitionHead:
    run_id: str
    account_id: str | None
    proof_count: int
    proof_xor: str
    last_revision: int

    def wire(self) -> bytes:
        from src.trading_runtime.keeper_ownership import _identity
        _identity(self.run_id, "run")
        if self.account_id is not None:
            _identity(self.account_id, "account")
        if (type(self.proof_count) is not int or self.proof_count < 0
                or type(self.last_revision) is not int or self.last_revision < 0
                or re.fullmatch(r"[0-9a-f]{64}", self.proof_xor) is None):
            raise ValueError("Portfolio sync transition head is invalid")
        return (f"1\n{self.run_id}\n{self.account_id or ''}\n"
                f"{self.proof_count}\n{self.proof_xor}\n{self.last_revision}").encode()


def load_attested_portfolio_sync(
    client: Any, keeper: Any, *, run_id: str, account_id: str,
    state_revision: int,
) -> dict[str, Any] | None:
    """Cold admission read: a ClickHouse fence alone is not authority."""
    fence = load_fenced_portfolio_sync(
        client, run_id=run_id, account_id=account_id,
        state_revision=state_revision)
    if fence is None:
        return None
    proof = keeper.load_portfolio_snapshot_receipt(run_id, account_id, state_revision)
    if (not isinstance(proof, KeeperSyncAttestation)
            or proof.run_id != run_id or proof.account_id != account_id
            or proof.state_revision != state_revision
            or proof.batch_id != fence["batch_id"]
            or proof.snapshot_hash != fence["snapshot_hash"]
            or not proof.owner_id or type(proof.epoch) is not int or proof.epoch < 1):
        raise RuntimeError("Portfolio sync has no matching Keeper CAS attestation")
    return fence


def _transition_hashes(client: Any, run_id: str, account_id: str,
                       revision: int) -> tuple[str, str, dict[str, Any]]:
    marker = _stored_marker(client, run_id, account_id, revision)
    fence = load_fenced_portfolio_sync(
        client, run_id=run_id, account_id=account_id,
        state_revision=revision)
    if marker is None or fence is None or marker["batch_id"] != fence["batch_id"]:
        raise RuntimeError("Portfolio sync transition lacks exact marker and fence")
    return (sha256(canonical_json(marker).encode()).hexdigest(),
            sha256(canonical_json(fence).encode()).hexdigest(), fence)


def load_attested_portfolio_sync_transition(
    client: Any, keeper: Any, *, run_id: str, account_id: str,
    state_revision: int,
) -> dict[str, Any]:
    """Strict V2 proof binds marker and late fence, not only batch/snapshot."""
    fence = load_attested_portfolio_sync(
        client, keeper, run_id=run_id, account_id=account_id,
        state_revision=state_revision)
    if fence is None:
        raise RuntimeError("Portfolio sync transition lacks committed ClickHouse facts")
    marker_hash, fence_hash, exact = _transition_hashes(
        client, run_id, account_id, state_revision)
    proof = keeper.load_portfolio_snapshot_receipt(
        run_id, account_id, state_revision)
    if (not isinstance(proof, KeeperSyncAttestation)
            or proof.marker_hash != marker_hash
            or proof.fence_hash != fence_hash
            or proof.batch_id != exact["batch_id"]
            or proof.snapshot_hash != exact["snapshot_hash"]):
        raise RuntimeError("Portfolio sync transition lacks matching V2 Keeper proof")
    if _transition_hashes(client, run_id, account_id, state_revision)[:2] != (
            marker_hash, fence_hash):
        raise RuntimeError("Portfolio sync transition changed during proof audit")
    return exact


def audit_attested_portfolio_sync_transitions(
    client: Any, keeper: Any, run_id: str, *, quiescence: Any,
    page_size: int = 256,
) -> int:
    """Bounded dual-table scan, with a durable run head catching orphan proofs."""
    if not run_id or type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("Portfolio sync transition audit identity or page size is invalid")
    if quiescence is None or not callable(getattr(quiescence, "assert_fenced", None)):
        raise RuntimeError("Portfolio sync transition audit needs writer-drain proof")
    quiescence.assert_fenced(run_id)

    def keys(table: str):
        cursor: tuple[str, int] | None = None
        while True:
            quiescence.assert_fenced(run_id)
            after = ("" if cursor is None else
                     "AND (account_id,state_revision) > "
                     f"({_literal(cursor[0])},{cursor[1]}) ")
            rows = _rows(client,
                "SELECT account_id,state_revision FROM arte."
                f"{table} WHERE run_id={_literal(run_id)} {after}"
                "ORDER BY account_id,state_revision "
                f"LIMIT {page_size} FORMAT JSONEachRow")
            if len(rows) > page_size:
                raise RuntimeError("Portfolio sync transition page exceeds bound")
            for row in rows:
                key = (row.get("account_id"), row.get("state_revision"))
                if (not isinstance(key[0], str) or not key[0]
                        or type(key[1]) is not int or key[1] < 1
                        or cursor is not None and key <= cursor):
                    raise RuntimeError("Portfolio sync transition keys are invalid or duplicate")
                cursor = key
                yield key
            if len(rows) < page_size:
                break

    zero = "0" * 64
    count = 0
    run_xor = 0
    account_heads: dict[str, KeeperSyncTransitionHead] = {}
    pinned = keeper.load_portfolio_sync_transition_head(run_id, None)
    for marker_key, fence_key in zip_longest(
            keys(_MARKER), keys(_FENCE)):
        quiescence.assert_fenced(run_id)
        if marker_key is None or marker_key != fence_key:
            raise RuntimeError("Portfolio sync marker/fence inventory differs")
        account_id, revision = marker_key
        load_attested_portfolio_sync_transition(
            client, keeper, run_id=run_id, account_id=account_id,
            state_revision=revision)
        proof = keeper.load_portfolio_snapshot_receipt(run_id, account_id, revision)
        digest = int.from_bytes(sha256(proof.wire()).digest(), "big")
        run_xor ^= digest
        prior = account_heads.get(account_id)
        if prior is None:
            prior = KeeperSyncTransitionHead(run_id, account_id, 0, zero, 0)
        if revision <= prior.last_revision:
            raise RuntimeError("Portfolio sync account revisions are not increasing")
        account_heads[account_id] = KeeperSyncTransitionHead(
            run_id, account_id, prior.proof_count + 1,
            f"{int(prior.proof_xor, 16) ^ digest:064x}", revision)
        count += 1
    quiescence.assert_fenced(run_id)
    final_run = keeper.load_portfolio_sync_transition_head(run_id, None)
    expected_run = KeeperSyncTransitionHead(
        run_id, None, count, f"{run_xor:064x}",
        max((head.last_revision for head in account_heads.values()), default=0))
    if (pinned != final_run
            or (final_run is None and count != 0)
            or final_run is not None and final_run[0] != expected_run):
        raise RuntimeError("Portfolio sync run proof head differs from CH transitions")
    for account_id, expected in account_heads.items():
        observed = keeper.load_portfolio_sync_transition_head(run_id, account_id)
        if observed is None or observed[0] != expected:
            raise RuntimeError("Portfolio sync account proof head differs")
    quiescence.assert_fenced(run_id)
    return count


class TypedPortfolioSyncAuthority:
    """Hold an account claim until a snapshot is committed and cold-read."""

    def __init__(self, *, client: Any, writer: Any, keeper: Any,
                 next_revision: Callable[..., int],
                 batch_identity: Callable[..., dict[str, Any]]) -> None:
        self._client = client
        self._writer = writer
        self._keeper = keeper
        self._revision = next_revision
        self._batch_identity = batch_identity

    def claim(self, run_id: str, account_id: str):
        return self._keeper.claim_portfolio_snapshot(run_id, account_id)

    def claim_is_current(self, lease: Any) -> bool:
        return bool(self._keeper.portfolio_snapshot_claim_is_current(lease))

    def next_revision(self, run_id: str, account_id: str, lease: Any) -> int:
        return self._revision(run_id, account_id, lease)

    async def publish(self, records: tuple[tuple[str, str, str, dict[str, Any]], ...],
                      captured: CapturedPortfolioSnapshot,
                      lease: Any) -> PortfolioSyncReceipt:
        if (not isinstance(captured, CapturedPortfolioSnapshot)
                or not await asyncio.to_thread(self.claim_is_current, lease)):
            raise RuntimeError("Typed portfolio sync lacks a current claim or capture")
        identity = await asyncio.to_thread(
            self._batch_identity, captured.run_id, captured.account_id, lease)
        if not isinstance(identity, dict) or set(identity) != {
                "run_month", "attempt_id", "batch_id", "prior_batch_id",
                "first_sequence", "source_cursor"}:
            raise RuntimeError("Typed portfolio sync lacks pinned event identity")
        batch = await asyncio.to_thread(
            project_portfolio_reconciliation_records,
            records, captured=captured, **identity)
        # The batch identity callback may block on coordination. A claim lost
        # while building the envelope must never reach the asynchronous lane.
        if not await asyncio.to_thread(self.claim_is_current, lease):
            raise RuntimeError("Typed portfolio sync lost its claim before enqueue")
        digest = await asyncio.wrap_future(
            self._writer.submit_portfolio_sync(batch, captured))
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RuntimeError("Typed portfolio sync writer returned no snapshot hash")
        if not await asyncio.to_thread(self.claim_is_current, lease):
            raise RuntimeError("Typed portfolio sync lost its claim while publishing")
        loaded = await asyncio.to_thread(
            load_fenced_portfolio_sync, self._client,
            run_id=captured.run_id, account_id=captured.account_id,
            state_revision=captured.state_revision)
        if (loaded is None or loaded["snapshot_hash"] != digest
                or loaded["batch_id"] != batch.batch_id
                or loaded["state_revision"] != captured.state_revision
                or not await asyncio.to_thread(self.claim_is_current, lease)):
            raise RuntimeError("Typed portfolio sync receipt differs from committed state")
        marker_hash, fence_hash, exact = await asyncio.to_thread(
            _transition_hashes, self._client, captured.run_id,
            captured.account_id, captured.state_revision)
        if exact != loaded:
            raise RuntimeError("Typed portfolio sync transition changed before attestation")
        proof = await asyncio.to_thread(
            self._keeper.attest_portfolio_snapshot_receipt,
            lease, captured.run_id, captured.account_id,
            captured.state_revision, batch.batch_id, digest,
            marker_hash=marker_hash, fence_hash=fence_hash)
        if not isinstance(proof, KeeperSyncAttestation):
            raise RuntimeError("Typed portfolio sync Keeper CAS attestation failed")
        admitted = await asyncio.to_thread(
            load_attested_portfolio_sync_transition, self._client, self._keeper,
            run_id=captured.run_id, account_id=captured.account_id,
            state_revision=captured.state_revision)
        if (admitted is None or admitted["snapshot_hash"] != digest
                or not await asyncio.to_thread(self.claim_is_current, lease)):
            raise RuntimeError("Typed portfolio sync lost claim after Keeper attestation")
        dispatch = _sync_dispatch(self._client)
        if dispatch is not None:
            await asyncio.to_thread(dispatch.compact,
                run_id=captured.run_id, account_id=captured.account_id,
                revision=captured.state_revision,
                marker_hash=marker_hash, fence_hash=fence_hash, proof=proof)
        return PortfolioSyncReceipt(captured.run_id, captured.account_id,
                                    captured.state_revision, digest)
