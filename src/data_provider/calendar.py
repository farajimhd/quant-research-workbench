from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas_market_calendars as mcal

from src.data_provider.raw_loader import raw_minute_path


MARKET_CALENDAR = "XNYS"


@dataclass(slots=True)
class RawFileInfo:
    session_date: str
    path: str
    exists: bool
    size_bytes: int
    modified_at: float | None
    expected_market_session: bool
    status: str


def market_sessions(start: date, end: date, calendar_name: str = MARKET_CALENDAR) -> list[date]:
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    return [session.date() for session in schedule.index]


def discover_raw_bounds(raw_root: Path) -> tuple[date | None, date | None, int]:
    files = sorted(raw_root.glob("*/*/*.csv.gz"))
    dates = []
    for path in files:
        try:
            dates.append(date.fromisoformat(path.stem.removesuffix(".csv")))
        except ValueError:
            continue
    if not dates:
        return None, None, 0
    return min(dates), max(dates), len(dates)


def scan_market_source(raw_root: Path, start: date, end: date) -> list[RawFileInfo]:
    expected = set(market_sessions(start, end))
    rows: list[RawFileInfo] = []
    cursor = start
    while cursor <= end:
        path = raw_minute_path(raw_root, cursor)
        exists = path.exists()
        is_expected = cursor in expected
        if exists and is_expected:
            status = "ready"
        elif exists:
            status = "unexpected_file"
        elif is_expected:
            status = "missing"
        else:
            status = "closed"
        rows.append(
            RawFileInfo(
                session_date=cursor.isoformat(),
                path=str(path),
                exists=exists,
                size_bytes=path.stat().st_size if exists else 0,
                modified_at=path.stat().st_mtime if exists else None,
                expected_market_session=is_expected,
                status=status,
            )
        )
        cursor += timedelta(days=1)
    return rows
