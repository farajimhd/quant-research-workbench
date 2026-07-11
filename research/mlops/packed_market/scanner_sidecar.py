from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import ClickHouseHttpClient, parse_size_bytes, quote_ident, sql_string

SESSION_TZ = "America/New_York"
DEFAULT_SCANNER_TABLE = "packed_scanner_sidecar_bars"
DEFAULT_BASE_TIMEFRAME_US = 1_000_000
DEFAULT_WINDOW_SECONDS = 900
DEFAULT_FETCH_LOOKBACK_SECONDS = 300
DEFAULT_BASELINE_ET = "09:30:00"


@dataclass(frozen=True, slots=True)
class ScannerWindow:
    source_date: str
    emit_start_us: int
    emit_end_us: int
    baseline_start_us: int


@dataclass(frozen=True, slots=True)
class ScannerSidecarConfig:
    run_id: str
    database: str = "market_sip_compact"
    events_table_base: str = "events"
    scanner_table: str = DEFAULT_SCANNER_TABLE
    base_timeframe_us: int = DEFAULT_BASE_TIMEFRAME_US
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    fetch_lookback_seconds: int = DEFAULT_FETCH_LOOKBACK_SECONDS
    baseline_et: str = DEFAULT_BASELINE_ET
    max_threads: int = 16
    max_memory_usage: str = "64G"
    max_bytes_before_external_group_by: str = "32G"
    max_bytes_before_external_sort: str = "16G"


@dataclass(slots=True)
class ScannerSidecarProfile:
    built: bool
    source_date: str
    emit_start_us: int
    emit_end_us: int
    rows: int = 0
    seconds: float = 0.0
    query_id: str = ""


class ScannerSidecarManager:
    def __init__(self, *, config: ScannerSidecarConfig, client: ClickHouseHttpClient, query_id_prefix: str) -> None:
        self.config = config
        self.client = client
        self.query_id_prefix = query_id_prefix.rstrip("_")
        self._lock = threading.RLock()
        self._ensured = False
        self._built: set[tuple[str, int, int]] = set()
        self._query_counter = 0

    def ensure_table(self) -> None:
        with self._lock:
            if self._ensured:
                return
            self._execute("scanner_create_table", create_scanner_table_sql(self.config))
            self._ensured = True

    def cleanup_run(self) -> None:
        with self._lock:
            self._execute("scanner_cleanup_run", cleanup_run_sql(self.config))
            self._built.clear()

    def ensure_window_for_origin(self, origin_timestamp_us: int) -> ScannerSidecarProfile:
        window = scanner_window_for_origin(origin_timestamp_us, self.config)
        return self.ensure_window(window)

    def ensure_windows_for_origin_range(self, first_origin_us: int, last_origin_us: int) -> list[ScannerSidecarProfile]:
        profiles: list[ScannerSidecarProfile] = []
        cursor = int(first_origin_us)
        last = int(last_origin_us)
        while cursor <= last:
            profile = self.ensure_window_for_origin(cursor)
            profiles.append(profile)
            next_cursor = int(profile.emit_end_us)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return profiles

    def ensure_window(self, window: ScannerWindow) -> ScannerSidecarProfile:
        self.ensure_table()
        key = (window.source_date, int(window.emit_start_us), int(window.emit_end_us))
        with self._lock:
            if key in self._built:
                return ScannerSidecarProfile(
                    built=False,
                    source_date=window.source_date,
                    emit_start_us=int(window.emit_start_us),
                    emit_end_us=int(window.emit_end_us),
                )
            started = time.perf_counter()
            query_id = self._execute("scanner_insert_window", insert_scanner_window_sql(self.config, window))
            seconds = time.perf_counter() - started
            self._built.add(key)
            return ScannerSidecarProfile(
                built=True,
                source_date=window.source_date,
                emit_start_us=int(window.emit_start_us),
                emit_end_us=int(window.emit_end_us),
                rows=0,
                seconds=float(seconds),
                query_id=query_id,
            )

    def fetch_completed_sql_for_origin_range(self, *, first_origin_us: int, last_origin_us: int) -> str:
        completed_before_us = completed_scanner_bar_end_us(last_origin_us, base_us=int(self.config.base_timeframe_us))
        lower_bound_us = max(0, completed_scanner_bar_end_us(first_origin_us, base_us=int(self.config.base_timeframe_us)) - int(self.config.fetch_lookback_seconds) * 1_000_000)
        return fetch_completed_scanner_sql(
            self.config,
            lower_bound_us=lower_bound_us,
            completed_before_us=completed_before_us,
        )

    def _execute(self, label: str, sql: str) -> str:
        self._query_counter += 1
        query_id = f"{self.query_id_prefix}_{label}_{self._query_counter:06d}"
        self.client.execute(sql, query_id=query_id)
        return query_id


def scanner_window_for_origin(origin_timestamp_us: int, config: ScannerSidecarConfig) -> ScannerWindow:
    base_us = max(1, int(config.base_timeframe_us))
    window_us = max(base_us, int(config.window_seconds) * 1_000_000)
    emit_start_us = (int(origin_timestamp_us) // window_us) * window_us
    emit_end_us = emit_start_us + window_us
    origin_dt = datetime.fromtimestamp(int(origin_timestamp_us) / 1_000_000, tz=ZoneInfo("UTC")).astimezone(ZoneInfo(SESSION_TZ))
    local_day = origin_dt.date()
    baseline_clock = parse_clock(config.baseline_et)
    baseline_dt = datetime.combine(local_day, baseline_clock, tzinfo=ZoneInfo(SESSION_TZ)).astimezone(ZoneInfo("UTC"))
    baseline_start_us = int(baseline_dt.timestamp() * 1_000_000)
    return ScannerWindow(
        source_date=local_day.isoformat(),
        emit_start_us=int(emit_start_us),
        emit_end_us=int(emit_end_us),
        baseline_start_us=min(int(baseline_start_us), int(emit_start_us)),
    )


def completed_scanner_bar_end_us(origin_timestamp_us: int, *, base_us: int = DEFAULT_BASE_TIMEFRAME_US) -> int:
    return (int(origin_timestamp_us) // int(base_us)) * int(base_us)


def create_scanner_table_sql(config: ScannerSidecarConfig) -> str:
    table = scanner_table_name(config)
    return f"""
CREATE TABLE IF NOT EXISTS {table}
(
    run_id String,
    source_date Date,
    ticker LowCardinality(String),
    ticker_id UInt64,
    bucket_index Int64,
    bar_start_timestamp_us Int64,
    bar_end_timestamp_us Int64,
    bar_start_local_us Int64,
    bar_end_local_us Int64,
    trade_available UInt8,
    trade_open Float32,
    trade_close Float32,
    trade_high Float32,
    trade_low Float32,
    trade_size_sum Float64,
    trade_event_count UInt32,
    quote_bid_available UInt8,
    quote_bid_open Float32,
    quote_bid_close Float32,
    quote_bid_high Float32,
    quote_bid_low Float32,
    quote_bid_size_sum Float64,
    quote_bid_event_count UInt32,
    quote_ask_available UInt8,
    quote_ask_open Float32,
    quote_ask_close Float32,
    quote_ask_high Float32,
    quote_ask_low Float32,
    quote_ask_size_sum Float64,
    quote_ask_event_count UInt32,
    top_gainers_rank Int32,
    top_gainers_score Float32,
    top_gainers_percentile Float32,
    top_volume_rank Int32,
    top_volume_score Float32,
    top_volume_percentile Float32,
    top_volume_penny_rank Int32,
    top_volume_penny_score Float32,
    top_volume_penny_percentile Float32,
    created_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = MergeTree
PARTITION BY (run_id, toYYYYMM(source_date))
ORDER BY (run_id, bar_end_timestamp_us, ticker)
"""


def cleanup_run_sql(config: ScannerSidecarConfig) -> str:
    return f"ALTER TABLE {scanner_table_name(config)} DELETE WHERE run_id = {sql_string(config.run_id)}"


def insert_scanner_window_sql(config: ScannerSidecarConfig, window: ScannerWindow) -> str:
    table = events_table_name(config.database, config.events_table_base, window.source_date)
    base_us = int(config.base_timeframe_us)
    query_start_us = min(int(window.baseline_start_us), int(window.emit_start_us))
    return f"""
INSERT INTO {scanner_table_name(config)}
WITH
raw AS
(
    SELECT
        ticker,
        cityHash64(ticker) AS ticker_id,
        sip_timestamp_us,
        ordinal,
        bitAnd(event_meta, 1) AS event_type,
        toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TZ)}) AS ts_local,
        toDate(ts_local) AS source_date,
        toUInt64(dateDiff('microsecond', toStartOfDay(ts_local), ts_local)) AS local_session_us,
        toInt64(sip_timestamp_us) - toInt64(local_session_us) AS source_midnight_timestamp_us,
        toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) > 0, 10000.0, 100.0) AS primary_price,
        toFloat64(price_secondary_int) / if(bitAnd(event_meta, 4) > 0, 10000.0, 100.0) AS secondary_price,
        toFloat64(size_primary) AS size_primary,
        toFloat64(size_secondary) AS size_secondary
    FROM {table}
    PREWHERE event_date = toDate({sql_string(window.source_date)})
    WHERE sip_timestamp_us >= toInt64({query_start_us})
      AND sip_timestamp_us < toInt64({int(window.emit_end_us)})
      AND ticker != ''
),
expanded AS
(
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, source_midnight_timestamp_us,
        'trade' AS bar_family, primary_price AS price, size_primary AS size
    FROM raw
    WHERE event_type = 1 AND primary_price > 0
    UNION ALL
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, source_midnight_timestamp_us,
        'quote_bid' AS bar_family, secondary_price AS price, size_secondary AS size
    FROM raw
    WHERE event_type = 0 AND secondary_price > 0
    UNION ALL
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, source_midnight_timestamp_us,
        'quote_ask' AS bar_family, primary_price AS price, size_primary AS size
    FROM raw
    WHERE event_type = 0 AND primary_price > 0
),
bars AS
(
    SELECT
        source_date,
        ticker,
        ticker_id,
        bar_family,
        toInt64(intDiv(toInt64(local_session_us), toInt64({base_us}))) AS bucket_index,
        toInt64(intDiv(toInt64(local_session_us), toInt64({base_us})) * toInt64({base_us})) AS bar_start_local_us,
        toInt64((intDiv(toInt64(local_session_us), toInt64({base_us})) + 1) * toInt64({base_us})) AS bar_end_local_us,
        toInt64(min(source_midnight_timestamp_us)) + bar_start_local_us AS bar_start_timestamp_us,
        toInt64(min(source_midnight_timestamp_us)) + bar_end_local_us AS bar_end_timestamp_us,
        toFloat32(argMin(price, tuple(sip_timestamp_us, ordinal))) AS open,
        toFloat32(argMax(price, tuple(sip_timestamp_us, ordinal))) AS close,
        toFloat32(max(price)) AS high,
        toFloat32(min(price)) AS low,
        toFloat64(sum(size)) AS size_sum,
        toUInt32(count()) AS event_count
    FROM expanded
    GROUP BY source_date, ticker, ticker_id, bar_family, bucket_index, bar_start_local_us, bar_end_local_us
),
wide AS
(
    SELECT
        source_date,
        ticker,
        ticker_id,
        bucket_index,
        any(bar_start_timestamp_us) AS bar_start_timestamp_us,
        any(bar_end_timestamp_us) AS bar_end_timestamp_us,
        any(bar_start_local_us) AS bar_start_local_us,
        any(bar_end_local_us) AS bar_end_local_us,
        toUInt8(countIf(bar_family = 'trade') > 0) AS trade_available,
        toFloat32(maxIf(open, bar_family = 'trade')) AS trade_open,
        toFloat32(maxIf(close, bar_family = 'trade')) AS trade_close,
        toFloat32(maxIf(high, bar_family = 'trade')) AS trade_high,
        toFloat32(maxIf(low, bar_family = 'trade')) AS trade_low,
        toFloat64(maxIf(size_sum, bar_family = 'trade')) AS trade_size_sum,
        toUInt32(maxIf(event_count, bar_family = 'trade')) AS trade_event_count,
        toUInt8(countIf(bar_family = 'quote_bid') > 0) AS quote_bid_available,
        toFloat32(maxIf(open, bar_family = 'quote_bid')) AS quote_bid_open,
        toFloat32(maxIf(close, bar_family = 'quote_bid')) AS quote_bid_close,
        toFloat32(maxIf(high, bar_family = 'quote_bid')) AS quote_bid_high,
        toFloat32(maxIf(low, bar_family = 'quote_bid')) AS quote_bid_low,
        toFloat64(maxIf(size_sum, bar_family = 'quote_bid')) AS quote_bid_size_sum,
        toUInt32(maxIf(event_count, bar_family = 'quote_bid')) AS quote_bid_event_count,
        toUInt8(countIf(bar_family = 'quote_ask') > 0) AS quote_ask_available,
        toFloat32(maxIf(open, bar_family = 'quote_ask')) AS quote_ask_open,
        toFloat32(maxIf(close, bar_family = 'quote_ask')) AS quote_ask_close,
        toFloat32(maxIf(high, bar_family = 'quote_ask')) AS quote_ask_high,
        toFloat32(maxIf(low, bar_family = 'quote_ask')) AS quote_ask_low,
        toFloat64(maxIf(size_sum, bar_family = 'quote_ask')) AS quote_ask_size_sum,
        toUInt32(maxIf(event_count, bar_family = 'quote_ask')) AS quote_ask_event_count
    FROM bars
    GROUP BY source_date, ticker, ticker_id, bucket_index
),
trade_scanner AS
(
    SELECT
        source_date,
        ticker,
        bucket_index,
        trade_close,
        trade_size_sum,
        toFloat32(if(first_value(trade_open) OVER (PARTITION BY source_date, ticker ORDER BY bucket_index) > 0,
            trade_close / first_value(trade_open) OVER (PARTITION BY source_date, ticker ORDER BY bucket_index) - 1.0,
            0.0)) AS change_score
    FROM wide
    WHERE trade_available = 1
),
gainers AS
(
    SELECT
        source_date,
        ticker,
        bucket_index,
        toInt32(row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY change_score DESC, ticker ASC) - 1) AS rank_value,
        toFloat32(change_score) AS score_value,
        toFloat32(if(count() OVER (PARTITION BY source_date, bucket_index) > 1,
            1.0 - ((row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY change_score DESC, ticker ASC) - 1) / (count() OVER (PARTITION BY source_date, bucket_index) - 1)),
            1.0)) AS percentile_value
    FROM trade_scanner
),
volume_nonpenny AS
(
    SELECT
        source_date,
        ticker,
        bucket_index,
        toInt32(row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY trade_size_sum DESC, ticker ASC) - 1) AS rank_value,
        toFloat32(trade_size_sum) AS score_value,
        toFloat32(if(count() OVER (PARTITION BY source_date, bucket_index) > 1,
            1.0 - ((row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY trade_size_sum DESC, ticker ASC) - 1) / (count() OVER (PARTITION BY source_date, bucket_index) - 1)),
            1.0)) AS percentile_value
    FROM trade_scanner
    WHERE trade_close >= 1.0
),
volume_penny AS
(
    SELECT
        source_date,
        ticker,
        bucket_index,
        toInt32(row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY trade_size_sum DESC, ticker ASC) - 1) AS rank_value,
        toFloat32(trade_size_sum) AS score_value,
        toFloat32(if(count() OVER (PARTITION BY source_date, bucket_index) > 1,
            1.0 - ((row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY trade_size_sum DESC, ticker ASC) - 1) / (count() OVER (PARTITION BY source_date, bucket_index) - 1)),
            1.0)) AS percentile_value
    FROM trade_scanner
    WHERE trade_close < 1.0
)
SELECT
    {sql_string(config.run_id)} AS run_id,
    w.source_date,
    w.ticker,
    w.ticker_id,
    w.bucket_index,
    w.bar_start_timestamp_us,
    w.bar_end_timestamp_us,
    w.bar_start_local_us,
    w.bar_end_local_us,
    w.trade_available,
    w.trade_open,
    w.trade_close,
    w.trade_high,
    w.trade_low,
    w.trade_size_sum,
    w.trade_event_count,
    w.quote_bid_available,
    w.quote_bid_open,
    w.quote_bid_close,
    w.quote_bid_high,
    w.quote_bid_low,
    w.quote_bid_size_sum,
    w.quote_bid_event_count,
    w.quote_ask_available,
    w.quote_ask_open,
    w.quote_ask_close,
    w.quote_ask_high,
    w.quote_ask_low,
    w.quote_ask_size_sum,
    w.quote_ask_event_count,
    ifNull(g.rank_value, -1) AS top_gainers_rank,
    ifNull(g.score_value, 0.0) AS top_gainers_score,
    ifNull(g.percentile_value, 0.0) AS top_gainers_percentile,
    ifNull(v.rank_value, -1) AS top_volume_rank,
    ifNull(v.score_value, 0.0) AS top_volume_score,
    ifNull(v.percentile_value, 0.0) AS top_volume_percentile,
    ifNull(p.rank_value, -1) AS top_volume_penny_rank,
    ifNull(p.score_value, 0.0) AS top_volume_penny_score,
    ifNull(p.percentile_value, 0.0) AS top_volume_penny_percentile,
    now64(6) AS created_at_utc
FROM wide AS w
LEFT JOIN gainers AS g ON g.source_date = w.source_date AND g.ticker = w.ticker AND g.bucket_index = w.bucket_index
LEFT JOIN volume_nonpenny AS v ON v.source_date = w.source_date AND v.ticker = w.ticker AND v.bucket_index = w.bucket_index
LEFT JOIN volume_penny AS p ON p.source_date = w.source_date AND p.ticker = w.ticker AND p.bucket_index = w.bucket_index
WHERE w.bar_start_timestamp_us >= toInt64({int(window.emit_start_us)})
  AND w.bar_start_timestamp_us < toInt64({int(window.emit_end_us)})
{settings_sql(config)}
"""


def fetch_completed_scanner_sql(config: ScannerSidecarConfig, *, lower_bound_us: int, completed_before_us: int) -> str:
    return f"""
SELECT *
FROM {scanner_table_name(config)}
WHERE run_id = {sql_string(config.run_id)}
  AND bar_end_timestamp_us > toInt64({int(lower_bound_us)})
  AND bar_end_timestamp_us <= toInt64({int(completed_before_us)})
ORDER BY bar_end_timestamp_us ASC, ticker ASC
{settings_sql(config)}
"""


def scanner_table_name(config: ScannerSidecarConfig) -> str:
    return f"{quote_ident(config.database)}.{quote_ident(config.scanner_table)}"


def events_table_name(database: str, events_table_base: str, source_date: str) -> str:
    year = int(str(source_date)[:4])
    return f"{quote_ident(database)}.{quote_ident(f'{events_table_base}_{year}')}"


def settings_sql(config: ScannerSidecarConfig) -> str:
    return (
        "SETTINGS "
        f"max_threads = {int(config.max_threads)}, "
        f"max_memory_usage = {parse_size_bytes(str(config.max_memory_usage))}, "
        f"max_bytes_before_external_group_by = {parse_size_bytes(str(config.max_bytes_before_external_group_by))}, "
        f"max_bytes_before_external_sort = {parse_size_bytes(str(config.max_bytes_before_external_sort))}"
    )


def parse_clock(value: str) -> dt_time:
    parts = [int(item) for item in str(value).strip().split(":")]
    if len(parts) == 2:
        return dt_time(parts[0], parts[1])
    if len(parts) == 3:
        return dt_time(parts[0], parts[1], parts[2])
    raise ValueError(f"Invalid time value {value!r}; expected HH:MM or HH:MM:SS.")
