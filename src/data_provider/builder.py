from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import polars as pl

from src.data_provider.config import BuildRequest
from src.data_provider.features import FEATURE_COLUMNS, add_feature_columns, select_feature_group
from src.data_provider.manifest import ArtifactRecord, upsert_artifact
from src.data_provider.raw_loader import load_raw_minute_bars, raw_minute_path, scan_source
from src.data_provider.store import partition_path, write_frame
from src.data_provider.supervision import build_bar_supervision, build_method_supervision, build_scanner_supervision
from src.data_provider.timeframes import aggregate_daily, aggregate_intraday, aggregate_monthly, canonicalize_1m


ProgressCallback = Callable[[dict], None]


def should_write(path: Path, mode: str) -> bool:
    if mode == "force_rebuild":
        return True
    if mode in {"skip_existing", "build_missing"}:
        return not path.exists()
    return not path.exists()


def write_artifact(
    *,
    root: Path,
    group: str,
    timeframe: str,
    session_date: str,
    frame: pl.DataFrame,
    source_path: Path | None = None,
    rebuild_mode: str,
) -> Path:
    path = partition_path(root, group, timeframe, session_date)
    if should_write(path, rebuild_mode):
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
) -> dict[str, pl.DataFrame]:
    if bars.is_empty():
        return {}
    features = add_feature_columns(bars)
    outputs = {}
    for group in request.feature_groups:
        if group not in FEATURE_COLUMNS:
            continue
        group_frame = select_feature_group(features, group)
        write_artifact(
            root=request.processed_root,
            group=f"features_{group}",
            timeframe=timeframe,
            session_date=session_date,
            frame=group_frame,
            source_path=source_path,
            rebuild_mode=request.rebuild_mode,
        )
        outputs[group] = group_frame
    return outputs


def build_supervision_groups(
    *,
    request: BuildRequest,
    timeframe: str,
    session_date: str,
    bars: pl.DataFrame,
    source_path: Path,
) -> None:
    if bars.is_empty() or not request.supervision_groups:
        return
    bar_supervision = None
    method_supervision = None
    if "bar" in request.supervision_groups:
        bar_supervision = build_bar_supervision(bars)
        write_artifact(
            root=request.processed_root,
            group="supervision_bar",
            timeframe=timeframe,
            session_date=session_date,
            frame=bar_supervision,
            source_path=source_path,
            rebuild_mode=request.rebuild_mode,
        )
    if "method" in request.supervision_groups or "scanner" in request.supervision_groups:
        method_supervision = build_method_supervision(bars)
        if "method" in request.supervision_groups:
            write_artifact(
                root=request.processed_root,
                group="supervision_method",
                timeframe=timeframe,
                session_date=session_date,
                frame=method_supervision,
                source_path=source_path,
                rebuild_mode=request.rebuild_mode,
            )
    if "scanner" in request.supervision_groups:
        if method_supervision is None:
            method_supervision = build_method_supervision(bars)
        scanner_supervision = build_scanner_supervision(method_supervision)
        write_artifact(
            root=request.processed_root,
            group="supervision_scanner",
            timeframe=timeframe,
            session_date=session_date,
            frame=scanner_supervision,
            source_path=source_path,
            rebuild_mode=request.rebuild_mode,
        )


def build_market_data(request: BuildRequest, progress_callback: ProgressCallback | None = None) -> dict:
    statuses = scan_source(request.raw_root, request.start_date, request.end_date)
    completed = []
    daily_frames = []

    for index, status in enumerate(statuses, start=1):
        if progress_callback:
            progress_callback({"phase": "read_raw", "session_date": status.session_date, "index": index, "total": len(statuses)})
        if not status.exists:
            completed.append({"session_date": status.session_date, "status": "missing_raw", "rows": 0})
            continue

        session_date = datetime.fromisoformat(status.session_date).date()
        source_path = raw_minute_path(request.raw_root, session_date)
        raw = load_raw_minute_bars(request.raw_root, session_date, request.tickers)
        bars_1m = canonicalize_1m(raw, request.exchange_timezone)
        if "1m" in request.timeframes:
            write_artifact(
                root=request.processed_root,
                group="bars",
                timeframe="1m",
                session_date=status.session_date,
                frame=bars_1m,
                source_path=source_path,
                rebuild_mode=request.rebuild_mode,
            )
            build_feature_groups(request=request, timeframe="1m", session_date=status.session_date, bars=bars_1m, source_path=source_path)
            build_supervision_groups(request=request, timeframe="1m", session_date=status.session_date, bars=bars_1m, source_path=source_path)

        day_frames_for_session = []
        for timeframe in request.timeframes:
            if timeframe == "1m":
                continue
            if timeframe in {"5m", "15m", "30m", "1h", "2h", "4h"}:
                if progress_callback:
                    progress_callback({"phase": f"build_{timeframe}", "session_date": status.session_date, "index": index, "total": len(statuses)})
                bars = aggregate_intraday(bars_1m, timeframe)
                write_artifact(
                    root=request.processed_root,
                    group="bars",
                    timeframe=timeframe,
                    session_date=status.session_date,
                    frame=bars,
                    source_path=source_path,
                    rebuild_mode=request.rebuild_mode,
                )
                build_feature_groups(request=request, timeframe=timeframe, session_date=status.session_date, bars=bars, source_path=source_path)
                build_supervision_groups(request=request, timeframe=timeframe, session_date=status.session_date, bars=bars, source_path=source_path)
            elif timeframe == "1d":
                bars = aggregate_daily(bars_1m)
                day_frames_for_session.append(bars)
                write_artifact(
                    root=request.processed_root,
                    group="bars",
                    timeframe="1d",
                    session_date=status.session_date,
                    frame=bars,
                    source_path=source_path,
                    rebuild_mode=request.rebuild_mode,
                )
                build_feature_groups(request=request, timeframe="1d", session_date=status.session_date, bars=bars, source_path=source_path)
                build_supervision_groups(request=request, timeframe="1d", session_date=status.session_date, bars=bars, source_path=source_path)
        daily_frames.extend(day_frames_for_session)
        completed.append({"session_date": status.session_date, "status": "complete", "rows": bars_1m.height})
        if progress_callback:
            progress_callback({"phase": "complete", "session_date": status.session_date, "index": index, "total": len(statuses), "rows": bars_1m.height})

    if "1mo" in request.timeframes and daily_frames:
        daily = pl.concat(daily_frames, how="diagonal").sort(["ticker", "bar_time_utc"])
        monthly = aggregate_monthly(daily)
        for session_month, month_frame in monthly.group_by("session_month", maintain_order=True):
            month_value = session_month[0] if isinstance(session_month, tuple) else session_month
            session_date = f"{month_value}-01"
            source_path = request.raw_root
            write_artifact(
                root=request.processed_root,
                group="bars",
                timeframe="1mo",
                session_date=session_date,
                frame=month_frame,
                source_path=None,
                rebuild_mode=request.rebuild_mode,
            )
            build_feature_groups(request=request, timeframe="1mo", session_date=session_date, bars=month_frame, source_path=source_path)
            build_supervision_groups(request=request, timeframe="1mo", session_date=session_date, bars=month_frame, source_path=source_path)

    return {
        "processed_root": str(request.processed_root),
        "completed": completed,
        "request": {
            "raw_root": str(request.raw_root),
            "processed_root": str(request.processed_root),
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "timeframes": request.timeframes,
            "feature_groups": request.feature_groups,
            "supervision_groups": request.supervision_groups,
            "rebuild_mode": request.rebuild_mode,
        },
    }
