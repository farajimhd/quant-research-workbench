from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping
from urllib import error as url_error
from urllib import request as url_request
from urllib import parse as url_parse
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import numpy as np

from pipelines.market_sip.events.clickhouse_build_unified_events import events_table_for_year, events_table_uses_year_suffix
from research.mlops.clickhouse import parse_size_bytes, quote_ident, sql_string


@dataclass(frozen=True, slots=True)
class PackedContextConfig:
    database: str = "market_sip_compact"
    q_live_database: str = "q_live"
    sec_context_database: str = "market_sip_compact"
    events_table: str = "events"
    macro_bars_table: str = "macro_bars_by_time_symbol"
    news_embedding_table: str = "news_text_embeddings"
    sec_filing_text_embedding_table: str = "sec_filing_text_embeddings"
    sec_xbrl_context_table: str = "sec_xbrl_context"
    category_reference_table: str = "training_category_reference"
    stock_split_table: str = "market_stock_split_v1"
    cash_dividend_table: str = "market_cash_dividend_v1"
    max_threads: int = 8
    max_memory_usage: str = "80G"
    global_symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA")
    macro_lookback_days: int = 400
    label_lookahead_days: int = 400
    corporate_action_lookback_days: int = 3650


@dataclass(frozen=True, slots=True)
class MonthWindow:
    month: str
    first_date: dt.date
    next_month_date: dt.date
    first_session_start_us: int
    last_session_end_us: int
    first_session_start_utc: str
    last_session_end_utc: str
    timezone: str = "America/New_York"


def month_window(month: str, *, timezone: str = "America/New_York") -> MonthWindow:
    first = _parse_month(month)
    next_month = _add_months(first, 1)
    tz = ZoneInfo(timezone)
    first_start_local = dt.datetime.combine(first, dt.time(4, 0), tzinfo=tz)
    last_end_local = dt.datetime.combine(next_month - dt.timedelta(days=1), dt.time(20, 0), tzinfo=tz)
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


def _parse_month(value: str) -> dt.date:
    try:
        parsed = dt.datetime.strptime(str(value).strip(), "%Y-%m").date()
    except ValueError as exc:
        raise ValueError(f"Invalid month {value!r}; expected YYYY-MM.") from exc
    return parsed.replace(day=1)


def _add_months(date_value: dt.date, months: int) -> dt.date:
    month_index = (date_value.year * 12 + date_value.month - 1) + int(months)
    year, month0 = divmod(month_index, 12)
    return dt.date(year, month0 + 1, 1)


def parse_day_horizons(value: str | tuple[int, ...]) -> tuple[int, ...]:
    raw_values = (item.strip().lower() for item in value.split(",")) if isinstance(value, str) else (str(item).strip().lower() for item in value)
    days: set[int] = set()
    for item in raw_values:
        if not item:
            continue
        parsed = int(float(item[:-1] if item.endswith("d") else item))
        if parsed > 0:
            days.add(parsed)
    return tuple(sorted(days))

NEWS_TOKEN_COLUMNS: tuple[str, ...] = (
    "ticker", "timestamp_us", "source_id", "provider", "provider_article_id", "title", "article_url", "url_domain",
    "channels", "provider_tags", "quality_flags", "tokenizer_model", "max_tokens", "token_chunk_index", "token_start",
    "token_end", "original_token_count", "token_count", "padding_tokens", "was_truncated", "input_ids", "attention_mask",
    "text_hash", "text_char_count", "source_text_char_count", "text_prefix_truncated",
)
SEC_TOKEN_COLUMNS: tuple[str, ...] = (
    "ticker", "timestamp_us", "source_id", "accession_number", "cik", "form_type", "text_rank", "document_id",
    "text_kind", "quality_flags", "tokenizer_model", "max_tokens", "token_chunk_index", "token_start", "token_end",
    "original_token_count", "token_count", "padding_tokens", "was_truncated", "input_ids", "attention_mask",
    "text_hash", "text_char_count", "source_text_char_count", "text_prefix_truncated",
)
NEWS_EMBEDDING_COLUMNS: tuple[str, ...] = tuple(column for column in NEWS_TOKEN_COLUMNS if column not in {"input_ids", "attention_mask"}) + ("embedding_model", "embedding_pooling", "embedding_dtype", "embedding_dim", "embedding")
SEC_EMBEDDING_COLUMNS: tuple[str, ...] = tuple(column for column in SEC_TOKEN_COLUMNS if column not in {"input_ids", "attention_mask"}) + ("embedding_model", "embedding_pooling", "embedding_dtype", "embedding_dim", "embedding")

DEFAULTS: dict[str, Any] = {
    "database": "market_sip_compact",
    "sec_context_database": "market_sip_compact",
    "events_table": "events",
    "condition_token_reference_table": "event_condition_token_reference",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "news_token_table": "news_text_tokens",
    "sec_filing_text_token_table": "sec_filing_text_tokens",
    "news_embedding_table": "news_text_embeddings",
    "sec_filing_text_embedding_table": "sec_filing_text_embeddings",
    "sec_xbrl_context_table": "sec_xbrl_context",
    "category_reference_table": "training_category_reference",
    "q_live_database": "q_live",
    "stock_split_table": "market_stock_split_v1",
    "cash_dividend_table": "market_cash_dividend_v1",
    "events_per_chunk": 128,
    "short_context_chunks": 32,
    "context_chunk_stride_events": 64,
    "short_context_stride_chunks": 1,
    "long_context_lags": "",
    "sample_stride_events": 1,
    "max_threads": 8,
    "max_memory_usage": "120G",
    "macro_lookback_days": 400,
    "label_lookahead_days": 400,
    "news_lookback_days": 30,
    "sec_lookback_days": 365,
    "xbrl_lookback_days": 730,
    "ticker_news_items": 8,
    "market_news_items": 16,
    "sec_filing_items": 4,
    "ticker_news_prior_items": 64,
    "market_news_prior_items": 512,
    "sec_filing_prior_items": 32,
    "xbrl_items": 4096,
    "xbrl_prior_rows": 4096,
    "corporate_action_items": 128,
    "corporate_action_lookback_days": 3650,
    "corporate_action_label_days": "1,2,3,7,28",
    "intraday_label_horizons": "100ms,200ms,300ms,400ms,500ms,1s,2s,3s,5s,10s,15s,30s,60s,120s,180s,300s,600s,900s,1200s,1800s,3600s,7200s,3h,4h,5h,eod",
    "intraday_context_horizons": "100ms,200ms,300ms,400ms,500ms,1s,2s,3s,5s,10s,15s,30s,60s,120s,180s,300s,600s,900s,1200s,1800s,3600s,7200s,3h,4h,5h,eod",
}

SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60
INTRADAY_LABEL_GRID_RESOLUTIONS_US: tuple[int, ...] = (100_000, 1_000_000, 5_000_000, 30_000_000, 60_000_000)
FUTURE_CONDITION_GROUPS: tuple[tuple[str, tuple[tuple[str, tuple[int, ...]], ...]], ...] = (
    ("condition_halt_pause_flag", (("cta_security_status", (102, 114, 117)), ("halt_reason", (153, 154, 155, 156, 157, 158, 159, 160, 161, 163, 165, 166, 168, 184, 186)), ("quote_conditions", (43,)), ("luld_indicators", (17,)))),
    ("condition_resume_flag", (("cta_security_status", (103,)), ("halt_reason", (169, 170, 171, 172, 173, 174, 178)), ("quote_conditions", (16,)))),
    ("condition_news_risk_flag", (("halt_reason", (151,)), ("quote_conditions", (25, 27)), ("halt_reason", (152, 167)), ("quote_conditions", (21, 23)))),
    ("condition_luld_limit_state_flag", (("cta_security_status", (114,)), ("halt_reason", (153, 165, 166, 186)), ("quote_conditions", (35, 39, 43)), ("luld_indicators", (11, 12, 22, 23, 24, 25, 26, 27, 28, 29, 30)))),
)
SPECIAL_DIVIDEND_TYPES: frozenset[str] = frozenset({"special", "irregular", "supplemental", "extra", "non-recurring", "non recurring"})
QUERY_ID_PREFIX = "packed_market_context_"
PROCESS_QUERY_ID_PREFIX = f"{QUERY_ID_PREFIX}{os.getpid()}_"


def date_time64_from_us(timestamp_us: int) -> str:
    value = dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc)
    return f"toDateTime64({sql_string(value.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"


def current_rss_mib() -> float:
    try:
        import psutil  # type: ignore
        return float(psutil.Process().memory_info().rss / (1024 * 1024))
    except Exception:
        return 0.0


def _clickhouse_query_id_prefix() -> str:
    return PROCESS_QUERY_ID_PREFIX


class ActiveQueryRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queries: dict[str, dict[str, Any]] = {}

    def register(self, query_id: str, *, label: str = "") -> None:
        with self._lock:
            self._queries[str(query_id)] = {
                "label": str(label),
                "started_at": time.time(),
                "thread_id": threading.get_ident(),
            }

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._queries.pop(str(query_id), None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            now = time.time()
            out: dict[str, dict[str, Any]] = {}
            for key, value in self._queries.items():
                row = dict(value)
                row["seconds"] = max(0.0, now - float(row.get("started_at") or now))
                out[key] = row
            return out

    def clear(self) -> None:
        with self._lock:
            self._queries.clear()


ACTIVE_QUERIES = ActiveQueryRegistry()
QUERY_CONTEXT = threading.local()

def _events_source_table(config: PackedContextConfig, start_date: str | dt.date, end_date: str | dt.date) -> str:
    base_table = str(config.events_table)
    if not events_table_uses_year_suffix(base_table):
        return f"{quote_ident(config.database)}.{quote_ident(base_table)}"
    start_year = dt.date.fromisoformat(str(start_date)[:10]).year
    end_year = dt.date.fromisoformat(str(end_date)[:10]).year
    tables = [events_table_for_year(base_table, year) for year in range(start_year, end_year + 1)]
    if len(tables) == 1:
        return f"{quote_ident(config.database)}.{quote_ident(tables[0])}"
    pattern = "^(" + "|".join(re.escape(table) for table in tables) + ")$"
    return f"merge({sql_string(config.database)}, {sql_string(pattern)})"

def _available_time_feature_sql(timestamp_expr: str, *, prefix: str = "available") -> str:
    ts = str(timestamp_expr)
    return f"""
    toFloat32(sin(2 * pi() * dateDiff('second', toStartOfDay(fromUnixTimestamp64Micro({ts}, 'UTC')), fromUnixTimestamp64Micro({ts}, 'UTC')) / 86400.0)) AS {quote_ident(prefix + "_utc_second_of_day_sin")},
    toFloat32(cos(2 * pi() * dateDiff('second', toStartOfDay(fromUnixTimestamp64Micro({ts}, 'UTC')), fromUnixTimestamp64Micro({ts}, 'UTC')) / 86400.0)) AS {quote_ident(prefix + "_utc_second_of_day_cos")},
    toFloat32(sin(2 * pi() * (toDayOfWeek(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 7.0)) AS {quote_ident(prefix + "_utc_day_of_week_sin")},
    toFloat32(cos(2 * pi() * (toDayOfWeek(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 7.0)) AS {quote_ident(prefix + "_utc_day_of_week_cos")},
    toFloat32(sin(2 * pi() * (toDayOfYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 366.0)) AS {quote_ident(prefix + "_utc_day_of_year_sin")},
    toFloat32(cos(2 * pi() * (toDayOfYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 366.0)) AS {quote_ident(prefix + "_utc_day_of_year_cos")},
    toFloat32(toYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 2000 + (toDayOfYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 366.0) AS {quote_ident(prefix + "_years_since_2000")}""".strip()

def _bar_start_time_feature_sql(timestamp_expr: str) -> str:
    ts = str(timestamp_expr)
    return f"""
    toFloat32(sin(2 * pi() * dateDiff('second', toStartOfDay({ts}), {ts}) / 86400.0)) AS bar_start_utc_second_of_day_sin,
    toFloat32(cos(2 * pi() * dateDiff('second', toStartOfDay({ts}), {ts}) / 86400.0)) AS bar_start_utc_second_of_day_cos,
    toFloat32(sin(2 * pi() * (toDayOfWeek({ts}) - 1) / 7.0)) AS bar_start_utc_day_of_week_sin,
    toFloat32(cos(2 * pi() * (toDayOfWeek({ts}) - 1) / 7.0)) AS bar_start_utc_day_of_week_cos,
    toFloat32(sin(2 * pi() * (toDayOfYear({ts}) - 1) / 366.0)) AS bar_start_utc_day_of_year_sin,
    toFloat32(cos(2 * pi() * (toDayOfYear({ts}) - 1) / 366.0)) AS bar_start_utc_day_of_year_cos,
    toFloat32(toYear({ts}) - 2000 + (toDayOfYear({ts}) - 1) / 366.0) AS bar_start_years_since_2000""".strip()

def query_ticker_news(args: argparse.Namespace, client_opts: Mapping[str, str], config: PackedContextConfig, window: Any, ticker: str) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.news_embedding_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in NEWS_EMBEDDING_COLUMNS)
    time_columns = _available_time_feature_sql("timestamp_us", prefix="available")
    prior_items = max(0, int(getattr(args, "ticker_news_prior_items", 0) or 0))
    query = f"""
WITH prior_items AS
(
    SELECT
        toString(source_id) AS source_id_key,
        toString(provider_article_id) AS provider_article_id_key,
        toString(text_hash) AS text_hash_key
    FROM {table}
    WHERE ticker = {sql_string(ticker)}
      AND timestamp_us < {int(window.first_session_start_us)}
      AND published_at_utc < {date_time64_from_us(window.first_session_start_us)}
    GROUP BY
        source_id_key,
        provider_article_id_key,
        text_hash_key
    ORDER BY
        max(timestamp_us) DESC,
        source_id_key,
        provider_article_id_key,
        text_hash_key
    LIMIT {int(prior_items)}
)
SELECT
    {columns},
    {time_columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us < {int(window.last_session_end_us)}
  AND published_at_utc < {date_time64_from_us(window.last_session_end_us)}
  AND (
      timestamp_us >= {int(window.first_session_start_us)}
      OR tuple(toString(source_id), toString(provider_article_id), toString(text_hash)) IN (SELECT source_id_key, provider_article_id_key, text_hash_key FROM prior_items)
  )
ORDER BY ticker, timestamp_us, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)

def query_market_news(args: argparse.Namespace, client_opts: Mapping[str, str], config: PackedContextConfig, window: Any) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.news_embedding_table)}"
    source_columns = ",\n        ".join(f"t.{quote_ident(column)}" for column in NEWS_EMBEDDING_COLUMNS if column != "ticker")
    time_columns = _available_time_feature_sql("t.timestamp_us", prefix="available")
    prior_items = max(0, int(getattr(args, "market_news_prior_items", 0) or 0))
    query = f"""
WITH prior_items AS
(
    SELECT
        toString(source_id) AS source_id_key,
        toString(provider_article_id) AS provider_article_id_key,
        toString(text_hash) AS text_hash_key
    FROM {table}
    WHERE timestamp_us < {int(window.first_session_start_us)}
      AND published_at_utc < {date_time64_from_us(window.first_session_start_us)}
    GROUP BY
        source_id_key,
        provider_article_id_key,
        text_hash_key
    ORDER BY
        max(timestamp_us) DESC,
        source_id_key,
        provider_article_id_key,
        text_hash_key
    LIMIT {int(prior_items)}
)
SELECT
    '__MARKET__' AS ticker,
    {source_columns},
    {time_columns}
FROM
(
    SELECT *
    FROM {table}
    WHERE timestamp_us < {int(window.last_session_end_us)}
      AND published_at_utc < {date_time64_from_us(window.last_session_end_us)}
      AND (
          timestamp_us >= {int(window.first_session_start_us)}
          OR tuple(toString(source_id), toString(provider_article_id), toString(text_hash)) IN (SELECT source_id_key, provider_article_id_key, text_hash_key FROM prior_items)
      )
    ORDER BY source_id, provider_article_id, text_hash, token_chunk_index, ticker
    LIMIT 1 BY source_id, provider_article_id, text_hash, token_chunk_index
) AS t
ORDER BY timestamp_us, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)

def query_sec_embeddings(args: argparse.Namespace, client_opts: Mapping[str, str], config: PackedContextConfig, window: Any, ticker: str) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_filing_text_embedding_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in SEC_EMBEDDING_COLUMNS)
    time_columns = _available_time_feature_sql("timestamp_us", prefix="available")
    prior_items = max(0, int(getattr(args, "sec_filing_prior_items", 0) or 0))
    query = f"""
WITH prior_items AS
(
    SELECT
        toString(accession_number) AS accession_number_key,
        toString(document_id) AS document_id_key,
        toString(text_rank) AS text_rank_key,
        toString(source_id) AS source_id_key
    FROM {table}
    WHERE ticker = {sql_string(ticker)}
      AND timestamp_us < {int(window.first_session_start_us)}
      AND accepted_at_utc < {date_time64_from_us(window.first_session_start_us)}
    GROUP BY
        accession_number_key,
        document_id_key,
        text_rank_key,
        source_id_key
    ORDER BY
        max(timestamp_us) DESC,
        accession_number_key,
        document_id_key,
        text_rank_key,
        source_id_key
    LIMIT {int(prior_items)}
)
SELECT
    {columns},
    {time_columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us < {int(window.last_session_end_us)}
  AND accepted_at_utc < {date_time64_from_us(window.last_session_end_us)}
  AND (
      timestamp_us >= {int(window.first_session_start_us)}
      OR tuple(toString(accession_number), toString(document_id), toString(text_rank), toString(source_id)) IN (SELECT accession_number_key, document_id_key, text_rank_key, source_id_key FROM prior_items)
  )
ORDER BY ticker, timestamp_us, accession_number, text_rank, document_id, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)

def query_xbrl(args: argparse.Namespace, client_opts: Mapping[str, str], config: PackedContextConfig, window: Any, ticker: str) -> Any:
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_xbrl_context_table)}"
    reference_table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.category_reference_table)}"
    prior_rows = max(0, int(getattr(args, "xbrl_prior_rows", 0) or 0))
    time_columns = _available_time_feature_sql("timestamp_us", prefix="available")
    category_reference_cte = f"""
refs AS
(
    SELECT
        field_name,
        category_value,
        argMax(category_id, updated_at) AS category_id
    FROM {reference_table}
    WHERE domain = 'xbrl'
    GROUP BY
        field_name,
        category_value
)
"""
    category_joins = """
    LEFT JOIN refs AS taxonomy_ref ON taxonomy_ref.field_name = 'taxonomy' AND taxonomy_ref.category_value = trim(BOTH ' ' FROM toString(x.taxonomy))
    LEFT JOIN refs AS tag_ref ON tag_ref.field_name = 'tag' AND tag_ref.category_value = trim(BOTH ' ' FROM toString(x.tag))
    LEFT JOIN refs AS unit_ref ON unit_ref.field_name = 'unit_code' AND unit_ref.category_value = trim(BOTH ' ' FROM toString(x.unit_code))
    LEFT JOIN refs AS form_ref ON form_ref.field_name = 'form_type' AND form_ref.category_value = trim(BOTH ' ' FROM toString(x.form_type))
    LEFT JOIN refs AS row_kind_ref ON row_kind_ref.field_name = 'xbrl_row_kind' AND row_kind_ref.category_value = trim(BOTH ' ' FROM toString(x.xbrl_row_kind))
    LEFT JOIN refs AS location_ref ON location_ref.field_name = 'location_code' AND location_ref.category_value = trim(BOTH ' ' FROM toString(x.location_code))
    LEFT JOIN refs AS fiscal_period_ref ON fiscal_period_ref.field_name = 'fiscal_period' AND fiscal_period_ref.category_value = trim(BOTH ' ' FROM toString(x.fiscal_period))
    LEFT JOIN refs AS calendar_period_ref ON calendar_period_ref.field_name = 'calendar_period_code' AND calendar_period_ref.category_value = trim(BOTH ' ' FROM toString(x.calendar_period_code))
"""
    category_columns = """
        toUInt32(ifNull(taxonomy_ref.category_id, 0)) AS taxonomy_id,
        toUInt32(ifNull(tag_ref.category_id, 0)) AS tag_id,
        toUInt32(ifNull(unit_ref.category_id, 0)) AS unit_id,
        toUInt32(ifNull(form_ref.category_id, 0)) AS form_id,
        toUInt32(ifNull(row_kind_ref.category_id, 0)) AS row_kind_id,
        toUInt32(ifNull(location_ref.category_id, 0)) AS location_id,
        toUInt32(ifNull(fiscal_period_ref.category_id, 0)) AS fiscal_period_id,
        toUInt32(ifNull(calendar_period_ref.category_id, 0)) AS calendar_period_id,
"""
    query = f"""
WITH {category_reference_cte},
prior_rows AS
(
    SELECT
        x.ticker,
        x.timestamp_us,
        x.source_id,
        x.cik,
        x.issuer_id,
        x.taxonomy,
        x.tag,
        x.unit_code,
        x.fiscal_year,
        x.fiscal_period,
        x.form_type,
        x.accepted_at_source,
        x.accession_number,
        x.period_end_date,
        x.value,
        x.calendar_period_code,
        x.location_code,
        x.xbrl_row_kind,
        x.bridge_id,
        x.mapping_confidence AS mapping_confidence_score,
        {category_columns}
        {time_columns}
    FROM {table} AS x
    {category_joins}
    WHERE x.ticker = {sql_string(ticker)}
      AND x.timestamp_us < {int(window.first_session_start_us)}
    ORDER BY x.ticker, x.timestamp_us DESC, x.xbrl_row_kind DESC, x.taxonomy DESC, x.tag DESC, x.unit_code DESC, x.period_end_date DESC
    LIMIT {int(prior_rows)}
)
SELECT
    x.ticker,
    x.timestamp_us,
    x.source_id,
    x.cik,
    x.issuer_id,
    x.taxonomy,
    x.tag,
    x.unit_code,
    x.fiscal_year,
    x.fiscal_period,
    x.form_type,
    x.accepted_at_source,
    x.accession_number,
    x.period_end_date,
    x.value,
    x.calendar_period_code,
    x.location_code,
    x.xbrl_row_kind,
    x.bridge_id,
    x.mapping_confidence AS mapping_confidence_score,
    {category_columns}
    {time_columns}
FROM {table} AS x
{category_joins}
WHERE x.ticker = {sql_string(ticker)}
  AND x.timestamp_us >= {int(window.first_session_start_us)}
  AND x.timestamp_us < {int(window.last_session_end_us)}
UNION ALL
SELECT *
FROM prior_rows
ORDER BY ticker, timestamp_us, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)

def query_daily_bars(args: argparse.Namespace, client_opts: Mapping[str, str], config: PackedContextConfig, window: Any, *, symbols: tuple[str, ...]) -> Any:
    if not symbols:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.macro_bars_table)}"
    symbol_sql = ", ".join(sql_string(str(symbol)) for symbol in symbols)
    start = window.first_date - dt.timedelta(days=max(0, int(config.macro_lookback_days)))
    end = window.next_month_date + dt.timedelta(days=max(0, int(config.label_lookahead_days)))
    time_columns = _bar_start_time_feature_sql("bar_start")
    query = f"""
SELECT
    sym,
    timeframe,
    toString(bar_family) AS bar_family,
    toUnixTimestamp64Milli(bar_start) AS bar_start_ms,
    open,
    close,
    high,
    low,
    size_sum,
    size_open,
    size_close,
    size_high,
    size_low,
    event_count,
    {time_columns}
FROM {table}
WHERE timeframe = '1d'
  AND sym IN ({symbol_sql})
  AND bar_start >= toDateTime64({sql_string(start.isoformat() + " 00:00:00")}, 3, 'UTC')
  AND bar_start < toDateTime64({sql_string(end.isoformat() + " 00:00:00")}, 3, 'UTC')
ORDER BY sym, timeframe, bar_start
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)

def query_corporate_actions(args: argparse.Namespace, client_opts: Mapping[str, str], config: PackedContextConfig, window: Any, ticker: str) -> Any:
    if bool(getattr(args, "skip_corporate_actions", False)):
        return _empty_frame()
    split_table = f"{quote_ident(config.q_live_database)}.{quote_ident(config.stock_split_table)}"
    dividend_table = f"{quote_ident(config.q_live_database)}.{quote_ident(config.cash_dividend_table)}"
    reference_table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.category_reference_table)}"
    start = window.first_date - dt.timedelta(days=max(0, int(config.corporate_action_lookback_days)))
    label_days = parse_day_horizons(args.corporate_action_label_days)
    end = window.next_month_date + dt.timedelta(days=max(label_days, default=0) + 1)
    available_columns = _available_time_feature_sql("available_timestamp_us", prefix="available")
    effective_columns = _available_time_feature_sql("effective_timestamp_us", prefix="effective")
    query = f"""
WITH
refs AS
(
    SELECT
        field_name,
        category_value,
        argMax(category_id, updated_at) AS category_id
    FROM {reference_table}
    WHERE domain = 'corporate_actions'
    GROUP BY
        field_name,
        category_value
),
actions AS
(
    SELECT
        upper(provider_ticker) AS ticker,
        toString(stock_split_id) AS corporate_action_id,
        'split' AS action_type,
        '' AS dividend_type,
        '' AS currency_code,
        '' AS frequency,
        toDate(execution_date) AS effective_date,
        toDate(execution_date) AS available_date,
        toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(toDate(execution_date)), ' 04:00:00'), 6, 'America/New_York'), 'UTC')) AS effective_timestamp_us,
        toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(toDate(execution_date)), ' 04:00:00'), 6, 'America/New_York'), 'UTC')) AS available_timestamp_us,
        toFloat32(ifNull(split_from, 0)) AS split_from,
        toFloat32(ifNull(split_to, 0)) AS split_to,
        toFloat32(0) AS cash_amount,
        toInt32(0) AS declaration_epoch_day,
        toInt32(toRelativeDayNum(toDate(execution_date)) - toRelativeDayNum(toDate('1970-01-01'))) AS effective_epoch_day,
        toInt32(0) AS pay_epoch_day,
        toInt32(0) AS record_epoch_day
    FROM {split_table}
    WHERE upper(provider_ticker) = {sql_string(ticker.upper())}
      AND execution_date >= toDate({sql_string(start.isoformat())})
      AND execution_date < toDate({sql_string(end.isoformat())})
    UNION ALL
    SELECT
        upper(provider_ticker) AS ticker,
        toString(cash_dividend_id) AS corporate_action_id,
        'dividend' AS action_type,
        toString(dividend_type) AS dividend_type,
        toString(currency_code) AS currency_code,
        toString(frequency) AS frequency,
        toDate(ex_dividend_date) AS effective_date,
        if(isNull(declaration_date), toDate(ex_dividend_date), addDays(toDate(declaration_date), 1)) AS available_date,
        toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(toDate(ex_dividend_date)), ' 04:00:00'), 6, 'America/New_York'), 'UTC')) AS effective_timestamp_us,
        toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(if(isNull(declaration_date), toDate(ex_dividend_date), addDays(toDate(declaration_date), 1))), ' 04:00:00'), 6, 'America/New_York'), 'UTC')) AS available_timestamp_us,
        toFloat32(0) AS split_from,
        toFloat32(0) AS split_to,
        toFloat32(ifNull(cash_amount, 0)) AS cash_amount,
        toInt32(if(isNull(declaration_date), 0, toRelativeDayNum(toDate(declaration_date)) - toRelativeDayNum(toDate('1970-01-01')))) AS declaration_epoch_day,
        toInt32(toRelativeDayNum(toDate(ex_dividend_date)) - toRelativeDayNum(toDate('1970-01-01'))) AS effective_epoch_day,
        toInt32(if(isNull(pay_date), 0, toRelativeDayNum(toDate(pay_date)) - toRelativeDayNum(toDate('1970-01-01')))) AS pay_epoch_day,
        toInt32(if(isNull(record_date), 0, toRelativeDayNum(toDate(record_date)) - toRelativeDayNum(toDate('1970-01-01')))) AS record_epoch_day
    FROM {dividend_table}
    WHERE upper(provider_ticker) = {sql_string(ticker.upper())}
      AND NOT isNull(ex_dividend_date)
      AND ex_dividend_date >= toDate({sql_string(start.isoformat())})
      AND ex_dividend_date < toDate({sql_string(end.isoformat())})
)
SELECT
    a.*,
    toUInt32(ifNull(action_ref.category_id, 0)) AS action_type_id,
    toUInt32(ifNull(dividend_type_ref.category_id, 0)) AS dividend_type_id,
    toUInt32(ifNull(currency_ref.category_id, 0)) AS currency_id,
    toUInt32(ifNull(frequency_ref.category_id, 0)) AS frequency_id,
    toFloat32(if(split_from > 0 AND split_to > 0, split_to / split_from, 0)) AS share_factor,
    toFloat32(if(split_from > 0 AND split_to > 0, split_from / split_to, 0)) AS price_factor,
    toFloat32(if(split_from > 0 AND split_to > 0, log(split_to / split_from), 0)) AS log_share_factor,
    toFloat32(if(split_from > 0 AND split_to > 0, log(split_from / split_to), 0)) AS log_price_factor,
    toFloat32(log1p(greatest(cash_amount, 0))) AS log1p_cash_amount,
    toUInt8(action_type = 'split') AS is_split,
    toUInt8(action_type = 'split' AND split_to > split_from AND split_from > 0) AS is_forward_split,
    toUInt8(action_type = 'split' AND split_to < split_from AND split_to > 0) AS is_reverse_split,
    toUInt8(action_type = 'dividend') AS is_dividend,
    toUInt8(action_type = 'dividend' AND lowerUTF8(dividend_type) IN ({", ".join(sql_string(value) for value in sorted(SPECIAL_DIVIDEND_TYPES))})) AS is_special_dividend,
    {available_columns},
    {effective_columns}
FROM actions AS a
LEFT JOIN refs AS action_ref ON action_ref.field_name = 'action_type' AND action_ref.category_value = a.action_type
LEFT JOIN refs AS dividend_type_ref ON dividend_type_ref.field_name = 'dividend_type' AND dividend_type_ref.category_value = a.dividend_type
LEFT JOIN refs AS currency_ref ON currency_ref.field_name = 'currency_code' AND currency_ref.category_value = a.currency_code
LEFT JOIN refs AS frequency_ref ON frequency_ref.field_name = 'frequency' AND frequency_ref.category_value = a.frequency
ORDER BY ticker, available_timestamp_us, effective_timestamp_us, action_type, corporate_action_id
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)

def query_polars(client_opts: Mapping[str, str], query: str) -> Any:
    try:
        import clickhouse_connect  # type: ignore
    except ModuleNotFoundError:
        return query_polars_http_arrow(client_opts, query)
    parsed = urlparse(str(client_opts["clickhouse_url"]))
    secure = parsed.scheme == "https"
    retries = max(0, int(client_opts.get("query_retries") or 0))
    backoff_seconds = max(0.0, float(client_opts.get("query_retry_backoff_seconds") or 0.0))
    attempt = 0
    while True:
        retry_sleep = 0.0
        query_id = f"{_clickhouse_query_id_prefix()}{threading.get_ident()}_{uuid.uuid4().hex}"
        ACTIVE_QUERIES.register(query_id, label=str(getattr(QUERY_CONTEXT, "label", "")))
        client = clickhouse_connect.get_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or (8443 if secure else 8123),
            username=str(client_opts.get("user") or "default"),
            password=str(client_opts.get("password") or ""),
            secure=secure,
        )
        try:
            table = _query_arrow_with_id(client, query=query, query_id=query_id)
            return _polars().from_arrow(table)
        except Exception as exc:
            if attempt >= retries or not _is_transient_clickhouse_read_error(exc):
                raise
            retry_sleep = backoff_seconds * float(2**attempt)
            attempt += 1
        finally:
            ACTIVE_QUERIES.unregister(query_id)
            try:
                client.close()
            except Exception:
                pass
        if retry_sleep > 0:
            time.sleep(retry_sleep)

def query_polars_http_arrow(client_opts: Mapping[str, str], query: str) -> Any:
    retries = max(0, int(client_opts.get("query_retries") or 0))
    backoff_seconds = max(0.0, float(client_opts.get("query_retry_backoff_seconds") or 0.0))
    attempt = 0
    while True:
        retry_sleep = 0.0
        query_id = f"{_clickhouse_query_id_prefix()}{threading.get_ident()}_{uuid.uuid4().hex}"
        ACTIVE_QUERIES.register(query_id, label=str(getattr(QUERY_CONTEXT, "label", "")))
        try:
            data = _execute_clickhouse_arrow_stream(client_opts=client_opts, query=query, query_id=query_id)
            try:
                import pyarrow as pa  # type: ignore
            except ModuleNotFoundError as exc:
                raise RuntimeError("Install pyarrow to use the ClickHouse HTTP ArrowStream fallback.") from exc
            with pa.ipc.open_stream(BytesIO(data)) as reader:
                table = reader.read_all()
            return _polars().from_arrow(table)
        except Exception as exc:
            if attempt >= retries or not _is_transient_clickhouse_read_error(exc):
                raise
            retry_sleep = backoff_seconds * float(2**attempt)
            attempt += 1
        finally:
            ACTIVE_QUERIES.unregister(query_id)
        if retry_sleep > 0:
            time.sleep(retry_sleep)

def _execute_clickhouse_arrow_stream(*, client_opts: Mapping[str, str], query: str, query_id: str) -> bytes:
    base_url = str(client_opts["clickhouse_url"]).rstrip("/")
    url = base_url + "/?" + url_parse.urlencode({"query_id": query_id})
    sql = query.strip().rstrip(";").rstrip()
    if " FORMAT " not in f" {sql[-64:].upper()} ":
        sql += "\nFORMAT ArrowStream"
    req = url_request.Request(url, data=sql.encode("utf-8"), method="POST")
    user = str(client_opts.get("user") or "default")
    password = str(client_opts.get("password") or "")
    if user:
        req.add_header("X-ClickHouse-User", user)
    if password:
        req.add_header("X-ClickHouse-Key", password)
    try:
        with url_request.urlopen(req, timeout=None) as response:
            return response.read()
    except url_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc

def _query_arrow_with_id(client: Any, *, query: str, query_id: str) -> Any:
    try:
        return client.query_arrow(query, settings={"query_id": query_id})
    except TypeError:
        try:
            return client.query_arrow(query, query_id=query_id)
        except TypeError:
            return client.query_arrow(f"/* query_id={query_id} */\n{query}")

def _is_transient_clickhouse_read_error(exc: BaseException) -> bool:
    text = repr(exc)
    if "QUERY_WAS_CANCELLED" in text or "DB::Exception" in text:
        return False
    transient_markers = (
        "IncompleteRead",
        "ProtocolError",
        "Connection broken",
        "RemoteDisconnected",
        "Connection reset",
        "Read timed out",
        "timed out",
    )
    return any(marker in text for marker in transient_markers)

def cancel_active_clickhouse_queries(*, client_opts: Mapping[str, str], stats: Any | None = None) -> int:
    active = ACTIVE_QUERIES.snapshot()
    if not active:
        return 0
    ids = sorted(active)
    if stats is not None:
        stats.log_event("clickhouse_cancel_start", active_query_ids=ids, active_queries=active)
    try:
        quoted = ", ".join(sql_string(query_id) for query_id in ids)
        _execute_clickhouse_cancel(
            client_opts=client_opts,
            sql=f"KILL QUERY WHERE query_id IN ({quoted}) SYNC",
            timeout_seconds=5.0,
        )
    except Exception as exc:
        if stats is not None:
            stats.log_event("clickhouse_cancel_error", error=repr(exc), active_query_ids=ids)
        return 0
    if stats is not None:
        stats.log_event("clickhouse_cancel_done", cancelled=len(ids), active_query_ids=ids)
    return len(ids)

def cancel_process_clickhouse_queries(*, client_opts: Mapping[str, str], stats: Any | None = None, reason: str = "") -> int:
    prefix_like = _clickhouse_query_id_prefix() + "%"
    try:
        text = _execute_clickhouse_cancel(
            client_opts=client_opts,
            sql=f"KILL QUERY WHERE query_id LIKE {sql_string(prefix_like)} SYNC",
            timeout_seconds=10.0,
        )
    except Exception as exc:
        if stats is not None:
            stats.log_event("clickhouse_process_cancel_error", reason=reason, prefix=prefix_like, error=repr(exc))
        return 0
    cancelled = sum(1 for line in text.splitlines() if line.strip().startswith("finished\t"))
    if stats is not None:
        stats.log_event("clickhouse_process_cancel_done", reason=reason, prefix=prefix_like, cancelled=cancelled)
        if cancelled:
            stats.message(f"{reason or 'shutdown'}: cancelled {cancelled} ClickHouse quer{'y' if cancelled == 1 else 'ies'} by process prefix")
    return cancelled

def _execute_clickhouse_cancel(*, client_opts: Mapping[str, str], sql: str, timeout_seconds: float) -> str:
    url = str(client_opts["clickhouse_url"]).rstrip("/") + "/"
    req = url_request.Request(url, data=sql.encode("utf-8"), method="POST")
    user = str(client_opts.get("user") or "default")
    password = str(client_opts.get("password") or "")
    if user:
        req.add_header("X-ClickHouse-User", user)
    if password:
        req.add_header("X-ClickHouse-Key", password)
    with url_request.urlopen(req, timeout=max(1.0, float(timeout_seconds))) as response:
        return response.read().decode("utf-8", errors="replace")

def _settings_sql(config: PackedContextConfig, *, extra: Mapping[str, Any] | None = None) -> str:
    settings: dict[str, Any] = {}
    if int(config.max_threads) > 0:
        settings["max_threads"] = int(config.max_threads)
    if str(config.max_memory_usage) != "0":
        settings["max_memory_usage"] = parse_size_bytes(str(config.max_memory_usage))
    settings.update(dict(extra or {}))
    if not settings:
        return ""
    parts = []
    for key, value in settings.items():
        if isinstance(value, str):
            parts.append(f"{key} = {sql_string(value)}")
        else:
            parts.append(f"{key} = {value}")
    return "SETTINGS " + ", ".join(parts)

def _empty_frame() -> Any:
    return _polars().DataFrame()

def _polars() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars in this environment to build ticker/month caches.") from exc
    return pl

def build_intraday_base_bars(events: Any) -> Any:
    pl = _polars()
    if events.height == 0:
        return _empty_frame()
    base = (
        events.filter((pl.col("session_second") >= SESSION_START_SECOND) & (pl.col("session_second") < SESSION_END_SECOND))
        .with_columns(
            [
                (pl.col("event_meta").cast(pl.UInt32) % 2).alias("_event_type"),
                pl.when(((pl.col("event_meta").cast(pl.UInt32) // 2) % 2) > 0).then(pl.lit(10000.0)).otherwise(pl.lit(100.0)).alias("_primary_scale"),
                pl.when(((pl.col("event_meta").cast(pl.UInt32) // 4) % 2) > 0).then(pl.lit(10000.0)).otherwise(pl.lit(100.0)).alias("_secondary_scale"),
            ]
        )
        .with_columns(
            [
                (pl.col("price_primary_int").cast(pl.Float64) / pl.col("_primary_scale")).cast(pl.Float32).alias("_price_primary"),
                (pl.col("price_secondary_int").cast(pl.Float64) / pl.col("_secondary_scale")).cast(pl.Float32).alias("_price_secondary"),
            ]
        )
    )
    trade = (
        base.filter(pl.col("_event_type") == 1)
        .select(
            [
                "ticker",
                "ticker_id",
                "local_date",
                "timestamp_us",
                "local_session_us",
                "ordinal",
                pl.lit("trade").alias("bar_family"),
                pl.col("_price_primary").alias("price"),
                pl.col("size_primary").cast(pl.Float32).alias("size"),
            ]
        )
        .filter(pl.col("price") > 0)
    )
    quote_bid = (
        base.filter(pl.col("_event_type") == 0)
        .select(
            [
                "ticker",
                "ticker_id",
                "local_date",
                "timestamp_us",
                "local_session_us",
                "ordinal",
                pl.lit("quote_bid").alias("bar_family"),
                pl.col("_price_secondary").alias("price"),
                pl.col("size_secondary").cast(pl.Float32).alias("size"),
            ]
        )
        .filter(pl.col("price") > 0)
    )
    quote_ask = (
        base.filter(pl.col("_event_type") == 0)
        .select(
            [
                "ticker",
                "ticker_id",
                "local_date",
                "timestamp_us",
                "local_session_us",
                "ordinal",
                pl.lit("quote_ask").alias("bar_family"),
                pl.col("_price_primary").alias("price"),
                pl.col("size_primary").cast(pl.Float32).alias("size"),
            ]
        )
        .filter(pl.col("price") > 0)
    )
    stacked = pl.concat([trade, quote_bid, quote_ask], how="vertical")
    if stacked.height == 0:
        return _empty_frame()
    frames = []
    for resolution_us in INTRADAY_LABEL_GRID_RESOLUTIONS_US:
        frame = (
            stacked.with_columns(
                [
                    pl.lit(int(resolution_us)).cast(pl.Int64).alias("label_resolution_us"),
                    (pl.col("local_session_us").cast(pl.Int64) // int(resolution_us)).cast(pl.Int64).alias("bucket_index"),
                ]
            )
            .filter((pl.col("bucket_index") >= int(SESSION_START_SECOND * 1_000_000 // resolution_us)) & (pl.col("bucket_index") < int(SESSION_END_SECOND * 1_000_000 // resolution_us)))
            .sort(["ticker", "local_date", "label_resolution_us", "bucket_index", "bar_family", "timestamp_us", "ordinal"])
            .group_by(["ticker", "ticker_id", "local_date", "label_resolution_us", "bucket_index", "bar_family"], maintain_order=True)
            .agg(
                [
                    pl.col("price").first().cast(pl.Float32).alias("open"),
                    pl.col("price").last().cast(pl.Float32).alias("close"),
                    pl.col("price").max().cast(pl.Float32).alias("high"),
                    pl.col("price").min().cast(pl.Float32).alias("low"),
                    pl.col("size").sum().cast(pl.Float32).alias("size_sum"),
                    pl.col("size").first().cast(pl.Float32).alias("size_open"),
                    pl.col("size").last().cast(pl.Float32).alias("size_close"),
                    pl.col("size").max().cast(pl.Float32).alias("size_high"),
                    pl.col("size").min().cast(pl.Float32).alias("size_low"),
                    pl.len().cast(pl.UInt32).alias("event_count"),
                    pl.col("timestamp_us").first().cast(pl.Int64).alias("first_event_timestamp_us"),
                    pl.col("timestamp_us").last().cast(pl.Int64).alias("last_event_timestamp_us"),
                ]
            )
            .with_columns(
                [
                    (pl.col("bucket_index") * int(resolution_us)).cast(pl.Int64).alias("bar_start_session_us"),
                    ((pl.col("bucket_index") + 1) * int(resolution_us)).cast(pl.Int64).alias("bar_end_session_us"),
                ]
            )
        )
        frames.append(frame)
    return pl.concat(frames, how="vertical").sort(["ticker", "local_date", "label_resolution_us", "bucket_index", "bar_family"])

def build_intraday_condition_events(events: Any) -> Any:
    pl = _polars()
    if events.height == 0:
        return _empty_frame()
    token_columns = [f"condition_token_{idx}" for idx in range(1, 6)]
    out = events.select(["ticker", "ticker_id", "ordinal", "timestamp_us", "local_date", "local_session_us", *token_columns])
    flag_exprs = []
    for name, groups in FUTURE_CONDITION_GROUPS:
        tokens = sorted({int(token) for _family, values in groups for token in values})
        checks = [pl.col(column).is_in(tokens) for column in token_columns]
        flag_exprs.append(pl.any_horizontal(checks).cast(pl.UInt8).alias(name))
    out = out.with_columns(flag_exprs)
    flags = [name for name, _groups in FUTURE_CONDITION_GROUPS]
    return out.filter(pl.any_horizontal([pl.col(flag) > 0 for flag in flags])).select(["ticker", "ticker_id", "ordinal", "timestamp_us", "local_date", "local_session_us", *flags])
