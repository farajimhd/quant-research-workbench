from __future__ import annotations

import gc
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
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
from src.data_provider.supervision import (
    FIXED_HORIZONS_MINUTES,
    METHOD_WINDOWS,
    build_method_supervision,
    build_scanner_supervision,
    iter_bar_supervision_frames,
)
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


def effective_worker_count(request: BuildRequest, requested_workers: int, buildable_sessions: int) -> tuple[int, str | None]:
    requested = max(1, int(requested_workers))
    capped = min(requested, buildable_sessions) if buildable_sessions else 0
    if capped > 1 and request.tickers is None and "1m" in request.timeframes and request.supervision_groups:
        horizon_work = sum(FIXED_HORIZONS_MINUTES) if "bar" in request.supervision_groups else 0
        if horizon_work > 60:
            return 1, "full_universe_1m_bar_horizons_memory_bound"
        limited = min(capped, 2)
        if limited < capped:
            return limited, "full_universe_1m_supervision_parallel_cap"
    return capped, None


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


def write_bar_supervision_artifact(
    *,
    request: BuildRequest,
    timeframe: str,
    session_date: str,
    bars: pl.DataFrame,
    source_path: Path,
    progress_callback: ProgressCallback | None,
    progress_state: dict | None,
) -> Path:
    import pyarrow.parquet as pq

    path = partition_path(request.processed_root, "supervision_bar", timeframe, session_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)
    writer = None
    rows_out = 0
    columns: list[str] = []
    horizons = list(FIXED_HORIZONS_MINUTES)
    started_at = perf_counter()

    def on_horizon_start(horizon_index: int, horizon: int, horizon_total: int) -> None:
        emit(
            progress_callback,
            {
                "event": "phase_checkpoint",
                "phase": "supervision_bar",
                "status": "running",
                "session_date": session_date,
                "timeframe": timeframe,
                "group": "supervision_bar",
                "horizon": f"{horizon}m",
                "horizon_index": horizon_index,
                "horizon_total": horizon_total,
                "rows_out": rows_out,
                "duration_sec": elapsed_since(started_at),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )

    try:
        for horizon_index, (horizon, horizon_frame) in enumerate(iter_bar_supervision_frames(bars, horizons, on_horizon_start), start=1):
            table = horizon_frame.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(tmp_path, table.schema, compression="zstd")
                columns = list(horizon_frame.columns)
            writer.write_table(table)
            rows_out += horizon_frame.height
            emit(
                progress_callback,
                {
                    "event": "phase_progress",
                    "phase": "supervision_bar",
                    "status": "running",
                    "session_date": session_date,
                    "timeframe": timeframe,
                    "group": "supervision_bar",
                    "horizon": f"{horizon}m",
                    "horizon_index": horizon_index,
                    "horizon_total": len(horizons),
                    "rows_out": rows_out,
                    "duration_sec": elapsed_since(started_at),
                    "work_completed": progress_state.get("completed_units") if progress_state else None,
                    "work_total": progress_state.get("total_units") if progress_state else None,
                },
            )
            del table, horizon_frame
            gc.collect()
    except Exception:
        if writer is not None:
            writer.close()
        tmp_path.unlink(missing_ok=True)
        raise
    if writer is None:
        write_frame(tmp_path, pl.DataFrame())
    else:
        writer.close()
    tmp_path.replace(path)

    source_exists = source_path.exists()
    upsert_artifact(
        request.processed_root,
        ArtifactRecord(
            group="supervision_bar",
            timeframe=timeframe,
            session_date=session_date,
            path=str(path),
            rows=rows_out,
            columns=columns,
            built_at=datetime.now().isoformat(timespec="seconds"),
            source_path=str(source_path),
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
    estimated_rows = {
        "supervision_bar": bars.height * len(FIXED_HORIZONS_MINUTES) if "bar" in request.supervision_groups else 0,
        "supervision_method": bars.height * len(METHOD_WINDOWS) if "method" in request.supervision_groups or "scanner" in request.supervision_groups else 0,
        "supervision_scanner": bars.height * len(METHOD_WINDOWS) if "scanner" in request.supervision_groups else 0,
    }
    bar_supervision = None
    method_supervision = None
    if "bar" in request.supervision_groups:
        emit(
            progress_callback,
            {
                "event": "phase_started",
                "phase": "supervision_bar",
                "status": "running",
                "session_date": session_date,
                "timeframe": timeframe,
                "rows_in": bars.height,
                "estimated_rows_out": estimated_rows["supervision_bar"],
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
        started_at = perf_counter()
        path = write_bar_supervision_artifact(
            request=request,
            timeframe=timeframe,
            session_date=session_date,
            bars=bars,
            source_path=source_path,
            progress_callback=progress_callback,
            progress_state=progress_state,
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
                "rows_out": estimated_rows["supervision_bar"],
                "duration_sec": elapsed_since(started_at),
                "path": str(path),
                "size_bytes": artifact_size(path),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
    if "method" in request.supervision_groups or "scanner" in request.supervision_groups:
        emit(
            progress_callback,
            {
                "event": "phase_started",
                "phase": "supervision_method",
                "status": "running",
                "session_date": session_date,
                "timeframe": timeframe,
                "rows_in": bars.height,
                "estimated_rows_out": estimated_rows["supervision_method"],
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
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
        emit(
            progress_callback,
            {
                "event": "phase_started",
                "phase": "supervision_scanner",
                "status": "running",
                "session_date": session_date,
                "timeframe": timeframe,
                "rows_in": method_supervision.height,
                "estimated_rows_out": estimated_rows["supervision_scanner"],
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
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


def build_session_artifacts(
    *,
    request: BuildRequest,
    status: dict,
    index: int,
    total: int,
    progress_callback: ProgressCallback | None,
    progress_state: dict,
) -> dict:
    session_text = str(status["session_date"])
    session_date = datetime.fromisoformat(session_text).date()
    source_path = raw_minute_path(request.raw_root, session_date)
    emit(
        progress_callback,
        {
            "event": "session_started",
            "phase": "session",
            "status": "running",
            "session_date": session_text,
            "index": index,
            "total": total,
        },
    )
    started_at = perf_counter()
    raw = load_raw_minute_bars(request.raw_root, session_date, request.tickers)
    progress_state["completed_units"] += 1
    emit(
        progress_callback,
        {
            "event": "phase_complete",
            "phase": "raw_load",
            "status": "complete",
            "session_date": session_text,
            "rows_out": raw.height,
            "duration_sec": elapsed_since(started_at),
            "source_path": str(source_path),
            "source_size_bytes": status.get("size_bytes", 0),
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
            "session_date": session_text,
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
            session_date=session_text,
            bars=bars_1m,
            source_path=source_path,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )
        featured_bars_1m = build_feature_groups(
            request=request,
            timeframe="1m",
            session_date=session_text,
            bars=bars_1m,
            source_path=source_path,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )
        build_supervision_groups(
            request=request,
            timeframe="1m",
            session_date=session_text,
            bars=featured_bars_1m,
            source_path=source_path,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )

    touched_month = None
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
                    "session_date": session_text,
                    "timeframe": timeframe,
                    "rows_in": bars_1m.height,
                    "rows_out": bars.height,
                    "duration_sec": elapsed_since(started_at),
                },
            )
            write_bars_artifact(
                request=request,
                timeframe=timeframe,
                session_date=session_text,
                bars=bars,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            featured_bars = build_feature_groups(
                request=request,
                timeframe=timeframe,
                session_date=session_text,
                bars=bars,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            build_supervision_groups(
                request=request,
                timeframe=timeframe,
                session_date=session_text,
                bars=featured_bars,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
        elif timeframe == "1d":
            started_at = perf_counter()
            bars = aggregate_daily(bars_1m)
            emit(
                progress_callback,
                {
                    "event": "phase_complete",
                    "phase": "aggregate_daily",
                    "status": "complete",
                    "session_date": session_text,
                    "timeframe": "1d",
                    "rows_in": bars_1m.height,
                    "rows_out": bars.height,
                    "duration_sec": elapsed_since(started_at),
                },
            )
            write_bars_artifact(
                request=request,
                timeframe="1d",
                session_date=session_text,
                bars=bars,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            featured_bars = build_feature_groups(
                request=request,
                timeframe="1d",
                session_date=session_text,
                bars=bars,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            build_supervision_groups(
                request=request,
                timeframe="1d",
                session_date=session_text,
                bars=featured_bars,
                source_path=source_path,
                progress_callback=progress_callback,
                progress_state=progress_state,
            )
            touched_month = session_text[:7]
    emit(
        progress_callback,
        {
            "event": "session_complete",
            "phase": "session",
            "status": "complete",
            "session_date": session_text,
            "index": index,
            "total": total,
            "rows_out": bars_1m.height,
        },
    )
    return {"session_date": session_text, "status": "complete", "rows": bars_1m.height, "touched_month": touched_month}


def _parallel_session_worker(
    request: BuildRequest,
    status: dict,
    index: int,
    total: int,
    total_units: int,
    job_path_text: str,
) -> dict:
    from src.data_provider.jobs import BuildCancelled, append_event, check_cancelled

    job_path = Path(job_path_text)
    session_text = str(status["session_date"])
    progress_state = {"completed_units": 0, "total_units": total_units}

    def on_progress(event: dict) -> None:
        payload = dict(event)
        payload["parallel_session"] = True
        payload["worker_pid"] = os.getpid()
        payload.setdefault("work_total", total_units)
        payload.pop("work_completed", None)
        append_event(job_path, payload)
        check_cancelled(job_path)

    try:
        check_cancelled(job_path)
        return build_session_artifacts(
            request=request,
            status=status,
            index=index,
            total=total,
            progress_callback=on_progress,
            progress_state=progress_state,
        )
    except BuildCancelled:
        append_event(
            job_path,
            {
                "event": "session_cancelled",
                "phase": "cancel",
                "status": "cancelled",
                "session_date": session_text,
                "index": index,
                "total": total,
                "worker_pid": os.getpid(),
            },
        )
        raise
    except Exception as exc:
        append_event(
            job_path,
            {
                "event": "session_failed",
                "phase": "session",
                "status": "failed",
                "session_date": session_text,
                "index": index,
                "total": total,
                "message": str(exc),
                "worker_pid": os.getpid(),
            },
        )
        raise


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

        result = build_session_artifacts(
            request=request,
            status=asdict(status),
            index=index,
            total=len(statuses),
            progress_callback=progress_callback,
            progress_state=progress_state,
        )
        if result.get("touched_month"):
            touched_months.add(str(result["touched_month"]))
        completed.append({"session_date": status.session_date, "status": "complete", "rows": int(result.get("rows") or 0)})

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


def build_market_data_parallel(
    request: BuildRequest,
    *,
    job_path: Path | None,
    max_workers: int = 4,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    if job_path is None:
        return build_market_data(request, progress_callback=progress_callback)
    if request.start_date > request.end_date:
        raise ValueError("start_date must be on or before end_date")

    from src.data_provider.jobs import check_cancelled

    run_started_at = perf_counter()
    scan_started_at = perf_counter()
    statuses = scan_market_source(request.raw_root, request.start_date, request.end_date)
    buildable_statuses = [status for status in statuses if status.expected_market_session and status.exists]
    missing_statuses = [status for status in statuses if status.expected_market_session and not status.exists]
    touched_month_count = len({status.session_date[:7] for status in buildable_statuses})
    total_units = estimate_session_units(request, len(buildable_statuses), touched_month_count)
    daily_units = estimate_session_units(request, 1, 0)
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
            "work_total": total_units,
            "plan": [asdict(status) for status in statuses],
        },
    )

    completed: list[dict] = []
    build_jobs: list[tuple[int, dict]] = []
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
        build_jobs.append((index, asdict(status)))

    worker_count, worker_limit_reason = effective_worker_count(request, max_workers, len(build_jobs))
    emit(
        progress_callback,
        {
            "event": "parallel_started",
            "phase": "parallel_sessions",
            "status": "running" if worker_count else "complete",
            "worker_count": worker_count,
            "requested_worker_count": max_workers,
            "worker_limit_reason": worker_limit_reason,
            "buildable_sessions": len(build_jobs),
            "polars_threads_per_worker": os.environ.get("POLARS_MAX_THREADS"),
            "work_total": total_units,
        },
    )

    touched_months: set[str] = set()
    if build_jobs:
        executor = ProcessPoolExecutor(max_workers=worker_count)
        futures = {
            executor.submit(
                _parallel_session_worker,
                request,
                status,
                index,
                len(statuses),
                total_units,
                str(job_path),
            ): status
            for index, status in build_jobs
        }
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                if not done:
                    check_cancelled(job_path)
                    continue
                for future in done:
                    result = future.result()
                    if result.get("touched_month"):
                        touched_months.add(str(result["touched_month"]))
                    completed.append(
                        {
                            "session_date": str(result.get("session_date")),
                            "status": str(result.get("status") or "complete"),
                            "rows": int(result.get("rows") or 0),
                        }
                    )
                check_cancelled(job_path)
        except BrokenProcessPool as exc:
            message = (
                "A daily build worker terminated abruptly, usually because the worker exceeded available memory while "
                "building a large supervision artifact."
            )
            emit(
                progress_callback,
                {
                    "event": "parallel_failed",
                    "phase": "parallel_sessions",
                    "status": "failed",
                    "message": message,
                    "worker_count": worker_count,
                    "worker_limit_reason": worker_limit_reason,
                },
            )
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError(message) from exc
        except Exception:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    progress_state = {"completed_units": daily_units * len(build_jobs), "total_units": total_units}
    rebuild_monthly_artifacts(request, touched_months, progress_callback, progress_state)
    emit(
        progress_callback,
        {
            "event": "run_complete",
            "phase": "run",
            "status": "complete",
            "duration_sec": elapsed_since(run_started_at),
            "work_completed": total_units,
            "work_total": total_units,
        },
    )

    completed.sort(key=lambda row: str(row.get("session_date") or ""))
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
