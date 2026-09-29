"""Cold, SELECT-only account images at one exact Strategy 1 V4 cursor.

This proves one recovery family. It does not restore an actor or authorize
Backtest resume until manager, broker, OMS, and controller state also agree.
"""
from __future__ import annotations

from datetime import date, timezone
from typing import Any

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import (
    V4CommittedPrefix, load_verified_v4_prefix,
)
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_portfolio_snapshot import load_portfolio_snapshot


def load_v4_running_portfolio_images(
    client: Any, *, run_id: str, account_ids: tuple[str, ...],
) -> tuple[V4CommittedPrefix, dict[str, dict[str, Any]]]:
    """Reject an incomplete, moved, or cross-cursor account recovery image."""
    if (not isinstance(run_id, str) or not run_id
            or not isinstance(account_ids, tuple) or not account_ids
            or len(set(account_ids)) != len(account_ids)
            or any(not isinstance(account_id, str) or not account_id
                   for account_id in account_ids)):
        raise ValueError("V4 running portfolio needs distinct pinned accounts")
    prefix = load_verified_v4_prefix(client, run_id)
    if (not isinstance(prefix, V4CommittedPrefix)
            or prefix.run_id != run_id or prefix.status != "running"
            or prefix.last_sequence < 1 or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]):
        raise RuntimeError("V4 running portfolio lacks a verified prefix")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict)
            or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != prefix.last_sequence
            or cursor.get("batch_id") != prefix.last_batch_id):
        raise RuntimeError("V4 running portfolio lacks the exact market cursor")
    try:
        day = date.fromisoformat(cursor["session_date"])
        boundary_ms = cursor["boundary_ms"]
        if type(boundary_ms) is not int or boundary_ms < 0 or boundary_ms % 100:
            raise ValueError
        completed_at = market_day_boundary(day, boundary_ms).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("V4 running portfolio cursor clock is invalid") from exc
    snapshots = {}
    for account_id in account_ids:
        snapshot = load_portfolio_snapshot(
            client, run_id=run_id, account_id=account_id,
            state_revision=prefix.last_sequence)
        if (snapshot is None
                or snapshot.get("state_revision") != prefix.last_sequence
                or snapshot.get("snapshot_at") != completed_at.isoformat()
                or len(snapshot.get("families", {}).get(
                    "trading_portfolio_snapshot_v1", ())) != 1):
            raise RuntimeError(
                f"V4 running portfolio lacks exact account image: {account_id}")
        root = snapshot["families"]["trading_portfolio_snapshot_v1"][0]
        if (root.get("run_id") != run_id
                or root.get("account_id") != account_id
                or root.get("state_revision") != prefix.last_sequence):
            raise RuntimeError("V4 running portfolio account image changed identity")
        snapshots[account_id] = snapshot
    if load_verified_v4_prefix(client, run_id) != prefix:
        raise RuntimeError("V4 running portfolio prefix moved during cold read")
    return prefix, snapshots
