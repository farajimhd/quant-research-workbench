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
DEFAULT_WARMUP_SECONDS = 5
DEFAULT_BASELINE_ET = "04:00:00"
DEFAULT_SESSION_START_ET = "04:00:00"
DEFAULT_SESSION_END_ET = "20:00:00"
DEFAULT_PENNY_PRICE_THRESHOLD = 1.0
DEFAULT_SMALL_PRICE_THRESHOLD = 20.0
DEFAULT_MID_PRICE_THRESHOLD = 100.0
DEFAULT_RANK_TOP_K = 16


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
    warmup_seconds: int = DEFAULT_WARMUP_SECONDS
    baseline_et: str = DEFAULT_BASELINE_ET
    session_start_et: str = DEFAULT_SESSION_START_ET
    session_end_et: str = DEFAULT_SESSION_END_ET
    cleanup_previous_source_dates: bool = True
    penny_price_threshold: float = DEFAULT_PENNY_PRICE_THRESHOLD
    small_price_threshold: float = DEFAULT_SMALL_PRICE_THRESHOLD
    mid_price_threshold: float = DEFAULT_MID_PRICE_THRESHOLD
    rank_top_k: int = DEFAULT_RANK_TOP_K
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
        self._built_until_us: dict[str, int] = {}
        self._active_source_date: str | None = None
        self._requested_targets: dict[str, int] = {}
        self._background_thread: threading.Thread | None = None
        self._background_stop = threading.Event()
        self._query_counter = 0

    def ensure_table(self) -> None:
        with self._lock:
            if self._ensured:
                return
            self._execute("scanner_create_table", create_scanner_table_sql(self.config))
            for sql in upgrade_scanner_table_sqls(self.config):
                self._execute("scanner_upgrade_table", sql)
            self._ensured = True

    def cleanup_run(self) -> None:
        with self._lock:
            self._execute("scanner_cleanup_run", cleanup_run_sql(self.config))
            self._built.clear()
            self._built_until_us.clear()

    def cleanup_before_source_date(self, source_date: str) -> None:
        with self._lock:
            self._execute("scanner_cleanup_before_source_date", cleanup_before_source_date_sql(self.config, source_date))
            self._built = {key for key in self._built if key[0] >= str(source_date)}
            self._built_until_us = {day: value for day, value in self._built_until_us.items() if day >= str(source_date)}

    def ensure_window_for_origin(self, origin_timestamp_us: int) -> ScannerSidecarProfile:
        window = scanner_window_for_origin(origin_timestamp_us, self.config, target_seconds=int(self.config.window_seconds))
        return self.ensure_built_until(window.emit_end_us, source_date=window.source_date)

    def ensure_warmup_for_origin(self, origin_timestamp_us: int) -> ScannerSidecarProfile:
        window = scanner_window_for_origin(origin_timestamp_us, self.config, target_seconds=int(self.config.warmup_seconds))
        return self.ensure_built_until(window.emit_end_us, source_date=window.source_date)

    def request_build_through_origin(self, origin_timestamp_us: int, *, lookahead_seconds: int | None = None) -> None:
        window = scanner_window_for_origin(
            origin_timestamp_us,
            self.config,
            target_seconds=int(self.config.window_seconds if lookahead_seconds is None else lookahead_seconds),
        )
        with self._lock:
            current = self._requested_targets.get(window.source_date, 0)
            if int(window.emit_end_us) > current:
                self._requested_targets[window.source_date] = int(window.emit_end_us)
            if self._background_thread is None or not self._background_thread.is_alive():
                self._background_stop.clear()
                self._background_thread = threading.Thread(target=self._background_loop, name="scanner-sidecar-builder", daemon=True)
                self._background_thread.start()

    def ensure_windows_for_origin_range(self, first_origin_us: int, last_origin_us: int) -> list[ScannerSidecarProfile]:
        first = self.ensure_warmup_for_origin(first_origin_us)
        self.request_build_through_origin(last_origin_us)
        return [first]

    def ensure_built_until(self, target_end_us: int, *, source_date: str) -> ScannerSidecarProfile:
        self.ensure_table()
        source_date = str(source_date)
        with self._lock:
            self._maybe_cleanup_for_source_date(source_date)
            current_end = self._built_until_us.get(source_date)
        if current_end is None:
            current_end = self._load_existing_built_until(source_date)
        session_start_us, session_end_us = session_bounds_us(source_date, self.config)
        emit_start_us = max(int(session_start_us), int(current_end))
        emit_end_us = min(int(session_end_us), max(int(target_end_us), emit_start_us))
        if emit_end_us <= emit_start_us:
            return ScannerSidecarProfile(
                built=False,
                source_date=source_date,
                emit_start_us=int(emit_start_us),
                emit_end_us=int(emit_end_us),
            )
        window = ScannerWindow(
            source_date=source_date,
            emit_start_us=int(emit_start_us),
            emit_end_us=int(emit_end_us),
            baseline_start_us=int(session_start_us),
        )
        return self.ensure_window(window)

    def ensure_window(self, window: ScannerWindow) -> ScannerSidecarProfile:
        self.ensure_table()
        key = (window.source_date, int(window.emit_start_us), int(window.emit_end_us))
        with self._lock:
            self._maybe_cleanup_for_source_date(window.source_date)
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
            self._built_until_us[window.source_date] = max(
                int(self._built_until_us.get(window.source_date, 0)),
                int(window.emit_end_us),
            )
            return ScannerSidecarProfile(
                built=True,
                source_date=window.source_date,
                emit_start_us=int(window.emit_start_us),
                emit_end_us=int(window.emit_end_us),
                rows=0,
                seconds=float(seconds),
                query_id=query_id,
            )

    def fetch_completed_sql_for_origin_range(self, *, first_origin_us: int, last_origin_us: int, ticker: str = "") -> str:
        completed_before_us = completed_scanner_bar_end_us(last_origin_us, base_us=int(self.config.base_timeframe_us))
        lower_bound_us = max(0, completed_scanner_bar_end_us(first_origin_us, base_us=int(self.config.base_timeframe_us)) - int(self.config.fetch_lookback_seconds) * 1_000_000)
        return fetch_completed_scanner_sql(
            self.config,
            lower_bound_us=lower_bound_us,
            completed_before_us=completed_before_us,
            ticker=ticker,
        )

    def stop(self) -> None:
        self._background_stop.set()
        thread = self._background_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _background_loop(self) -> None:
        while not self._background_stop.is_set():
            task: tuple[str, int] | None = None
            with self._lock:
                if self._requested_targets:
                    source_date = min(self._requested_targets)
                    target = self._requested_targets.pop(source_date)
                    task = (source_date, int(target))
            if task is None:
                time.sleep(0.25)
                continue
            source_date, target = task
            try:
                self.ensure_built_until(target, source_date=source_date)
            except Exception:
                # The foreground path will surface scanner issues when it needs the rows.
                time.sleep(1.0)

    def _load_existing_built_until(self, source_date: str) -> int:
        self.ensure_table()
        sql = existing_built_until_sql(self.config, source_date)
        with self._lock:
            text = self.client.query_tsv(sql).strip()
            value = int(text or "0")
            session_start_us, _session_end_us = session_bounds_us(source_date, self.config)
            value = max(int(session_start_us), value)
            self._built_until_us[source_date] = value
            return value

    def _maybe_cleanup_for_source_date(self, source_date: str) -> None:
        if not bool(self.config.cleanup_previous_source_dates):
            return
        if self._active_source_date is None:
            self._active_source_date = str(source_date)
            return
        if str(source_date) > self._active_source_date:
            self._execute("scanner_cleanup_previous_source_dates", cleanup_before_source_date_sql(self.config, source_date))
            self._built = {key for key in self._built if key[0] >= str(source_date)}
            self._built_until_us = {day: value for day, value in self._built_until_us.items() if day >= str(source_date)}
            self._active_source_date = str(source_date)

    def _execute(self, label: str, sql: str) -> str:
        self._query_counter += 1
        query_id = f"{self.query_id_prefix}_{label}_{self._query_counter:06d}"
        self.client.execute(sql, query_id=query_id)
        return query_id


def scanner_window_for_origin(origin_timestamp_us: int, config: ScannerSidecarConfig, *, target_seconds: int | None = None) -> ScannerWindow:
    base_us = max(1, int(config.base_timeframe_us))
    target_us = max(base_us, int(config.warmup_seconds if target_seconds is None else target_seconds) * 1_000_000)
    completed_us = completed_scanner_bar_end_us(origin_timestamp_us, base_us=base_us)
    origin_dt = datetime.fromtimestamp(int(origin_timestamp_us) / 1_000_000, tz=ZoneInfo("UTC")).astimezone(ZoneInfo(SESSION_TZ))
    local_day = origin_dt.date()
    source_date = local_day.isoformat()
    session_start_us, session_end_us = session_bounds_us(source_date, config)
    emit_start_us = int(session_start_us)
    emit_end_us = min(int(session_end_us), max(int(completed_us) + target_us, int(session_start_us)))
    return ScannerWindow(
        source_date=source_date,
        emit_start_us=int(emit_start_us),
        emit_end_us=int(emit_end_us),
        baseline_start_us=int(session_start_us),
    )


def session_bounds_us(source_date: str, config: ScannerSidecarConfig) -> tuple[int, int]:
    local_day = datetime.fromisoformat(str(source_date)).date()
    session_start_dt = datetime.combine(local_day, parse_clock(config.session_start_et), tzinfo=ZoneInfo(SESSION_TZ)).astimezone(ZoneInfo("UTC"))
    session_end_dt = datetime.combine(local_day, parse_clock(config.session_end_et), tzinfo=ZoneInfo(SESSION_TZ)).astimezone(ZoneInfo("UTC"))
    return int(session_start_dt.timestamp() * 1_000_000), int(session_end_dt.timestamp() * 1_000_000)


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
    top_volume_large_rank Int32,
    top_volume_large_score Float32,
    top_volume_large_percentile Float32,
    top_volume_mid_rank Int32,
    top_volume_mid_score Float32,
    top_volume_mid_percentile Float32,
    top_volume_small_rank Int32,
    top_volume_small_score Float32,
    top_volume_small_percentile Float32,
    top_volume_penny_rank Int32,
    top_volume_penny_score Float32,
    top_volume_penny_percentile Float32,
    created_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = MergeTree
PARTITION BY (run_id, toYYYYMM(source_date))
ORDER BY (run_id, bar_end_timestamp_us, ticker)
"""


def upgrade_scanner_table_sqls(config: ScannerSidecarConfig) -> list[str]:
    table = scanner_table_name(config)
    return [
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_large_rank Int32 AFTER top_volume_percentile",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_large_score Float32 AFTER top_volume_large_rank",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_large_percentile Float32 AFTER top_volume_large_score",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_mid_rank Int32 AFTER top_volume_large_percentile",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_mid_score Float32 AFTER top_volume_mid_rank",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_mid_percentile Float32 AFTER top_volume_mid_score",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_small_rank Int32 AFTER top_volume_mid_percentile",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_small_score Float32 AFTER top_volume_small_rank",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS top_volume_small_percentile Float32 AFTER top_volume_small_score",
    ]


def cleanup_run_sql(config: ScannerSidecarConfig) -> str:
    return f"ALTER TABLE {scanner_table_name(config)} DELETE WHERE run_id = {sql_string(config.run_id)}"


def cleanup_before_source_date_sql(config: ScannerSidecarConfig, source_date: str) -> str:
    return f"ALTER TABLE {scanner_table_name(config)} DELETE WHERE run_id = {sql_string(config.run_id)} AND source_date < toDate({sql_string(source_date)})"


def existing_built_until_sql(config: ScannerSidecarConfig, source_date: str) -> str:
    return f"""
SELECT toInt64(ifNull(max(bar_end_timestamp_us), 0))
FROM {scanner_table_name(config)}
WHERE run_id = {sql_string(config.run_id)}
  AND source_date = toDate({sql_string(source_date)})
"""


def insert_scanner_window_sql(config: ScannerSidecarConfig, window: ScannerWindow) -> str:
    table = events_table_name(config.database, config.events_table_base, window.source_date)
    base_us = int(config.base_timeframe_us)
    query_start_us = int(window.emit_start_us)
    penny = float(config.penny_price_threshold)
    small = float(config.small_price_threshold)
    mid = float(config.mid_price_threshold)
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
      AND sip_timestamp_us >= toInt64({query_start_us})
      AND sip_timestamp_us < toInt64({int(window.emit_end_us)})
    WHERE ticker != ''
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
day_open_candidates AS
(
    SELECT ticker, bucket_index, trade_open
    FROM {scanner_table_name(config)}
    WHERE run_id = {sql_string(config.run_id)}
      AND source_date = toDate({sql_string(window.source_date)})
      AND trade_available = 1
      AND bar_start_timestamp_us < toInt64({int(window.emit_end_us)})
    UNION ALL
    SELECT ticker, bucket_index, trade_open
    FROM wide
    WHERE trade_available = 1
),
day_open AS
(
    SELECT
        ticker,
        argMin(trade_open, bucket_index) AS day_trade_open
    FROM day_open_candidates
    GROUP BY ticker
),
trade_scanner AS
(
    SELECT
        w.source_date,
        w.ticker,
        w.bucket_index,
        w.trade_close,
        w.trade_size_sum,
        toFloat32(if(d.day_trade_open > 0,
            w.trade_close / d.day_trade_open - 1.0,
            0.0)) AS change_score
    FROM wide AS w
    INNER JOIN day_open AS d ON d.ticker = w.ticker
    WHERE w.trade_available = 1
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
volume_all AS
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
),
volume_large AS
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
    WHERE trade_close >= {mid}
),
volume_mid AS
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
    WHERE trade_close >= {small} AND trade_close < {mid}
),
volume_small AS
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
    WHERE trade_close >= {penny} AND trade_close < {small}
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
    WHERE trade_close > 0 AND trade_close < {penny}
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
    if(g.ticker = '', -1, g.rank_value) AS top_gainers_rank,
    if(g.ticker = '', 0.0, g.score_value) AS top_gainers_score,
    if(g.ticker = '', 0.0, g.percentile_value) AS top_gainers_percentile,
    if(v.ticker = '', -1, v.rank_value) AS top_volume_rank,
    if(v.ticker = '', 0.0, v.score_value) AS top_volume_score,
    if(v.ticker = '', 0.0, v.percentile_value) AS top_volume_percentile,
    if(vl.ticker = '', -1, vl.rank_value) AS top_volume_large_rank,
    if(vl.ticker = '', 0.0, vl.score_value) AS top_volume_large_score,
    if(vl.ticker = '', 0.0, vl.percentile_value) AS top_volume_large_percentile,
    if(vm.ticker = '', -1, vm.rank_value) AS top_volume_mid_rank,
    if(vm.ticker = '', 0.0, vm.score_value) AS top_volume_mid_score,
    if(vm.ticker = '', 0.0, vm.percentile_value) AS top_volume_mid_percentile,
    if(vs.ticker = '', -1, vs.rank_value) AS top_volume_small_rank,
    if(vs.ticker = '', 0.0, vs.score_value) AS top_volume_small_score,
    if(vs.ticker = '', 0.0, vs.percentile_value) AS top_volume_small_percentile,
    if(p.ticker = '', -1, p.rank_value) AS top_volume_penny_rank,
    if(p.ticker = '', 0.0, p.score_value) AS top_volume_penny_score,
    if(p.ticker = '', 0.0, p.percentile_value) AS top_volume_penny_percentile,
    now64(6) AS created_at_utc
FROM wide AS w
LEFT JOIN gainers AS g ON g.source_date = w.source_date AND g.ticker = w.ticker AND g.bucket_index = w.bucket_index
LEFT JOIN volume_all AS v ON v.source_date = w.source_date AND v.ticker = w.ticker AND v.bucket_index = w.bucket_index
LEFT JOIN volume_large AS vl ON vl.source_date = w.source_date AND vl.ticker = w.ticker AND vl.bucket_index = w.bucket_index
LEFT JOIN volume_mid AS vm ON vm.source_date = w.source_date AND vm.ticker = w.ticker AND vm.bucket_index = w.bucket_index
LEFT JOIN volume_small AS vs ON vs.source_date = w.source_date AND vs.ticker = w.ticker AND vs.bucket_index = w.bucket_index
LEFT JOIN volume_penny AS p ON p.source_date = w.source_date AND p.ticker = w.ticker AND p.bucket_index = w.bucket_index
WHERE w.bar_start_timestamp_us >= toInt64({int(window.emit_start_us)})
  AND w.bar_start_timestamp_us < toInt64({int(window.emit_end_us)})
{settings_sql(config)}
"""


def fetch_completed_scanner_sql(config: ScannerSidecarConfig, *, lower_bound_us: int, completed_before_us: int, ticker: str = "") -> str:
    top_k = max(1, int(config.rank_top_k))
    ticker_filter = f" OR ticker = {sql_string(str(ticker).upper())}" if str(ticker).strip() else ""
    return f"""
SELECT *
FROM {scanner_table_name(config)}
WHERE run_id = {sql_string(config.run_id)}
  AND bar_end_timestamp_us > toInt64({int(lower_bound_us)})
  AND bar_end_timestamp_us <= toInt64({int(completed_before_us)})
  AND (
      (top_gainers_rank >= 0 AND top_gainers_rank < {top_k})
      OR (top_volume_rank >= 0 AND top_volume_rank < {top_k})
      OR (top_volume_large_rank >= 0 AND top_volume_large_rank < {top_k})
      OR (top_volume_mid_rank >= 0 AND top_volume_mid_rank < {top_k})
      OR (top_volume_small_rank >= 0 AND top_volume_small_rank < {top_k})
      OR (top_volume_penny_rank >= 0 AND top_volume_penny_rank < {top_k})
      {ticker_filter}
  )
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
