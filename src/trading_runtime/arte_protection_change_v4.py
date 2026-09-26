"""Use the existing exact, normalized protection contract in V4 commits."""
from __future__ import annotations

from datetime import date

from src.backend.backtest_protection_change_v3 import project_protection_change_v3
from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, V4ProtectionChangeBatch,
)
from src.trading_runtime.journal_contract import JournalRecord


def protection_change_batch_v4(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
) -> V4ProtectionChangeBatch:
    """Project an OMS protection record without recalculation or opaque data."""
    projected = project_protection_change_v3(
        record, attempt_id=attempt_id, batch_id=batch_id)
    if run_month.isoformat() != projected.event["event_month"]:
        raise ValueError("Protection change differs from the run month")
    base = TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running",
        (projected.event,),
    )
    return V4ProtectionChangeBatch(
        base, projected.detail, projected.entry_orders)
