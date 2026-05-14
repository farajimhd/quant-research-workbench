from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.data_provider.config import FEATURE_GROUPS, TIMEFRAMES


PHASE_LABELS = {
    "scan_source": "Scan",
    "raw_load": "Raw load",
    "canonicalize_1m": "Normalize",
    "aggregate": "Intraday aggregates",
    "aggregate_daily": "Daily aggregate",
    "bars_write": "Bar files",
    "feature_compute": "Session/carry-over features",
    "feature_write": "Feature files",
    "supervision_bar": "Bar label files",
    "supervision_method": "Method label files",
    "supervision_scanner": "Scanner files",
}


def is_output_row(row: dict[str, Any]) -> bool:
    role = row.get("build_role")
    return role == "output" or role is None


def session_timeframes(timeframes: Iterable[str] | None = None) -> list[str]:
    selected = list(TIMEFRAMES) if timeframes is None else list(timeframes)
    return [timeframe for timeframe in selected if timeframe != "1mo"]


def intraday_timeframes(timeframes: Iterable[str] | None = None) -> list[str]:
    return [timeframe for timeframe in session_timeframes(timeframes) if timeframe not in {"1m", "1d"}]


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


def phase_totals(
    plan_rows: list[dict[str, Any]],
    timeframes: list[str],
    feature_groups: list[str],
    supervision_groups: list[str],
) -> dict[str, int]:
    buildable = [row for row in plan_rows if is_output_row(row) and row.get("expected_market_session") and row.get("exists")]
    buildable_count = len(buildable)
    selected_timeframes = session_timeframes(timeframes)
    contexts = buildable_count * len(selected_timeframes)
    totals = {
        "scan_source": 1,
        "raw_load": buildable_count,
        "canonicalize_1m": buildable_count,
        "aggregate": buildable_count * len(intraday_timeframes(timeframes)),
        "aggregate_daily": buildable_count if "1d" in selected_timeframes else 0,
        "bars_write": contexts,
        "feature_compute": contexts if feature_groups or supervision_groups else 0,
        "feature_write": contexts * len(feature_groups),
        "supervision_bar": contexts if "bar" in supervision_groups else 0,
        "supervision_method": contexts if "method" in supervision_groups else 0,
        "supervision_scanner": contexts if "scanner" in supervision_groups else 0,
    }
    return totals


def summarize_phases(
    plan_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    timeframes: list[str],
    feature_groups: list[str],
    supervision_groups: list[str],
) -> list[dict[str, Any]]:
    totals = phase_totals(plan_rows, timeframes, feature_groups, supervision_groups)
    completed = {phase: 0 for phase in totals}
    elapsed = {phase: 0.0 for phase in totals}
    active = {phase: [] for phase in totals}
    partials: dict[str, dict[tuple[str, str, str], float]] = {phase: {} for phase in totals}
    for event in events:
        phase = str(event.get("phase") or "")
        if phase not in totals:
            continue
        partial_key = (
            str(event.get("session_date") or ""),
            str(event.get("timeframe") or ""),
            str(event.get("group") or ""),
        )
        if event.get("event") == "phase_progress":
            total = float(event.get("horizon_total") or event.get("progress_total") or 0)
            current = float(event.get("horizon_index") or event.get("progress_completed") or 0)
            if total > 0:
                partials[phase][partial_key] = max(partials[phase].get(partial_key, 0.0), min(1.0, current / total))
            continue
        if event.get("event") == "phase_started" and event.get("status") == "running":
            active[phase].append(event)
            continue
        if event.get("event") in {"plan_complete", "phase_complete", "artifact_complete", "run_complete"} and event.get("status") == "complete":
            completed[phase] = completed.get(phase, 0) + 1
            elapsed[phase] = elapsed.get(phase, 0.0) + float(event.get("duration_sec") or 0.0)
            if active.get(phase):
                active[phase].pop(0)
            partials[phase].pop(partial_key, None)

    now = datetime.now(timezone.utc)
    rows = []
    for phase, total in totals.items():
        done = min(completed.get(phase, 0), total)
        display_done = min(float(total), float(done) + sum(partials[phase].values())) if total else 0.0
        phase_elapsed = elapsed.get(phase, 0.0) + sum(event_elapsed_seconds(started, now) for started in active.get(phase, []))
        rows.append(
            {
                "phase": phase,
                "label": PHASE_LABELS.get(phase, phase.replace("_", " ").title()),
                "done": round(display_done, 2),
                "total": total,
                "elapsed_sec": round(phase_elapsed, 3),
                "progress": round((display_done / total) * 100.0, 2) if total else 0.0,
            }
        )
    return [row for row in rows if row["total"] > 0 or row["phase"] == "scan_source"]


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
            "expected_market_session": row.get("expected_market_session"),
            "exists": row.get("exists"),
            "status": "queued" if row.get("exists") and row.get("expected_market_session") else row.get("status", "closed"),
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
            if row.get("expected_market_session") and row.get("exists") and total and done >= total:
                row["status"] = "complete"
            elif row.get("expected_market_session") and row.get("exists") and done > 0:
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
    expected = [row for row in plan_rows if row.get("expected_market_session")]
    output = [row for row in expected if is_output_row(row)]
    reference = [row for row in expected if row.get("build_role") == "reference_only"]
    buildable = [row for row in output if row.get("exists")]
    missing = [row for row in expected if not row.get("exists")]
    missing_reference = [row for row in reference if not row.get("exists")]
    closed = [row for row in plan_rows if not row.get("expected_market_session")]
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
