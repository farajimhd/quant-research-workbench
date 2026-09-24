"""Verified typed broker-sync snapshot receipt, separate from live cutover."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
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
        if not current(lease):
            raise RuntimeError("Portfolio sync resume lacks a current Keeper claim")
        marker = _stored_marker(client, run_id, account_id, state_revision)
        if marker is None:
            raise RuntimeError("Portfolio sync resume lacks its prepared marker")
        existing = load_fenced_portfolio_sync(
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision)
        if existing is not None:
            if existing["batch_id"] != marker["batch_id"]:
                raise RuntimeError("Portfolio sync resume fence differs from marker")
            return existing
        batch_id = str(marker["batch_id"])
        prefix = load_committed_prefix(client, run_id)
        if prefix is None:
            raise RuntimeError("Portfolio sync resume lacks a committed event prefix")
        commits = _rows(client,
            "SELECT attempt_id,first_sequence,last_sequence FROM arte.trading_commit_v1 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
        details = _rows(client,
            "SELECT difference_hash,snapshot_id FROM arte.trading_portfolio_reconciliation_event_v1 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
        snapshot = load_portfolio_snapshot(
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
        load_fenced_portfolio_sync(
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision, _provisional_fence=fence)
        if not current(lease):
            raise RuntimeError("Portfolio sync resume lost its Keeper claim")
        _insert(client, _FENCE, (fence,),
                f"portfolio-sync:{run_id}:{account_id}:{state_revision}")
        verified = load_fenced_portfolio_sync(
            client, run_id=run_id, account_id=account_id,
            state_revision=state_revision)
        if verified is None or verified["batch_id"] != batch_id or not current(lease):
            raise RuntimeError("Portfolio sync resume fence did not become durable")
        return verified


def _publish_marker(client: Any, row: Mapping[str, Any]) -> None:
    expected = _canonical_typed_content(
        _MARKER, {key: value for key, value in row.items() if key != "content_hash"})
    run_id, account_id = str(row["run_id"]), str(row["account_id"])
    revision = int(row["state_revision"])
    prior = _stored_marker(client, run_id, account_id, revision)
    if prior is not None:
        if prior != expected:
            raise RuntimeError("Portfolio sync snapshot marker revision conflicts")
        return
    _insert(client, _MARKER, (row,),
            f"portfolio-sync:{run_id}:{account_id}:{revision}:marker")
    if _stored_marker(client, run_id, account_id, revision) != expected:
        raise RuntimeError("Portfolio sync snapshot marker did not become durable")


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
        return digest
    _insert(client, _FENCE, (fence,),
            f"portfolio-sync:{captured.run_id}:{captured.account_id}:{captured.state_revision}")
    verified = load_fenced_portfolio_sync(
        client, run_id=captured.run_id, account_id=captured.account_id,
        state_revision=captured.state_revision)
    if verified is None or verified["snapshot_hash"] != digest:
        raise RuntimeError("Portfolio sync fence did not become durable")
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
        proof = await asyncio.to_thread(
            self._keeper.attest_portfolio_snapshot_receipt,
            lease, captured.run_id, captured.account_id,
            captured.state_revision, batch.batch_id, digest)
        if not isinstance(proof, KeeperSyncAttestation):
            raise RuntimeError("Typed portfolio sync Keeper CAS attestation failed")
        admitted = await asyncio.to_thread(
            load_attested_portfolio_sync, self._client, self._keeper,
            run_id=captured.run_id, account_id=captured.account_id,
            state_revision=captured.state_revision)
        if (admitted is None or admitted["snapshot_hash"] != digest
                or not await asyncio.to_thread(self.claim_is_current, lease)):
            raise RuntimeError("Typed portfolio sync lost claim after Keeper attestation")
        return PortfolioSyncReceipt(captured.run_id, captured.account_id,
                                    captured.state_revision, digest)
