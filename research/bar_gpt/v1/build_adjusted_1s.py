from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.bar_gpt.v1.build_1s import (
    BuildReporter,
    _event_source,
    _family_aggregates,
    _relation_aggregates,
    _size_literal,
)
from research.bar_gpt.v1.cohort import (
    BAR_GPT_ADJUSTED_1S_MANIFEST_TABLE,
    BAR_GPT_ADJUSTED_1S_TABLE,
    BAR_GPT_COHORT_2TB,
    BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
    BAR_GPT_COHORT_2TB_TABLE,
    BAR_GPT_SPLIT_FACTOR_TABLE,
    BAR_GPT_REVIEWED_TICKER_CHAINS,
)
from research.bar_gpt.v1.schema import (
    FEATURE_NAMES,
    ONE_SECOND_US,
    SCHEMA_VERSION,
    SESSION_END_SECOND,
    SESSION_START_SECOND,
    SESSION_TIMEZONE,
    table_columns,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    insert_json_each_row,
    mergetree_settings_sql,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status


BUILD_VERSION = "bar_gpt_1s_split_adjusted_v2"
FEATURE_VERSION = "bar_gpt_1s_sufficient_stats_split_adjusted_v2"
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_SPLIT_DATABASE = "q_live"
DEFAULT_SPLIT_TABLE = "market_stock_split_v1"
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_adjusted_1s")

ADJUSTMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("adjustment_asof_date", "Date"),
    ("split_schedule_sha256", "FixedString(64)"),
    ("source_ticker", "LowCardinality(String)"),
    ("build_method", "LowCardinality(String)"),
)
FACTOR_TYPES = {
    "adjustment_asof_date": "Date", "ticker": "LowCardinality(String)", "local_date": "Date",
    "future_price_factor": "Float64", "future_size_factor": "Float64",
    "split_day_price_factor": "Float64", "split_day_size_factor": "Float64",
    "split_day_action_count": "UInt16", "schedule_sha256": "FixedString(64)",
    "built_at": "DateTime64(3, 'UTC')",
}
MANIFEST_TYPES = {
    "artifact_name": "LowCardinality(String)", "unit_id": "String", "partition_month": "Date",
    "method": "LowCardinality(String)", "status": "LowCardinality(String)",
    "adjustment_asof_date": "Date", "schedule_sha256": "FixedString(64)",
    "source_row_count": "UInt64", "source_event_count": "UInt64", "output_row_count": "UInt64",
    "message": "String", "completed_at": "DateTime64(3, 'UTC')",
}


def clickhouse_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def adjusted_table_columns() -> tuple[tuple[str, str], ...]:
    base = table_columns()
    return (*base[:-1], *ADJUSTMENT_COLUMNS, base[-1])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a current-basis split-adjusted BarGPT 1s table from v1, replaying split days from raw events."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="auto", help="Exclusive; auto uses the v1 maximum plus one day.")
    parser.add_argument("--adjustment-asof-date", default="auto")
    parser.add_argument("--tickers", default=",".join(BAR_GPT_COHORT_2TB))
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--source-table", default=BAR_GPT_COHORT_2TB_TABLE)
    parser.add_argument("--source-manifest-table", default=BAR_GPT_COHORT_2TB_MANIFEST_TABLE)
    parser.add_argument("--target-table", default=BAR_GPT_ADJUSTED_1S_TABLE)
    parser.add_argument("--manifest-table", default=BAR_GPT_ADJUSTED_1S_MANIFEST_TABLE)
    parser.add_argument("--factor-table", default=BAR_GPT_SPLIT_FACTOR_TABLE)
    parser.add_argument("--events-table-base", default="events")
    parser.add_argument("--index-table", default="events_ticker_day_index")
    parser.add_argument("--split-database", default=DEFAULT_SPLIT_DATABASE)
    parser.add_argument("--split-table", default=DEFAULT_SPLIT_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="48G")
    parser.add_argument("--max-bytes-before-external-group-by", default="12G")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    return parser.parse_args(argv)


def query_settings(args: argparse.Namespace) -> str:
    return "\nSETTINGS " + ", ".join((
        f"max_threads={max(1, args.max_threads)}", f"max_memory_usage={_size_literal(args.max_memory_usage)}",
        f"max_bytes_before_external_group_by={_size_literal(args.max_bytes_before_external_group_by)}",
        "max_execution_time=0", "log_queries=1",
    ))


def create_target_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {kind}" for name, kind in adjusted_table_columns())
    return f"""CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.target_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(local_date)
ORDER BY (ticker, local_date, bucket_index)
{mergetree_settings_sql(args.storage_policy)}"""


def create_factor_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {kind}" for name, kind in FACTOR_TYPES.items())
    return f"""CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.factor_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYear(local_date)
ORDER BY (adjustment_asof_date, ticker, local_date)
{mergetree_settings_sql(args.storage_policy)}"""


def create_manifest_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {kind}" for name, kind in MANIFEST_TYPES.items())
    return f"""CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(completed_at)
PARTITION BY toYear(partition_month)
ORDER BY (artifact_name, unit_id)
{mergetree_settings_sql(args.storage_policy)}"""


def _query_tsv(client: ClickHouseHttpClient, sql: str) -> list[list[str]]:
    return [line.split("\t") for line in client.execute(sql.strip().rstrip(";") + " FORMAT TSVRaw").splitlines() if line]


def validate_table(client: ClickHouseHttpClient, database: str, table: str, expected: dict[str, str]) -> None:
    rows = _query_tsv(client, f"SELECT name,type FROM system.columns WHERE database={sql_string(database)} AND table={sql_string(table)} ORDER BY position")
    actual = {name: kind for name, kind, *_ in rows}
    mismatch = {name: kind for name, kind in expected.items() if actual.get(name) != kind}
    extras = sorted(set(actual) - set(expected))
    if mismatch or extras:
        raise RuntimeError(f"{database}.{table} schema mismatch: expected={mismatch} unexpected={extras}")


def resolve_range(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[dt.date, dt.date, dt.date]:
    start = dt.date.fromisoformat(args.start_date)
    if args.end_date == "auto":
        row = _query_tsv(client, f"SELECT addDays(max(local_date),1) FROM {quote_ident(args.database)}.{quote_ident(args.source_table)}")[0]
        end = dt.date.fromisoformat(row[0])
    else:
        end = dt.date.fromisoformat(args.end_date)
    today_ny = dt.datetime.now(ZoneInfo(SESSION_TIMEZONE)).date()
    asof = today_ny if args.adjustment_asof_date == "auto" else dt.date.fromisoformat(args.adjustment_asof_date)
    if end <= start or asof < start:
        raise ValueError("invalid start/end/adjustment-asof range")
    return start, end, asof


def requested_tickers(text: str) -> tuple[str, ...]:
    values = tuple(sorted({item.strip().upper() for item in text.split(",") if item.strip()}))
    if not values:
        raise ValueError("at least one ticker is required")
    return values


def load_split_actions(
    client: ClickHouseHttpClient, args: argparse.Namespace, tickers: tuple[str, ...], asof: dt.date
) -> tuple[dict[str, list[tuple[dt.date, float, float]]], str]:
    ticker_sql = ",".join(sql_string(value) for value in tickers)
    rows = _query_tsv(client, f"""
SELECT DISTINCT upper(provider_ticker), execution_date, toFloat64(split_from), toFloat64(split_to)
FROM {quote_ident(args.split_database)}.{quote_ident(args.split_table)}
WHERE upper(provider_ticker) IN ({ticker_sql}) AND execution_date <= toDate({sql_string(str(asof))})
  AND split_from > 0 AND split_to > 0
ORDER BY upper(provider_ticker), execution_date, split_from, split_to
""")
    actions: dict[str, list[tuple[dt.date, float, float]]] = defaultdict(list)
    canonical: list[dict[str, Any]] = []
    for ticker, date_text, from_text, to_text, *_ in rows:
        split_from, split_to = float(from_text), float(to_text)
        date = dt.date.fromisoformat(date_text)
        actions[ticker].append((date, split_from / split_to, split_to / split_from))
        canonical.append({"ticker": ticker, "execution_date": date_text, "split_from": split_from, "split_to": split_to})
    # The cutoff date is stored separately.  This digest identifies the
    # economic split schedule so a resumable provider job may cross midnight
    # only when the applicable action set remains identical.
    digest = hashlib.sha256(
        json.dumps({"tickers": list(tickers), "actions": canonical}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return actions, digest


def adjustment_basis_hash(split_schedule_sha256: str, tickers: tuple[str, ...]) -> str:
    identity = {
        ticker: BAR_GPT_REVIEWED_TICKER_CHAINS[ticker]
        for ticker in tickers if ticker in BAR_GPT_REVIEWED_TICKER_CHAINS
    }
    payload = {"split_schedule_sha256": split_schedule_sha256, "identity_chains": identity}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def factor_rows(
    tickers: tuple[str, ...], start: dt.date, end: dt.date, asof: dt.date,
    actions: dict[str, list[tuple[dt.date, float, float]]], digest: str,
) -> list[dict[str, Any]]:
    built_at = clickhouse_utc_now()
    rows: list[dict[str, Any]] = []
    day = start
    while day < end:
        for ticker in tickers:
            future_pf = future_sf = day_pf = day_sf = 1.0
            day_count = 0
            for action_date, price_factor, size_factor in actions.get(ticker, []):
                if action_date > day:
                    future_pf *= price_factor; future_sf *= size_factor
                elif action_date == day:
                    day_pf *= price_factor; day_sf *= size_factor; day_count += 1
            rows.append({"adjustment_asof_date": str(asof), "ticker": ticker, "local_date": str(day),
                         "future_price_factor": future_pf, "future_size_factor": future_sf,
                         "split_day_price_factor": day_pf, "split_day_size_factor": day_sf,
                         "split_day_action_count": day_count, "schedule_sha256": digest, "built_at": built_at})
        day += dt.timedelta(days=1)
    return rows


def materialize_factor_schedule(
    client: ClickHouseHttpClient, args: argparse.Namespace, rows: list[dict[str, Any]],
    tickers: tuple[str, ...], start: dt.date, end: dt.date, asof: dt.date, digest: str,
) -> None:
    # Keep HTTP request bodies bounded; the full cohort/date grid is roughly
    # 300k rows and must not be serialized as one giant request.
    for offset in range(0, len(rows), 10_000):
        insert_json_each_row(client, args.database, args.factor_table, list(FACTOR_TYPES), rows[offset:offset + 10_000])
    expected = len(tickers) * (end - start).days
    result = _query_tsv(client, f"""SELECT count(), uniqExact(tuple(ticker,local_date)), uniqExact(schedule_sha256)
FROM {quote_ident(args.database)}.{quote_ident(args.factor_table)} FINAL
WHERE adjustment_asof_date=toDate({sql_string(str(asof))}) AND local_date>=toDate({sql_string(str(start))})
AND local_date<toDate({sql_string(str(end))}) AND schedule_sha256={sql_string(digest)}""")[0]
    total, unique, hashes = map(int, result)
    if total != expected or unique != expected or hashes != 1:
        raise RuntimeError(f"split factor certification failed: rows={total} unique={unique} expected={expected} hashes={hashes}")


PRICE_FAMILY_COLUMNS = {f"{family}_{field}" for family in ("trade", "bid", "ask") for field in ("open", "high", "low", "close")}
SIZE_COLUMNS = {f"{family}_{field}" for family in ("trade", "bid", "ask") for field in ("size_sum", "size_open", "size_high", "size_low", "size_close")}
SIZE_SQUARED_COLUMNS = {f"{family}_size_squared_sum" for family in ("trade", "bid", "ask")}
PRICE_SIZE_COLUMNS = {f"{family}_price_size_sum" for family in ("trade", "bid", "ask")}
RELATION_PRICE_COLUMNS = {f"{family}_{field}" for family in ("spread", "midpoint", "microprice") for field in ("open", "high", "low", "close", "sum")}
RELATION_SQUARED_COLUMNS = {f"{family}_squared_sum" for family in ("spread", "midpoint", "microprice")}


def scaled_feature_expression(name: str, source: str = "s") -> str:
    value = f"{source}.{quote_ident(name)}"
    if name in PRICE_FAMILY_COLUMNS or name in RELATION_PRICE_COLUMNS:
        return f"{value} * f.future_price_factor"
    if name in SIZE_COLUMNS:
        return f"{value} * f.future_size_factor"
    if name in SIZE_SQUARED_COLUMNS:
        return f"{value} * f.future_size_factor * f.future_size_factor"
    if name in PRICE_SIZE_COLUMNS:
        return f"{value} * f.future_price_factor * f.future_size_factor"
    if name in RELATION_SQUARED_COLUMNS:
        return f"{value} * f.future_price_factor * f.future_price_factor"
    return value


def identity_alias_intervals(
    tickers: tuple[str, ...], start: dt.date, end: dt.date
) -> list[tuple[str, str, dt.date, dt.date]]:
    intervals: list[tuple[str, str, dt.date, dt.date]] = []
    for canonical in tickers:
        for provider, left_text, right_text in BAR_GPT_REVIEWED_TICKER_CHAINS.get(canonical, ()):
            left = max(start, dt.date.fromisoformat(left_text))
            right = min(end, dt.date.fromisoformat(right_text))
            if provider != canonical and left < right:
                intervals.append((canonical, provider, left, right))
    return intervals


def identity_exclusion_sql(
    intervals: list[tuple[str, str, dt.date, dt.date]], *, source_alias: str = "s"
) -> str:
    clauses = [
        f"({source_alias}.ticker={sql_string(canonical)} AND "
        f"{source_alias}.local_date>=toDate({sql_string(str(left))}) AND "
        f"{source_alias}.local_date<toDate({sql_string(str(right))}))"
        for canonical, _provider, left, right in intervals
    ]
    return " AND NOT (" + " OR ".join(clauses) + ")" if clauses else ""


def identity_alias_days(
    client: ClickHouseHttpClient, args: argparse.Namespace,
    intervals: list[tuple[str, str, dt.date, dt.date]],
) -> list[tuple[str, str, dt.date]]:
    units: list[tuple[str, str, dt.date]] = []
    for canonical, provider, left, right in intervals:
        rows = _query_tsv(client, f"""SELECT source_date
FROM {quote_ident(args.database)}.{quote_ident(args.index_table)}
WHERE upper(ticker)={sql_string(provider)} AND source_date>=toDate({sql_string(str(left))})
AND source_date<toDate({sql_string(str(right))}) AND event_count>0
GROUP BY source_date ORDER BY source_date""")
        units.extend((canonical, provider, dt.date.fromisoformat(row[0])) for row in rows)
    return units


def bulk_month_sql(
    args: argparse.Namespace, range_start: dt.date, range_end: dt.date,
    asof: dt.date, digest: str, tickers: tuple[str, ...],
    identity_intervals: list[tuple[str, str, dt.date, dt.date]] | None = None,
) -> str:
    columns = ",\n    ".join(quote_ident(name) for name, _kind in adjusted_table_columns())
    selections = [
        "s.schema_version", sql_string(FEATURE_VERSION), "s.local_date", "s.ticker", "s.bucket_index",
        "s.bar_start_us", "s.bar_end_us", "s.available_at_us", "s.source_first_ordinal", "s.source_last_ordinal",
        "s.source_first_timestamp_us", "s.source_last_timestamp_us",
        *(scaled_feature_expression(name) for name in FEATURE_NAMES),
        f"toDate({sql_string(str(asof))})", sql_string(digest), "s.ticker",
        sql_string("linear_sufficient_stats"), "now64(3,'UTC')",
    ]
    select_sql = ",\n    ".join(selections)
    ticker_sql = ",".join(sql_string(value) for value in tickers)
    return f"""INSERT INTO {quote_ident(args.database)}.{quote_ident(args.target_table)} ({columns})
SELECT
    {select_sql}
FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} AS s
INNER JOIN
(
    SELECT * FROM {quote_ident(args.database)}.{quote_ident(args.factor_table)} FINAL
    WHERE adjustment_asof_date=toDate({sql_string(str(asof))}) AND schedule_sha256={sql_string(digest)}
) AS f ON f.ticker=s.ticker AND f.local_date=s.local_date
PREWHERE s.local_date>=toDate({sql_string(str(range_start))}) AND s.local_date<toDate({sql_string(str(range_end))})
WHERE s.ticker IN ({ticker_sql}) AND f.split_day_action_count=0{identity_exclusion_sql(identity_intervals or [])}
{query_settings(args)}"""


def split_day_reference_price(
    client: ClickHouseHttpClient, args: argparse.Namespace, day: dt.date, ticker: str
) -> float | None:
    source = _event_source(args, day)
    next_day = day + dt.timedelta(days=1)
    rows = _query_tsv(client, f"""
WITH bitAnd(event_meta,1) AS event_type,
toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us,'UTC'),{sql_string(SESSION_TIMEZONE)}) AS ts_local,
toDate(ts_local) AS local_date_value, dateDiff('second',toStartOfDay(ts_local),ts_local) AS local_second,
toFloat64(if(price_primary_int>0,price_primary_int/if(bitAnd(event_meta,2)=2,10000.0,100.0),0.0)) AS p1,
toFloat64(if(price_secondary_int>0,price_secondary_int/if(bitAnd(event_meta,4)=4,10000.0,100.0),0.0)) AS p2
SELECT quantileExact(0.5)(price)
FROM (SELECT arrayJoin([p1,p2]) AS price FROM {source}
PREWHERE event_date>=toDate({sql_string(str(day))}) AND event_date<toDate({sql_string(str(next_day))})
WHERE upper(ticker)={sql_string(ticker)} AND local_date_value=toDate({sql_string(str(day))})
AND local_second>={SESSION_START_SECOND} AND local_second<{SESSION_END_SECOND}) WHERE price>0
""")
    if not rows or rows[0][0] in ("", "nan", "\\N"):
        return None
    value = float(rows[0][0])
    return value if math.isfinite(value) and value > 0 else None


def split_day_sql(
    args: argparse.Namespace, day: dt.date, ticker: str, asof: dt.date, digest: str,
    reference_price: float, future_pf: float, future_sf: float, day_pf: float, day_sf: float,
    *, output_ticker: str | None = None, build_method: str = "event_replay_split_day",
) -> str:
    source = _event_source(args, day)
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    next_day = day + dt.timedelta(days=1)
    order = "tuple(sip_timestamp_us,ordinal)"
    trade_valid = "event_type=1 AND trade_price>0 AND trade_size>0"
    bid_valid = "event_type=0 AND bid_price>0 AND bid_size>0"
    ask_valid = "event_type=0 AND ask_price>0 AND ask_size>0"
    pair_valid = "event_type=0 AND bid_price>0 AND ask_price>0 AND bid_size>0 AND ask_size>0 AND bid_price<=ask_price"
    aggregates = [
        *_family_aggregates("trade", "trade_price", "trade_size", trade_valid),
        *_family_aggregates("bid", "bid_price", "bid_size", bid_valid),
        *_family_aggregates("ask", "ask_price", "ask_size", ask_valid),
        f"toUInt8(countIf({pair_valid})>0) AS quote_pair_present", f"toUInt64(countIf({pair_valid})) AS quote_pair_count",
        *_relation_aggregates("spread", "spread", pair_valid), *_relation_aggregates("midpoint", "midpoint", pair_valid),
        *_relation_aggregates("microprice", "microprice", pair_valid),
        *_relation_aggregates("queue_imbalance", "queue_imbalance", pair_valid),
        "toUInt64(countIf(event_type=0 AND bid_price=ask_price AND bid_price>0)) AS locked_quote_count",
        "toUInt64(countIf(event_type=0 AND bid_price>ask_price AND ask_price>0)) AS crossed_quote_count",
        "toUInt64(sum(toUInt8(condition_token_1>0)+toUInt8(condition_token_2>0)+toUInt8(condition_token_3>0)+toUInt8(condition_token_4>0)+toUInt8(condition_token_5>0))) AS condition_nonzero_count",
        "toUInt64(count()) AS source_event_count",
    ]
    columns = ",\n    ".join(quote_ident(name) for name, _kind in adjusted_table_columns())
    aggregate_sql = ",\n    ".join(aggregates)
    # Each price leg is classified independently.  The alternative candidate
    # applies the execution-day split and is used only when it is closer in log
    # space to the robust same-day median; its paired size receives reciprocal
    # scaling.  All events then receive later-split factors.
    canonical_ticker = output_ticker or ticker
    return f"""INSERT INTO {target} ({columns})
WITH
toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us,'UTC'),{sql_string(SESSION_TIMEZONE)}) AS ts_local,
toDate(ts_local) AS local_date_value, dateDiff('second',toStartOfDay(ts_local),ts_local) AS local_second,
dateDiff('microsecond',toStartOfDay(ts_local),ts_local) AS local_session_us, bitAnd(event_meta,1) AS event_type,
toFloat64(if(price_primary_int>0,price_primary_int/if(bitAnd(event_meta,2)=2,10000.0,100.0),0.0)) AS raw_primary,
toFloat64(if(price_secondary_int>0,price_secondary_int/if(bitAnd(event_meta,4)=4,10000.0,100.0),0.0)) AS raw_secondary,
raw_primary>0 AND abs(log(raw_primary*{day_pf:.17g}/{reference_price:.17g}))<abs(log(raw_primary/{reference_price:.17g})) AS primary_stale,
raw_secondary>0 AND abs(log(raw_secondary*{day_pf:.17g}/{reference_price:.17g}))<abs(log(raw_secondary/{reference_price:.17g})) AS secondary_stale,
if(primary_stale,raw_primary*{day_pf:.17g},raw_primary)*{future_pf:.17g} AS primary_price,
if(secondary_stale,raw_secondary*{day_pf:.17g},raw_secondary)*{future_pf:.17g} AS secondary_price,
if(primary_stale,toFloat64(size_primary)*{day_sf:.17g},toFloat64(size_primary))*{future_sf:.17g} AS primary_size,
if(secondary_stale,toFloat64(size_secondary)*{day_sf:.17g},toFloat64(size_secondary))*{future_sf:.17g} AS secondary_size,
if(event_type=1,primary_price,0.0) AS trade_price, if(event_type=1,primary_size,0.0) AS trade_size,
if(event_type=0,primary_price,0.0) AS ask_price, if(event_type=0,secondary_price,0.0) AS bid_price,
if(event_type=0,primary_size,0.0) AS ask_size, if(event_type=0,secondary_size,0.0) AS bid_size,
ask_price-bid_price AS spread, (ask_price+bid_price)/2.0 AS midpoint,
if(ask_size+bid_size>0,(ask_price*bid_size+bid_price*ask_size)/(ask_size+bid_size),0.0) AS microprice,
if(ask_size+bid_size>0,(bid_size-ask_size)/(bid_size+ask_size),0.0) AS queue_imbalance,
intDiv(toUInt64(local_session_us),toUInt64({ONE_SECOND_US})) AS second_bucket_index,
intDiv(toUInt64(sip_timestamp_us),toUInt64({ONE_SECOND_US}))*toUInt64({ONE_SECOND_US}) AS second_start_us
SELECT toUInt16({SCHEMA_VERSION}), {sql_string(FEATURE_VERSION)}, local_date_value, {sql_string(canonical_ticker)}, second_bucket_index,
second_start_us, second_start_us+toUInt64({ONE_SECOND_US}), second_start_us+toUInt64({ONE_SECOND_US}),
min(toUInt64(ordinal)),max(toUInt64(ordinal)),min(toUInt64(sip_timestamp_us)),max(toUInt64(sip_timestamp_us)),
{aggregate_sql}, toDate({sql_string(str(asof))}), {sql_string(digest)}, {sql_string(ticker)},
{sql_string(build_method)}, now64(3,'UTC')
FROM {source}
PREWHERE event_date>=toDate({sql_string(str(day))}) AND event_date<toDate({sql_string(str(next_day))})
WHERE local_date_value=toDate({sql_string(str(day))}) AND upper(ticker)={sql_string(ticker)}
AND local_second>={SESSION_START_SECOND} AND local_second<{SESSION_END_SECOND}
GROUP BY local_date_value,ticker,second_bucket_index,second_start_us
{query_settings(args)}"""


def months(start: dt.date, end: dt.date) -> list[dt.date]:
    value = dt.date(start.year, start.month, 1); result = []
    while value < end:
        result.append(value)
        value = dt.date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)
    return result


def manifest_units(client: ClickHouseHttpClient, args: argparse.Namespace, asof: dt.date, digest: str) -> set[str]:
    rows = _query_tsv(client, f"""SELECT unit_id FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name={sql_string(args.target_table)} AND adjustment_asof_date=toDate({sql_string(str(asof))})
AND schedule_sha256={sql_string(digest)} AND status='certified'""")
    return {row[0] for row in rows}


def insert_manifest(client: ClickHouseHttpClient, args: argparse.Namespace, *, unit_id: str, month: dt.date,
                    method: str, asof: dt.date, digest: str, source_rows: int, source_events: int,
                    output_rows: int, message: str) -> None:
    row = {"artifact_name": args.target_table, "unit_id": unit_id, "partition_month": str(month), "method": method,
           "status": "certified", "adjustment_asof_date": str(asof), "schedule_sha256": digest,
           "source_row_count": source_rows, "source_event_count": source_events, "output_row_count": output_rows,
           "message": message, "completed_at": clickhouse_utc_now()}
    insert_json_each_row(client, args.database, args.manifest_table, list(MANIFEST_TYPES), [row])


def scalar_stats(client: ClickHouseHttpClient, sql: str) -> tuple[int, int]:
    row = _query_tsv(client, sql)[0]
    return int(row[0]) if row[0] not in ("", "\\N") else 0, int(row[1]) if row[1] not in ("", "\\N") else 0


def source_linear_stats(
    client: ClickHouseHttpClient, args: argparse.Namespace, *, left: dt.date, right: dt.date,
    asof: dt.date, digest: str, tickers: tuple[str, ...], split_units: list[tuple[str, dt.date, float, float]],
    identity_intervals: list[tuple[str, str, dt.date, dt.date]],
) -> tuple[int, int]:
    canonical = tickers == tuple(sorted(BAR_GPT_COHORT_2TB)) and args.source_table == BAR_GPT_COHORT_2TB_TABLE
    if not canonical:
        ticker_sql = ",".join(sql_string(value) for value in tickers)
        return scalar_stats(client, f"""SELECT count(),sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} AS s INNER JOIN
(SELECT * FROM {quote_ident(args.database)}.{quote_ident(args.factor_table)} FINAL
 WHERE adjustment_asof_date=toDate({sql_string(str(asof))}) AND schedule_sha256={sql_string(digest)}) AS f
ON f.ticker=s.ticker AND f.local_date=s.local_date
WHERE s.local_date>=toDate({sql_string(str(left))}) AND s.local_date<toDate({sql_string(str(right))})
AND s.ticker IN ({ticker_sql}) AND f.split_day_action_count=0{identity_exclusion_sql(identity_intervals)}""")
    total_rows, total_events = scalar_stats(client, f"""SELECT sum(output_row_count),sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.source_manifest_table)} FINAL
WHERE artifact_name={sql_string(args.source_table)} AND status='complete'
AND local_date>=toDate({sql_string(str(left))}) AND local_date<toDate({sql_string(str(right))})""")
    exclusions = [(ticker, day) for ticker, day, _pf, _sf in split_units if left <= day < right]
    split_rows = split_events = 0
    if exclusions:
        tuple_sql = ",".join(f"({sql_string(ticker)},toDate({sql_string(str(day))}))" for ticker, day in exclusions)
        split_rows, split_events = scalar_stats(client, f"""SELECT count(),sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.source_table)}
WHERE tuple(ticker,local_date) IN ({tuple_sql})""")
    identity_rows = identity_events = 0
    identity_clause = identity_exclusion_sql(identity_intervals).removeprefix(" AND NOT ")
    if identity_clause:
        identity_rows, identity_events = scalar_stats(client, f"""SELECT count(),sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} AS s
WHERE s.local_date>=toDate({sql_string(str(left))}) AND s.local_date<toDate({sql_string(str(right))})
AND {identity_clause}""")
    return total_rows - split_rows - identity_rows, total_events - split_events - identity_events


def validate_existing_basis(client: ClickHouseHttpClient, args: argparse.Namespace, asof: dt.date, digest: str) -> None:
    rows = _query_tsv(client, f"SELECT toString(adjustment_asof_date),split_schedule_sha256 FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} LIMIT 1")
    if rows and (rows[0][0] != str(asof) or rows[0][1] != digest):
        raise RuntimeError(f"target already binds a different adjustment basis ({rows[0][0]}, {rows[0][1]}); use new versioned table names")


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_clickhouse_env_files())
    args = parse_args(argv)
    tickers = requested_tickers(args.tickers)
    if not args.execute:
        print(json.dumps({"source": f"{args.database}.{args.source_table}", "target": f"{args.database}.{args.target_table}",
                          "strategy": "linear sufficient-stat transform except raw-event replay on split execution dates",
                          "tickers": len(tickers), "start": args.start_date, "end": args.end_date,
                          "adjustment_asof": args.adjustment_asof_date}, indent=2))
        print(create_target_table_sql(args)); print(create_factor_table_sql(args)); print(create_manifest_table_sql(args))
        return 0
    if not args.storage_policy:
        raise RuntimeError("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required")
    run_dir = args.runtime_root / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, args.clickhouse_password,
                                  timeout_seconds=300, persistent=True)
    try:
        start, end, asof = resolve_range(client, args)
        actions, split_schedule_sha256 = load_split_actions(client, args, tickers, asof)
        digest = adjustment_basis_hash(split_schedule_sha256, tickers)
        client.execute(create_target_table_sql(args)); client.execute(create_factor_table_sql(args)); client.execute(create_manifest_table_sql(args))
        validate_table(client, args.database, args.target_table, dict(adjusted_table_columns()))
        validate_table(client, args.database, args.factor_table, FACTOR_TYPES)
        validate_table(client, args.database, args.manifest_table, MANIFEST_TYPES)
        validate_existing_basis(client, args, asof, digest)
        schedule = factor_rows(tickers, start, end, asof, actions, digest)
        materialize_factor_schedule(client, args, schedule, tickers, start, end, asof, digest)
        identity_intervals = identity_alias_intervals(tickers, start, end)
        alias_units = identity_alias_days(client, args, identity_intervals)
        alias_dates = {(canonical, day) for canonical, _provider, day in alias_units}
        split_units = [
            (str(row["ticker"]), dt.date.fromisoformat(str(row["local_date"])),
             float(row["split_day_price_factor"]), float(row["split_day_size_factor"]))
            for row in schedule if int(row["split_day_action_count"]) > 0
            and (str(row["ticker"]), dt.date.fromisoformat(str(row["local_date"]))) not in alias_dates
        ]
        total_units = len(months(start, end)) + len(split_units) + len(alias_units)
        report_path = run_dir / "build.jsonl"
        done = manifest_units(client, args, asof, digest)
        with BuildReporter(report_path=report_path, total_days=total_units,
                           interactive=args.progress_layout in ("auto", "rich"),
                           title="BarGPT split-adjusted 1s build", progress_noun="units",
                           job_label="BarGPT split-adjusted one-second build") as reporter:
            reporter.event("preflight", message=(f"basis asof={asof} hash={digest} split_days={len(split_units)} "
                                                  f"identity_alias_days={len(alias_units)}"),
                           secrets=secret_status([]), source=args.source_table, target=args.target_table)
            for month in months(start, end):
                unit = f"linear:{month:%Y-%m}:{digest[:16]}"
                reporter.update(day=str(month), unit=unit, stage="copying")
                if unit in done:
                    reporter.skipped_units += 1; reporter.completed_days += 1; reporter.update(message="Already certified"); continue
                next_month = dt.date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
                left, right = max(start, month), min(end, next_month)
                source_rows, source_events = source_linear_stats(
                    client, args, left=left, right=right, asof=asof, digest=digest,
                    tickers=tickers, split_units=split_units, identity_intervals=identity_intervals,
                )
                began = time.perf_counter(); client.execute(
                    bulk_month_sql(args, left, right, asof, digest, tickers, identity_intervals),
                    query_id=f"bargpt_adjust_linear_{month:%Y%m}_{uuid.uuid4().hex}",
                )
                output_rows, _ = scalar_stats(client, f"""SELECT count(),sum(source_event_count) FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE local_date>=toDate({sql_string(str(left))}) AND local_date<toDate({sql_string(str(right))})
AND split_schedule_sha256={sql_string(digest)} AND build_method='linear_sufficient_stats'""")
                if output_rows != source_rows:
                    raise RuntimeError(f"{unit} row mismatch source={source_rows} target={output_rows}")
                insert_manifest(client, args, unit_id=unit, month=month, method="linear_sufficient_stats", asof=asof,
                                digest=digest, source_rows=source_rows, source_events=source_events,
                                output_rows=output_rows, message="Linear split scaling of v1 sufficient statistics; split days excluded")
                reporter.record_unit_complete(output_rows=output_rows, source_events=source_events, seconds=time.perf_counter()-began)
                reporter.completed_days += 1
            schedule_by_key = {(str(item["ticker"]), str(item["local_date"])): item for item in schedule}
            for canonical, provider, day in alias_units:
                unit = f"identity:{canonical}:{provider}:{day}:{digest[:16]}"
                reporter.update(day=str(day), unit=unit, stage="identity replay")
                if unit in done:
                    reporter.skipped_units += 1; reporter.completed_days += 1
                    reporter.update(message="Already certified"); continue
                factor = schedule_by_key[(canonical, str(day))]
                day_pf = float(factor["split_day_price_factor"])
                day_sf = float(factor["split_day_size_factor"])
                reference = 1.0 if day_pf == 1.0 else split_day_reference_price(client, args, day, provider)
                if reference is None:
                    raise RuntimeError(f"{canonical}/{provider} {day} has no raw-event reference price")
                began = time.perf_counter()
                client.execute(
                    split_day_sql(
                        args, day, provider, asof, digest, reference,
                        float(factor["future_price_factor"]), float(factor["future_size_factor"]),
                        day_pf, day_sf, output_ticker=canonical,
                        build_method="event_replay_identity_alias",
                    ),
                    query_id=f"bargpt_identity_{canonical}_{provider}_{day:%Y%m%d}_{uuid.uuid4().hex}",
                )
                rows = _query_tsv(client, f"""SELECT count(),uniqExact(bucket_index),sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE ticker={sql_string(canonical)} AND local_date=toDate({sql_string(str(day))})
AND split_schedule_sha256={sql_string(digest)} AND build_method='event_replay_identity_alias'""")[0]
                output_rows, unique_rows, replay_events = (int(value) for value in rows)
                if output_rows != unique_rows or output_rows == 0 or replay_events == 0:
                    raise RuntimeError(
                        f"{unit} key/event audit failed rows={output_rows} unique={unique_rows} events={replay_events}"
                    )
                insert_manifest(
                    client, args, unit_id=unit, month=dt.date(day.year, day.month, 1),
                    method="event_replay_identity_alias", asof=asof, digest=digest,
                    source_rows=output_rows, source_events=replay_events, output_rows=output_rows,
                    message=f"Canonical {canonical} from reviewed provider ticker {provider}",
                )
                reporter.record_unit_complete(
                    output_rows=output_rows, source_events=replay_events,
                    seconds=time.perf_counter() - began,
                )
                reporter.completed_days += 1
            for ticker, day, day_pf, day_sf in split_units:
                unit = f"replay:{ticker}:{day}:{digest[:16]}"; reporter.update(day=str(day), unit=unit, stage="replaying")
                if unit in done:
                    reporter.skipped_units += 1; reporter.completed_days += 1; reporter.update(message="Already certified"); continue
                row = next(item for item in schedule if item["ticker"] == ticker and item["local_date"] == str(day))
                reference = split_day_reference_price(client, args, day, ticker)
                if reference is None:
                    source_rows, source_events = scalar_stats(client, f"SELECT count(),sum(source_event_count) FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} WHERE ticker={sql_string(ticker)} AND local_date=toDate({sql_string(str(day))})")
                    if source_rows:
                        raise RuntimeError(f"{ticker} {day} has v1 rows but no raw-event reference price")
                    insert_manifest(client, args, unit_id=unit, month=dt.date(day.year, day.month, 1), method="event_replay_split_day",
                                    asof=asof, digest=digest, source_rows=0, source_events=0, output_rows=0,
                                    message="No source events on split execution date")
                    reporter.record_unit_complete(output_rows=0, source_events=0, seconds=0); reporter.completed_days += 1; continue
                source_rows, source_events = scalar_stats(client, f"SELECT count(),sum(source_event_count) FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} WHERE ticker={sql_string(ticker)} AND local_date=toDate({sql_string(str(day))})")
                began = time.perf_counter()
                client.execute(split_day_sql(args, day, ticker, asof, digest, reference,
                                             float(row["future_price_factor"]), float(row["future_size_factor"]),
                                             day_pf, day_sf), query_id=f"bargpt_adjust_replay_{ticker}_{day:%Y%m%d}_{uuid.uuid4().hex}")
                output_rows, replay_events = scalar_stats(client, f"""SELECT count(),sum(source_event_count) FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE ticker={sql_string(ticker)} AND local_date=toDate({sql_string(str(day))}) AND split_schedule_sha256={sql_string(digest)}""")
                if output_rows != source_rows or replay_events != source_events:
                    raise RuntimeError(f"{unit} audit mismatch source rows/events={source_rows}/{source_events} target={output_rows}/{replay_events}")
                insert_manifest(client, args, unit_id=unit, month=dt.date(day.year, day.month, 1), method="event_replay_split_day",
                                asof=asof, digest=digest, source_rows=source_rows, source_events=source_events,
                                output_rows=output_rows, message=f"Raw-event replay; robust reference={reference:.8g}; execution factor={day_pf:.12g}")
                reporter.record_unit_complete(output_rows=output_rows, source_events=source_events, seconds=time.perf_counter()-began)
                reporter.completed_days += 1
            reporter.update(stage="complete", message="All linear, identity-alias, and split-day units certified")
            reporter.event("complete", message=reporter.last_message, schedule_sha256=digest)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
