"""Read-only, disk-free audit of a complete terminal typed Backtest recovery.

This adds a whole-run admission check around the per-account snapshot reader.
It does not reconstruct a resumable controller or authorize live trading.
"""
from __future__ import annotations

from typing import Any

from src.trading_runtime.arte_backtest_snapshot_anchor import load_terminal_backtest_snapshot
from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import (
    _literal, _rows, load_committed_prefix, load_typed_run_context,
)


def audit_terminal_backtest_recovery(
    client: Any, run_id: str, *, page_size: int = 500,
) -> dict[str, dict[str, Any]]:
    """Require exactly one anchored snapshot per pinned account and full event joins.

    Reads at most one event page at a time, never silently truncates the
    prefix, and checks the prefix/context again after the read to detect a
    concurrent or inconsistent authority change.
    """
    if not isinstance(run_id, str) or not run_id or not 1 <= page_size <= 1000:
        raise ValueError("Terminal Backtest recovery run/page bounds are invalid")
    context = load_typed_run_context(client, run_id)
    prefix = load_committed_prefix(client, run_id)
    if (context.get("mode") != "backtest" or prefix is None
            or prefix.status not in {"completed", "stopped", "failed"}):
        raise ValueError("Terminal Backtest recovery requires a typed terminal run")
    accounts = tuple(context.get("account_ids") or ())
    if not accounts or len(accounts) > 65535 or len(set(accounts)) != len(accounts) or any(
        not isinstance(account, str) or not account for account in accounts
    ):
        raise RuntimeError("Terminal Backtest run has invalid pinned accounts")
    # Existing per-account reads miss an extra foreign/orphan anchor. The
    # LIMIT is one above the pinned count, so a duplicate or extra cannot be
    # hidden by truncation even for a large run.
    rows = _rows(client,
        "SELECT account_id FROM arte.trading_backtest_snapshot_anchor_v1 "
        f"WHERE run_id={_literal(run_id)} "
        f"ORDER BY account_id LIMIT {len(accounts) + 1} FORMAT JSONEachRow")
    if (len(rows) != len(accounts) or len({str(row["account_id"]) for row in rows}) != len(rows)
            or {str(row["account_id"]) for row in rows} != set(accounts)):
        raise RuntimeError("Terminal Backtest anchors differ from pinned account membership")
    cursor = 0
    while cursor < prefix.last_sequence:
        page = load_typed_event_page(client, prefix, after_sequence=cursor, limit=page_size)
        if not page:
            raise RuntimeError("Terminal Backtest committed event/detail scan ended early")
        next_cursor = int(page[-1].event["sequence"])
        if next_cursor <= cursor or next_cursor > prefix.last_sequence:
            raise RuntimeError("Terminal Backtest event page did not advance causally")
        cursor = next_cursor
    snapshots = {account_id: load_terminal_backtest_snapshot(
        client, prefix, account_id=account_id,
    ) for account_id in sorted(accounts)}
    if load_committed_prefix(client, run_id) != prefix or load_typed_run_context(client, run_id) != context:
        raise RuntimeError("Terminal Backtest recovery authority changed during audit")
    return snapshots
