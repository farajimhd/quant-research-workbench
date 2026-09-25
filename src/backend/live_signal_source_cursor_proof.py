"""Inactive, read-only Signal Stream source proof for plan membership.

The proof covers a Keeper-attested typed cursor prefix, not SQLite state or a
mutable current watchlist. It does not make the typed producer safe to enable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.backend.live_activation_cold_bootstrap import _BoundedSourceCommits
from src.backend.signal_stream_session_head import SessionHead, SignalSessionHeadKeeper
from src.backend.signal_stream_typed_readback import SignalColdStorage, recover_committed_head


@dataclass(frozen=True, slots=True)
class AttestedSignalSourceCursorProof:
    identity: str
    content_hash: str
    configuration_revision: str
    source_revision: str
    sequence: int
    _keeper: SignalSessionHeadKeeper
    _head: SessionHead

    def assert_current(self) -> None:
        """Reject a changed Keeper head, epoch, or session generation."""
        if self._keeper.read_head(self.identity) != self._head:
            raise RuntimeError("Signal Stream source cursor head changed")


def cold_attested_signal_source_cursor(
    storage: SignalColdStorage, commit_client: Any,
    keeper: SignalSessionHeadKeeper, *, session_key: str,
    configuration_revision: str, source_revision: str,
    catalogs: Mapping[str, Any], max_batches: int = 100_000,
) -> AttestedSignalSourceCursorProof:
    """Verify an exact bounded typed prefix under a stable Keeper head.

    This control-plane read cannot run on the realtime delivery path. A caller
    must call ``assert_current`` again immediately before consuming the proof.
    """
    if type(max_batches) is not int or not 1 <= max_batches <= 100_000:
        raise ValueError("Signal Stream source cursor batch bound is invalid")
    first = keeper.read_head(session_key)
    if (first.session_key != session_key or not 1 <= first.batch_sequence <= max_batches
            or first.configuration_revision != configuration_revision
            or first.source_revision != source_revision):
        raise ValueError("Signal Stream Keeper head differs from source proof scope")
    bounded = _BoundedSourceCommits(storage, commit_client, limit=max_batches + 1)
    recovered = recover_committed_head(
        bounded, session_key=session_key,
        configuration_revision=configuration_revision,
        source_revision=source_revision, catalogs=catalogs)
    if (recovered.sequence != first.batch_sequence
            or recovered.content_hash != first.cursor_commit_hash):
        raise ValueError("Signal Stream typed cursor differs from Keeper head")
    proof = AttestedSignalSourceCursorProof(
        session_key, recovered.content_hash, configuration_revision,
        source_revision, recovered.sequence, keeper, first)
    proof.assert_current()
    return proof
