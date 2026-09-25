"""Read-only, bounded fixed-Backtest activity from verified V2 event facts.

This is an event/evidence page, not reconstruction of legacy JSON activity or
a saved-review/resume authority. Callers must supply a cold-verified V2 prefix.
"""
from __future__ import annotations

from typing import Any

from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import V2CommittedPrefix


_ACTIVITY_CATEGORIES = frozenset({
    "market_discovery_signal", "watchlist_membership", "strategy",
    "strategy_decision", "order_management",
})


def load_fixed_typed_activity_page(
    client: Any, prefix: V2CommittedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> dict[str, Any]:
    """Advance the complete event cursor; never skip unknown or corrupt facts."""
    if not isinstance(prefix, V2CommittedPrefix):
        raise ValueError("Fixed typed activity requires a verified V2 prefix")
    if type(after_sequence) is not int or not 0 <= after_sequence <= prefix.last_sequence:
        raise ValueError("Fixed typed activity cursor exceeds its verified prefix")
    rows = load_typed_event_page(
        client, prefix, after_sequence=after_sequence, limit=limit)
    events = tuple({
        "event": row.event,
        "detail_family": row.detail_family,
        "detail": row.detail,
    } for row in rows if row.event["category"] in _ACTIVITY_CATEGORIES)
    next_sequence = int(rows[-1].event["sequence"]) if rows else after_sequence
    return {
        "schema_version": "fixed-typed-activity-v2",
        "run_id": prefix.run_id,
        "verified_prefix_sequence": prefix.last_sequence,
        "next_sequence": next_sequence,
        "scanned_event_count": len(rows),
        "non_activity_event_count": len(rows) - len(events),
        "events": events,
        "caught_up_to_prefix": next_sequence == prefix.last_sequence,
    }
