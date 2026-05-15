from __future__ import annotations

import gc
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from datetime import datetime
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
    FIXED_HORIZON_BARS,
    build_method_supervision,
    build_scanner_supervision,
    iter_bar_supervision_frames,
    method_windows_for_timeframe,
)
from src.data_provider.timeframes import aggregate_daily, aggregate_intraday, canonicalize_1m


ProgressCallback = Callable[[dict], None]
STATEFUL_TIMEFRAME_ORDER = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
CARRYOVER_TIMEFRAMES = set(STATEFUL_TIMEFRAME_ORDER)
REFERENCE_LOOKBACK_SESSIONS = 13
STATEFUL_WORKERS = 2
STATEFUL_LOOKBACK_SESSIONS = {
    "1m": 1,
    "5m": 2,
    "15m": 4,
    "30m": 7,
    "1h": 13,
    "2h": 25,
    "4h": 50,
    "1d": 200,
}
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
STATEFUL_WRITE_QUEUE_LIMIT = 4


def build_process_pool(max_workers: int) -> ProcessPoolExecutor:
    # Recycle workers after each session/artifact so Polars allocator high-water memory
    # is returned to the OS instead of accumulating across reused worker processes.
    return ProcessPoolExecutor(max_workers=max_workers, max_tasks_per_child=1)


def emit(progress_callback: ProgressCallback | None, event: dict) -> None:
    if progress_callback:
        progress_callback(event)


def elapsed_since(started_at: float) -> float:
    return round(perf_counter() - started_at, 4)


def artifact_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def session_timeframes(request: BuildRequest) -> list[str]:
    return [timeframe for timeframe in request.timeframes if timeframe != "1mo"]


def is_stateful_resume(request: BuildRequest) -> bool:
    return str(request.resume_stage or "").lower() == "stateful_features"


def estimate_session_units(request: BuildRequest, output_sessions: int, monthly_periods: int = 0) -> int:
    timeframes = session_timeframes(request)
    supervision_units = len(request.supervision_groups)
    feature_compute_units = 1 if request.feature_groups or request.supervision_groups else 0
    per_timeframe = 1 + 1 + feature_compute_units + len(request.feature_groups) + supervision_units
    per_session = 1 + (len(timeframes) * per_timeframe)
    return max(1, output_sessions * per_session)


def build_plan(statuses: list) -> list[dict]:
    rows: list[dict] = []
    expected_seen = 0
    output_started = False
    for status in statuses:
        row = asdict(status)
        if not status.expected_market_session:
            row.update(
                {
                    "build_role": "closed",
                    "write_output": False,
                    "reference_only": False,
                    "reason": "Market closed.",
                }
            )
        else:
            expected_seen += 1
            if expected_seen <= REFERENCE_LOOKBACK_SESSIONS:
                row.update(
                    {
                        "build_role": "reference_only",
                        "write_output": False,
                        "reference_only": True,
                        "reason": f"Warm-up context for carry-over indicators ({expected_seen}/{REFERENCE_LOOKBACK_SESSIONS}).",
                    }
                )
            else:
                output_started = True
                row.update(
                    {
                        "build_role": "output",
                        "write_output": True,
                        "reference_only": False,
                        "reason": "Artifacts will be written for this session." if status.exists else "Output session raw file is missing.",
                    }
                )
        rows.append(row)
    if not output_started:
        for row in rows:
            if row.get("build_role") == "reference_only":
                row["reason"] = "Reference-only warm-up session; no output artifacts will be written until at least 14 market sessions are in scope."
    return rows


def output_start_date(plan_rows: list[dict]) -> str | None:
    output_rows = [row for row in plan_rows if row.get("build_role") == "output"]
    return str(output_rows[0]["session_date"]) if output_rows else None


def plan_metadata(plan_rows: list[dict]) -> dict:
    reference_rows = [row for row in plan_rows if row.get("build_role") == "reference_only"]
    output_rows = [row for row in plan_rows if row.get("build_role") == "output"]
    missing_reference_rows = [row for row in reference_rows if row.get("expected_market_session") and not row.get("exists")]
    return {
        "reference_sessions": len(reference_rows),
        "missing_reference_sessions": len(missing_reference_rows),
        "output_sessions": len(output_rows),
        "output_start_date": output_start_date(plan_rows),
        "warmup_sessions": REFERENCE_LOOKBACK_SESSIONS,
        "carryover_timeframes": sorted(CARRYOVER_TIMEFRAMES),
    }


def write_artifact(
    *,
    root: Path,
    group: str,
    timeframe: str,
    session_date: str,
    frame: pl.DataFrame,
    build_id: str | None = None,
    build_name: str | None = None,
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
            build_id=build_id,
            build_name=build_name,
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
    artifact_session_date: str | None = None,
) -> Path:
    import pyarrow.parquet as pq

    path = partition_path(request.processed_root, "supervision_bar", timeframe, session_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)
    writer = None
    rows_out = 0
    columns: list[str] = []
    horizons = list(FIXED_HORIZON_BARS)
    started_at = perf_counter()

    def on_horizon_start(horizon_index: int, horizon_bars: int, horizon_total: int) -> None:
        emit(
            progress_callback,
            {
                "event": "phase_checkpoint",
                "phase": "supervision_bar",
                "status": "running",
                "session_date": session_date,
                "timeframe": timeframe,
                "group": "supervision_bar",
                "horizon": f"{horizon_bars}bar",
                "horizon_bars": horizon_bars,
                "horizon_index": horizon_index,
                "horizon_total": horizon_total,
                "rows_out": rows_out,
                "duration_sec": elapsed_since(started_at),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )

    try:
        for horizon_index, (horizon_bars, horizon_frame) in enumerate(iter_bar_supervision_frames(bars, horizons, on_horizon_start, assume_sorted=True), start=1):
            if artifact_session_date is not None and "session_date" in horizon_frame.columns:
                horizon_frame = horizon_frame.filter(pl.col("session_date") == artifact_session_date)
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
                    "horizon": f"{horizon_bars}bar",
                    "horizon_bars": horizon_bars,
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
            build_id=request.build_id,
            build_name=request.build_name,
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
    artifact_session_date: str | None = None,
    stateful: bool = False,
) -> pl.DataFrame:
    if bars.is_empty():
        return bars
    started_at = perf_counter()
    emit(
        progress_callback,
        {
            "event": "phase_started",
            "phase": "feature_compute",
            "status": "running",
            "session_date": session_date,
            "timeframe": timeframe,
            "rows_in": bars.height,
            "artifact_session_date": artifact_session_date,
            "stateful": stateful,
            "calculation_mode": "session_local_and_carryover" if timeframe in CARRYOVER_TIMEFRAMES else "session_local",
            "warmup_sessions": stateful_lookback_sessions(timeframe) if timeframe in CARRYOVER_TIMEFRAMES else 0,
            "work_completed": progress_state.get("completed_units") if progress_state else None,
            "work_total": progress_state.get("total_units") if progress_state else None,
        },
    )
    features = add_feature_columns(bars)
    artifact_features = (
        features.filter(pl.col("session_date") == artifact_session_date)
        if artifact_session_date is not None and "session_date" in features.columns
        else features
    )
    if progress_state is not None and (request.feature_groups or request.supervision_groups):
        progress_state["completed_units"] += 1
    emit(
        progress_callback,
        {
            "event": "phase_complete",
            "phase": "feature_compute",
            "status": "complete",
            "session_date": session_date,
            "timeframe": timeframe,
            "rows_in": bars.height,
            "rows_out": artifact_features.height,
            "artifact_session_date": artifact_session_date,
            "stateful": stateful,
            "calculation_mode": "session_local_and_carryover" if timeframe in CARRYOVER_TIMEFRAMES else "session_local",
            "warmup_sessions": stateful_lookback_sessions(timeframe) if timeframe in CARRYOVER_TIMEFRAMES else 0,
            "duration_sec": elapsed_since(started_at),
            "work_completed": progress_state.get("completed_units") if progress_state else None,
            "work_total": progress_state.get("total_units") if progress_state else None,
        },
    )
    for group in request.feature_groups:
        if group not in FEATURE_COLUMNS:
            continue
        started_at = perf_counter()
        group_frame = select_feature_group(artifact_features, group)
        path = write_artifact(
            root=request.processed_root,
            group=f"features_{group}",
            timeframe=timeframe,
            session_date=session_date,
            frame=group_frame,
            build_id=request.build_id,
            build_name=request.build_name,
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
                "stateful": stateful,
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
    artifact_session_date: str | None = None,
    stateful: bool = False,
) -> None:
    if bars.is_empty() or not request.supervision_groups:
        return
    artifact_rows = (
        bars.filter(pl.col("session_date") == artifact_session_date)
        if artifact_session_date is not None and "session_date" in bars.columns
        else bars
    )
    artifact_height = artifact_rows.height
    del artifact_rows
    estimated_rows = {
        "supervision_bar": artifact_height * len(FIXED_HORIZON_BARS) if "bar" in request.supervision_groups else 0,
        "supervision_method": artifact_height * len(method_windows_for_timeframe(timeframe)) if "method" in request.supervision_groups or "scanner" in request.supervision_groups else 0,
        "supervision_scanner": artifact_height * len(method_windows_for_timeframe(timeframe)) if "scanner" in request.supervision_groups else 0,
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
                "stateful": stateful,
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
            artifact_session_date=artifact_session_date,
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
                "stateful": stateful,
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
                "stateful": stateful,
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
        started_at = perf_counter()
        method_supervision = build_method_supervision(bars, assume_sorted=True)
        if artifact_session_date is not None and "session_date" in method_supervision.columns:
            method_supervision = method_supervision.filter(pl.col("session_date") == artifact_session_date)
        if "method" in request.supervision_groups:
            path = write_artifact(
                root=request.processed_root,
                group="supervision_method",
                timeframe=timeframe,
                session_date=session_date,
                frame=method_supervision,
                build_id=request.build_id,
                build_name=request.build_name,
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
                    "stateful": stateful,
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
            method_supervision = build_method_supervision(bars, assume_sorted=True)
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
                "stateful": stateful,
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
        started_at = perf_counter()
        scanner_supervision = build_scanner_supervision(method_supervision)
        method_rows = method_supervision.height
        path = write_artifact(
            root=request.processed_root,
            group="supervision_scanner",
            timeframe=timeframe,
            session_date=session_date,
            frame=scanner_supervision,
            build_id=request.build_id,
            build_name=request.build_name,
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
                "stateful": stateful,
                "rows_in": method_rows,
                "rows_out": scanner_supervision.height,
                "duration_sec": elapsed_since(started_at),
                "path": str(path),
                "size_bytes": artifact_size(path),
                "work_completed": progress_state.get("completed_units") if progress_state else None,
                "work_total": progress_state.get("total_units") if progress_state else None,
            },
        )
        del scanner_supervision
    del method_supervision
    gc.collect()


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
    emit(
        progress_callback,
        {
            "event": "phase_started",
            "phase": "bars_write",
            "status": "running",
            "session_date": session_date,
            "timeframe": timeframe,
            "group": "bars",
            "rows_in": bars.height,
            "work_completed": progress_state.get("completed_units") if progress_state else None,
            "work_total": progress_state.get("total_units") if progress_state else None,
        },
    )
    path = write_artifact(
        root=request.processed_root,
        group="bars",
        timeframe=timeframe,
        session_date=session_date,
        frame=bars,
        build_id=request.build_id,
        build_name=request.build_name,
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


def build_session_bars(
    *,
    request: BuildRequest,
    status: dict,
    index: int,
    total: int,
    progress_callback: ProgressCallback | None,
    progress_state: dict,
    build_artifacts: bool = False,
    artifact_timeframes: set[str] | None = None,
) -> dict:
    session_text = str(status["session_date"])
    session_date = datetime.fromisoformat(session_text).date()
    source_path = raw_minute_path(request.raw_root, session_date)
    selected_timeframes = session_timeframes(request)
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
    emit(
        progress_callback,
        {
            "event": "phase_started",
            "phase": "raw_load",
            "status": "running",
            "session_date": session_text,
            "source_path": str(source_path),
            "source_size_bytes": status.get("size_bytes", 0),
            "work_completed": progress_state.get("completed_units"),
            "work_total": progress_state.get("total_units"),
        },
    )
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
    emit(
        progress_callback,
        {
            "event": "phase_started",
            "phase": "canonicalize_1m",
            "status": "running",
            "session_date": session_text,
            "timeframe": "1m",
            "rows_in": raw.height,
            "work_completed": progress_state.get("completed_units"),
            "work_total": progress_state.get("total_units"),
        },
    )
    bars_1m = canonicalize_1m(raw, request.exchange_timezone)
    raw_rows = raw.height
    del raw
    gc.collect()
    progress_state["completed_units"] += 1
    emit(
        progress_callback,
        {
            "event": "phase_complete",
            "phase": "canonicalize_1m",
            "status": "complete",
            "session_date": session_text,
            "timeframe": "1m",
            "rows_in": raw_rows,
            "rows_out": bars_1m.height,
            "duration_sec": elapsed_since(started_at),
            "work_completed": progress_state["completed_units"],
            "work_total": progress_state["total_units"],
        },
    )
    built_timeframes: list[str] = []
    artifact_results: list[dict] = []
    should_build_artifacts = artifact_timeframes if artifact_timeframes is not None else set(selected_timeframes)
    if "1m" in selected_timeframes:
        write_bars_artifact(
            request=request,
            timeframe="1m",
            session_date=session_text,
            bars=bars_1m,
            source_path=source_path,
            progress_callback=progress_callback,
            progress_state=progress_state,
        )
        built_timeframes.append("1m")
        if build_artifacts and "1m" in should_build_artifacts:
            artifact_results.append(
                build_timeframe_artifacts(
                    request=request,
                    session_text=session_text,
                    timeframe="1m",
                    progress_callback=progress_callback,
                    progress_state=progress_state,
                    current_bars=bars_1m,
                )
            )

    for timeframe in selected_timeframes:
        if timeframe == "1m":
            continue
        if timeframe in {"5m", "15m", "30m", "1h", "2h", "4h"}:
            started_at = perf_counter()
            emit(
                progress_callback,
                {
                    "event": "phase_started",
                    "phase": "aggregate",
                    "status": "running",
                    "session_date": session_text,
                    "timeframe": timeframe,
                    "rows_in": bars_1m.height,
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
                },
            )
            bars = aggregate_intraday(bars_1m, timeframe)
            progress_state["completed_units"] += 1
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
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
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
            built_timeframes.append(timeframe)
            if build_artifacts and timeframe in should_build_artifacts:
                artifact_results.append(
                    build_timeframe_artifacts(
                        request=request,
                        session_text=session_text,
                        timeframe=timeframe,
                        progress_callback=progress_callback,
                        progress_state=progress_state,
                        current_bars=bars,
                    )
                )
            del bars
            gc.collect()
        elif timeframe == "1d":
            started_at = perf_counter()
            emit(
                progress_callback,
                {
                    "event": "phase_started",
                    "phase": "aggregate_daily",
                    "status": "running",
                    "session_date": session_text,
                    "timeframe": "1d",
                    "rows_in": bars_1m.height,
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
                },
            )
            bars = aggregate_daily(bars_1m)
            progress_state["completed_units"] += 1
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
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
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
            built_timeframes.append("1d")
            if build_artifacts and "1d" in should_build_artifacts:
                artifact_results.append(
                    build_timeframe_artifacts(
                        request=request,
                        session_text=session_text,
                        timeframe="1d",
                        progress_callback=progress_callback,
                        progress_state=progress_state,
                        current_bars=bars,
                    )
                )
            del bars
            gc.collect()
    rows_out = bars_1m.height
    del bars_1m
    gc.collect()
    emit(
        progress_callback,
        {
            "event": "session_complete",
            "phase": "session",
            "status": "bars_complete",
            "session_date": session_text,
            "index": index,
            "total": total,
            "rows_out": rows_out,
        },
    )
    return {
        "session_date": session_text,
        "status": "bars_complete",
        "rows": rows_out,
        "timeframes": built_timeframes,
        "artifacts": artifact_results,
    }


def artifact_source_path(request: BuildRequest, timeframe: str, session_text: str) -> Path:
    if timeframe == "1m":
        return raw_minute_path(request.raw_root, datetime.fromisoformat(session_text).date())
    return partition_path(request.processed_root, "bars", timeframe, session_text)


def read_timeframe_context_bars(
    request: BuildRequest,
    timeframe: str,
    session_text: str,
    current_bars: pl.DataFrame | None = None,
) -> pl.DataFrame:
    session = datetime.fromisoformat(session_text).date()
    if timeframe in CARRYOVER_TIMEFRAMES:
        context_sessions = market_sessions(request.start_date, session)
        prior_sessions = [item for item in context_sessions if item < session][-stateful_lookback_sessions(timeframe):]
        frames = []
        for context_session in prior_sessions:
            raw = load_raw_minute_bars(request.raw_root, context_session, request.tickers)
            if raw.is_empty():
                del raw
                continue
            bars_1m = canonicalize_1m(raw, request.exchange_timezone)
            if timeframe == "1m":
                frames.append(bars_1m)
            else:
                frames.append(aggregate_intraday(bars_1m, timeframe))
            del raw
            if timeframe != "1m":
                del bars_1m
            gc.collect()
        if current_bars is not None:
            frames.append(current_bars)
        else:
            raw = load_raw_minute_bars(request.raw_root, session, request.tickers)
            if not raw.is_empty():
                bars_1m = canonicalize_1m(raw, request.exchange_timezone)
                frames.append(bars_1m if timeframe == "1m" else aggregate_intraday(bars_1m, timeframe))
                if timeframe != "1m":
                    del bars_1m
            del raw
            gc.collect()
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal").sort(["ticker", "bar_time_utc"])

    if current_bars is not None:
        return current_bars.sort(["ticker", "bar_time_utc"])
    source_path = partition_path(request.processed_root, "bars", timeframe, session_text)
    if timeframe != "1d":
        return read_frame(source_path)
    context_sessions = [session]
    frames = []
    for context_session in context_sessions:
        path = partition_path(request.processed_root, "bars", timeframe, context_session.isoformat())
        if path.exists():
            frames.append(read_frame(path))
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal").sort(["ticker", "bar_time_utc"])


def build_timeframe_artifacts(
    *,
    request: BuildRequest,
    session_text: str,
    timeframe: str,
    progress_callback: ProgressCallback | None,
    progress_state: dict,
    current_bars: pl.DataFrame | None = None,
    stateful: bool = False,
) -> dict:
    bars = read_timeframe_context_bars(request, timeframe, session_text, current_bars=current_bars)
    if bars.is_empty():
        return {"session_date": session_text, "timeframe": timeframe, "status": "missing_bars", "rows": 0}

    source_path = artifact_source_path(request, timeframe, session_text)
    featured_bars = build_feature_groups(
        request=request,
        timeframe=timeframe,
        session_date=session_text,
        bars=bars,
        source_path=source_path,
        progress_callback=progress_callback,
        progress_state=progress_state,
        artifact_session_date=session_text,
        stateful=stateful,
    )
    build_supervision_groups(
        request=request,
        timeframe=timeframe,
        session_date=session_text,
        bars=featured_bars,
        source_path=source_path,
        progress_callback=progress_callback,
        progress_state=progress_state,
        artifact_session_date=session_text,
        stateful=stateful,
    )
    rows = featured_bars.filter(pl.col("session_date") == session_text).height if "session_date" in featured_bars.columns else featured_bars.height
    del featured_bars, bars
    gc.collect()
    return {"session_date": session_text, "timeframe": timeframe, "status": "complete", "rows": rows}


def _parallel_session_worker(
    request: BuildRequest,
    status: dict,
    index: int,
    total: int,
    total_units: int,
    job_path_text: str,
    artifact_timeframes: set[str] | None = None,
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
        return build_session_bars(
            request=request,
            status=status,
            index=index,
            total=total,
            progress_callback=on_progress,
            progress_state=progress_state,
            build_artifacts=True,
            artifact_timeframes=artifact_timeframes,
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


def stateful_timeframe_tasks(tasks: list[dict]) -> list[dict]:
    sessions_by_timeframe: dict[str, set[str]] = {}
    for task in tasks:
        timeframe = str(task["timeframe"])
        session = str(task["session_date"])
        sessions_by_timeframe.setdefault(timeframe, set()).add(session)
    order = {timeframe: index for index, timeframe in enumerate(STATEFUL_TIMEFRAME_ORDER)}
    return [
        {"timeframe": timeframe, "sessions": sorted(sessions)}
        for timeframe, sessions in sorted(sessions_by_timeframe.items(), key=lambda item: order.get(item[0], len(order)))
        if sessions
    ]


def stateful_task_groups(timeframe_tasks: list[dict]) -> list[dict]:
    low_latency = [task for task in timeframe_tasks if str(task["timeframe"]) in {"1m", "5m"}]
    higher = [task for task in timeframe_tasks if str(task["timeframe"]) not in {"1m", "5m"}]
    groups: list[dict] = []
    if low_latency:
        groups.append({"group": "1m_5m", "tasks": low_latency})
    if higher:
        groups.append({"group": "higher_timeframes", "tasks": higher})
    return groups


def stateful_lookback_sessions(timeframe: str) -> int:
    return STATEFUL_LOOKBACK_SESSIONS.get(timeframe, REFERENCE_LOOKBACK_SESSIONS)


def stateful_chunk_size(timeframe: str) -> int:
    return STATEFUL_OUTPUT_CHUNK_SESSIONS.get(timeframe, 10)


def iter_stateful_session_chunks(timeframe: str, sessions: list[str]) -> list[list[str]]:
    size = max(1, stateful_chunk_size(timeframe))
    return [sessions[index : index + size] for index in range(0, len(sessions), size)]


def stateful_warmup_sessions(request: BuildRequest, first_output_session: str, lookback_sessions: int) -> list[str]:
    first_session = datetime.fromisoformat(first_output_session).date()
    sessions = market_sessions(request.start_date, first_session)
    return [session.isoformat() for session in sessions if session < first_session][-lookback_sessions:]


def read_stateful_bar_frames(request: BuildRequest, timeframe: str, sessions: list[str]) -> list[pl.DataFrame]:
    frames: list[pl.DataFrame] = []
    for session_text in sessions:
        path = partition_path(request.processed_root, "bars", timeframe, session_text)
        if not path.exists():
            continue
        frame = read_frame(path)
        if not frame.is_empty():
            frames.append(frame)
        else:
            del frame
    return frames


def stateful_tail_frame(bars: pl.DataFrame, tail_sessions: list[str]) -> pl.DataFrame | None:
    if not tail_sessions or bars.is_empty() or "session_date" not in bars.columns:
        return None
    tail = bars.filter(pl.col("session_date").is_in(tail_sessions))
    return tail if not tail.is_empty() else None


def session_period_fields(prefix: str, sessions: list[str]) -> dict[str, str | None | int]:
    return {
        f"{prefix}_start": sessions[0] if sessions else None,
        f"{prefix}_end": sessions[-1] if sessions else None,
        f"{prefix}_count": len(sessions),
    }


def artifact_is_readable(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        pl.scan_parquet(path).head(1).collect()
        return True
    except Exception:
        return False


def stateful_session_outputs_complete(request: BuildRequest, timeframe: str, session_text: str) -> bool:
    for group in request.feature_groups:
        if group not in FEATURE_COLUMNS:
            continue
        path = partition_path(request.processed_root, f"features_{group}", timeframe, session_text)
        if not artifact_is_readable(path):
            return False
    return True


def write_stateful_feature_artifact(
    *,
    request: BuildRequest,
    timeframe: str,
    session_text: str,
    group: str,
    frame: pl.DataFrame,
) -> dict:
    source_path = partition_path(request.processed_root, "bars", timeframe, session_text)
    path = write_artifact(
        root=request.processed_root,
        group=f"features_{group}",
        timeframe=timeframe,
        session_date=session_text,
        frame=frame,
        build_id=request.build_id,
        build_name=request.build_name,
        source_path=source_path,
    )
    return {
        "path": str(path),
        "size_bytes": artifact_size(path),
        "rows_out": frame.height,
        "session_date": session_text,
        "timeframe": timeframe,
        "group": f"features_{group}",
    }


def drain_stateful_writes(
    *,
    pending_writes: dict,
    progress_callback: ProgressCallback | None,
    progress_state: dict,
    wait_all: bool = False,
    queue_limit: int = STATEFUL_WRITE_QUEUE_LIMIT,
) -> int:
    completed = 0
    while pending_writes and (wait_all or len(pending_writes) >= queue_limit):
        done, _ = wait(set(pending_writes), return_when=FIRST_COMPLETED)
        for future in done:
            started_at = pending_writes.pop(future)
            result = future.result()
            progress_state["completed_units"] = progress_state.get("completed_units", 0) + 1
            emit(
                progress_callback,
                {
                    "event": "artifact_complete",
                    "phase": "feature_write",
                    "status": "complete",
                    "session_date": result["session_date"],
                    "timeframe": result["timeframe"],
                    "group": result["group"],
                    "stateful": True,
                    "rows_out": result["rows_out"],
                    "duration_sec": elapsed_since(started_at),
                    "path": result["path"],
                    "size_bytes": result["size_bytes"],
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
                },
            )
            completed += 1
    return completed


def build_stateful_timeframe_artifacts(
    *,
    request: BuildRequest,
    timeframe: str,
    output_sessions: list[str],
    progress_callback: ProgressCallback | None,
    progress_state: dict,
) -> dict:
    if not output_sessions:
        return {"timeframe": timeframe, "status": "skipped", "sessions": 0, "rows": 0}
    started_at = perf_counter()
    pending_sessions = {
        session_text
        for session_text in output_sessions
        if not stateful_session_outputs_complete(request, timeframe, session_text)
    }
    chunks = iter_stateful_session_chunks(timeframe, output_sessions)
    lookback_sessions = stateful_lookback_sessions(timeframe)
    emit(
        progress_callback,
        {
            "event": "phase_started",
            "phase": "stateful_features",
            "status": "running",
            "timeframe": timeframe,
            "stateful": True,
            "session_count": len(output_sessions),
            "pending_session_count": len(pending_sessions),
            "chunk_count": len(chunks),
            "chunk_size": stateful_chunk_size(timeframe),
            "lookback_sessions": lookback_sessions,
            "calculation_mode": "eager_rolling_timeframe_window",
            **session_period_fields("output", output_sessions),
            "work_total": progress_state.get("total_units"),
        },
    )
    total_rows = 0
    completed_sessions = len(output_sessions) - len(pending_sessions)
    tail_bars: pl.DataFrame | None = None
    tail_sessions: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as writer:
        pending_writes: dict = {}
        for chunk_index, chunk_sessions in enumerate(chunks, start=1):
            writable_sessions = [session_text for session_text in chunk_sessions if session_text in pending_sessions]
            if not writable_sessions:
                next_tail_sessions = chunk_sessions[-lookback_sessions:]
                if len(next_tail_sessions) < lookback_sessions:
                    next_tail_sessions = [*tail_sessions, *next_tail_sessions][-lookback_sessions:]
                if next_tail_sessions:
                    frames = read_stateful_bar_frames(request, timeframe, next_tail_sessions)
                    tail_bars = pl.concat(frames, how="diagonal", rechunk=False) if frames else None
                    tail_sessions = next_tail_sessions if frames else []
                    del frames
                    gc.collect()
                continue

            chunk_started_at = perf_counter()
            if tail_bars is None:
                tail_sessions = stateful_warmup_sessions(request, chunk_sessions[0], lookback_sessions)
                warmup_frames = read_stateful_bar_frames(request, timeframe, tail_sessions)
            else:
                warmup_frames = [tail_bars]
            output_frames = read_stateful_bar_frames(request, timeframe, chunk_sessions)
            bar_frames = [*warmup_frames, *output_frames]
            if not bar_frames:
                continue
            window_sessions = [*tail_sessions, *chunk_sessions]
            emit(
                progress_callback,
                {
                    "event": "phase_started",
                    "phase": "feature_compute",
                    "status": "running",
                    "timeframe": timeframe,
                    "stateful": True,
                    "session_count": len(chunk_sessions),
                    "write_session_count": len(writable_sessions),
                    "completed_sessions": completed_sessions,
                    "pending_session_count": len(pending_sessions),
                    "chunk_index": chunk_index,
                    "chunk_total": len(chunks),
                    "bar_file_count": len(output_frames) + (0 if tail_bars is not None else len(warmup_frames)),
                    "calculation_mode": "eager_rolling_timeframe_window",
                    "lookback_sessions": lookback_sessions,
                    "warmup_sessions": len(tail_sessions),
                    "write_queue_limit": STATEFUL_WRITE_QUEUE_LIMIT,
                    **session_period_fields("warmup", tail_sessions),
                    **session_period_fields("output", chunk_sessions),
                    **session_period_fields("write", writable_sessions),
                    **session_period_fields("window", window_sessions),
                    "work_total": progress_state.get("total_units"),
                },
            )
            bars = pl.concat(bar_frames, how="diagonal", rechunk=False)
            del warmup_frames, output_frames, bar_frames
            gc.collect()
            features = add_feature_columns(bars)
            feature_columns = set(features.columns)
            if "session_date" in feature_columns:
                chunk_features = features.filter(pl.col("session_date").is_in(writable_sessions))
            else:
                chunk_features = features
            progress_state["completed_units"] = progress_state.get("completed_units", 0) + 1
            emit(
                progress_callback,
                {
                    "event": "phase_complete",
                    "phase": "feature_compute",
                    "status": "complete",
                    "timeframe": timeframe,
                    "stateful": True,
                    "rows_out": chunk_features.height,
                    "duration_sec": elapsed_since(chunk_started_at),
                    "calculation_mode": "eager_rolling_timeframe_window",
                    "chunk_index": chunk_index,
                    "chunk_total": len(chunks),
                    **session_period_fields("warmup", tail_sessions),
                    **session_period_fields("output", chunk_sessions),
                    **session_period_fields("write", writable_sessions),
                    **session_period_fields("window", window_sessions),
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
                },
            )
            for group in request.feature_groups:
                if group not in FEATURE_COLUMNS:
                    continue
                group_started_at = perf_counter()
                emit(
                    progress_callback,
                    {
                        "event": "phase_checkpoint",
                        "phase": "feature_write",
                        "status": "running",
                        "timeframe": timeframe,
                        "group": f"features_{group}",
                        "stateful": True,
                        "chunk_index": chunk_index,
                        "chunk_total": len(chunks),
                        "session_count": len(writable_sessions),
                        "message": "Queueing feature artifacts from the current eager stateful window.",
                        **session_period_fields("write", writable_sessions),
                        **session_period_fields("output", chunk_sessions),
                        "work_completed": progress_state.get("completed_units"),
                        "work_total": progress_state.get("total_units"),
                    },
                )
                chunk_columns = set(chunk_features.columns)
                group_columns = [column for column in FEATURE_COLUMNS[group] if column in chunk_columns]
                split_columns = group_columns if "session_date" in group_columns or "session_date" not in chunk_columns else [*group_columns, "session_date"]
                group_chunk = chunk_features.select(split_columns)
                for session_text in writable_sessions:
                    group_frame = group_chunk.filter(pl.col("session_date") == session_text) if "session_date" in group_chunk.columns else group_chunk
                    if "session_date" in group_frame.columns and "session_date" not in group_columns:
                        group_frame = group_frame.drop("session_date")
                    pending_writes[
                        writer.submit(
                            write_stateful_feature_artifact,
                            request=request,
                            timeframe=timeframe,
                            session_text=session_text,
                            group=group,
                            frame=group_frame,
                        )
                    ] = perf_counter()
                    total_rows += group_frame.height
                    del group_frame
                    drain_stateful_writes(
                        pending_writes=pending_writes,
                        progress_callback=progress_callback,
                        progress_state=progress_state,
                    )
                del group_chunk
                gc.collect()
                emit(
                    progress_callback,
                    {
                        "event": "phase_checkpoint",
                        "phase": "feature_write",
                        "status": "queued",
                        "timeframe": timeframe,
                        "group": f"features_{group}",
                        "stateful": True,
                        "chunk_index": chunk_index,
                        "chunk_total": len(chunks),
                        "duration_sec": elapsed_since(group_started_at),
                        "pending_writes": len(pending_writes),
                        **session_period_fields("write", writable_sessions),
                        **session_period_fields("output", chunk_sessions),
                        "work_completed": progress_state.get("completed_units"),
                        "work_total": progress_state.get("total_units"),
                    },
                )
            drain_stateful_writes(
                pending_writes=pending_writes,
                progress_callback=progress_callback,
                progress_state=progress_state,
                wait_all=True,
            )
            completed_sessions += len(writable_sessions)
            combined_sessions = [*tail_sessions, *chunk_sessions]
            tail_sessions = combined_sessions[-lookback_sessions:] if lookback_sessions else []
            next_tail = stateful_tail_frame(bars, tail_sessions)
            del tail_bars
            tail_bars = next_tail
            del chunk_features, features, bars
            gc.collect()
            emit(
                progress_callback,
                {
                    "event": "phase_checkpoint",
                    "phase": "stateful_features",
                    "status": "running",
                    "timeframe": timeframe,
                    "stateful": True,
                    "completed_sessions": completed_sessions,
                    "session_count": len(output_sessions),
                    "chunk_index": chunk_index,
                    "chunk_total": len(chunks),
                    "duration_sec": elapsed_since(chunk_started_at),
                    "lookback_sessions": lookback_sessions,
                    "tail_sessions": tail_sessions,
                    **session_period_fields("warmup", tail_sessions),
                    **session_period_fields("output", chunk_sessions),
                    **session_period_fields("write", writable_sessions),
                    "work_completed": progress_state.get("completed_units"),
                    "work_total": progress_state.get("total_units"),
                },
            )
    if not pending_sessions:
        emit(
            progress_callback,
            {
                "event": "phase_checkpoint",
                "phase": "stateful_features",
                "status": "skipped",
                "timeframe": timeframe,
                "stateful": True,
                "message": "All stateful feature files already exist and are readable.",
                "session_count": len(output_sessions),
                "duration_sec": elapsed_since(started_at),
            },
        )
    emit(
        progress_callback,
        {
            "event": "phase_complete",
            "phase": "stateful_features",
            "status": "complete",
            "timeframe": timeframe,
            "stateful": True,
            "session_count": len(output_sessions),
            "completed_sessions": completed_sessions,
            "rows_out": total_rows,
            "duration_sec": elapsed_since(started_at),
            "work_completed": progress_state.get("completed_units"),
            "work_total": progress_state.get("total_units"),
        },
    )
    gc.collect()
    return {"timeframe": timeframe, "status": "complete", "sessions": len(output_sessions), "rows": total_rows}


def _stateful_artifact_worker(
    request: BuildRequest,
    task: dict,
    total_units: int,
    job_path_text: str,
) -> dict:
    from src.data_provider.jobs import BuildCancelled, append_event, check_cancelled

    job_path = Path(job_path_text)
    timeframe = str(task["timeframe"])
    output_sessions = [str(session) for session in task.get("sessions", [])]
    progress_state = {"completed_units": 0, "total_units": total_units}

    def on_progress(event: dict) -> None:
        payload = dict(event)
        payload["stateful"] = True
        payload["worker_pid"] = os.getpid()
        payload.setdefault("timeframe", timeframe)
        payload.setdefault("work_total", total_units)
        payload.pop("work_completed", None)
        append_event(job_path, payload)
        check_cancelled(job_path)

    try:
        check_cancelled(job_path)
        result = build_stateful_timeframe_artifacts(
            request=request,
            timeframe=timeframe,
            output_sessions=output_sessions,
            progress_callback=on_progress,
            progress_state=progress_state,
        )
        return result
    except BuildCancelled:
        append_event(
            job_path,
            {
                "event": "stateful_job_cancelled",
                "phase": "cancel",
                "status": "cancelled",
                "timeframe": timeframe,
                "worker_pid": os.getpid(),
                "stateful": True,
            },
        )
        raise
    except Exception as exc:
        append_event(
            job_path,
            {
                "event": "stateful_job_failed",
                "phase": "stateful_features",
                "status": "failed",
                "timeframe": timeframe,
                "message": str(exc),
                "worker_pid": os.getpid(),
                "stateful": True,
            },
        )
        raise


def _stateful_artifact_group_worker(
    request: BuildRequest,
    task_group: dict,
    total_units: int,
    job_path_text: str,
) -> dict:
    group_name = str(task_group.get("group") or "stateful")
    results: list[dict] = []
    for task in task_group.get("tasks", []):
        results.append(_stateful_artifact_worker(request, task, total_units, job_path_text))
    return {
        "group": group_name,
        "status": "complete",
        "results": results,
        "timeframes": [str(result.get("timeframe")) for result in results],
    }


def build_stateful_artifact_tasks(
    *,
    request: BuildRequest,
    tasks: list[dict],
    total_units: int,
    job_path: Path | None,
    progress_callback: ProgressCallback | None,
    progress_state: dict | None = None,
) -> list[dict]:
    if not tasks:
        return []
    timeframe_tasks = stateful_timeframe_tasks(tasks)
    if job_path is None:
        state = progress_state or {"completed_units": 0, "total_units": total_units}
        results: list[dict] = []
        for task in timeframe_tasks:
            results.append(
                build_stateful_timeframe_artifacts(
                request=request,
                timeframe=str(task["timeframe"]),
                output_sessions=[str(session) for session in task.get("sessions", [])],
                progress_callback=progress_callback,
                progress_state=state,
            )
            )
        return results

    from src.data_provider.jobs import check_cancelled

    task_groups = stateful_task_groups(timeframe_tasks)
    worker_count = min(STATEFUL_WORKERS, len(task_groups))
    emit(
        progress_callback,
        {
            "event": "parallel_started",
            "phase": "stateful_features",
            "status": "running",
            "worker_count": worker_count,
            "requested_worker_count": STATEFUL_WORKERS,
            "artifact_jobs": len(timeframe_tasks),
            "worker_groups": [group["group"] for group in task_groups],
            "polars_threads_per_worker": os.environ.get("POLARS_MAX_THREADS"),
            "work_total": total_units,
            "stateful": True,
        },
    )
    executor = build_process_pool(worker_count)
    futures = {
        executor.submit(
            _stateful_artifact_group_worker,
            request,
            task_group,
            total_units,
            str(job_path),
        ): task_group
        for task_group in task_groups
    }
    pending = set(futures)
    results: list[dict] = []
    try:
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            if not done:
                check_cancelled(job_path)
                continue
            for future in done:
                group_result = future.result()
                results.extend(group_result.get("results", []))
            check_cancelled(job_path)
    except BrokenProcessPool as exc:
        message = (
            "A stateful feature worker terminated abruptly, usually because the worker exceeded available memory while "
            "building carry-over features."
        )
        emit(
            progress_callback,
            {
                "event": "parallel_failed",
                "phase": "stateful_features",
                "status": "failed",
                "message": message,
                "worker_count": worker_count,
                "stateful": True,
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
    return results


def build_market_data(request: BuildRequest, progress_callback: ProgressCallback | None = None) -> dict:
    if request.start_date > request.end_date:
        raise ValueError("start_date must be on or before end_date")
    run_started_at = perf_counter()
    scan_started_at = perf_counter()
    statuses = scan_market_source(request.raw_root, request.start_date, request.end_date)
    plan_rows = build_plan(statuses)
    plan_by_session = {str(row["session_date"]): row for row in plan_rows}
    output_statuses = [status for status in statuses if plan_by_session[status.session_date].get("build_role") == "output"]
    buildable_statuses = [status for status in output_statuses if status.expected_market_session and status.exists]
    missing_statuses = [status for status in output_statuses if status.expected_market_session and not status.exists]
    progress_state = {"completed_units": 0, "total_units": estimate_session_units(request, len(buildable_statuses))}
    metadata = plan_metadata(plan_rows)
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
            "plan": plan_rows,
            "resume_from_build_id": request.resume_from_build_id,
            "resume_stage": request.resume_stage,
            **metadata,
        },
    )
    completed = []
    artifact_tasks: list[dict] = []
    artifact_results: list[dict] = []
    selected_timeframes = set(session_timeframes(request))
    local_artifact_timeframes = selected_timeframes - CARRYOVER_TIMEFRAMES
    stateful_tasks: list[dict] = []
    resume_stateful = is_stateful_resume(request)

    for index, status in enumerate(statuses, start=1):
        plan_row = plan_by_session[status.session_date]
        if not status.expected_market_session:
            completed.append({"session_date": status.session_date, "status": "closed", "rows": 0})
            continue
        if plan_row.get("build_role") == "reference_only":
            completed.append({"session_date": status.session_date, "status": "reference_only", "rows": 0})
            emit(
                progress_callback,
                {
                    "event": "session_skipped",
                    "phase": "reference_warmup",
                    "status": "reference_only" if status.exists else "missing_reference",
                    "session_date": status.session_date,
                    "index": index,
                    "total": len(statuses),
                    "reason": plan_row.get("reason"),
                },
            )
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

        if resume_stateful:
            for timeframe in sorted(selected_timeframes & CARRYOVER_TIMEFRAMES):
                stateful_tasks.append({"session_date": status.session_date, "timeframe": timeframe})
            completed.append({"session_date": status.session_date, "status": "resume_stateful", "rows": 0})
        else:
            result = build_session_bars(
                request=request,
                status=asdict(status),
                index=index,
                total=len(statuses),
                progress_callback=progress_callback,
                progress_state=progress_state,
                build_artifacts=True,
                artifact_timeframes=local_artifact_timeframes,
            )
            for artifact_result in result.get("artifacts", []):
                artifact_tasks.append({"session_date": status.session_date, "timeframe": str(artifact_result.get("timeframe"))})
                artifact_results.append(artifact_result)
            for timeframe in result.get("timeframes", []):
                if str(timeframe) in CARRYOVER_TIMEFRAMES:
                    stateful_tasks.append({"session_date": status.session_date, "timeframe": str(timeframe)})
            completed.append({"session_date": status.session_date, "status": "bars_complete", "rows": int(result.get("rows") or 0)})

    stateful_results = build_stateful_artifact_tasks(
        request=request,
        tasks=stateful_tasks,
        total_units=progress_state["total_units"],
        job_path=None,
        progress_callback=progress_callback,
        progress_state=progress_state,
    )
    complete_stateful_timeframes = {str(result.get("timeframe")) for result in stateful_results if str(result.get("status")) == "complete"}
    for task in stateful_tasks:
        artifact_tasks.append({"session_date": str(task["session_date"]), "timeframe": str(task["timeframe"])})
        if str(task["timeframe"]) in complete_stateful_timeframes:
            artifact_results.append({"session_date": str(task["session_date"]), "timeframe": str(task["timeframe"]), "status": "complete"})

    expected_by_session: dict[str, int] = {}
    complete_by_session: dict[str, int] = {}
    for task in artifact_tasks:
        session_key = str(task["session_date"])
        expected_by_session[session_key] = expected_by_session.get(session_key, 0) + 1
    for result in artifact_results:
        if str(result.get("status")) == "complete":
            session_key = str(result.get("session_date"))
            complete_by_session[session_key] = complete_by_session.get(session_key, 0) + 1
    for row in completed:
        session_key = str(row.get("session_date"))
        if expected_by_session.get(session_key) and complete_by_session.get(session_key, 0) >= expected_by_session[session_key]:
            row["status"] = "complete"
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
        "plan": plan_rows,
        **metadata,
        "request": {
            "raw_root": str(request.raw_root),
            "processed_root": str(request.processed_root),
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "timeframes": request.timeframes,
            "feature_groups": request.feature_groups,
            "supervision_groups": request.supervision_groups,
            "rebuild_mode": "force_rebuild",
            "resume_from_build_id": request.resume_from_build_id,
            "resume_stage": request.resume_stage,
        },
    }


def build_market_data_parallel(
    request: BuildRequest,
    *,
    job_path: Path | None,
    session_workers: int = 8,
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
    plan_rows = build_plan(statuses)
    plan_by_session = {str(row["session_date"]): row for row in plan_rows}
    output_statuses = [status for status in statuses if plan_by_session[status.session_date].get("build_role") == "output"]
    buildable_statuses = [status for status in output_statuses if status.expected_market_session and status.exists]
    missing_statuses = [status for status in output_statuses if status.expected_market_session and not status.exists]
    total_units = estimate_session_units(request, len(buildable_statuses))
    metadata = plan_metadata(plan_rows)
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
            "plan": plan_rows,
            "resume_from_build_id": request.resume_from_build_id,
            "resume_stage": request.resume_stage,
            **metadata,
        },
    )

    completed: list[dict] = []
    build_jobs: list[tuple[int, dict]] = []
    for index, status in enumerate(statuses, start=1):
        plan_row = plan_by_session[status.session_date]
        if not status.expected_market_session:
            completed.append({"session_date": status.session_date, "status": "closed", "rows": 0})
            continue
        if plan_row.get("build_role") == "reference_only":
            completed.append({"session_date": status.session_date, "status": "reference_only", "rows": 0})
            emit(
                progress_callback,
                {
                    "event": "session_skipped",
                    "phase": "reference_warmup",
                    "status": "reference_only" if status.exists else "missing_reference",
                    "session_date": status.session_date,
                    "index": index,
                    "total": len(statuses),
                    "reason": plan_row.get("reason"),
                },
            )
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

    artifact_tasks: list[dict] = []
    artifact_results: list[dict] = []
    selected_timeframes = set(session_timeframes(request))
    local_artifact_timeframes = selected_timeframes - CARRYOVER_TIMEFRAMES
    stateful_tasks: list[dict] = []
    resume_stateful = is_stateful_resume(request)
    if resume_stateful:
        for _, status in build_jobs:
            session_text = str(status["session_date"])
            for timeframe in sorted(selected_timeframes & CARRYOVER_TIMEFRAMES):
                stateful_tasks.append({"session_date": session_text, "timeframe": timeframe})
            completed.append({"session_date": session_text, "status": "resume_stateful", "rows": 0})
        build_jobs = []

    worker_count = min(max(1, int(session_workers)), len(build_jobs)) if build_jobs else 0
    emit(
        progress_callback,
        {
            "event": "parallel_started",
            "phase": "parallel_bars",
            "status": "running" if worker_count else "complete",
            "worker_count": worker_count,
            "requested_worker_count": session_workers,
            "worker_limit_reason": None,
            "buildable_sessions": len(build_jobs),
            "polars_threads_per_worker": os.environ.get("POLARS_MAX_THREADS"),
            "work_total": total_units,
        },
    )

    if build_jobs:
        executor = build_process_pool(worker_count)
        futures = {
            executor.submit(
                _parallel_session_worker,
                request,
                status,
                index,
                len(statuses),
                total_units,
                str(job_path),
                local_artifact_timeframes,
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
                    for artifact_result in result.get("artifacts", []):
                        artifact_tasks.append(
                            {
                                "session_date": str(result.get("session_date")),
                                "timeframe": str(artifact_result.get("timeframe")),
                            }
                        )
                        artifact_results.append(artifact_result)
                    for timeframe in result.get("timeframes", []):
                        if str(timeframe) in CARRYOVER_TIMEFRAMES:
                            stateful_tasks.append({"session_date": str(result.get("session_date")), "timeframe": str(timeframe)})
                    completed.append(
                        {
                            "session_date": str(result.get("session_date")),
                            "status": str(result.get("status") or "bars_complete"),
                            "rows": int(result.get("rows") or 0),
                        }
                    )
                check_cancelled(job_path)
        except BrokenProcessPool as exc:
            message = (
                "A session build worker terminated abruptly, usually because the worker exceeded available memory while "
                "building bars and features for one session."
            )
            emit(
                progress_callback,
                {
                    "event": "parallel_failed",
                    "phase": "parallel_bars",
                    "status": "failed",
                    "message": message,
                    "worker_count": worker_count,
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

    stateful_results = build_stateful_artifact_tasks(
        request=request,
        tasks=stateful_tasks,
        total_units=total_units,
        job_path=job_path,
        progress_callback=progress_callback,
    )
    complete_stateful_timeframes = {str(result.get("timeframe")) for result in stateful_results if str(result.get("status")) == "complete"}
    for task in stateful_tasks:
        artifact_tasks.append({"session_date": str(task["session_date"]), "timeframe": str(task["timeframe"])})
        if str(task["timeframe"]) in complete_stateful_timeframes:
            artifact_results.append({"session_date": str(task["session_date"]), "timeframe": str(task["timeframe"]), "status": "complete"})

    expected_by_session: dict[str, int] = {}
    complete_by_session: dict[str, int] = {}
    for task in artifact_tasks:
        expected_by_session[str(task["session_date"])] = expected_by_session.get(str(task["session_date"]), 0) + 1
    for result in artifact_results:
        if str(result.get("status")) == "complete":
            session_text = str(result.get("session_date"))
            complete_by_session[session_text] = complete_by_session.get(session_text, 0) + 1
    for row in completed:
        session_text = str(row.get("session_date"))
        if expected_by_session.get(session_text) and complete_by_session.get(session_text, 0) >= expected_by_session[session_text]:
            row["status"] = "complete"

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
        "plan": plan_rows,
        **metadata,
        "request": {
            "raw_root": str(request.raw_root),
            "processed_root": str(request.processed_root),
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "timeframes": request.timeframes,
            "feature_groups": request.feature_groups,
            "supervision_groups": request.supervision_groups,
            "rebuild_mode": "force_rebuild",
            "resume_from_build_id": request.resume_from_build_id,
            "resume_stage": request.resume_stage,
        },
    }
