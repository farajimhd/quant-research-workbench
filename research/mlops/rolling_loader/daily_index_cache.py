from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.data.config import RollingMarketDataConfig, TimeBarHorizon
from research.mlops.data.contracts import BAR_FAMILY_FEATURE_KEYS, BAR_FAMILY_KEYS


DAILY_INDEX_CACHE_FORMAT = "daily_index_streaming_cache"
DAILY_INDEX_CACHE_VERSION = 1
DEFAULT_DAILY_INDEX_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache")
SESSION_TIMEZONE = "America/New_York"
SESSION_START = dt.time(4, 0, 0)
SESSION_END = dt.time(20, 0, 0)
SESSION_LENGTH_US = 16 * 60 * 60 * 1_000_000
_WRITE_JSON_LOCK = threading.Lock()

EVENT_PAYLOAD_COLUMNS: tuple[str, ...] = (
    "ticker_id",
    "ticker",
    "ordinal",
    "event_meta",
    "timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_token_1",
    "condition_token_2",
    "condition_token_3",
    "condition_token_4",
    "condition_token_5",
)

EVENT_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "utc_second_of_day_sin",
    "utc_second_of_day_cos",
    "utc_day_of_week_sin",
    "utc_day_of_week_cos",
    "utc_day_of_year_sin",
    "utc_day_of_year_cos",
    "years_since_2000",
    "session_second",
    "session_progress",
    "is_regular_hours",
    "is_premarket",
    "is_afterhours",
)

CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "available_utc_second_of_day_sin",
    "available_utc_second_of_day_cos",
    "available_utc_day_of_week_sin",
    "available_utc_day_of_week_cos",
    "available_utc_day_of_year_sin",
    "available_utc_day_of_year_cos",
    "available_years_since_2000",
)

BAR_START_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "bar_start_utc_second_of_day_sin",
    "bar_start_utc_second_of_day_cos",
    "bar_start_utc_day_of_week_sin",
    "bar_start_utc_day_of_week_cos",
    "bar_start_utc_day_of_year_sin",
    "bar_start_utc_day_of_year_cos",
    "bar_start_years_since_2000",
)

BAR_END_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "bar_end_utc_second_of_day_sin",
    "bar_end_utc_second_of_day_cos",
    "bar_end_utc_day_of_week_sin",
    "bar_end_utc_day_of_week_cos",
    "bar_end_utc_day_of_year_sin",
    "bar_end_utc_day_of_year_cos",
    "bar_end_years_since_2000",
)

CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "effective_utc_second_of_day_sin",
    "effective_utc_second_of_day_cos",
    "effective_utc_day_of_week_sin",
    "effective_utc_day_of_week_cos",
    "effective_utc_day_of_year_sin",
    "effective_utc_day_of_year_cos",
    "effective_years_since_2000",
)


@dataclass(frozen=True, slots=True)
class MonthWindow:
    month: str
    first_date: dt.date
    next_month_date: dt.date
    first_session_start_us: int
    last_session_end_us: int
    first_session_start_utc: str
    last_session_end_utc: str
    timezone: str = SESSION_TIMEZONE

def month_window(month: str, *, timezone: str = SESSION_TIMEZONE) -> MonthWindow:
    first = parse_month(month)
    next_month = add_months(first, 1)
    tz = ZoneInfo(timezone)
    first_start_local = dt.datetime.combine(first, SESSION_START, tzinfo=tz)
    last_session_date = next_month - dt.timedelta(days=1)
    last_end_local = dt.datetime.combine(last_session_date, SESSION_END, tzinfo=tz)
    first_start_utc = first_start_local.astimezone(dt.timezone.utc)
    last_end_utc = last_end_local.astimezone(dt.timezone.utc)
    return MonthWindow(
        month=first.strftime("%Y-%m"),
        first_date=first,
        next_month_date=next_month,
        first_session_start_us=int(first_start_utc.timestamp() * 1_000_000),
        last_session_end_us=int(last_end_utc.timestamp() * 1_000_000),
        first_session_start_utc=first_start_utc.isoformat(),
        last_session_end_utc=last_end_utc.isoformat(),
        timezone=timezone,
    )


def parse_month(value: str) -> dt.date:
    text = str(value).strip()
    try:
        parsed = dt.datetime.strptime(text, "%Y-%m").date()
    except ValueError as exc:
        raise ValueError(f"Invalid month {value!r}; expected YYYY-MM.") from exc
    return parsed.replace(day=1)


def add_months(date_value: dt.date, months: int) -> dt.date:
    month_index = (date_value.year * 12 + date_value.month - 1) + int(months)
    year, month0 = divmod(month_index, 12)
    return dt.date(year, month0 + 1, 1)


def full_months_in_period(start_utc: str, end_utc: str) -> tuple[str, ...]:
    start_date = parse_utc_date(start_utc)
    end_date = parse_utc_date(end_utc)
    if end_date <= start_date:
        raise ValueError("--end-utc must be after --start-utc.")
    first_month = start_date.replace(day=1)
    if start_date != first_month:
        first_month = add_months(first_month, 1)
    months: list[str] = []
    current = first_month
    while add_months(current, 1) <= end_date:
        months.append(current.strftime("%Y-%m"))
        current = add_months(current, 1)
    return tuple(months)


def parse_utc_date(value: str) -> dt.date:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).date()


def timestamp_us_to_utc(timestamp_us: int) -> str:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).isoformat()


def timestamp_us_to_ny_date(timestamp_us: int) -> str:
    tz = ZoneInfo(SESSION_TIMEZONE)
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).astimezone(tz).date().isoformat()


def month_dir_for(cache_root: Path, month: str) -> Path:
    return Path(cache_root) / f"month={month}"


def ticker_package_dir(month_dir: Path, ticker: str) -> Path:
    return Path(month_dir) / f"ticker={ticker_path_token(ticker)}"


def ticker_path_token(ticker: str) -> str:
    return str(ticker).encode("utf-8").hex()


def ticker_from_path_token(token: str) -> str:
    value = str(token)
    if value and len(value) % 2 == 0:
        try:
            return bytes.fromhex(value).decode("utf-8")
        except ValueError:
            pass
        except UnicodeDecodeError:
            pass
    return value




def context_lags_from_args(
    *,
    events_per_chunk: int,
    short_context_chunks: int,
    context_chunk_stride_events: int,
    short_context_stride_chunks: int,
    long_context_lags: Iterable[int],
) -> tuple[int, ...]:
    stride = max(1, int(context_chunk_stride_events)) * max(1, int(short_context_stride_chunks))
    dense = range(0, max(0, int(short_context_chunks)) * stride, stride)
    return tuple(sorted(set(int(value) for value in dense).union(int(value) for value in long_context_lags)))


def required_event_lookback_rows(context_lags: tuple[int, ...], events_per_chunk: int) -> int:
    return int(max(context_lags, default=0)) + max(1, int(events_per_chunk))


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
        with _WRITE_JSON_LOCK:
            tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


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








def build_config_from_args(args: Any) -> RollingMarketDataConfig:
    return RollingMarketDataConfig(
        database=args.database,
        q_live_database=getattr(args, "q_live_database", "q_live"),
        sec_context_database=args.sec_context_database,
        events_table=args.events_table,
        condition_token_reference_table=getattr(args, "condition_token_reference_table", "event_condition_token_reference"),
        macro_bars_table=args.macro_bars_table,
        news_token_table=args.news_token_table,
        sec_filing_text_token_table=args.sec_filing_text_token_table,
        news_embedding_table=getattr(args, "news_embedding_table", "news_text_embeddings"),
        sec_filing_text_embedding_table=getattr(args, "sec_filing_text_embedding_table", "sec_filing_text_embeddings"),
        sec_xbrl_context_table=args.sec_xbrl_context_table,
        category_reference_table=args.category_reference_table,
        stock_split_table=getattr(args, "stock_split_table", "market_stock_split_v1"),
        cash_dividend_table=getattr(args, "cash_dividend_table", "market_cash_dividend_v1"),
        events_per_chunk=max(1, int(args.events_per_chunk)),
        short_context_chunks=max(0, int(args.short_context_chunks)),
        short_context_stride_chunks=max(1, int(args.short_context_stride_chunks)),
        long_context_lags=tuple(parse_lags(args.long_context_lags)),
        sample_stride_events=max(1, int(args.sample_stride_events)),
        max_threads=max(1, int(args.max_threads)),
        max_memory_usage=str(args.max_memory_usage),
        macro_timeframes=("1d",),
        label_timeframes=("1d",),
        macro_lookback_days=max(0, int(args.macro_lookback_days)),
        label_lookahead_days=max(0, int(args.label_lookahead_days)),
        q_live_contexts=tuple(_enabled_contexts(args)),
        news_lookback_days=max(0, int(args.news_lookback_days)),
        sec_lookback_days=max(0, int(args.sec_lookback_days)),
        xbrl_lookback_days=max(0, int(args.xbrl_lookback_days)),
        corporate_action_lookback_days=max(0, int(getattr(args, "corporate_action_lookback_days", 3650))),
        news_max_items=max(0, int(args.ticker_news_items)),
        market_news_max_items=max(0, int(args.market_news_items)),
        sec_max_items=max(0, int(args.sec_filing_items)),
        xbrl_max_items=max(0, int(args.xbrl_items)),
        corporate_action_max_items=max(0, int(getattr(args, "corporate_action_items", 128))),
        corporate_action_label_days=tuple(parse_day_horizons(getattr(args, "corporate_action_label_days", "1,2,3,7,28"))),
        intraday_label_horizons=tuple(parse_horizons(args.intraday_label_horizons)),
    )


def _enabled_contexts(args: Any) -> tuple[str, ...]:
    contexts: list[str] = []
    if not bool(getattr(args, "skip_token_contexts", False)):
        contexts.extend(["ticker_news", "market_news", "sec_filings"])
    if not bool(getattr(args, "skip_xbrl", False)):
        contexts.append("xbrl")
    if not bool(getattr(args, "skip_corporate_actions", False)):
        contexts.append("corporate_actions")
    return tuple(contexts)


def parse_lags(value: str | Iterable[int]) -> tuple[int, ...]:
    if isinstance(value, str):
        if not value.strip():
            return ()
        return tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    return tuple(sorted({int(item) for item in value}))


def parse_horizons(value: str | Iterable[TimeBarHorizon]) -> tuple[TimeBarHorizon, ...]:
    if not isinstance(value, str):
        return tuple(value)
    text = value.strip()
    if not text:
        return tuple(RollingMarketDataConfig().intraday_label_horizons)
    out: list[TimeBarHorizon] = []
    for item in text.split(","):
        name = item.strip()
        if not name:
            continue
        out.append(TimeBarHorizon(name=name, microseconds=parse_duration_us(name)))
    return tuple(out)


def parse_day_horizons(value: str | Iterable[int]) -> tuple[int, ...]:
    if isinstance(value, str):
        raw_values = (item.strip().lower() for item in value.split(","))
    else:
        raw_values = (str(item).strip().lower() for item in value)
    days: set[int] = set()
    for item in raw_values:
        if not item:
            continue
        if item.endswith("d"):
            item = item[:-1]
        parsed = int(float(item))
        if parsed > 0:
            days.add(parsed)
    return tuple(sorted(days))


def parse_duration_us(value: str) -> int:
    text = str(value).strip().lower()
    if text in {"eod", "end_of_day", "end-of-day"}:
        return SESSION_LENGTH_US
    if text.endswith("ms"):
        return int(float(text[:-2]) * 1_000)
    if text.endswith("us"):
        return int(float(text[:-2]))
    if text.endswith("s"):
        return int(float(text[:-1]) * 1_000_000)
    if text.endswith("m"):
        return int(float(text[:-1]) * 60_000_000)
    if text.endswith("h"):
        return int(float(text[:-1]) * 3_600_000_000)
    return int(float(text) * 1_000_000)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))




def redacted_args(args: Any) -> dict[str, Any]:
    out = dict(vars(args))
    for key, value in list(out.items()):
        if isinstance(value, Path):
            out[key] = str(value)
        upper = str(key).upper()
        secret_like = (
            "PASSWORD" in upper
            or "SECRET" in upper
            or upper == "TOKEN"
            or upper.endswith("_TOKEN")
            or upper == "KEY"
            or upper.endswith("_KEY")
            or "API_KEY" in upper
        )
        if secret_like:
            out[key] = "<present>" if value else ""
    return out



