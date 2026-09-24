"""Read-only, all-or-nothing projection of a fixed Backtest journal prefix.

This module prepares normalized arte batches. It does not submit or fence them;
the fixed Backtest launch remains blocked until typed recovery is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import TypedJournalBatch


NIL_BATCH_ID = str(UUID(int=0))


@dataclass(frozen=True, slots=True)
class ProjectedBacktestPrefix:
    batches: tuple[TypedJournalBatch, ...]
    last_sequence: int
    last_batch_id: str
    source_cursor: str


def project_pending_backtest_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    expected_market_start: datetime | None = None,
) -> ProjectedBacktestPrefix:
    """Project every pending record or reject the entire prefix before writes.

    Each logical event receives one deterministic typed batch. A completed
    market-boundary event advances the source cursor only within its own batch.
    """
    attempt = str(UUID(attempt_id))
    previous = str(UUID(prior_batch_id))
    if (run_month.day != 1 or type(prior_sequence) is not int
            or prior_sequence < 0 or not source_cursor
            or (prior_sequence == 0 and previous != NIL_BATCH_ID)
            or (prior_sequence > 0 and previous == NIL_BATCH_ID)):
        raise ValueError("Backtest typed prefix identity is invalid")
    records = journal.unfenced_records(after_sequence=prior_sequence)
    batches: list[TypedJournalBatch] = []
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("Backtest typed prefix is not one contiguous run")
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v1:{record.run_id}:{attempt}:{record.sequence}:{record.record_id}"))
        if (record.category, record.entity_type) == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        batch = project_journal_record(
            record, run_month=run_month, attempt_id=attempt,
            batch_id=batch_id, prior_batch_id=previous, source_cursor=cursor,
            expected_config=expected_config, expected_mode="backtest",
            fixed_market_parent_plan=fixed_market_parent_plan,
            fixed_market_execution_plan=fixed_market_execution_plan,
            expected_market_start=expected_market_start,
        )
        if (batch.run_id != journal.run_id or batch.first_sequence != sequence
                or batch.last_sequence != sequence or len(batch.events) != 1):
            raise ValueError("Backtest typed projector changed event identity")
        batches.append(batch)
        previous = batch_id
    return ProjectedBacktestPrefix(tuple(batches),
                                   prior_sequence + len(batches), previous, cursor)
