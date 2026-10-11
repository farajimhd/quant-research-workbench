"""Cold dispatch recovery against a separately verified source prefix.

A source batch with no dispatch rows is pending work. A partial intent/ACK
write is ambiguous and needs reconciliation; it is never treated as pending.
The existing normalized families and Keeper receipts remain the authorities.
"""
from __future__ import annotations

from dataclasses import dataclass

from .signal_dispatch_typed_cursor import ACK_COMMIT, _hex, read_committed_dispatch_prefix
from .signal_stream_typed_readback import canonical_row
from .qmd_columnar_history_transport import _owned_worker


@dataclass(frozen=True)
class DispatchRecovery:
    completed: tuple
    pending_source_sequences: tuple[int, ...]


def recover_dispatch_backlog(storage, *, session_key: str, source_commit_hashes: tuple[str, ...],
        configuration_revision_id: str, registered_dispatch) -> DispatchRecovery:
    """Keep the dispatch gate cold while proving a complete ACK prefix and empty tail."""
    if registered_dispatch is None:
        raise ValueError("Dispatch recovery requires registered cold receipt authority")
    hashes = tuple(_hex(value) for value in source_commit_hashes)
    inventory = getattr(storage, "has_dispatch_rows_after", None)
    if not callable(inventory):
        raise ValueError("Dispatch recovery lacks all-family tail inventory")
    acks = [canonical_row(ACK_COMMIT, row) for row in
        storage.list_dispatch_commits(ACK_COMMIT.name, session_key=session_key)]
    count = len(acks)
    if (count > len(hashes) or sorted(row["source_batch_sequence"] for row in acks)
            != list(range(1, count + 1))):
        raise ValueError("Dispatch ACK prefix is duplicate, gapped or beyond source")
    if inventory(session_key=session_key, source_batch_sequence=count):
        raise ValueError("Dispatch tail contains uncommitted rows; reconcile before resuming")
    completed = read_committed_dispatch_prefix(storage, session_key=session_key,
        source_commit_hashes=hashes[:count], configuration_revision_id=configuration_revision_id,
        registered_dispatch=registered_dispatch)
    return DispatchRecovery(completed, tuple(range(count + 1, len(hashes) + 1)))


async def recover_dispatch_backlog_async(storage, **scope) -> DispatchRecovery:
    """Own/drain SELECT and Keeper reads without blocking the app event loop."""
    return await _owned_worker(recover_dispatch_backlog, storage, **scope)
