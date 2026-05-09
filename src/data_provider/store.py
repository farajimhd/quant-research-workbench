from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from src.data_provider.config import DEFAULT_PROCESSED_ROOT


def partition_path(root: Path, group: str, timeframe: str, session: date | str) -> Path:
    session_text = session.isoformat() if isinstance(session, date) else str(session)
    year, month = session_text[:4], session_text[5:7]
    return root / group / timeframe / year / month / f"{session_text}.parquet"


def manifest_path(root: Path = DEFAULT_PROCESSED_ROOT) -> Path:
    return root / "manifest.json"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_frame(path: Path, frame: pl.DataFrame) -> None:
    ensure_parent(path)
    frame.write_parquet(path)


def read_frame(path: Path) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path)


def scan_frame(path: Path) -> pl.LazyFrame:
    return pl.scan_parquet(path)


def existing_dates(root: Path, group: str, timeframe: str) -> list[str]:
    base = root / group / timeframe
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*/*/*.parquet"))
