from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402


BAR_SCHEMA_VERSION = 2
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE = "events"
DEFAULT_BARS_TABLE = "live_market_bars"
DEFAULT_TIMEFRAMES = ("1s", "5s", "1m", "5m", "1d", "1w", "1mo")
DEFAULT_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT_WIN / "trade_bars"


@dataclass(frozen=True, slots=True)
class TimeframeSpec:
    name: str
    seconds: int
    bucket_sql: str
    end_sql: str


TIMEFRAME_SPECS: dict[str, TimeframeSpec] = {
    "1s": TimeframeSpec("1s", 1, "toStartOfInterval(event_dt, INTERVAL 1 SECOND, 'UTC')", "bar_start + INTERVAL 1 SECOND"),
    "5s": TimeframeSpec("5s", 5, "toStartOfInterval(event_dt, INTERVAL 5 SECOND, 'UTC')", "bar_start + INTERVAL 5 SECOND"),
    "10s": TimeframeSpec("10s", 10, "toStartOfInterval(event_dt, INTERVAL 10 SECOND, 'UTC')", "bar_start + INTERVAL 10 SECOND"),
    "30s": TimeframeSpec("30s", 30, "toStartOfInterval(event_dt, INTERVAL 30 SECOND, 'UTC')", "bar_start + INTERVAL 30 SECOND"),
    "1m": TimeframeSpec("1m", 60, "toStartOfInterval(event_dt, INTERVAL 1 MINUTE, 'UTC')", "bar_start + INTERVAL 1 MINUTE"),
    "5m": TimeframeSpec("5m", 300, "toStartOfInterval(event_dt, INTERVAL 5 MINUTE, 'UTC')", "bar_start + INTERVAL 5 MINUTE"),
    "1h": TimeframeSpec("1h", 3600, "toStartOfInterval(event_dt, INTERVAL 1 HOUR, 'UTC')", "bar_start + INTERVAL 1 HOUR"),
    "1d": TimeframeSpec("1d", 86400, "toStartOfDay(event_dt, 'UTC')", "bar_start + INTERVAL 1 DAY"),
    "1w": TimeframeSpec("1w", 604800, "toStartOfWeek(event_dt, 1, 'UTC')", "bar_start + INTERVAL 1 WEEK"),
    "1mo": TimeframeSpec("1mo", 0, "toStartOfMonth(event_dt, 'UTC')", "addMonths(bar_start, 1)"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build qmd-gateway-compatible live_market_bars rows from market_sip_compact.events. "
            "The output schema mirrors services/qmd-gateway/src/bars.rs BAR_SCHEMA_VERSION=2."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--bars-table", default=DEFAULT_BARS_TABLE)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2026-12-31")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--replace-range", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--drop-table", action="store_true", help="Drop the bar table before rebuilding.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    started = time.perf_counter()
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    report_path = Path(args.output_root_win) / f"trade_bars_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    specs = parse_timeframes(args.timeframes)

    print("=" * 96, flush=True)
    print("Build qmd-compatible ClickHouse SIP bars", flush=True)
    print(f"database={args.database} events_table={args.events_table} bars_table={args.bars_table}", flush=True)
    print(f"timeframes={','.join(spec.name for spec in specs)}", flush=True)
    print(f"date_range={args.start_date}->{args.end_date}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"replace_range={args.replace_range} drop_table={args.drop_table} dry_run={args.dry_run}", flush=True)
    print(f"report={report_path}", flush=True)
    print(
        "secret_status="
        f"{secret_status(['CLICKHOUSE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_URL', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD'])}",
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    build_live_market_bars(client, args, specs=specs, report_path=report_path)
    print("=" * 96, flush=True)
    print(f"DONE elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f} report={report_path}", flush=True)
    print("=" * 96, flush=True)


def build_live_market_bars(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    *,
    specs: list[TimeframeSpec] | None = None,
    report_path: Path | None = None,
) -> list[dict[str, object]]:
    specs = specs or parse_timeframes(args.timeframes)
    report_path = report_path or (Path(args.output_root_win) / f"trade_bars_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    if args.dry_run:
        print_sql_preview("create", create_bar_table_sql(args.database, args.bars_table, args.storage_policy))
        if args.drop_table:
            print_sql_preview("drop", drop_table_sql(args.database, args.bars_table))
        if args.replace_range:
            print_sql_preview("delete range", delete_range_sql(args.database, args.bars_table, args.start_date, args.end_date, args))
        for spec in specs:
            print_sql_preview(f"insert {spec.name}", insert_live_market_bars_sql(args, spec))
        return results

    if args.drop_table:
        client.execute(drop_table_sql(args.database, args.bars_table))
        print(f"DROPPED {args.database}.{args.bars_table}", flush=True)
    client.execute(create_bar_table_sql(args.database, args.bars_table, args.storage_policy))

    if args.replace_range:
        delete_profile = run_profiled(
            client,
            f"delete_{args.bars_table}_{args.start_date}_{args.end_date}",
            delete_range_sql(args.database, args.bars_table, args.start_date, args.end_date, args),
        )
        append_jsonl(report_path, {"operation": "delete_range", "profile": asdict(delete_profile)})
        print_profile("DELETE", delete_profile)

    for index, spec in enumerate(specs, start=1):
        print("=" * 96, flush=True)
        print(f"BAR START [{index:,}/{len(specs):,}] timeframe={spec.name} table={args.bars_table}", flush=True)
        insert_profile = run_profiled(
            client,
            f"insert_{args.bars_table}_{spec.name}_{args.start_date}_{args.end_date}",
            insert_live_market_bars_sql(args, spec),
            query_settings(args),
        )
        summary = summarize_table(client, args.database, args.bars_table, spec.name, args.start_date, args.end_date)
        result = {"operation": "insert", "timeframe": spec.name, "profile": asdict(insert_profile), "summary": summary}
        append_jsonl(report_path, result)
        results.append(result)
        print_profile("INSERT", insert_profile)
        print(
            f"BAR DONE timeframe={spec.name} rows={summary['rows']:,} tickers={summary['tickers']:,} "
            f"volume={summary['volume']:.0f} min_bar={summary['min_bar_start']} max_bar={summary['max_bar_start']}",
            flush=True,
        )
    return results


def parse_timeframes(text: str) -> list[TimeframeSpec]:
    requested = [item.strip().lower() for item in text.split(",") if item.strip()]
    if not requested:
        raise ValueError("--timeframes must include at least one value")
    invalid = [item for item in requested if item not in TIMEFRAME_SPECS]
    if invalid:
        raise ValueError(f"Unsupported timeframes {invalid}; supported={sorted(TIMEFRAME_SPECS)}")
    seen: set[str] = set()
    specs: list[TimeframeSpec] = []
    for item in requested:
        if item not in seen:
            specs.append(TIMEFRAME_SPECS[item])
            seen.add(item)
    return specs


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def create_bar_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    session_date Date,
    schema_version UInt16,
    timeframe LowCardinality(String),
    sym LowCardinality(String),
    bar_start DateTime64(3, 'UTC'),
    bar_end DateTime64(3, 'UTC'),
    is_closed UInt8,
    first_event_ts Nullable(DateTime64(3, 'UTC')),
    last_event_ts Nullable(DateTime64(3, 'UTC')),
    open Float64,
    high Float64,
    low Float64,
    close Float64,
    volume Float64,
    dollar_volume Float64,
    trade_count UInt64,
    vwap Float64,
    avg_trade_size Float64,
    median_trade_size Float64,
    max_trade_size Float64,
    large_trade_count UInt64,
    large_trade_volume Float64,
    large_trade_notional Float64,
    trade_rate Float64,
    volume_rate Float64,
    dollar_volume_rate Float64,
    price_change Float64,
    price_change_pct Float64,
    high_low_range Float64,
    high_low_range_pct Float64,
    bid_open Float64,
    bid_high Float64,
    bid_low Float64,
    bid_close Float64,
    ask_open Float64,
    ask_high Float64,
    ask_low Float64,
    ask_close Float64,
    mid_open Float64,
    mid_high Float64,
    mid_low Float64,
    mid_close Float64,
    spread_open Float64,
    spread_high Float64,
    spread_low Float64,
    spread_close Float64,
    spread_mean Float64,
    spread_bps_mean Float64,
    spread_bps_close Float64,
    quoted_bid_size_mean Float64,
    quoted_ask_size_mean Float64,
    quote_count UInt64,
    quote_rate Float64,
    quote_update_intensity Float64,
    locked_crossed_quote_count UInt64,
    buy_trade_count UInt64,
    sell_trade_count UInt64,
    buy_volume Float64,
    sell_volume Float64,
    buy_dollar_volume Float64,
    sell_dollar_volume Float64,
    tape_imbalance Float64,
    aggressive_buy_ratio Float64,
    aggressive_sell_ratio Float64,
    buy_sell_volume_delta Float64,
    cumulative_delta Float64,
    effective_spread_mean Float64,
    realized_spread_proxy Float64,
    price_impact_1s Float64,
    price_impact_5s Float64,
    slippage_proxy_bps Float64,
    depth_imbalance_proxy Float64,
    liquidity_score Float64,
    spread_volume_ratio Float64,
    return_1_bar Float64,
    return_3_bar Float64,
    return_5_bar Float64,
    volume_accel Float64,
    trade_count_accel Float64,
    dollar_volume_accel Float64,
    quote_rate_accel Float64,
    tape_imbalance_accel Float64,
    vwap_distance_pct Float64,
    mid_vwap_distance_pct Float64,
    realized_volatility Float64,
    micro_price_volatility Float64,
    mid_price_volatility Float64,
    mean_abs_trade_return Float64,
    direction_change_count UInt64,
    chop_score Float64,
    estimated_luld_active UInt8,
    estimated_luld_reference_price Float64,
    estimated_luld_lower_price Float64,
    estimated_luld_upper_price Float64,
    estimated_luld_parameter_pct Float64,
    estimated_luld_distance_to_upper_pct Float64,
    estimated_luld_distance_to_lower_pct Float64,
    estimated_luld_state LowCardinality(String)
)
ENGINE = ReplacingMergeTree
PARTITION BY session_date
ORDER BY (session_date, timeframe, sym, bar_start)
{mergetree_settings_sql(storage_policy)}
"""


def drop_table_sql(database: str, table: str) -> str:
    return f"DROP TABLE IF EXISTS {quote_ident(database)}.{quote_ident(table)}"


def delete_range_sql(database: str, table: str, start_date: str, end_date: str, args: argparse.Namespace) -> str:
    return f"""
ALTER TABLE {quote_ident(database)}.{quote_ident(table)}
DELETE WHERE bar_start < (toDateTime64(toDate({sql_string(end_date)}) + INTERVAL 1 DAY, 3, 'UTC'))
  AND bar_end > toDateTime64(toDate({sql_string(start_date)}), 3, 'UTC')
{mutation_settings(args)}
"""


def insert_live_market_bars_sql(args: argparse.Namespace, spec: TimeframeSpec) -> str:
    db = quote_ident(args.database)
    src = f"{db}.{quote_ident(args.events_table)}"
    dst = f"{db}.{quote_ident(args.bars_table)}"
    trade_price = "if(bitAnd(event_flags, 1) = 1, toFloat64(price_primary_int) / 10000.0, toFloat64(price_primary_int) / 100.0)"
    ask_price = trade_price
    bid_price = "if(bitAnd(bitShiftRight(event_flags, 1), 1) = 1, toFloat64(price_secondary_int) / 10000.0, toFloat64(price_secondary_int) / 100.0)"
    seconds_expr = f"greatest(1.0, toFloat64(dateDiff('second', bar_start, bar_end)))" if spec.seconds <= 0 else f"toFloat64({spec.seconds})"
    return f"""
INSERT INTO {dst}
WITH
source AS
(
    SELECT
        ticker AS sym,
        ordinal,
        event_type,
        sip_timestamp_us,
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us), 'UTC') AS event_dt,
        {trade_price} AS trade_price,
        toFloat64(size_primary) AS trade_size,
        {ask_price} AS ask_price,
        {bid_price} AS bid_price,
        toFloat64(size_primary) AS ask_size,
        toFloat64(size_secondary) AS bid_size,
        event_date
    FROM {src}
    WHERE event_date >= toDate({sql_string(args.start_date)})
      AND event_date <= toDate({sql_string(args.end_date)})
      AND ticker != ''
      AND sip_timestamp_us > 0
),
bucketed AS
(
    SELECT
        *,
        {spec.bucket_sql} AS bar_start,
        {spec.end_sql} AS bar_end,
        event_type = 1 AND trade_price > 0 AND trade_size > 0 AS valid_trade,
        event_type = 0 AND bid_price > 0 AND ask_price > 0 AS valid_quote,
        if(event_type = 0 AND bid_price > 0 AND ask_price > 0, (bid_price + ask_price) / 2.0, 0.0) AS mid_price,
        if(event_type = 0 AND bid_price > 0 AND ask_price > 0, ask_price - bid_price, 0.0) AS spread
    FROM source
),
base AS
(
    SELECT
        toDate(bar_start) AS session_date,
        toUInt16({BAR_SCHEMA_VERSION}) AS schema_version,
        {sql_string(spec.name)} AS timeframe,
        sym,
        bar_start,
        bar_end,
        toUInt8(1) AS is_closed,
        min(event_dt) AS first_event_ts_raw,
        max(event_dt) AS last_event_ts_raw,
        argMinIf(trade_price, tuple(sip_timestamp_us, ordinal), valid_trade) AS open,
        maxIf(trade_price, valid_trade) AS high,
        minIf(trade_price, valid_trade) AS low,
        argMaxIf(trade_price, tuple(sip_timestamp_us, ordinal), valid_trade) AS close,
        sumIf(trade_size, valid_trade) AS volume,
        sumIf(trade_price * trade_size, valid_trade) AS dollar_volume,
        countIf(valid_trade) AS trade_count,
        quantileExactIf(0.5)(trade_size, valid_trade) AS median_trade_size,
        maxIf(trade_size, valid_trade) AS max_trade_size,
        countIf(valid_trade AND (trade_size >= 10000 OR trade_price * trade_size >= 100000)) AS large_trade_count,
        sumIf(trade_size, valid_trade AND (trade_size >= 10000 OR trade_price * trade_size >= 100000)) AS large_trade_volume,
        sumIf(trade_price * trade_size, valid_trade AND (trade_size >= 10000 OR trade_price * trade_size >= 100000)) AS large_trade_notional,
        argMinIf(bid_price, tuple(sip_timestamp_us, ordinal), valid_quote) AS bid_open,
        maxIf(bid_price, valid_quote) AS bid_high,
        minIf(bid_price, valid_quote) AS bid_low,
        argMaxIf(bid_price, tuple(sip_timestamp_us, ordinal), valid_quote) AS bid_close,
        argMinIf(ask_price, tuple(sip_timestamp_us, ordinal), valid_quote) AS ask_open,
        maxIf(ask_price, valid_quote) AS ask_high,
        minIf(ask_price, valid_quote) AS ask_low,
        argMaxIf(ask_price, tuple(sip_timestamp_us, ordinal), valid_quote) AS ask_close,
        argMinIf(mid_price, tuple(sip_timestamp_us, ordinal), valid_quote) AS mid_open,
        maxIf(mid_price, valid_quote) AS mid_high,
        minIf(mid_price, valid_quote) AS mid_low,
        argMaxIf(mid_price, tuple(sip_timestamp_us, ordinal), valid_quote) AS mid_close,
        argMinIf(spread, tuple(sip_timestamp_us, ordinal), valid_quote) AS spread_open,
        maxIf(spread, valid_quote) AS spread_high,
        minIf(spread, valid_quote) AS spread_low,
        argMaxIf(spread, tuple(sip_timestamp_us, ordinal), valid_quote) AS spread_close,
        avgIf(spread, valid_quote) AS spread_mean,
        avgIf(if(mid_price > 0, spread / mid_price * 10000.0, 0.0), valid_quote) AS spread_bps_mean,
        avgIf(bid_size, valid_quote) AS quoted_bid_size_mean,
        avgIf(ask_size, valid_quote) AS quoted_ask_size_mean,
        countIf(valid_quote) AS quote_count,
        countIf(valid_quote AND bid_price >= ask_price) AS locked_crossed_quote_count,
        stddevPopIf(trade_price, valid_trade) AS trade_price_stddev,
        stddevPopIf(mid_price, valid_quote) AS mid_price_stddev,
        avgIf(abs(trade_price), valid_trade) AS mean_trade_price_abs,
        avgIf(abs(mid_price), valid_quote) AS mean_mid_price_abs
    FROM bucketed
    GROUP BY sym, bar_start, bar_end
    HAVING trade_count > 0 OR quote_count > 0
),
metrics AS
(
    SELECT
        *,
        {seconds_expr} AS timeframe_seconds,
        if(volume > 0, dollar_volume / volume, 0.0) AS vwap,
        if(trade_count > 0, volume / toFloat64(trade_count), 0.0) AS avg_trade_size,
        close - open AS price_change,
        if(open > 0, (close - open) / open * 100.0, 0.0) AS price_change_pct,
        if(high > 0 AND low > 0, high - low, 0.0) AS high_low_range,
        if(open > 0 AND high > 0 AND low > 0, (high - low) / open * 100.0, 0.0) AS high_low_range_pct,
        if(mid_close > 0, spread_close / mid_close * 10000.0, 0.0) AS spread_bps_close,
        if(trade_count > 0, toFloat64(trade_count) / timeframe_seconds, 0.0) AS trade_rate,
        volume / timeframe_seconds AS volume_rate,
        dollar_volume / timeframe_seconds AS dollar_volume_rate,
        if(quote_count > 0, toFloat64(quote_count) / timeframe_seconds, 0.0) AS quote_rate,
        if(greatest(trade_count, 1) > 0, toFloat64(quote_count) / toFloat64(greatest(trade_count, 1)), 0.0) AS quote_update_intensity,
        if(vwap > 0, (close - vwap) / vwap * 100.0, 0.0) AS vwap_distance_pct,
        if(vwap > 0, (mid_close - vwap) / vwap * 100.0, 0.0) AS mid_vwap_distance_pct,
        if(mean_trade_price_abs > 0, trade_price_stddev / mean_trade_price_abs, 0.0) AS realized_volatility,
        if(mean_mid_price_abs > 0, mid_price_stddev / mean_mid_price_abs, 0.0) AS mid_price_volatility,
        if(quoted_bid_size_mean + quoted_ask_size_mean > 0, (quoted_bid_size_mean - quoted_ask_size_mean) / (quoted_bid_size_mean + quoted_ask_size_mean), 0.0) AS depth_imbalance_proxy,
        if(greatest(spread_bps_mean, 1.0) > 0, dollar_volume / greatest(spread_bps_mean, 1.0), 0.0) AS liquidity_score,
        if(dollar_volume > 0, spread_bps_mean / dollar_volume, 0.0) AS spread_volume_ratio
    FROM base
),
history AS
(
    SELECT
        *,
        lagInFrame(close, 1, 0.0) OVER bar_window AS prev_close_1,
        lagInFrame(close, 3, 0.0) OVER bar_window AS prev_close_3,
        lagInFrame(close, 5, 0.0) OVER bar_window AS prev_close_5,
        lagInFrame(volume, 1, 0.0) OVER bar_window AS prev_volume,
        lagInFrame(toFloat64(trade_count), 1, 0.0) OVER bar_window AS prev_trade_count,
        lagInFrame(dollar_volume, 1, 0.0) OVER bar_window AS prev_dollar_volume,
        lagInFrame(quote_rate, 1, 0.0) OVER bar_window AS prev_quote_rate,
        lagInFrame(0.0, 1, 0.0) OVER bar_window AS prev_tape_imbalance
    FROM metrics
    WINDOW bar_window AS (PARTITION BY sym, timeframe ORDER BY bar_start ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT
    session_date,
    schema_version,
    timeframe,
    sym,
    bar_start,
    bar_end,
    is_closed,
    first_event_ts_raw AS first_event_ts,
    last_event_ts_raw AS last_event_ts,
    open,
    high,
    low,
    close,
    volume,
    dollar_volume,
    trade_count,
    vwap,
    avg_trade_size,
    median_trade_size,
    max_trade_size,
    large_trade_count,
    large_trade_volume,
    large_trade_notional,
    trade_rate,
    volume_rate,
    dollar_volume_rate,
    price_change,
    price_change_pct,
    high_low_range,
    high_low_range_pct,
    bid_open,
    bid_high,
    bid_low,
    bid_close,
    ask_open,
    ask_high,
    ask_low,
    ask_close,
    mid_open,
    mid_high,
    mid_low,
    mid_close,
    spread_open,
    spread_high,
    spread_low,
    spread_close,
    spread_mean,
    spread_bps_mean,
    spread_bps_close,
    quoted_bid_size_mean,
    quoted_ask_size_mean,
    quote_count,
    quote_rate,
    quote_update_intensity,
    locked_crossed_quote_count,
    toUInt64(0) AS buy_trade_count,
    toUInt64(0) AS sell_trade_count,
    0.0 AS buy_volume,
    0.0 AS sell_volume,
    0.0 AS buy_dollar_volume,
    0.0 AS sell_dollar_volume,
    0.0 AS tape_imbalance,
    0.0 AS aggressive_buy_ratio,
    0.0 AS aggressive_sell_ratio,
    0.0 AS buy_sell_volume_delta,
    0.0 AS cumulative_delta,
    spread_bps_mean AS effective_spread_mean,
    spread_bps_mean AS realized_spread_proxy,
    vwap_distance_pct AS price_impact_1s,
    vwap_distance_pct AS price_impact_5s,
    greatest(spread_bps_mean, spread_bps_close) AS slippage_proxy_bps,
    depth_imbalance_proxy,
    liquidity_score,
    spread_volume_ratio,
    if(prev_close_1 > 0, (close - prev_close_1) / prev_close_1 * 100.0, 0.0) AS return_1_bar,
    if(prev_close_3 > 0, (close - prev_close_3) / prev_close_3 * 100.0, 0.0) AS return_3_bar,
    if(prev_close_5 > 0, (close - prev_close_5) / prev_close_5 * 100.0, 0.0) AS return_5_bar,
    volume - prev_volume AS volume_accel,
    toFloat64(trade_count) - prev_trade_count AS trade_count_accel,
    dollar_volume - prev_dollar_volume AS dollar_volume_accel,
    quote_rate - prev_quote_rate AS quote_rate_accel,
    0.0 - prev_tape_imbalance AS tape_imbalance_accel,
    vwap_distance_pct,
    mid_vwap_distance_pct,
    realized_volatility,
    mid_price_volatility AS micro_price_volatility,
    mid_price_volatility,
    realized_volatility AS mean_abs_trade_return,
    toUInt64(0) AS direction_change_count,
    if(high_low_range > 0, realized_volatility * close / high_low_range, 0.0) AS chop_score,
    toUInt8(0) AS estimated_luld_active,
    0.0 AS estimated_luld_reference_price,
    0.0 AS estimated_luld_lower_price,
    0.0 AS estimated_luld_upper_price,
    0.0 AS estimated_luld_parameter_pct,
    0.0 AS estimated_luld_distance_to_upper_pct,
    0.0 AS estimated_luld_distance_to_lower_pct,
    'inactive' AS estimated_luld_state
FROM history
"""


def summarize_table(client: ClickHouseHttpClient, database: str, table: str, timeframe: str, start_date: str, end_date: str) -> dict[str, int | float | str]:
    rows = client.query_tsv(
        f"""
SELECT
    count(),
    uniqExact(sym),
    if(count() = 0, '', toString(min(bar_start))),
    if(count() = 0, '', toString(max(bar_start))),
    if(count() = 0, 0, sum(volume)),
    if(count() = 0, 0, sum(trade_count)),
    if(count() = 0, 0, sum(quote_count))
FROM {quote_ident(database)}.{quote_ident(table)}
WHERE timeframe = {sql_string(timeframe)}
  AND bar_start < (toDateTime64(toDate({sql_string(end_date)}) + INTERVAL 1 DAY, 3, 'UTC'))
  AND bar_end > toDateTime64(toDate({sql_string(start_date)}), 3, 'UTC')
"""
    ).strip()
    parts = rows.split("\t") if rows else ["0", "0", "", "", "0", "0", "0"]
    return {
        "rows": int(parts[0] or 0),
        "tickers": int(parts[1] or 0),
        "min_bar_start": parts[2],
        "max_bar_start": parts[3],
        "volume": float(parts[4] or 0.0),
        "trade_count": int(float(parts[5] or 0)),
        "quote_count": int(float(parts[6] or 0)),
    }


def print_sql_preview(label: str, sql: str, *, limit: int = 2400) -> None:
    body = sql.strip()
    print(f"--- {label} SQL preview ---", flush=True)
    print(body[:limit] + ("\n..." if len(body) > limit else ""), flush=True)


def print_profile(prefix: str, profile: QueryProfile) -> None:
    print(
        f"{prefix} profile wall={profile.wall_seconds:.1f}s "
        f"read_rows={profile.read_rows:,} written_rows={profile.written_rows:,} "
        f"memory={profile.memory_usage_bytes:,}",
        flush=True,
    )


def append_jsonl(path: Path, item: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def mutation_settings(args: argparse.Namespace) -> str:
    settings = ["mutations_sync = 2"]
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def expand_date_range_for_timeframes(start_date: str, end_date: str, timeframes: str) -> tuple[str, str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    frames = {item.strip().lower() for item in timeframes.split(",") if item.strip()}
    if "1w" in frames:
        start = start - timedelta(days=start.weekday())
        end = end + timedelta(days=6 - end.weekday())
    if "1mo" in frames:
        start = start.replace(day=1)
        next_month = end.replace(day=28) + timedelta(days=4)
        end = next_month.replace(day=1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


if __name__ == "__main__":
    main()
