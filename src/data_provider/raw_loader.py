from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl


RAW_COLUMNS = ["ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"]
SPREAD_COLUMNS = [
    "ticker",
    "window_start",
    "quote_bid_price",
    "quote_ask_price",
    "spread",
    "spread_midpoint",
    "spread_bps",
    "quote_bid_size",
    "quote_ask_size",
    "quote_sip_timestamp",
    "quote_missing",
    "spread_is_locked_or_crossed",
]


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


def spread_minute_path(spread_root: Path, session: date) -> Path:
    return spread_root / f"{session.year:04d}" / f"{session.month:02d}" / f"{session.isoformat()}.parquet"


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


def load_minute_spreads(spread_root: Path, session: date, tickers: list[str] | None = None) -> pl.DataFrame:
    source = spread_minute_path(spread_root, session)
    if not source.exists():
        return pl.DataFrame()
    scan = pl.scan_parquet(source)
    schema = scan.collect_schema()
    scan = scan.select([column for column in SPREAD_COLUMNS if column in schema])
    rename_map = {
        source_name: target_name
        for source_name, target_name in {
            "spread": "actual_spread",
            "spread_midpoint": "quote_midpoint",
            "spread_bps": "actual_spread_bps",
        }.items()
        if source_name in schema
    }
    if tickers:
        scan = scan.filter(pl.col("ticker").is_in(tickers))
    return (
        scan.with_columns(
            pl.col("ticker").cast(pl.Utf8),
            pl.col("window_start").cast(pl.Int64),
        )
        .rename(rename_map)
        .collect()
    )
