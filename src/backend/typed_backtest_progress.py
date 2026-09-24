"""Typed fixed-Backtest controller progress at a completed market boundary.

This preserves scalar replay counters and clocks. It does not replace the
remaining strategy, broker, and market-state recovery contracts.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_projection import (
    backtest_cursor_batch, backtest_cursor_record_fields, load_latest_backtest_cursor,
)
from src.trading_runtime.arte_journal_writer import (
    CommittedPrefix, TypedJournalBatch, _canonical_typed_content, _literal, _rows,
)
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


def _counter(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError(f"Backtest {name} must be a UInt64")
    return value


def project_backtest_progress(
    record: JournalRecord, state: Mapping[str, Any], *,
    run_month: date, attempt_id: str, batch_id: str, prior_batch_id: str,
    status: str = "running",
) -> TypedJournalBatch:
    """Tie controller counters to the same typed event as the market cursor."""
    if (not isinstance(state, Mapping)
            or dict(state.get("identity") or {}).get("run_id") != record.run_id
            or dict(state.get("identity") or {}).get("mode") != "backtest"):
        raise ValueError("Backtest progress checkpoint identity is invalid")
    controller = dict(state.get("controller") or {})
    runtime = dict(state.get("runtime") or {})
    entity_id, payload = backtest_cursor_record_fields(
        controller.get("source_cursor"), controller.get("frame_cursor"),
        completed_at=record.event_time)
    if (record.entity_id != entity_id
            or {key: record.payload.get(key) for key in payload} != payload):
        raise ValueError("Backtest progress differs from its market boundary")
    current_time = datetime.fromisoformat(str(controller.get("current_time") or ""))
    if current_time.tzinfo is None or current_time != record.event_time:
        raise ValueError("Backtest controller clock differs from its market boundary")
    last = runtime.get("last_event_time")
    last_at = datetime.fromisoformat(str(last)) if last is not None else None
    if last_at is not None and (last_at.tzinfo is None or last_at > current_time):
        raise ValueError("Backtest runtime clock is not causal")
    batch = backtest_cursor_batch(
        record, run_month=run_month, attempt_id=attempt_id,
        batch_id=batch_id, prior_batch_id=prior_batch_id, source_cursor=entity_id)
    row = dict(
        record_id=str(uuid5(NAMESPACE_URL, f"{record.record_id}:backtest-progress")),
        run_id=record.run_id, event_month=batch.events[0]["event_month"],
        batch_id=batch_id, parent_record_id=record.record_id,
        controller_time=current_time.astimezone(timezone.utc).isoformat(),
        controller_processed_events=_counter(controller.get("processed_events"), "processed_events"),
        controller_warmup_events=_counter(controller.get("warmup_events"), "warmup_events"),
        controller_processed_frames=_counter(controller.get("processed_frames"), "processed_frames"),
        runtime_processed_events=_counter(runtime.get("processed_events"), "runtime processed_events"),
        runtime_last_event_time=(last_at.astimezone(timezone.utc).isoformat()
                                 if last_at is not None else None),
    )
    if status not in {"running", "completed", "stopped", "failed"}:
        raise ValueError("Backtest progress status is invalid")
    return replace(batch, status=status, backtest_progress=(row,))


def load_committed_backtest_progress(
    client: Any, prefix: CommittedPrefix, *, required: bool = False,
) -> dict[str, Any] | None:
    """Read progress tied to the latest verified typed market cursor."""
    cursor = load_latest_backtest_cursor(client, prefix)
    if cursor is None:
        if required:
            raise RuntimeError("Backtest recovery lacks a typed market boundary")
        return None
    parent = str(UUID(str(cursor["record_id"])))
    rows = _rows(client,
        "SELECT record_id,run_id,event_month,batch_id,parent_record_id,"
        "controller_time,controller_processed_events,controller_warmup_events,"
        "controller_processed_frames,runtime_processed_events,runtime_last_event_time,"
        "content_hash FROM arte.trading_backtest_progress_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND parent_record_id=toUUID({_literal(parent)}) "
        f"AND batch_id=toUUID({_literal(cursor['batch_id'])}) FORMAT JSONEachRow")
    if not rows and not required:
        return None  # Older committed prefixes predate the additive progress family.
    if len(rows) != 1:
        raise RuntimeError("Backtest recovery lacks one typed progress row")
    row = rows[0]
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content("trading_backtest_progress_v1", content,
                                         stored_utc=True)
    digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    from src.backend.backtest_market_data import market_day_boundary
    expected_time = market_day_boundary(
        date.fromisoformat(cursor["session_date"]), int(cursor["boundary_ms"]))
    if (digest != str(row["content_hash"])
            or canonical["parent_record_id"] != parent
            or canonical["run_id"] != prefix.run_id
            or canonical["batch_id"] != cursor["batch_id"]
            or canonical["controller_time"] != expected_time.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S.%f") + "000"):
        raise RuntimeError("Backtest progress differs from its committed cursor")
    return canonical
