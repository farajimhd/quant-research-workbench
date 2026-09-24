"""Typed terminal Backtest account capture and committed readback boundary.

This does not serialize the controller or broker restart checkpoint. It only
links the terminal event prefix to complete normalized portfolio snapshots.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.trading_runtime.arte_journal_writer import CommittedPrefix, TypedJournalBatch
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
from src.trading_runtime.arte_backtest_snapshot_anchor import load_terminal_backtest_snapshot


def capture_terminal_backtest_accounts(
    portfolio: Any, batch: TypedJournalBatch, *, account_ids: tuple[str, ...],
) -> tuple[CapturedPortfolioSnapshot, ...]:
    """Freeze every pinned account against one terminal lifecycle event."""
    if (batch.status not in {"completed", "stopped", "failed"}
            or len(batch.events) != 1 or len(batch.run_transitions) != 1
            or batch.events[0]["category"] != "lifecycle"
            or batch.events[0]["entity_type"] != "run"
            or batch.events[0]["entity_id"] != batch.run_id
            or int(batch.events[0]["sequence"]) != batch.last_sequence
            or batch.run_transitions[0]["run_id"] != batch.run_id
            or batch.run_transitions[0]["record_id"] != batch.events[0]["record_id"]
            or batch.run_transitions[0]["status"] != batch.status
            or not account_ids or len(set(account_ids)) != len(account_ids)
            or any(not account_id for account_id in account_ids)
            or getattr(portfolio, "run_id", None) != batch.run_id):
        raise ValueError("Terminal Backtest capture lacks one pinned lifecycle/account set")
    at = datetime.fromisoformat(str(batch.events[0]["event_time"]))
    if at.tzinfo is None:
        raise ValueError("Terminal Backtest event time is naive")
    at = at.astimezone(timezone.utc)
    captured = tuple(portfolio.capture_recovery_snapshot(
        account_id, state_revision=batch.last_sequence, snapshot_at=at)
        for account_id in sorted(account_ids))
    if any(not isinstance(row, CapturedPortfolioSnapshot)
           or row.run_id != batch.run_id
           or row.account_id != account_id
           or row.state_revision != batch.last_sequence
           or row.snapshot_at.astimezone(timezone.utc) != at
           for account_id, row in zip(sorted(account_ids), captured, strict=True)):
        raise ValueError("Terminal Backtest account capture changed causal identity")
    return captured


def load_terminal_backtest_accounts(
    client: Any, prefix: CommittedPrefix, *, account_ids: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """Read only accounts anchored to the same verified terminal prefix."""
    if (not isinstance(prefix, CommittedPrefix)
            or prefix.status not in {"completed", "stopped", "failed"}
            or not account_ids or len(set(account_ids)) != len(account_ids)
            or any(not account_id for account_id in account_ids)):
        raise ValueError("Terminal Backtest readback needs pinned distinct accounts")
    return {account_id: load_terminal_backtest_snapshot(
        client, prefix, account_id=account_id)
        for account_id in sorted(account_ids)}
