"""Read-only, disk-free execution page from the normalized ARTE journal.

This is a bounded data contract, not the legacy saved-run UI projection.
Account recovery is joined only through a terminal snapshot anchor that binds
the snapshot hash to the exact committed event prefix.
"""
from __future__ import annotations

from typing import Any

from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_backtest_snapshot_anchor import load_terminal_backtest_snapshot
from src.trading_runtime.arte_journal_writer import (
    load_committed_commission_page, load_committed_execution_page,
    load_committed_prefix, load_typed_run_context,
)


def load_typed_backtest_financial_page(
    client: Any, run_id: str, *, after_fill_sequence: int = 0,
    after_commission_sequence: int = 0, limit: int = 500,
) -> dict[str, Any]:
    """Return a verified terminal run and bounded fill/fee evidence page.

    Fill and commission cursors are independent: interleaved events must not
    cause one family to skip unreturned rows from the other. No local files or
    opaque payloads participate in this read.
    """
    if (not run_id or after_fill_sequence < 0 or after_commission_sequence < 0
            or not 1 <= limit <= 1000):
        raise ValueError("Typed Backtest financial page has invalid bounds")
    context = load_typed_run_context(client, run_id)
    if context["mode"] != "backtest":
        raise ValueError("Typed financial review accepts Backtest runs only")
    prefix = load_committed_prefix(client, run_id)
    if prefix is None or prefix.status not in {"completed", "stopped", "failed"}:
        raise ValueError("Typed financial review requires a terminal committed run")
    if max(after_fill_sequence, after_commission_sequence) > prefix.last_sequence:
        raise ValueError("Typed financial page cursor exceeds the committed prefix")
    cursor = load_latest_backtest_cursor(client, prefix)
    if cursor is not None and str(cursor["session_date"]) != str(context["session_date"]):
        raise RuntimeError("Backtest market cursor differs from the pinned session")
    accounts = {account_id: load_terminal_backtest_snapshot(
        client, prefix, account_id=account_id)
        for account_id in context["account_ids"]}
    fills = load_committed_execution_page(
        client, prefix, after_sequence=after_fill_sequence, limit=limit)
    fees = load_committed_commission_page(
        client, prefix, after_sequence=after_commission_sequence, limit=limit)
    return {
        "run": context, "status": prefix.status,
        "committed_sequence": prefix.last_sequence,
        "cursor": cursor, "accounts": accounts,
        "fills": fills, "commissions": fees,
        "next_fill_sequence": (int(fills[-1]["sequence"]) if fills else after_fill_sequence),
        "next_commission_sequence": (
            int(fees[-1]["sequence"]) if fees else after_commission_sequence),
        "complete": len(fills) < limit and len(fees) < limit,
    }
