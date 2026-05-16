from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.data_provider.config import FEATURE_GROUPS, TIMEFRAMES


CARRYOVER_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
STATEFUL_OUTPUT_CHUNK_SESSIONS = {
    "1m": 6,
    "5m": 18,
    "15m": 28,
    "30m": 42,
    "1h": 50,
    "2h": 60,
    "4h": 80,
    "1d": 120,
}


def is_output_row(row: dict[str, Any]) -> bool:
    role = row.get("build_role")
    return role in {"output", "spread_backfill"} or (role is None and row.get("write_output") is not False)


def is_expected_row(row: dict[str, Any]) -> bool:
    if "expected_market_session" in row:
        return bool(row.get("expected_market_session"))
    return row.get("build_role") not in {"closed", "reference_only"}


def has_required_sources(row: dict[str, Any]) -> bool:
    return bool(row.get("exists")) and row.get("spread_exists") is not False


def session_timeframes(timeframes: Iterable[str] | None = None) -> list[str]:
    selected = list(TIMEFRAMES) if timeframes is None else list(timeframes)
    return [timeframe for timeframe in selected if timeframe != "1mo"]


def intraday_timeframes(timeframes: Iterable[str] | None = None) -> list[str]:
    return [timeframe for timeframe in session_timeframes(timeframes) if timeframe not in {"1m", "1d"}]


def chunk_count_for_sessions(session_count: int, timeframe: str) -> int:
    if session_count <= 0:
        return 0
    chunk_size = max(1, int(STATEFUL_OUTPUT_CHUNK_SESSIONS.get(timeframe, 10)))
    return (session_count + chunk_size - 1) // chunk_size


def request_options(job_status: dict[str, Any] | None) -> tuple[list[str], list[str], list[str]]:
    request = (job_status or {}).get("request") or {}
    return (
        list(request.get("timeframes") or TIMEFRAMES),
        list(request.get("feature_groups") or FEATURE_GROUPS),
        list(request.get("supervision_groups") or []),
    )


def event_elapsed_seconds(event: dict[str, Any], now: datetime | None = None) -> float:
    emitted_at = event.get("emitted_at")
    if not emitted_at:
        return 0.0
    try:
        started_at = datetime.fromisoformat(str(emitted_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    current = now or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0.0, (current - started_at.astimezone(timezone.utc)).total_seconds())


def started_at_seconds(started_at: str | None) -> float:
    if not started_at:
        return 0.0
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())


def build_plan_rows(events: list[dict[str, Any]], fallback_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for event in reversed(events):
        if event.get("event") == "plan_complete" and isinstance(event.get("plan"), list):
            return list(event["plan"])
    return fallback_rows


def summary_stage(
    phase: str,
    label: str,
    done: float,
    total: int,
    elapsed_sec: float = 0.0,
    active_items: list[dict[str, Any]] | None = None,
    unit_label: str = "items",
    active_count: int | None = None,
) -> dict[str, Any]:
    active = active_items or []
    bounded_done = min(float(total), max(0.0, float(done))) if total else 0.0
    return {
        "phase": phase,
        "label": label,
        "done": round(bounded_done, 2),
        "total": int(total),
        "elapsed_sec": round(float(elapsed_sec), 3),
        "progress": round((bounded_done / total) * 100.0, 2) if total else 0.0,
        "active_items": active,
        "unit_label": unit_label,
        "active_count": int(active_count if active_count is not None else len(active)),
    }


def progress_stage_for_event(event: dict[str, Any]) -> str | None:
    phase = str(event.get("phase") or "")
    group = str(event.get("group") or "")
    stateful = bool(event.get("stateful")) or phase == "stateful_features"
    if phase == "scan_source":
        return "scan_source"
    if phase == "reference_warmup":
        return "reference_window"
    if phase in {"raw_load", "canonicalize_1m", "spread_join", "spread_backfill", "aggregate", "aggregate_daily", "bars_write"} or group == "bars":
        return "build_bars"
    if stateful and (phase in {"feature_compute", "feature_write", "stateful_features"} or group.startswith("features_")):
        return "build_stateful"
    if phase in {"feature_compute", "feature_write"} or group.startswith("features_"):
        return "build_features"
    if phase == "run":
        return "finalize"
    return None


def active_item_key(event: dict[str, Any]) -> tuple[str, str, str, str]:
    if bool(event.get("stateful")) and event.get("chunk_index") is not None:
        return (
            "",
            str(event.get("timeframe") or ""),
            str(event.get("chunk_index") or ""),
            "stateful_chunk",
        )
    return (
        str(event.get("session_date") or ""),
        str(event.get("timeframe") or ""),
        str(event.get("group") or ""),
        str(event.get("phase") or ""),
    )


def compact_period(start: Any, end: Any) -> str:
    start_text = str(start or "")
    end_text = str(end or "")
    if start_text and end_text and start_text != end_text:
        return f"{start_text} to {end_text}"
    return start_text or end_text


def active_item_label(event: dict[str, Any]) -> str:
    session = str(event.get("session_date") or "")
    timeframe = str(event.get("timeframe") or "")
    group = str(event.get("group") or "")
    phase = str(event.get("phase") or "").replace("_", " ")
    if bool(event.get("stateful")):
        chunk_index = event.get("chunk_index")
        chunk_total = event.get("chunk_total") or event.get("chunk_count")
        write_period = compact_period(event.get("write_start"), event.get("write_end"))
        output_period = compact_period(event.get("output_start"), event.get("output_end"))
        period = write_period or output_period
        if chunk_index and chunk_total:
            base = f"{timeframe} | chunk {chunk_index}/{chunk_total}"
            return f"{base} | {period}" if period else base
        pending = event.get("pending_session_count")
        session_count = event.get("session_count")
        if pending is not None and session_count is not None:
            return f"{timeframe} | {pending}/{session_count} sessions pending"
        return f"{timeframe} | {phase or 'stateful'}"
    parts = [item for item in [session, timeframe, group or phase] if item]
    return " | ".join(parts) if parts else phase or "processing"


def active_item_detail(event: dict[str, Any]) -> str:
    if not bool(event.get("stateful")):
        return ""
    details: list[str] = []
    warmup_period = compact_period(event.get("warmup_start"), event.get("warmup_end"))
    window_period = compact_period(event.get("window_start"), event.get("window_end"))
    if warmup_period:
        details.append(f"warmup {warmup_period}")
    if window_period:
        details.append(f"window {window_period}")
    if event.get("bar_file_count") is not None:
        details.append(f"{event.get('bar_file_count')} bar files")
    if event.get("pending_writes") is not None:
        details.append(f"{event.get('pending_writes')} writes queued")
    if event.get("group"):
        details.append(str(event.get("group")))
    return " | ".join(details)


def stateful_chunk_key(event: dict[str, Any]) -> tuple[str, str, str, str] | None:
    if not bool(event.get("stateful")) or event.get("chunk_index") is None:
        return None
    return ("", str(event.get("timeframe") or ""), str(event.get("chunk_index") or ""), "stateful_chunk")


def summarize_phases(
    plan_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    timeframes: list[str],
    feature_groups: list[str],
    supervision_groups: list[str],
) -> list[dict[str, Any]]:
    selected_timeframes = session_timeframes(timeframes)
    output_rows = [row for row in plan_rows if is_output_row(row) and is_expected_row(row)]
    buildable_rows = [row for row in output_rows if has_required_sources(row)]
    reference_rows = [row for row in plan_rows if row.get("build_role") == "reference_only" and is_expected_row(row)]
    expected_rows = [row for row in plan_rows if is_expected_row(row)]
    buildable_count = len(buildable_rows)
    local_timeframes = [timeframe for timeframe in selected_timeframes if timeframe not in CARRYOVER_TIMEFRAMES]
    stateful_timeframes = [timeframe for timeframe in selected_timeframes if timeframe in CARRYOVER_TIMEFRAMES]
    contexts = buildable_count * len(selected_timeframes)

    scan_total = len(expected_rows)
    reference_total = len(reference_rows)
    bar_total = contexts
    feature_total = buildable_count * len(local_timeframes) * len(feature_groups)
    stateful_total = buildable_count * len(stateful_timeframes) * len(feature_groups)
    expected_stateful_chunks = sum(chunk_count_for_sessions(buildable_count, timeframe) for timeframe in stateful_timeframes)
    plan_event = next((event for event in reversed(events) if event.get("event") == "plan_complete"), {})
    resume_stage = str(plan_event.get("resume_stage") or "").lower()

    scan_done = 0.0
    scan_elapsed = 0.0
    reference_done = 0.0
    bar_done = 0.0
    bar_elapsed = 0.0
    feature_done = 0.0
    feature_elapsed = 0.0
    finalize_done = 0.0
    stateful_done = 0.0
    stateful_elapsed = 0.0
    stateful_chunk_totals: dict[str, int] = {}
    stateful_done_chunks: set[tuple[str, int]] = set()
    complete_stateful_timeframes: set[str] = set()
    if resume_stage == "stateful_features":
        bar_done = float(bar_total)
        feature_done = float(feature_total)
    active_by_stage: dict[str, dict[tuple[str, str, str, str], dict[str, Any]]] = {
        "scan_source": {},
        "reference_window": {},
        "build_bars": {},
        "build_features": {},
        "build_stateful": {},
        "finalize": {},
    }
    active_session_workers: dict[str, dict[str, Any]] = {}
    for event in events:
        phase = str(event.get("phase") or "")
        event_name = str(event.get("event") or "")
        status = str(event.get("status") or "")
        group = str(event.get("group") or "")
        duration = float(event.get("duration_sec") or 0.0)
        stage = progress_stage_for_event(event)
        key = active_item_key(event)
        session_key = str(event.get("session_date") or "")

        if stage and status in {"running", "queued"} and event_name == "phase_started":
            active_by_stage.setdefault(stage, {})[key] = {
                "label": active_item_label(event),
                "detail": active_item_detail(event),
                "phase": phase,
                "session_date": event.get("session_date"),
                "timeframe": event.get("timeframe"),
                "group": event.get("group"),
                "chunk_index": event.get("chunk_index"),
                "chunk_total": event.get("chunk_total") or event.get("chunk_count"),
                "output_start": event.get("output_start"),
                "output_end": event.get("output_end"),
                "warmup_start": event.get("warmup_start"),
                "warmup_end": event.get("warmup_end"),
                "bar_file_count": event.get("bar_file_count"),
                "pending_writes": event.get("pending_writes"),
                "started_at": event.get("emitted_at"),
            }

        if event_name == "session_started" and status == "running" and session_key:
            active_session_workers[session_key] = {
                "label": f"{session_key} | session worker",
                "phase": "session_worker",
                "session_date": event.get("session_date"),
                "started_at": event.get("emitted_at"),
            }
        elif event_name in {"session_complete", "session_failed", "session_cancelled"} and session_key:
            active_session_workers.pop(session_key, None)

        if event_name == "plan_complete" and status == "complete":
            scan_done = scan_total
            scan_elapsed += duration
            active_by_stage["scan_source"].clear()
        elif event_name == "session_skipped" and phase == "reference_warmup":
            reference_done += 1.0
        elif event_name == "phase_complete" and status == "complete" and phase in {"raw_load", "canonicalize_1m", "spread_join", "spread_backfill", "aggregate", "aggregate_daily"}:
            bar_elapsed += duration
            active_by_stage["build_bars"].pop(key, None)
        elif event_name == "artifact_complete" and status == "complete" and group == "bars":
            bar_done += 1.0
            bar_elapsed += duration
            active_by_stage["build_bars"].pop(key, None)
        elif event_name == "phase_complete" and status == "complete" and phase == "feature_compute":
            if stage == "build_stateful":
                stateful_elapsed += duration
                chunk_key = stateful_chunk_key(event)
                if chunk_key is not None:
                    active_by_stage["build_stateful"].pop(chunk_key, None)
                else:
                    active_by_stage["build_stateful"].pop(key, None)
            else:
                feature_elapsed += duration
                active_by_stage["build_features"].pop(key, None)
        elif event_name == "artifact_complete" and status == "complete" and group.startswith("features_"):
            if stage == "build_stateful":
                stateful_done += 1.0
                stateful_elapsed += duration
            else:
                feature_done += 1.0
                feature_elapsed += duration
        elif event_name == "phase_complete" and status == "complete" and phase == "stateful_features":
            timeframe = str(event.get("timeframe") or "")
            if timeframe:
                complete_stateful_timeframes.add(timeframe)
                for active_key in list(active_by_stage["build_stateful"]):
                    if active_key[1] == timeframe:
                        active_by_stage["build_stateful"].pop(active_key, None)
            else:
                active_by_stage["build_stateful"].pop(key, None)
        if event_name == "phase_started" and status == "running" and phase == "stateful_features":
            timeframe = str(event.get("timeframe") or "")
            chunk_total = int(event.get("chunk_count") or 0)
            if timeframe and chunk_total:
                stateful_chunk_totals[timeframe] = chunk_total
        elif event_name == "phase_checkpoint" and phase == "stateful_features" and event.get("chunk_index") is not None:
            timeframe = str(event.get("timeframe") or "")
            if timeframe:
                stateful_done_chunks.add((timeframe, int(event.get("chunk_index") or 0)))
            chunk_key = stateful_chunk_key(event)
            if chunk_key is not None:
                active_by_stage["build_stateful"].pop(chunk_key, None)
        elif event_name == "phase_checkpoint" and phase == "feature_write" and status in {"complete", "queued"}:
            chunk_key = stateful_chunk_key(event)
            if chunk_key is not None and int(event.get("pending_writes") or 0) <= 0:
                active_by_stage["build_stateful"].pop(chunk_key, None)
        elif event_name == "run_complete" and status == "complete":
            finalize_done = 1.0
            active_by_stage["finalize"].clear()
            for items in active_by_stage.values():
                items.clear()

    active_items = {}
    stateful_done_counts_by_timeframe = {
        timeframe: len({chunk_index for done_timeframe, chunk_index in stateful_done_chunks if done_timeframe == timeframe})
        for timeframe in stateful_timeframes
    }
    for stage, items in active_by_stage.items():
        values = list(items.values())
        if stage == "build_stateful":
            chunk_timeframes = {str(item.get("timeframe") or "") for item in values if item.get("chunk_index") is not None}
            values = [
                item
                for item in values
                if item.get("chunk_index") is not None or str(item.get("timeframe") or "") not in chunk_timeframes
            ]
            values = [
                item
                for item in values
                if item.get("chunk_index") is not None
                or (
                    str(item.get("timeframe") or "") not in complete_stateful_timeframes
                    and stateful_done_counts_by_timeframe.get(str(item.get("timeframe") or ""), 0)
                    < (stateful_chunk_totals.get(str(item.get("timeframe") or "")) or chunk_count_for_sessions(buildable_count, str(item.get("timeframe") or "")))
                )
            ]
        active_items[stage] = sorted(values, key=lambda item: str(item.get("label") or ""))[:8]
    if active_session_workers:
        active_items["build_bars"] = sorted(active_session_workers.values(), key=lambda item: str(item.get("label") or ""))[:8]
    active_counts = {stage: len(active_items.get(stage, [])) for stage in active_by_stage}
    if active_session_workers:
        active_counts["build_bars"] = len(active_session_workers)

    stateful_unit_label = "feature files"
    if expected_stateful_chunks:
        completed_chunk_count = 0
        for timeframe in stateful_timeframes:
            chunk_total = stateful_chunk_totals.get(timeframe) or chunk_count_for_sessions(buildable_count, timeframe)
            if timeframe in complete_stateful_timeframes:
                completed_chunk_count += chunk_total
            else:
                completed_chunk_count += min(
                    chunk_total,
                    len({chunk_index for done_timeframe, chunk_index in stateful_done_chunks if done_timeframe == timeframe}),
                )
        stateful_done = float(completed_chunk_count)
        stateful_total = expected_stateful_chunks
        stateful_unit_label = "chunks"

    rows = [
        summary_stage("scan_source", "Scan source", scan_done, scan_total, scan_elapsed, active_items["scan_source"], "sessions", active_counts["scan_source"]),
        summary_stage("reference_window", "Reference window", reference_done, reference_total, active_items=active_items["reference_window"], unit_label="sessions", active_count=active_counts["reference_window"]),
        summary_stage("build_bars", "Build bars", bar_done, bar_total, bar_elapsed, active_items["build_bars"], "bar files", active_counts["build_bars"]),
        summary_stage("build_features", "Build session features", feature_done, feature_total, feature_elapsed, active_items["build_features"], "feature files", active_counts["build_features"]),
        summary_stage("build_stateful", "Build stateful features", stateful_done, stateful_total, stateful_elapsed, active_items["build_stateful"], stateful_unit_label, active_counts["build_stateful"]),
        summary_stage("finalize", "Finalize build", finalize_done, 1, active_items=active_items["finalize"], unit_label="step", active_count=active_counts["finalize"]),
    ]
    return rows


def stage_state(total: int) -> dict[str, Any]:
    return {"done": 0.0, "total": int(total), "elapsed_sec": 0.0, "active": [], "partial": 0.0}


def timeframe_stage_totals(feature_groups: list[str], supervision_groups: list[str]) -> dict[str, int]:
    return {
        "normalize": 1,
        "write_bars": 1,
        "feature_calc": 1 if feature_groups or supervision_groups else 0,
        "write_features": len(feature_groups),
        "bar_labels": 1 if "bar" in supervision_groups else 0,
        "method_labels": 1 if "method" in supervision_groups else 0,
        "scanner_labels": 1 if "scanner" in supervision_groups else 0,
    }


def new_timeframe_state(stage_totals: dict[str, int]) -> dict[str, dict[str, Any]]:
    return {stage: stage_state(total) for stage, total in stage_totals.items()}


def start_stage(state: dict[str, Any], event: dict[str, Any]) -> None:
    if int(state.get("total") or 0):
        state.setdefault("active", []).append(event)


def complete_stage(state: dict[str, Any], event: dict[str, Any], increment: float = 1.0) -> None:
    total = int(state.get("total") or 0)
    if total:
        state["done"] = min(float(total), float(state.get("done") or 0.0) + increment)
    state["elapsed_sec"] = float(state.get("elapsed_sec") or 0.0) + float(event.get("duration_sec") or 0.0)
    if state.get("active"):
        state["active"].pop(0)
    state["partial"] = 0.0


def set_stage_partial(state: dict[str, Any], event: dict[str, Any]) -> None:
    total = float(event.get("horizon_total") or event.get("progress_total") or 0)
    current = float(event.get("horizon_index") or event.get("progress_completed") or 0)
    if total > 0:
        state["partial"] = max(float(state.get("partial") or 0.0), min(1.0, current / total))


def materialize_stage(label: str, state: dict[str, Any]) -> dict[str, Any]:
    total = int(state.get("total") or 0)
    now = datetime.now(timezone.utc)
    elapsed = float(state.get("elapsed_sec") or 0.0) + sum(event_elapsed_seconds(event, now) for event in state.get("active", []))
    done = min(float(total), float(state.get("done") or 0.0) + float(state.get("partial") or 0.0)) if total else 0.0
    return {
        "label": label,
        "done": round(done, 2),
        "total": total,
        "elapsed_sec": round(elapsed, 3),
        "progress": round((done / total) * 100.0, 2) if total else 0.0,
    }


def total_stage_progress(stages: Iterable[dict[str, Any]]) -> tuple[float, int, float]:
    done = 0.0
    total = 0
    elapsed = 0.0
    now = datetime.now(timezone.utc)
    for state in stages:
        stage_total = int(state.get("total") or 0)
        total += stage_total
        done += min(float(stage_total), float(state.get("done") or 0.0) + float(state.get("partial") or 0.0)) if stage_total else 0.0
        elapsed += float(state.get("elapsed_sec") or 0.0)
        elapsed += sum(event_elapsed_seconds(event, now) for event in state.get("active", []))
    return done, total, elapsed


def build_session_cards(
    plan_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    timeframes: list[str],
    feature_groups: list[str],
    supervision_groups: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected_timeframes = session_timeframes(timeframes)
    tf_stage_totals = timeframe_stage_totals(feature_groups, supervision_groups)
    planned_total = 1 + max(0, len(selected_timeframes) - 1) + sum(tf_stage_totals.values()) * len(selected_timeframes)
    session_rows: dict[str, dict[str, Any]] = {}
    for row in plan_rows:
        if not is_output_row(row):
            continue
        session_rows[row["session_date"]] = {
            "session_date": row["session_date"],
            "expected_market_session": is_expected_row(row),
            "exists": row.get("exists"),
            "spread_exists": row.get("spread_exists"),
            "status": "queued" if has_required_sources(row) and is_expected_row(row) else row.get("status", "closed"),
            "phase": row.get("status", "queued"),
            "duration_sec": 0.0,
            "step_done": 0.0,
            "step_total": planned_total,
            "raw": stage_state(1),
            "other_timeframes": stage_state(max(0, len(selected_timeframes) - 1)),
            "timeframe_stages": {timeframe: new_timeframe_state(tf_stage_totals) for timeframe in selected_timeframes},
        }

    def timeframe_stage(row: dict[str, Any], timeframe: str, stage: str) -> dict[str, Any] | None:
        return row.get("timeframe_stages", {}).get(timeframe, {}).get(stage)

    for event in events:
        session_date = event.get("session_date")
        if not session_date or session_date not in session_rows:
            continue
        row = session_rows[str(session_date)]
        if event.get("timeframe") == "1mo" or event.get("phase") == "run":
            continue
        row["phase"] = event.get("phase", row.get("phase"))
        if event.get("status") in {"failed", "error"}:
            row["status"] = "failed"
        elif event.get("event") == "session_started":
            row["status"] = "running"
        elif event.get("event") != "session_skipped" and row.get("status") not in {"failed", "complete"}:
            row["status"] = "running"

        phase = event.get("phase")
        group = event.get("group")
        timeframe = str(event.get("timeframe") or "")

        if event.get("event") == "phase_started" and event.get("status") == "running":
            if phase == "raw_load":
                start_stage(row["raw"], event)
            elif phase in {"aggregate", "aggregate_daily"}:
                start_stage(row["other_timeframes"], event)
                stage = timeframe_stage(row, timeframe, "normalize")
                if stage is not None:
                    start_stage(stage, event)
            elif phase == "canonicalize_1m":
                stage = timeframe_stage(row, "1m", "normalize")
                if stage is not None:
                    start_stage(stage, event)
            elif phase == "bars_write":
                stage = timeframe_stage(row, timeframe, "write_bars")
                if stage is not None:
                    start_stage(stage, event)
            elif phase == "feature_compute":
                stage = timeframe_stage(row, timeframe, "feature_calc")
                if stage is not None:
                    start_stage(stage, event)
            elif phase == "supervision_bar":
                stage = timeframe_stage(row, timeframe, "bar_labels")
                if stage is not None:
                    start_stage(stage, event)
            elif phase == "supervision_method":
                stage = timeframe_stage(row, timeframe, "method_labels")
                if stage is not None:
                    start_stage(stage, event)
            elif phase == "supervision_scanner":
                stage = timeframe_stage(row, timeframe, "scanner_labels")
                if stage is not None:
                    start_stage(stage, event)

        if event.get("event") == "phase_progress" and phase == "supervision_bar":
            stage = timeframe_stage(row, timeframe, "bar_labels")
            if stage is not None:
                set_stage_partial(stage, event)

        if event.get("status") == "complete":
            if phase == "raw_load" and event.get("event") == "phase_complete":
                complete_stage(row["raw"], event)
            elif phase in {"aggregate", "aggregate_daily"} and event.get("event") == "phase_complete":
                complete_stage(row["other_timeframes"], event)
                stage = timeframe_stage(row, timeframe, "normalize")
                if stage is not None:
                    complete_stage(stage, event)
            elif phase == "canonicalize_1m" and event.get("event") == "phase_complete":
                stage = timeframe_stage(row, "1m", "normalize")
                if stage is not None:
                    complete_stage(stage, event)
            elif event.get("event") == "artifact_complete" and group == "bars":
                stage = timeframe_stage(row, timeframe, "write_bars")
                if stage is not None:
                    complete_stage(stage, event)
            elif phase == "feature_compute" and event.get("event") == "phase_complete":
                stage = timeframe_stage(row, timeframe, "feature_calc")
                if stage is not None:
                    complete_stage(stage, event)
            elif event.get("event") == "artifact_complete" and str(group or "").startswith("features_"):
                stage = timeframe_stage(row, timeframe, "write_features")
                if stage is not None:
                    complete_stage(stage, event)
            elif event.get("event") == "artifact_complete" and group == "supervision_bar":
                stage = timeframe_stage(row, timeframe, "bar_labels")
                if stage is not None:
                    complete_stage(stage, event)
            elif event.get("event") == "artifact_complete" and group == "supervision_method":
                stage = timeframe_stage(row, timeframe, "method_labels")
                if stage is not None:
                    complete_stage(stage, event)
            elif event.get("event") == "artifact_complete" and group == "supervision_scanner":
                stage = timeframe_stage(row, timeframe, "scanner_labels")
                if stage is not None:
                    complete_stage(stage, event)
        if event.get("event") == "session_skipped":
            row["status"] = event.get("status", "missing_raw")

    stage_labels = [
        ("normalize", "Normalize"),
        ("write_bars", "Write bars"),
        ("feature_calc", "Feature calc"),
        ("write_features", "Write features"),
        ("bar_labels", "Bar labels"),
        ("method_labels", "Method labels"),
        ("scanner_labels", "Scanner labels"),
    ]
    materialized = []
    for row in session_rows.values():
        all_stages = [row["raw"], row["other_timeframes"]]
        timeframe_cards = []
        for timeframe, stages in row.get("timeframe_stages", {}).items():
            tf_done, tf_total, tf_elapsed = total_stage_progress(stages.values())
            all_stages.extend(stages.values())
            timeframe_cards.append(
                {
                    "timeframe": timeframe,
                    "done": round(tf_done, 2),
                    "total": tf_total,
                    "elapsed_sec": round(tf_elapsed, 3),
                    "progress": round((tf_done / tf_total) * 100.0, 2) if tf_total else 0.0,
                    "stages": [
                        materialize_stage(label, stages.get(stage, stage_state(0)))
                        for stage, label in stage_labels
                        if int(stages.get(stage, {}).get("total") or 0) > 0
                    ],
                }
            )
        done, total, elapsed = total_stage_progress(all_stages)
        row["step_done"] = round(done, 2)
        row["step_total"] = total
        row["duration_sec"] = round(elapsed, 3)
        if row.get("status") != "failed":
            if row.get("expected_market_session") and has_required_sources(row) and total and done >= total:
                row["status"] = "complete"
            elif row.get("expected_market_session") and has_required_sources(row) and done > 0:
                row["status"] = "running"
        materialized.append(
            {
                "session_date": row["session_date"],
                "status": row["status"],
                "phase": row["phase"],
                "done": row["step_done"],
                "total": row["step_total"],
                "elapsed_sec": row["duration_sec"],
                "progress": round((row["step_done"] / row["step_total"]) * 100.0, 2) if row["step_total"] else 0.0,
                "day_stages": [
                    materialize_stage("Raw load", row["raw"]),
                    materialize_stage("Other timeframes", row["other_timeframes"]),
                ],
                "timeframes": timeframe_cards,
            }
        )

    completed = sorted([row for row in materialized if row["status"] == "complete"], key=lambda row: row["session_date"], reverse=True)
    active = sorted([row for row in materialized if row["status"] in {"queued", "running", "failed"}], key=lambda row: row["session_date"])
    return active[:5], completed


def build_metrics(plan_rows: list[dict[str, Any]], events: list[dict[str, Any]], job_status: dict[str, Any] | None) -> dict[str, Any]:
    expected = [row for row in plan_rows if is_expected_row(row)]
    output = [row for row in expected if is_output_row(row)]
    reference = [row for row in expected if row.get("build_role") == "reference_only"]
    buildable = [row for row in output if has_required_sources(row)]
    missing = [row for row in expected if not has_required_sources(row)]
    missing_reference = [row for row in reference if not row.get("exists")]
    closed = [row for row in plan_rows if not is_expected_row(row)]
    artifact_events = [event for event in events if event.get("event") == "artifact_complete"]
    run_complete = next((event for event in reversed(events) if event.get("event") == "run_complete"), None)
    elapsed = float(run_complete.get("duration_sec") or 0.0) if run_complete else started_at_seconds((job_status or {}).get("started_at"))
    status = str((job_status or {}).get("status") or ("ready" if not events else events[-1].get("status") or "running"))
    output_start = next((str(row.get("session_date")) for row in output), None)
    plan_event = next((event for event in reversed(events) if event.get("event") == "plan_complete"), {})
    return {
        "raw": len(buildable),
        "expected": len(expected),
        "missing": len(missing),
        "reference_sessions": len(reference),
        "missing_reference_sessions": len(missing_reference),
        "output_sessions": len(output),
        "output_start_date": output_start,
        "warmup_sessions": int(plan_event.get("warmup_sessions") or len(reference) or 0),
        "carryover_timeframes": list(plan_event.get("carryover_timeframes") or []),
        "closed": len(closed),
        "rows": sum(int(event.get("rows_out") or 0) for event in artifact_events),
        "written_bytes": sum(int(event.get("size_bytes") or 0) for event in artifact_events),
        "elapsed_sec": round(elapsed, 3),
        "status": status,
    }


def build_progress_model(
    *,
    source_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    job_status: dict[str, Any] | None,
) -> dict[str, Any]:
    timeframes, feature_groups, supervision_groups = request_options(job_status)
    plan_rows = build_plan_rows(events, source_rows)
    phases = summarize_phases(plan_rows, events, timeframes, feature_groups, supervision_groups)
    active_cards, completed_cards = build_session_cards(plan_rows, events, timeframes, feature_groups, supervision_groups)
    return {
        "metrics": build_metrics(plan_rows, events, job_status),
        "phases": phases,
        "active_sessions": active_cards,
        "completed_sessions": completed_cards,
        "plan": plan_rows,
        "artifact_events": [event for event in events if event.get("event") == "artifact_complete"],
        "phase_events": [event for event in events if event.get("duration_sec") is not None],
        "timeframes": timeframes,
        "feature_groups": feature_groups,
        "supervision_groups": supervision_groups,
    }
