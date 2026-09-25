"""Pure fixed-Backtest terminal suffix handoff; no publication authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_terminal_snapshot_v2 import project_snapshot_group
from src.backend.backtest_terminal_v2_fence import (
    project_terminal_v2_commit, seal_v2_row,
)
from src.trading_runtime.arte_journal_projection import runtime_lifecycle_batch
from src.trading_runtime.arte_journal_writer import CommittedPrefix, typed_row


@dataclass(frozen=True, slots=True)
class TerminalV2Handoff:
    prefix: CommittedPrefix
    account_ids: tuple[str, ...]
    attempt_id: str
    batch_id: str
    source_cursor: str
    status: str
    committed_at: datetime
    events: tuple[Mapping[str, Any], ...]
    transitions: tuple[Mapping[str, Any], ...]
    accounts: tuple[Mapping[str, Any], ...]
    positions: tuple[Mapping[str, Any], ...]
    commit: Mapping[str, Any]

    def publication_fields(self) -> dict[str, Any]:
        """Exact fields for the staged publisher, excluding the verified prefix."""
        return {key: getattr(self, key) for key in (
            "account_ids", "attempt_id", "batch_id", "source_cursor", "status",
            "committed_at", "events", "transitions", "accounts", "positions",
        )}


def prepare_terminal_v2_handoff(
    journal: BacktestMemoryJournal, prefix: CommittedPrefix, *,
    account_ids: tuple[str, ...], attempt_id: str, run_month: date,
    committed_at: datetime,
) -> TerminalV2Handoff:
    """Require complete account blocks followed by one terminal lifecycle."""
    if (not isinstance(journal, BacktestMemoryJournal)
            or not isinstance(prefix, CommittedPrefix)
            or journal.run_id != prefix.run_id
            or prefix.status != "running"
            or run_month.day != 1
            or committed_at.tzinfo is None):
        raise ValueError("Terminal V2 handoff lacks a fenced running prefix")
    attempt = str(UUID(attempt_id))
    records = tuple(journal.unfenced_records())
    if (len(records) < len(account_ids) + 1
            or not account_ids or len(set(account_ids)) != len(account_ids)
            or records[0].sequence != prefix.last_sequence + 1
            or records[-1].category != "lifecycle"
            or records[-1].entity_type != "run"
            or records[-1].entity_id != prefix.run_id
            or records[-1].payload.get("status") not in {"completed", "stopped", "failed"}
            or any(record.run_id != prefix.run_id
                   or record.sequence != prefix.last_sequence + index + 1
                   or record.event_time != records[-1].event_time
                   or record.event_time.astimezone(timezone.utc).date().replace(day=1)
                   != run_month
                   for index, record in enumerate(records))):
        raise ValueError("Terminal V2 handoff has an incomplete or noncausal suffix")
    batch_id = str(uuid5(
        NAMESPACE_URL,
        f"arte-backtest-terminal-v2:{prefix.run_id}:{attempt}:{records[-1].record_id}",
    ))
    accounts: list[Mapping[str, Any]] = []
    positions: list[Mapping[str, Any]] = []
    offset = 0
    while offset < len(records) - 1:
        account = records[offset]
        count = account.payload.get("expected_position_count")
        if (account.category, account.entity_type) != ("snapshot", "portfolio"):
            raise ValueError("Terminal V2 suffix contains an ungrouped record")
        if type(count) is not int or count < 0:
            raise ValueError("Terminal V2 account position count is invalid")
        children = records[offset + 1:offset + 1 + count]
        parent_row, child_rows = project_snapshot_group(
            account, children, batch_id=batch_id)
        accounts.append(seal_v2_row("trading_backtest_account_snapshot_v2", parent_row))
        positions.extend(seal_v2_row("trading_backtest_position_snapshot_v2", row)
                         for row in child_rows)
        offset += count + 1
    if offset != len(records) - 1:
        raise ValueError("Terminal V2 account blocks do not end at lifecycle")
    events = tuple(typed_row("trading_event_v1", {
        "run_id": record.run_id,
        "event_month": record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01"),
        "attempt_id": attempt, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": record.event_time, "recorded_at": record.recorded_at,
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id, "account_id": record.account_id,
        "correlation_id": str(record.payload.get("correlation_id") or ""),
        "causation_id": str(record.payload.get("causation_id") or ""),
    }) for record in records)
    lifecycle = runtime_lifecycle_batch(
        records[-1], run_month=run_month, attempt_id=attempt,
        batch_id=batch_id, prior_batch_id=prefix.last_batch_id,
        source_cursor=prefix.source_cursor,
    )
    transitions = (typed_row("trading_run_transition_v1",
                             lifecycle.run_transitions[0]),)
    status = str(records[-1].payload["status"])
    commit = project_terminal_v2_commit(
        prefix, account_ids=account_ids, attempt_id=attempt,
        batch_id=batch_id, source_cursor=prefix.source_cursor,
        status=status, committed_at=committed_at, events=events,
        transitions=transitions, accounts=tuple(accounts),
        positions=tuple(positions),
    )
    return TerminalV2Handoff(
        prefix, account_ids, attempt, batch_id, prefix.source_cursor, status,
        committed_at, events, transitions, tuple(accounts), tuple(positions),
        commit,
    )
