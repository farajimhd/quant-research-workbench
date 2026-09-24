"""Verified typed admission receipt bridge; no live cutover is enabled here."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any, Callable

from src.trading_runtime.arte_admission_fence import load_fenced_admission
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.arte_journal_projection import project_portfolio_admission_records
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    run_id: str
    account_id: str
    state_revision: int
    snapshot_hash: str
    batch_id: str


class TypedPortfolioAdmissionAuthority:
    """Join a Keeper claim, typed event projector, writer, and cold readback.

    The event projector must map every staged decision and reservation record
    to normalized detail families. It cannot discard unmodeled records.
    """

    def __init__(self, *, client: Any, writer: Any, keeper: Any,
                 next_revision: Callable[..., int],
                 batch_identity: Callable[..., dict[str, Any]]) -> None:
        self._client = client
        self._writer = writer
        self._keeper = keeper
        self._revision = next_revision
        self._batch_identity = batch_identity

    def claim(self, run_id: str, account_id: str, group_ids: tuple[str, ...]):
        return self._keeper.claim_portfolio_admission(run_id, account_id, group_ids)

    def claim_is_current(self, lease: Any) -> bool:
        return bool(self._keeper.portfolio_admission_claim_is_current(lease))

    def next_revision(self, run_id: str, account_id: str, lease: Any) -> int:
        return self._revision(run_id, account_id, lease)

    async def publish(self, records: tuple[tuple[str, str, str, dict[str, Any]], ...],
                      captured: CapturedPortfolioSnapshot, lease: Any) -> AdmissionReceipt:
        if (not self.claim_is_current(lease)
                or not any(kind == "portfolio_decision" for kind, _, _, _ in records)
                or not any(kind == "portfolio_reservation" for kind, _, _, _ in records)
                or any(account_id != captured.account_id for _, _, account_id, _ in records)):
            raise RuntimeError("Typed admission lacks a current claim or complete account facts")
        identity = self._batch_identity(captured.run_id, captured.account_id, lease)
        if not isinstance(identity, dict) or set(identity) != {
                "run_month", "attempt_id", "batch_id", "prior_batch_id",
                "first_sequence", "source_cursor"}:
            raise RuntimeError("Typed admission lacks a pinned batch identity")
        batch = project_portfolio_admission_records(
            records, run_id=captured.run_id, **identity)
        if (not isinstance(batch, TypedJournalBatch) or batch.run_id != captured.run_id
                or not any(event["account_id"] == captured.account_id for event in batch.events)):
            raise RuntimeError("Typed admission projector returned a foreign batch")
        digest = await asyncio.wrap_future(self._writer.submit_admission(batch, captured))
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RuntimeError("Typed admission writer did not return a snapshot hash")
        loaded = load_fenced_admission(
            self._client, run_id=captured.run_id, account_id=captured.account_id,
            state_revision=captured.state_revision)
        if (loaded is None or loaded["snapshot_hash"] != digest
                or loaded["batch_id"] != batch.batch_id
                or not self.claim_is_current(lease)):
            raise RuntimeError("Typed admission receipt differs from its durable fence")
        return AdmissionReceipt(captured.run_id, captured.account_id,
                                captured.state_revision, digest, batch.batch_id)
