from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Callable

import polars as pl

from src.data_provider.calendar import market_sessions, scan_market_source
from src.data_provider.config import BuildRequest
from src.data_provider.features import FEATURE_COLUMNS, add_feature_columns, select_feature_group
from src.data_provider.manifest import ArtifactRecord, upsert_artifact
from src.data_provider.raw_loader import load_raw_minute_bars, raw_minute_path
from src.data_provider.store import partition_path, read_frame, write_frame
from src.data_provider.supervision import build_bar_supervision, build_method_supervision, build_scanner_supervision
from src.data_provider.timeframes import aggregate_daily, aggregate_intraday, aggregate_monthly, canonicalize_1m


ProgressCallback = Callable[[dict], None]


def emit(progress_callback: ProgressCallback | None, event: dict) -> None:
    if progress_callback:
        progress_callback(event)


def elapsed_since(started_at: float) -> float:
    return round(perf_counter() - started_at, 4)


def artifact_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def estimate_session_units(request: BuildRequest, buildable_sessions: int, monthly_periods: int) -> int:
    session_timeframes = [timeframe for timeframe in request.timeframes if timeframe != "1mo"]
    per_timeframe = 1 + len(request.feature_groups) + len(request.supervision_groups)
    per_session = 2 + (len(session_timeframes) * per_timeframe)
    monthly_units = monthly_periods * per_timeframe if "1mo" in request.timeframes else 0
    return max(1, buildable_sessions * per_session + monthly_units)


def write_artifact(
    *,
    root: Path,
    group: str,
    timeframe: str,
    session_date: str,
    frame: pl.DataFrame,
    source_path: Path | None = None,
) -> Path:
    path = partition_path(root, group, timeframe, session_date)
    write_frame(path, frame)
    source_exists = source_path.exists() if source_path else False
    upsert_artifact(
        root,
        ArtifactRecord(
            group=group,
            timeframe=timeframe,
            session_date=session_date,
            path=str(path),
            rows=frame.height,
            columns=list(frame.columns),
            built_at=datetime.now().isoformat(timespec="seconds"),
            source_path=str(source_path) if source_path else None,
            source_modified_at=source_path.stat().st_mtime if source_exists else None,
            source_size_bytes=source_path.stat().st_size if source_exists else None,
        ),
    )
    return path


def build_feature_groups(
    *,
    request: BuildRequest,
    timeframe: str,
    session_date: str,
    bars: pl.DataFrame,
    source_path: Path,
    progress_callback: ProgressCallback | None = None,
    progress_state: dict | None = None,
) -> pl.DataFrame:
    if bars.is_empty():
        return bars
    started_at = perf_counter()
    features = add_feature_columns(bars)
    emit(
        progress_callback,
        {
            "event": "phase_complete",
            "phase": "feature_compute",
            "status": "complete",
            "session_date": session_date,
            "timeframe": timeframe,
            "rows_in": bars.height,
            "rows_out": features.height,
            "duration_sec": elapsed_since(started_at),
        },
    )
    for group in request.feature_groups:
        if group not in FEATURE_COLUMNS:
            continue
        started_at = perf_counter()
        group_frame = select_feature_group(features, group)
        path = write_artifact(
            root=request.processed_root,
            group=f"features_{group}",
            timeframe=timeframe,
            session_date=session_date,
            frame=group_frame,
            source_path=source_path,
        )
        if progress_state is not None:
            progress_state["completed_units"] += 1
        emit(
            progress_callback,
            {
                "event": "artifact_complete",
                "phase": "feature_write",
                "status": "complete",
                "session_date": session_date,
                "timeframe": timeframe,
                "group": f"features_{group}",
                "rows_out": group_frame.height,
                "duration_sec": elapsed_since(started_at),
                "path": str(path),
                "size_bytes": artifact_size(path),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
    return features


def build_supervision_groups(
    *,
    request: BuildRequest,
    timeframe: str,
    session_date: str,
    bars: pl.DataFrame,
    source_path: Path,
    progress_callback: ProgressCallback | None = None,
    progress_state: dict | None = None,
) -> None:
    if bars.is_empty() or not request.supervision_groups:
        return
    bar_supervision = None
    method_supervision = None
    if "bar" in request.supervision_groups:
        started_at = perf_counter()
        bar_supervision = build_bar_supervision(bars)
        path = write_artifact(
            root=request.processed_root,
            group="supervision_bar",
            timeframe=timeframe,
            session_date=session_date,
            frame=bar_supervision,
            source_path=source_path,
        )
        if progress_state is not None:
            progress_state["completed_units"] += 1
        emit(
            progress_callback,
            {
                "event": "artifact_complete",
                "phase": "supervision_bar",
                "status": "complete",
                "session_date": session_date,
                "timeframe": timeframe,
                "group": "supervision_bar",
                "rows_in": bars.height,
                "rows_out": bar_supervision.height,
                "duration_sec": elapsed_since(started_at),
                "path": str(path),
                "size_bytes": artifact_size(path),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
    if "method" in request.supervision_groups or "scanner" in request.supervision_groups:
        started_at = perf_counter()
        method_supervision = build_method_supervision(bars)
        if "method" in request.supervision_groups:
            path = write_artifact(
                root=request.processed_root,
                group="supervision_method",
                timeframe=timeframe,
                session_date=session_date,
                frame=method_supervision,
                source_path=source_path,
            )
            if progress_state is not None:
                progress_state["completed_units"] += 1
            emit(
                progress_callback,
                {
                    "event": "artifact_complete",
                    "phase": "supervision_method",
                    "status": "complete",
                    "session_date": session_date,
                    "timeframe": timeframe,
                    "group": "supervision_method",
                    "rows_in": bars.height,
                    "rows_out": method_supervision.height,
                    "duration_sec": elapsed_since(started_at),
                    "path": str(path),
                    "size_bytes": artifact_size(path),
                    "work_completed": progress_state.get("completed_units") if progress_state else None,
                    "work_total": progress_state.get("total_units") if progress_state else None,
                },
            )
    if "scanner" in request.supervision_groups:
        if method_supervision is None:
            method_supervision = build_method_supervision(bars)
        started_at = perf_counter()
        scanner_supervision = build_scanner_supervision(method_supervision)
        path = write_artifact(
            root=request.processed_root,
            group="supervision_scanner",
            timeframe=timeframe,
            session_date=session_date,
            frame=scanner_supervision,
            source_path=source_path,
        )
        if progress_state is not None:
            progress_state["completed_units"] += 1
        emit(
            progress_callback,
            {
                "event": "artifact_complete",
                "phase": "supervision_scanner",
                "status": "complete",
                "session_date": session_date,
                "timeframe": timeframe,
                "group": "supervision_scanner",
                "rows_in": method_supervision.height,
                "rows_out": scanner_supervision.height,
                "duration_sec": elapsed_since(started_at),
                "path": str(path),
                "size_bytes": artifact_size(path),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )


def write_bars_artifact(
    *,
    request: BuildRequest,
    timeframe: str,
    session_date: str,
    bars: pl.DataFrame,
    source_path: Path,
    progress_callback: ProgressCallback | None,
    progress_state: dict,
) -> Path:
    started_at = perf_counter()
    path = write_artifact(
        root=request.processed_root,
        group="bars",
        timeframe=timeframe,
        session_date=session_date,
        frame=bars,
        source_path=source_path,
    )
    progress_state["completed_units"] += 1
    emit(
        progress_callback,
        {
            "event": "artifact_complete",
            "phase": "bars_write",
            "status": "complete",
            "session_date": session_date,
            "timeframe": timeframe,
            "group": "bars",
            "rows_out": bars.height,
            "duration_sec": elapsed_since(started_at),
            "path": str(path),
            "size_bytes": artifact_size(path),
            "work_completed": progress_state["completed_units"],
            "work_total": progress_state["total_units"],
        },
    )
    return path


def month_session_dates(start: date, end: date) -> list[str]:
    return [session.isoformat() for session in market_sessions(start, end)]


def rebuild_monthly_artifacts(
    request: BuildRequest,
    touched_months: set[str],
    progress_callback: ProgressCallback | None,
    progress_state: dict,
) -> None:
    if "1mo" not in request.timeframes or not touched_months:
        return
    for month in sorted(touched_months):
        year, month_number = (int(part) for part in month.split("-"))
        month_start = datetime(year, month_number, 1).date()
        month_end = datetime(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1).date()
        month_end -= timedelta(days=1)
        started_at = perf_counter()
        daily_frames = []
        for session_text in month_session_dates(month_start, month_end):
            path = partition_path(request.processed_root, "bars", "1d", session_text)
            if path.exists():
                daily_frames.append(read_frame(path))
        if not daily_frames:
            continue
        daily = pl.concat(daily_frames, how="diagonal").sort(["ticker", "bar_time_utc"])
        monthly = aggregate_monthly(daily)
        session_date = f"{month}-01"
        emit(
            progress_callback,
            {
                "event": "phase_complete",
                "phase": "monthly_aggregate",
                "status": "complete",
                "session_date": session_date,
                "timeframe": "1mo",
                "rows_in": daily.height,
                "rows_out": monthly.height,
                "duration_sec": elapsed_since(started_at),
            },
        )
        write_bars_artifact(
            request=request,
            timeframe="1mo",
            session_date=session_date,
            bars=monthly,
            source_path=request.raw_root,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )
        featured_month = build_feature_groups(
            request=request,
            timeframe="1mo",
            session_date=session_date,
            bars=monthly,
            source_path=request.raw_root,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )
        build_supervision_groups(
            request=request,
            timeframe="1mo",
            session_date=session_date,
            bars=featured_month,
            source_path=request.raw_root,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )


def build_market_data(request: BuildRequest, progress_callback: ProgressCallback | None = None) -> dict:
    if request.start_date > request.end_date:
        raise ValueError("start_date must be on or before end_date")
    run_started_at = perf_counter()
    scan_started_at = perf_counter()
    statuses = scan_market_source(request.raw_root, request.start_date, request.end_date)
    buildable_statuses = [status for status in statuses if status.expected_market_session and status.exists]
    missing_statuses = [status for status in statuses if status.expected_market_session and not status.exists]
    touched_month_count = len({status.session_date[:7] for status in buildable_statuses})
    progress_state = {"completed_units": 0, "total_units": estimate_session_units(request, len(buildable_statuses), touched_month_count)}
    emit(
        progress_callback,
        {
            "event": "plan_complete",
            "phase": "scan_source",
            "status": "complete",
            "duration_sec": elapsed_since(scan_started_at),
            "raw_files_found": len(buildable_statuses),
            "missing_sessions": len(missing_statuses),
            "calendar_days": len(statuses),
            "work_total": progress_state["total_units"],
            "plan": [asdict(status) for status in statuses],
        },
    )
    completed = []
    touched_months: set[str] = set()

    for index, status in enumerate(statuses, start=1):
        if not status.expected_market_session:
            completed.append({"session_date": status.session_date, "status": "closed", "rows": 0})
            continue
        if not status.exists:
            completed.append({"session_date": status.session_date, "status": "missing_raw", "rows": 0})
            emit(
                progress_callback,
                {
                    "event": "session_skipped",
                    "phase": "missing_raw",
                    "status": "missing_raw",
                    "session_date": status.session_date,
                    "index": index,
                    "total": len(statuses),
                },
            )
            continue

        session_date = datetime.fromisoformat(status.session_date).date()
        source_path = raw_minute_path(request.raw_root, session_date)
        emit(progress_callback, {"event": "session_started", "phase": "session", "status": "running", "session_date": status.session_date, "index": index, "total": len(statuses)})
        started_at = perf_counter()
        raw = load_raw_minute_bars(request.raw_root, session_date, request.tickers)
        progress_state["completed_units"] += 1
        emit(
            progress_callback,
            {
                "event": "phase_complete",
                "phase": "raw_load",
                "status": "complete",
                "session_date": status.session_date,
                "rows_out": raw.height,
                "duration_sec": elapsed_since(started_at),
                "source_path": str(source_path),
                "source_size_bytes": status.size_bytes,
                "work_completed": progress_state["completed_units"],
                "work_total": progress_state["total_units"],
            },
        )
        started_at = perf_counter()
        bars_1m = canonicalize_1m(raw, request.exchange_timezone)
        progress_state["completed_units"] += 1
        emit(
            progress_callback,
            {
                "event": "phase_complete",
                "phase": "canonicalize_1m",
                "status": "complete",
                "session_date": status.session_date,
                "timeframe": "1m",
                "rows_in": raw.height,
                "rows_out": bars_1m.height,
                "duration_sec": elapsed_since(started_at),
                "work_completed": progress_state["completed_units"],
                "work_total": progress_state["total_units"],
            },
        )
        if "1m" in request.timeframes:
            write_bars_artifact(
                request=request,
                timeframe="1m",
                session_date=status.session_date,
                bars=bars_1m,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            featured_bars_1m = build_feature_groups(
                request=request,
                timeframe="1m",
                session_date=status.session_date,
                bars=bars_1m,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            build_supervision_groups(
                request=request,
                timeframe="1m",
                session_date=status.session_date,
                bars=featured_bars_1m,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )

        for timeframe in request.timeframes:
            if timeframe in {"1m", "1mo"}:
                continue
            if timeframe in {"5m", "15m", "30m", "1h", "2h", "4h"}:
                started_at = perf_counter()
                bars = aggregate_intraday(bars_1m, timeframe)
                emit(
                    progress_callback,
                    {
                        "event": "phase_complete",
                        "phase": "aggregate",
                        "status": "complete",
                        "session_date": status.session_date,
                        "timeframe": timeframe,
                        "rows_in": bars_1m.height,
                        "rows_out": bars.height,
                        "duration_sec": elapsed_since(started_at),
                    },
                )
                write_bars_artifact(
                    request=request,
                    timeframe=timeframe,
                    session_date=status.session_date,
                    bars=bars,
                    source_path=source_path,
                    progress_callback=progress_callback,
                    progress_state=progress_state,
                )
                featured_bars = build_feature_groups(request=request, timeframe=timeframe, session_date=status.session_date, bars=bars, source_path=source_path, progress_callback=progress_callback, progress_state=progress_state)
                build_supervision_groups(request=request, timeframe=timeframe, session_date=status.session_date, bars=featured_bars, source_path=source_path, progress_callback=progress_callback, progress_state=progress_state)
            elif timeframe == "1d":
                started_at = perf_counter()
                bars = aggregate_daily(bars_1m)
                emit(
                    progress_callback,
                    {
                        "event": "phase_complete",
                        "phase": "aggregate_daily",
                        "status": "complete",
                        "session_date": status.session_date,
                        "timeframe": "1d",
                        "rows_in": bars_1m.height,
                        "rows_out": bars.height,
                        "duration_sec": elapsed_since(started_at),
                    },
                )
                write_bars_artifact(
                    request=request,
                    timeframe="1d",
                    session_date=status.session_date,
                    bars=bars,
                    source_path=source_path,
                    progress_callback=progress_callback,
                    progress_state=progress_state,
                )
                featured_bars = build_feature_groups(request=request, timeframe="1d", session_date=status.session_date, bars=bars, source_path=source_path, progress_callback=progress_callback, progress_state=progress_state)
                build_supervision_groups(request=request, timeframe="1d", session_date=status.session_date, bars=featured_bars, source_path=source_path, progress_callback=progress_callback, progress_state=progress_state)
                touched_months.add(status.session_date[:7])
        completed.append({"session_date": status.session_date, "status": "complete", "rows": bars_1m.height})
        emit(progress_callback, {"event": "session_complete", "phase": "session", "status": "complete", "session_date": status.session_date, "index": index, "total": len(statuses), "rows_out": bars_1m.height})

    rebuild_monthly_artifacts(request, touched_months, progress_callback, progress_state)
    emit(
        progress_callback,
        {
            "event": "run_complete",
            "phase": "run",
            "status": "complete",
            "duration_sec": elapsed_since(run_started_at),
            "work_completed": progress_state["completed_units"],
            "work_total": progress_state["total_units"],
        },
    )

    return {
        "processed_root": str(request.processed_root),
        "completed": completed,
        "plan": [asdict(status) for status in statuses],
        "request": {
            "raw_root": str(request.raw_root),
            "processed_root": str(request.processed_root),
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "timeframes": request.timeframes,
            "feature_groups": request.feature_groups,
            "supervision_groups": request.supervision_groups,
            "rebuild_mode": "force_rebuild",
        },
    }
