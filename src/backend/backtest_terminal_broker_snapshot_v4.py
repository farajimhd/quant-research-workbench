"""Pure V4 terminal broker snapshot block normalization.

Broker account/position evidence is distinct from the portfolio recovery
capture. It survives as tabular Float64 rows with complete grouping and
source identities under the V4 terminal commit.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timezone
from typing import TYPE_CHECKING, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.backtest_terminal_snapshot_v2 import project_snapshot_group
from src.backend.backtest_terminal_v2_fence import seal_v2_row
from src.trading_runtime.journal_contract import JournalRecord

if TYPE_CHECKING:
    from src.trading_runtime.arte_journal_writer import TypedJournalBatch


@dataclass(frozen=True, slots=True)
class V4BrokerSnapshotRows:
    accounts: tuple[Mapping[str, object], ...]
    positions: tuple[Mapping[str, object], ...]
    first_sequence: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class V4TerminalBrokerBatch:
    """One terminal V4 commit envelope and its exact Float64 child families."""

    base: TypedJournalBatch
    broker_snapshots: V4BrokerSnapshotRows


def project_v4_terminal_broker_batch(
    records: tuple[JournalRecord, ...], *, run_id: str,
    account_ids: tuple[str, ...], attempt_id: str, run_month: date,
    prior_batch_id: str, source_cursor: str,
) -> V4TerminalBrokerBatch:
    """Build a single causal terminal suffix; no V1 decimal snapshot projection."""
    from src.trading_runtime.arte_journal_projection import runtime_lifecycle_batch
    from src.trading_runtime.arte_journal_writer import typed_row

    if (run_month.day != 1 or not source_cursor or not records
            or records[0].sequence < 1
            or any(record.event_time.tzinfo is None
                   or record.recorded_at.tzinfo is None
                   or record.event_time.astimezone(timezone.utc).date().replace(day=1)
                   != run_month for record in records)):
        raise ValueError("V4 terminal broker batch has an invalid clock or month")
    attempt = str(UUID(attempt_id))
    prior = str(UUID(prior_batch_id))
    batch_id = str(uuid5(
        NAMESPACE_URL,
        f"arte-backtest-terminal-v4:{run_id}:{attempt}:{records[-1].record_id}",
    ))
    snapshots = project_v4_terminal_broker_snapshots(
        records, run_id=run_id, account_ids=account_ids, batch_id=batch_id)
    events = tuple(typed_row("trading_event_v1", {
        "run_id": record.run_id,
        "event_month": run_month.isoformat(),
        "attempt_id": attempt, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id, "account_id": record.account_id,
        "correlation_id": str(record.payload.get("correlation_id") or ""),
        "causation_id": str(record.payload.get("causation_id") or ""),
    }) for record in records)
    lifecycle = runtime_lifecycle_batch(
        records[-1], run_month=run_month, attempt_id=attempt,
        batch_id=batch_id, prior_batch_id=prior, source_cursor=source_cursor)
    base = replace(lifecycle, first_sequence=records[0].sequence,
                   events=events)
    return V4TerminalBrokerBatch(base, snapshots)


def project_v4_terminal_broker_snapshots(
    records: tuple[JournalRecord, ...], *, run_id: str,
    account_ids: tuple[str, ...], batch_id: str,
) -> V4BrokerSnapshotRows:
    """Require complete account blocks immediately before terminal lifecycle."""
    UUID(batch_id)
    if (not records or not run_id or not account_ids
            or len(set(account_ids)) != len(account_ids)
            or any(row.run_id != run_id for row in records)
            or any(row.sequence != records[0].sequence + index
                   for index, row in enumerate(records))
            or (records[-1].category, records[-1].entity_type,
                records[-1].entity_id) != ("lifecycle", "run", run_id)
            or records[-1].payload.get("status") not in
            {"completed", "stopped", "failed"}):
        raise ValueError("V4 terminal broker snapshot suffix is incomplete")
    accounts = []
    positions = []
    offset = 0
    while offset < len(records) - 1:
        account = records[offset]
        count = account.payload.get("expected_position_count")
        if (account.category, account.entity_type) != ("snapshot", "portfolio") \
                or type(count) is not int or count < 0:
            raise ValueError("V4 terminal broker account block is invalid")
        children = records[offset + 1:offset + 1 + count]
        parent, child_rows = project_snapshot_group(
            account, children, batch_id=batch_id)
        accounts.append(seal_v2_row(
            "trading_backtest_account_snapshot_v2", parent))
        positions.extend(seal_v2_row(
            "trading_backtest_position_snapshot_v2", row)
            for row in child_rows)
        offset += count + 1
    if (offset != len(records) - 1
            or tuple(row["account_id"] for row in accounts)
            != account_ids
            or any(row.event_time != records[-1].event_time
                   for row in records[:-1])):
        raise ValueError("V4 terminal broker snapshot population differs from run")
    return V4BrokerSnapshotRows(
        tuple(accounts), tuple(positions), records[0].sequence,
        records[-1].sequence)
