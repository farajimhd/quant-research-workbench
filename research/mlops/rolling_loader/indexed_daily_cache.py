from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.rolling_loader.materialized_cache import DEFAULT_MATERIALIZED_CACHE_ROOT


INDEXED_DAILY_CACHE_FORMAT = "rolling_indexed_daily_cache"
INDEXED_DAILY_CACHE_VERSION = 1
DEFAULT_INDEXED_DAILY_CACHE_ROOT = DEFAULT_MATERIALIZED_CACHE_ROOT
SESSION_TIMEZONE = "America/New_York"
SESSION_START = dt.time(4, 0, 0)
SESSION_END = dt.time(20, 0, 0)

EVENT_PAYLOAD_COLUMNS: tuple[str, ...] = (
    "ordinal",
    "event_type",
    "timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "event_flags",
    "conditions_packed",
)

EVENT_SOURCE_COLUMNS: tuple[str, ...] = (
    "ordinal",
    "event_type",
    "sip_timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "event_flags",
    "conditions_packed",
)


@dataclass(frozen=True, slots=True)
class SessionWindow:
    session_date: dt.date
    timezone: str
    start_local: str
    end_local: str
    start_timestamp_us: int
    end_timestamp_us: int

    @property
    def start_utc(self) -> str:
        return timestamp_us_to_utc(self.start_timestamp_us)

    @property
    def end_utc(self) -> str:
        return timestamp_us_to_utc(self.end_timestamp_us)


@dataclass(frozen=True, slots=True)
class IndexedDailyCacheDayResult:
    session_date: str
    day_dir: Path
    origin_count: int
    event_count: int
    ticker_count: int
    bytes_written: int
    source_session_event_count: int
    skipped_not_enough_history: int
    skipped_window_gap: int
    status: str


def timestamp_us_to_utc(timestamp_us: int) -> str:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).isoformat()


def utc_date_from_us(timestamp_us: int) -> dt.date:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).date()


def parse_utc_us(value: str) -> int:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    else:
        parsed = parsed.astimezone(dt.timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def session_window(session_date: dt.date, *, timezone: str = SESSION_TIMEZONE) -> SessionWindow:
    tz = ZoneInfo(timezone)
    start_local = dt.datetime.combine(session_date, SESSION_START, tzinfo=tz)
    end_local = dt.datetime.combine(session_date, SESSION_END, tzinfo=tz)
    start_utc = start_local.astimezone(dt.timezone.utc)
    end_utc = end_local.astimezone(dt.timezone.utc)
    return SessionWindow(
        session_date=session_date,
        timezone=timezone,
        start_local=start_local.isoformat(),
        end_local=end_local.isoformat(),
        start_timestamp_us=int(start_utc.timestamp() * 1_000_000),
        end_timestamp_us=int(end_utc.timestamp() * 1_000_000),
    )


def iter_session_dates(start: dt.date, end_exclusive: dt.date) -> Iterable[dt.date]:
    current = start
    while current < end_exclusive:
        yield current
        current += dt.timedelta(days=1)


def context_lags_from_config(config: RollingMarketDataConfig) -> tuple[int, ...]:
    context_stride = max(1, int(config.events_per_chunk)) // 2
    context_stride = max(1, context_stride) * max(1, int(config.short_context_stride_chunks))
    dense = range(0, max(0, int(config.short_context_chunks)) * context_stride, context_stride)
    return tuple(sorted(set(int(value) for value in dense).union(int(value) for value in config.long_context_lags)))


def max_context_lag(config: RollingMarketDataConfig) -> int:
    return max(context_lags_from_config(config), default=0)


def required_event_lookback_rows(config: RollingMarketDataConfig) -> int:
    return int(max_context_lag(config)) + int(config.events_per_chunk)


def day_dir_for(cache_root: Path, split: str, session_date: dt.date) -> Path:
    return Path(cache_root) / str(split) / f"day={session_date.isoformat()}"


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            total += int(item.stat().st_size)
    return total


def cleanup_tmp_dirs(cache_root: Path) -> int:
    removed = 0
    if not cache_root.exists():
        return 0
    for path in cache_root.rglob("*.tmp"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        elif path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def replace_complete_dir(tmp_dir: Path, final_dir: Path, *, resume: bool) -> None:
    if final_dir.exists():
        if resume:
            shutil.rmtree(final_dir)
        else:
            raise FileExistsError(f"Refusing to overwrite existing day directory: {final_dir}")
    os.replace(tmp_dir, final_dir)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

