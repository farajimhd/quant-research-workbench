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
from research.mlops.rolling_loader.materialized_cache import DEFAULT_MATERIALIZED_CACHE_ROOT


TICKER_MONTH_CACHE_FORMAT = "rolling_ticker_month_ssd_cache"
TICKER_MONTH_CACHE_VERSION = 1
DEFAULT_TICKER_MONTH_CACHE_ROOT = DEFAULT_MATERIALIZED_CACHE_ROOT.parent / "rolling_ticker_month_cache"
SESSION_TIMEZONE = "America/New_York"
SESSION_START = dt.time(4, 0, 0)
SESSION_END = dt.time(20, 0, 0)
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


@dataclass(frozen=True, slots=True)
class TickerMonthResult:
    month: str
    ticker: str
    package_dir: Path
    status: str
    event_count: int = 0
    origin_count: int = 0
    label_rows: int = 0
    byte_count: int = 0
    skipped_not_enough_history: int = 0
    skipped_window_gap: int = 0
    error: str = ""


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


def month_dir_for(cache_root: Path, split: str, month: str) -> Path:
    return Path(cache_root) / str(split) / f"month={month}"


def ticker_package_dir(month_dir: Path, ticker: str) -> Path:
    upper = str(ticker).upper()
    bucket = f"{stable_ticker_bucket(upper):02x}"
    return Path(month_dir) / f"ticker_hash={bucket}" / f"ticker={upper}"


def stable_ticker_bucket(ticker: str, buckets: int = 256) -> int:
    value = 0
    for char in str(ticker).upper().encode("utf-8"):
        value = ((value * 131) + int(char)) & 0xFFFFFFFF
    return value % max(1, int(buckets))


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
            raise FileExistsError(f"Refusing to overwrite existing package directory: {final_dir}")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_dir, final_dir)


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
        corporate_action_label_days=tuple(parse_day_horizons(getattr(args, "corporate_action_label_days", "1,2,3,5,10,20,40"))),
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


def month_manifest_payload(*, args: Any, cache_id: str, cache_root: Path, loaded_env: list[Path], months: tuple[str, ...], context_lags: tuple[int, ...]) -> dict[str, Any]:
    return {
        "format": TICKER_MONTH_CACHE_FORMAT,
        "version": TICKER_MONTH_CACHE_VERSION,
        "status": "running",
        "cache_id": cache_id,
        "cache_root": str(cache_root),
        "split": str(args.split),
        "created_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "months": list(months),
        "source": {
            "database": args.database,
            "events_table": args.events_table,
            "condition_token_reference_table": getattr(args, "condition_token_reference_table", "event_condition_token_reference"),
            "macro_bars_table": args.macro_bars_table,
            "news_token_table": args.news_token_table,
            "news_embedding_table": getattr(args, "news_embedding_table", "news_text_embeddings"),
            "sec_context_database": args.sec_context_database,
            "sec_filing_text_token_table": args.sec_filing_text_token_table,
            "sec_filing_text_embedding_table": getattr(args, "sec_filing_text_embedding_table", "sec_filing_text_embeddings"),
            "sec_xbrl_context_table": args.sec_xbrl_context_table,
            "category_reference_table": args.category_reference_table,
            "q_live_database": getattr(args, "q_live_database", "q_live"),
            "stock_split_table": getattr(args, "stock_split_table", "market_stock_split_v1"),
            "cash_dividend_table": getattr(args, "cash_dividend_table", "market_cash_dividend_v1"),
        },
        "config": {
            "events_per_chunk": int(args.events_per_chunk),
            "context_lags": list(context_lags),
            "context_chunk_stride_events": int(args.context_chunk_stride_events),
            "sample_stride_events": int(args.sample_stride_events),
            "context_fetch_mode": "month_plus_latest_prior_items",
            "ticker_news_prior_items": int(getattr(args, "ticker_news_prior_items", 0)),
            "market_news_prior_items": int(getattr(args, "market_news_prior_items", 0)),
            "sec_filing_prior_items": int(getattr(args, "sec_filing_prior_items", 0)),
            "xbrl_prior_rows": int(getattr(args, "xbrl_prior_rows", 0)),
            "xbrl_items": int(getattr(args, "xbrl_items", 0)),
            "corporate_action_items": int(getattr(args, "corporate_action_items", 0)),
            "corporate_action_lookback_days": int(getattr(args, "corporate_action_lookback_days", 0)),
            "corporate_action_label_days": list(parse_day_horizons(getattr(args, "corporate_action_label_days", ""))),
            "event_payload_columns": list(EVENT_PAYLOAD_COLUMNS),
            "event_time_feature_columns": list(EVENT_TIME_FEATURE_COLUMNS),
            "context_available_time_feature_columns": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
            "context_effective_time_feature_columns": list(CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS),
            "intraday_label_horizons": [h.name for h in parse_horizons(args.intraday_label_horizons)],
            "future_condition_label_keys": [
                "condition_halt_pause_flag",
                "condition_resume_flag",
                "condition_news_risk_flag",
                "condition_luld_limit_state_flag",
            ],
            "future_external_arrival_label_keys": [
                "ticker_news_arrival_flag",
                "sec_filing_arrival_flag",
            ],
            "future_event_flag_label_keys": [
                "condition_halt_pause_flag",
                "condition_resume_flag",
                "condition_news_risk_flag",
                "condition_luld_limit_state_flag",
                "ticker_news_arrival_flag",
                "sec_filing_arrival_flag",
            ],
        },
        "env_files_loaded": [str(path) for path in loaded_env],
        "args": redacted_args(args),
        "packages": [],
    }


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


def month_window_dict(window: MonthWindow) -> dict[str, Any]:
    return asdict(window)
