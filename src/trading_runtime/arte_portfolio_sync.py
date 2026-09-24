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
    publish_typed_batch, typed_row,
)

from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, load_portfolio_snapshot,
    prepare_captured_portfolio_snapshot, publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.journal_contract import canonical_json

_FENCE = "trading_portfolio_sync_fence_v1"


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
                               state_revision: int) -> dict[str, Any] | None:
    rows = _rows(client, "SELECT * FROM arte.trading_portfolio_sync_fence_v1 "
                 f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
                 f"AND state_revision={int(state_revision)} FORMAT JSONEachRow")
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("Portfolio sync fence has duplicate revision")
    row = rows[0]
    content = _canonical_typed_content(
        _FENCE, {key: value for key, value in row.items() if key != "content_hash"},
        stored_utc=True)
    if sha256(canonical_json(content).encode("utf-8")).hexdigest() != str(row["content_hash"]):
        raise RuntimeError("Portfolio sync fence content hash differs")
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
    if (snapshot["state"]["snapshot_id"] != detail["snapshot_id"]
            or snapshot["state"]["account_key"] != detail["account_key"]
            or len(snapshot["state"]["reconciliation"]) != int(detail["difference_count"])
            or sha256(canonical_json(sorted(snapshot["state"]["reconciliation"],
                                      key=lambda row: (row["account_key"], row["ticker"]))).encode("utf-8")).hexdigest()
            != detail["difference_hash"]):
        raise RuntimeError("Portfolio sync event differs from normalized recovery differences")
    return content


def publish_fenced_portfolio_sync(
    client: Any, batch: TypedJournalBatch, captured: CapturedPortfolioSnapshot,
) -> str:
    """Worker-only late fence: event, snapshot, then verified transition."""
    if (len(batch.portfolio_reconciliation_events) != 1 or len(batch.events) != 1
            or batch.events[0]["account_id"] != captured.account_id
            or batch.portfolio_reconciliation_events[0]["difference_hash"]
            != portfolio_reconciliation_hash(captured)):
        raise ValueError("Portfolio sync batch does not bind captured differences")
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
        if not isinstance(captured, CapturedPortfolioSnapshot) or not self.claim_is_current(lease):
            raise RuntimeError("Typed portfolio sync lacks a current claim or capture")
        identity = self._batch_identity(captured.run_id, captured.account_id, lease)
        if not isinstance(identity, dict) or set(identity) != {
                "run_month", "attempt_id", "batch_id", "prior_batch_id",
                "first_sequence", "source_cursor"}:
            raise RuntimeError("Typed portfolio sync lacks pinned event identity")
        batch = project_portfolio_reconciliation_records(
            records, captured=captured, **identity)
        digest = await asyncio.wrap_future(
            self._writer.submit_portfolio_sync(batch, captured))
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RuntimeError("Typed portfolio sync writer returned no snapshot hash")
        loaded = load_fenced_portfolio_sync(
            self._client, run_id=captured.run_id, account_id=captured.account_id,
            state_revision=captured.state_revision)
        if (loaded is None or loaded["snapshot_hash"] != digest
                or loaded["batch_id"] != batch.batch_id
                or loaded["state_revision"] != captured.state_revision
                or not self.claim_is_current(lease)):
            raise RuntimeError("Typed portfolio sync receipt differs from committed state")
        return PortfolioSyncReceipt(captured.run_id, captured.account_id,
                                    captured.state_revision, digest)
