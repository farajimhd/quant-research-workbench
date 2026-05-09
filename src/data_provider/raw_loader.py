from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl


RAW_COLUMNS = ["ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"]


@dataclass(slots=True)
class SourceFileStatus:
    session_date: str
    path: str
    exists: bool
    size_bytes: int
    modified_at: float | None


def date_range(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def raw_minute_path(raw_root: Path, session: date) -> Path:
    return raw_root / f"{session.year:04d}" / f"{session.month:02d}" / f"{session.isoformat()}.csv.gz"


def scan_source(raw_root: Path, start: date, end: date) -> list[SourceFileStatus]:
    rows = []
    for session in date_range(start, end):
        path = raw_minute_path(raw_root, session)
        exists = path.exists()
        rows.append(
            SourceFileStatus(
                session_date=session.isoformat(),
                path=str(path),
                exists=exists,
                size_bytes=path.stat().st_size if exists else 0,
                modified_at=path.stat().st_mtime if exists else None,
            )
        )
    return rows


def load_raw_minute_bars(raw_root: Path, session: date, tickers: list[str] | None = None) -> pl.DataFrame:
    source = raw_minute_path(raw_root, session)
    if not source.exists():
        return pl.DataFrame()
    scan = pl.scan_csv(source).select(RAW_COLUMNS)
    if tickers:
        scan = scan.filter(pl.col("ticker").is_in(tickers))
    return scan.collect()
