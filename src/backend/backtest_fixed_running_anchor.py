"""Read-only running-prefix anchor for a future fully typed fixed resume.

This certifies only the last completed market cursor. It is not a broker,
strategy, or controller-state recovery contract and cannot resume execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from typing import Any
from uuid import UUID

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, market_day_boundary,
)
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_journal_writer import (
    V2CommittedPrefix, load_committed_prefix, load_typed_run_context,
)


@dataclass(frozen=True, slots=True)
class FixedRunningPrefixAnchor:
    run_id: str
    batch_id: str
    journal_sequence: int
    source_cursor: str
    session_date: str
    boundary_ms: int
    market_sequence: int
    completed_at: datetime
    frame_cursor: tuple[datetime, str, str, int] | None


def load_fixed_running_prefix_anchor(
    client: Any, *, run_id: str, plan: CertifiedMarketDayPlan,
    configuration_hash: str, account_ids: tuple[str, ...],
) -> FixedRunningPrefixAnchor:
    """Cold-verify one V2 journal prefix and its exact pinned market cursor.

    A caller must separately recover every mutable runtime family before using
    this anchor. No disk, SQLite, or retired Backtest journal path is consulted.
    """
    if (not run_id or not configuration_hash or not account_ids
            or len(set(account_ids)) != len(account_ids)
            or not isinstance(plan, CertifiedMarketDayPlan)
            or plan.execution_interval.kind != "fixed" or not plan.token):
        raise ValueError("Fixed running anchor lacks pinned run and market identity")
    context = load_typed_run_context(client, run_id)
    if (context.get("mode") != "backtest"
            or context.get("configuration_hash") != configuration_hash
            or context.get("market_plan_token") != plan.token
            or tuple(context.get("account_ids") or ()) != account_ids):
        raise RuntimeError("Fixed running anchor differs from typed run context")
    prefix = load_committed_prefix(client, run_id, journal_profile="backtest_v2")
    if (not isinstance(prefix, V2CommittedPrefix)
            or prefix.run_id != run_id or prefix.status != "running"
            or prefix.last_sequence < 1 or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]):
        raise RuntimeError("Fixed running anchor lacks a verified V2 running prefix")
    cursor = load_latest_backtest_cursor(client, prefix)
    if not isinstance(cursor, dict):
        raise RuntimeError("Fixed running anchor lacks a committed market cursor")
    try:
        day = date.fromisoformat(str(cursor["session_date"]))
        boundary_ms = cursor["boundary_ms"]
        market_sequence = cursor["market_sequence"]
        event_sequence = cursor["event_sequence"]
        batch_id = str(UUID(str(cursor["batch_id"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Fixed running cursor identity is malformed") from exc
    if (type(boundary_ms) is not int or boundary_ms % 100
            or type(market_sequence) is not int or market_sequence < 1
            or type(event_sequence) is not int
            or day.isoformat() not in plan.sessions
            or cursor.get("run_id") != run_id or cursor.get("account_id") != ""
            or cursor.get("event_month") != day.replace(day=1).isoformat()
            or event_sequence != prefix.last_sequence
            or batch_id != prefix.last_batch_id
            or prefix.source_cursor != f"{day.isoformat()}:{boundary_ms}"):
        raise RuntimeError("Fixed running cursor is not the exact terminal V2 prefix")
    completed_at = market_day_boundary(day, boundary_ms).astimezone(timezone.utc)
    frame_fields = tuple(cursor.get(key) for key in (
        "frame_as_of", "frame_ticker", "frame_timeframe", "frame_sequence"))
    frame_cursor = None
    if any(value is not None for value in frame_fields):
        if any(value is None for value in frame_fields):
            raise RuntimeError("Fixed running frame cursor is incomplete")
        try:
            frame_clock = str(frame_fields[0])
            if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}", frame_clock) is None:
                raise ValueError("Fixed frame clock is not stored UTC DateTime64(6)")
            frame_at = datetime.fromisoformat(frame_clock + "+00:00")
            interval = ExecutionInterval.parse(frame_fields[2])
        except ValueError as exc:
            raise RuntimeError("Fixed running frame cursor is invalid") from exc
        if (frame_at.tzinfo is None or frame_at.astimezone(timezone.utc) > completed_at
                or str(frame_fields[1]) not in plan.tickers
                or interval.kind != "fixed"
                or interval.milliseconds not in plan.required_resolutions_ms
                or type(frame_fields[3]) is not int
                or not 0 <= frame_fields[3] <= market_sequence):
            raise RuntimeError("Fixed running frame cursor is not causal or pinned")
        frame_cursor = (frame_at.astimezone(timezone.utc),
                        str(frame_fields[1]), str(frame_fields[2]), frame_fields[3])
    return FixedRunningPrefixAnchor(
        run_id, batch_id, event_sequence, prefix.source_cursor,
        day.isoformat(), boundary_ms, market_sequence, completed_at, frame_cursor)
