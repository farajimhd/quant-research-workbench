"""Inactive Keeper CAS receipt for an exact typed Signal Stream cursor fence.

This attests ClickHouse facts, not approved configuration or active QMD output.
An unattested committed row remains a cold-start blocker, never an ACK.
"""
from __future__ import annotations

from typing import Any

from src.backend.signal_stream_session_head import SignalSessionHeadKeeper, SessionHead
from src.backend.signal_stream_typed_cursor import COMMIT
from src.backend.signal_stream_typed_readback import (
    CommittedCursorHead, canonical_row,
)


class SignalCursorAttestor:
    def __init__(self, keeper: SignalSessionHeadKeeper, *, owner_id: str,
                 epoch: int) -> None:
        if not owner_id or type(epoch) is not int or epoch < 1:
            raise ValueError("typed Signal Stream Keeper claim is invalid")
        self._keeper = keeper
        self._owner_id = owner_id
        self._epoch = epoch
        self._heads: dict[str, SessionHead] = {}

    def bootstrap(self, recovered: CommittedCursorHead, *, configuration_revision: str,
                  source_revision: str) -> None:
        """Accept only a whole cold prefix identical to the Keeper head."""
        session_key = recovered.session_key
        if session_key in self._heads or not self._keeper.is_current(
                session_key, owner_id=self._owner_id, epoch=self._epoch):
            raise RuntimeError("typed Signal Stream Keeper claim is unavailable")
        head = self._keeper.read_head(session_key)
        if (head.batch_sequence != recovered.sequence
                or head.cursor_commit_hash != recovered.content_hash
                or (head.batch_sequence and (
                    head.configuration_revision != configuration_revision
                    or head.source_revision != source_revision))):
            raise ValueError("typed Signal Stream cold prefix differs from Keeper attestation")
        if self._keeper.read_head(session_key) != head:
            raise RuntimeError("typed Signal Stream Keeper head changed during cold bootstrap")
        self._heads[session_key] = head

    def attest(self, batch: Any, cursor_hash: str, storage: Any) -> str:
        """Require exact fence readback, then CAS the current owner epoch."""
        prior = self._heads.get(batch.session_key)
        if (prior is None or prior.batch_sequence + 1 != batch.batch_sequence
                or prior.cursor_commit_hash != batch.previous_commit_hash
                or not self._keeper.is_current(
                    batch.session_key, owner_id=self._owner_id, epoch=self._epoch)):
            raise RuntimeError("typed Signal Stream Keeper prior head or owner differs")
        rows = storage.read_cursor_rows(
            COMMIT.name, session_key=batch.session_key,
            batch_sequence=batch.batch_sequence)
        if len(rows) != 1:
            raise ValueError("typed Signal Stream commit readback is missing or duplicated")
        row = canonical_row(COMMIT, rows[0])
        if (row["schema_version"] != 1
                or row["session_key"] != batch.session_key
                or row["content_hash"] != cursor_hash
                or row["previous_commit_hash"] != prior.cursor_commit_hash
                or row["configuration_revision"] != batch.configuration_revision
                or row["source_revision"] != batch.source_revision
                or row["batch_sequence"] != batch.batch_sequence):
            raise ValueError("typed Signal Stream commit differs before Keeper attestation")
        confirmed = self._keeper.attest_next(
            batch.session_key, owner_id=self._owner_id, epoch=self._epoch,
            previous=prior, cursor_commit_hash=cursor_hash,
            configuration_revision=batch.configuration_revision,
            source_revision=batch.source_revision)
        if (confirmed.batch_sequence != batch.batch_sequence
                or confirmed.cursor_commit_hash != cursor_hash):
            raise RuntimeError("typed Signal Stream Keeper CAS receipt differs")
        self._heads[batch.session_key] = confirmed
        return cursor_hash
