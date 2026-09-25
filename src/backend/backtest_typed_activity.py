"""Read-only, bounded fixed-Backtest activity from verified V2 event facts.

This is an event/evidence page, not reconstruction of legacy JSON activity or
a saved-review/resume authority. Callers must supply a cold-verified V2 prefix.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_reader import (
    TypedJournalEvent, _verified_row, load_typed_event_page,
)
from src.trading_runtime.arte_journal_writer import (
    V2CommittedPrefix, _CONTRACTS, _committed_batch_filter, _literal, _rows,
)


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
    return project_fixed_typed_activity_rows(
        client, prefix, rows, after_sequence=after_sequence)


def project_fixed_typed_activity_rows(
    client: Any, prefix: V2CommittedPrefix,
    rows: tuple[TypedJournalEvent, ...], *, after_sequence: int,
) -> dict[str, Any]:
    """Add typed signal UI fields to an already verified V2 event page."""
    if not isinstance(prefix, V2CommittedPrefix):
        raise ValueError("Fixed typed activity requires a verified V2 prefix")
    signals = [row for row in rows if row.detail_family == "trading_strategy_signal_v2"]
    source_budget = sum(int(row.detail["source_signal_count"]) for row in signals)
    if source_budget > 10_000:
        raise ValueError("Typed signal source page exceeds its evidence bound")
    sources_by_parent: dict[str, list[dict[str, Any]]] = {}
    if signals:
        ids = ",".join(f"toUUID({_literal(str(UUID(row.event['record_id'])))})"
                       for row in signals)
        columns = ",".join(name for name, _ in _CONTRACTS["trading_signal_source_v1"].columns)
        raw_sources = _rows(client,
            f"SELECT {columns} FROM arte.trading_signal_source_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} "
            f"AND parent_record_id IN ({ids}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {source_budget + 1} FORMAT JSONEachRow")
        if len(raw_sources) != source_budget:
            raise RuntimeError("Typed signal source count differs from its parent")
        for raw in raw_sources:
            source = _verified_row("trading_signal_source_v1", raw)
            parent_id = str(UUID(source["parent_record_id"]))
            sources_by_parent.setdefault(parent_id, []).append(source)
    signal_summaries = []
    for row in signals:
        event, detail = row.event, row.detail
        if detail is None or int(detail["evidence_node_count"]) != 0:
            raise RuntimeError("Typed signal has unsupported generic evidence")
        source_rows = sorted(sources_by_parent.pop(str(UUID(event["record_id"])), []),
                             key=lambda source: int(source["source_ordinal"]))
        if (len(source_rows) != int(detail["source_signal_count"])
                or [int(source["source_ordinal"]) for source in source_rows]
                != list(range(len(source_rows)))
                or any(source["run_id"] != event["run_id"]
                       or source["event_month"] != event["event_month"]
                       or source["batch_id"] != event["batch_id"]
                       or not source["source_signal_id"] for source in source_rows)):
            raise RuntimeError("Typed signal sources differ from their parent")
        signal_summaries.append({
            "record_id": event["record_id"], "sequence": event["sequence"],
            "event_time": event["event_time"], "recorded_at": event["recorded_at"],
            "account_id": event["account_id"], "entity_id": event["entity_id"],
            "strategy_id": detail["strategy_id"],
            "strategy_revision": detail["strategy_revision"],
            "signal_id": detail["signal_id"], "signal_type": detail["signal_type"],
            "ticker": detail["ticker"], "action": detail["action"],
            "direction": detail["direction"], "score": detail["score"],
            "confidence": detail["confidence"], "reason": detail["reason"],
            "working_timeframe": detail["working_timeframe"],
            "invalidation_price": detail["invalidation_price"],
            "source_signal_ids": tuple(source["source_signal_id"]
                                       for source in source_rows),
            "assignment_id": detail["decision_assignment_id"],
            "reference_price": detail["decision_reference_price"],
            "status": detail["decision_status"],
            "reason_detail": detail["decision_reason_detail"],
        })
    if sources_by_parent:
        raise RuntimeError("Typed signal source has no page parent")
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
        "strategy_signal_summaries": tuple(signal_summaries),
        "unprojected_activity_count": len(events) - len(signal_summaries),
        "caught_up_to_prefix": next_sequence == prefix.last_sequence,
    }
