"""Owned asynchronous reads of staged shared decision references.

This reader does not admit a strategy or prove its referenced market/financial
state. Publication and independent causal replay remain required.
"""
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.rule_decision_basis_projection import load_committed_rule_decision_page
from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_completion_record
from src.trading_runtime.arte_journal_reader import TypedJournalEvent, load_typed_event_page
from src.trading_runtime.journal_contract import JournalRecord
from src.backend.signal_stream_typed_readback import _normalize
from datetime import datetime


def verify_decision_completion_page(rows, events):
    """Require an exact normalized journal completion for every decision row."""
    if len(events) != len(rows):
        raise ValueError("Rule decision journal completion coverage differs")
    for (parent, children), item in zip(rows, events, strict=True):
        if (type(item) is not TypedJournalEvent
                or item.detail_family not in {"trading_strategy_signal_v1", "trading_strategy_signal_v2"}
                or item.detail is None):
            raise ValueError("Rule decision journal completion family differs")
        event, detail = item.event, item.detail
        if (any(event.get(key) != parent[key] for key in ("run_id", "batch_id", "record_id", "account_id", "event_month"))
                or any(detail.get(key) != parent[key] for key in ("run_id", "batch_id", "record_id", "account_id", "event_month"))
                or detail.get("signal_id") != parent["decision_id"]
                or detail.get("decision_status") != "completed"
                or event.get("correlation_id") != parent["decision_id"]
                or _normalize(detail.get("source_event_time"), "DateTime64(6, 'UTC')", occurrence=False) != parent["boundary_at"]):
            raise ValueError("Rule decision journal completion envelope differs")
        record = JournalRecord(event["record_id"], event["run_id"], int(event["sequence"]),
            datetime.fromisoformat(_normalize(event["event_time"], "DateTime64(6, 'UTC')", occurrence=False)),
            datetime.fromisoformat(_normalize(event["recorded_at"], "DateTime64(6, 'UTC')", occurrence=False)),
            event["category"], event["entity_type"], event["entity_id"], event["account_id"],
            dict(signal_type=detail["signal_type"], causation_id=event["causation_id"],
                metadata=dict(assignment_id=detail["decision_assignment_id"], reason_detail=detail["decision_reason_detail"])))
        verify_rule_decision_completion_record(parent, children, record)
    return rows


async def read_rule_decision_page_async(reader_factory, prefix, *, after_sequence=0, limit=200):
    """Keep database work off-loop and drain the owned client on cancellation."""
    if (not callable(reader_factory) or type(prefix) is not V4CommittedPrefix
            or not prefix.batch_ids or type(after_sequence) is not int
            or not 0 <= after_sequence <= prefix.last_sequence
            or type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError("Rule decision recovery requires a bounded exact V4 prefix")

    def read():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != "1":
                raise ValueError("Rule decision recovery requires a SELECT-only client")
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Rule decision prefix changed before readback")
            rows = load_committed_rule_decision_page(client, prefix,
                after_sequence=after_sequence, limit=limit)
            if rows:
                events = load_typed_event_page(client, prefix, after_sequence=after_sequence,
                    limit=len(rows), selected_sequences=tuple(parent["decision_sequence"] for parent, _ in rows))
                verify_decision_completion_page(rows, events)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Rule decision prefix changed after readback")
            return rows
        finally:
            client.close()

    return await _owned_worker(read)
