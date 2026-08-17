from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass

from pipelines.market_sip.benchmarks.clickhouse_compact_schema_codec_benchmark import (
    TRADE_SCHEMA_STRING,
    price_int_sql,
    price_precision_clipped_sql,
    scale_code_sql,
    tape_code_sql,
)
from pipelines.market_sip.events.clickhouse_build_unified_events import events_table_for_source_date
from research.mlops.clickhouse import mergetree_settings_sql, quote_ident, sql_string


CORRECTION_OVERLAY_VERSION = "bar_gpt_trade_correction_causal_overlay_v1"
DEFAULT_CORRECTION_RECORD_TABLE = "bar_gpt_trade_correction_records_v1"
DEFAULT_CORRECTION_RECORD_MANIFEST_TABLE = "bar_gpt_trade_correction_record_manifest_v1"
DEFAULT_CORRECTION_OVERLAY_TABLE = "bar_gpt_trade_correction_overlay_v1"
# Corrections are normally same-tape-day records. One UTC day on either side
# covers the New York session's UTC-date boundary; any later unmatched record
# fails certification instead of being silently omitted.
PAIR_HALO_DAYS = 1


@dataclass(frozen=True, slots=True)
class SourceFileIdentity:
    source_date: dt.date
    path_win: str
    path_ch: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class CorrectionDayStats:
    source_date: dt.date
    record_rows: int
    pair_count: int
    overlay_rows: int


def _query_rows(client, sql: str) -> list[list[str]]:
    text = client.query_tsv(sql).strip()
    return [line.split("\t") for line in text.splitlines()] if text else []


def _execute(client, sql: str, *, query_id: str | None = None) -> str:
    return client.execute(sql, query_id=query_id)


def _clickhouse_source_path(path_win: str) -> str:
    normalized = str(path_win).strip().replace("\\", "/")
    marker = "/market-data/"
    lower = normalized.lower()
    position = lower.find(marker)
    if position < 0:
        raise RuntimeError(f"trade source path is outside the certified market-data root: {path_win!r}")
    return "/mnt/d/market-data/" + normalized[position + len(marker) :]


def create_record_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.correction_record_table)}
(
    source_file_date Date,
    event_date Date,
    source_file_size UInt64,
    source_file_mtime_ns UInt64,
    ticker LowCardinality(String),
    trade_id String,
    sequence_number UInt32,
    correction UInt8,
    sip_timestamp_ns UInt64,
    sip_timestamp_us UInt64,
    participant_timestamp_ns UInt64,
    event_meta UInt8,
    price_primary_int UInt32,
    size_primary Float32,
    exchange_primary UInt8,
    condition_token_1 UInt8,
    condition_token_2 UInt8,
    condition_token_3 UInt8,
    condition_token_4 UInt8,
    condition_token_5 UInt8,
    built_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(source_file_date)
ORDER BY (source_file_date, ticker, trade_id, sequence_number, correction, sip_timestamp_ns)
{mergetree_settings_sql(args.storage_policy)}
"""


def create_record_manifest_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.correction_record_manifest_table)}
(
    source_date Date,
    source_file_path String,
    source_file_size UInt64,
    source_file_mtime_ns UInt64,
    build_version LowCardinality(String),
    status LowCardinality(String),
    correction_01_rows UInt64,
    correction_12_rows UInt64,
    message String,
    updated_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(source_date)
ORDER BY source_date
{mergetree_settings_sql(args.storage_policy)}
"""


def create_overlay_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.correction_overlay_table)}
(
    source_date Date,
    ticker LowCardinality(String),
    ordinal UInt64,
    phase UInt8,
    pair_id UInt64,
    replacement_event_meta UInt8,
    replacement_price_primary_int UInt32,
    replacement_size_primary Float32,
    replacement_exchange_primary UInt8,
    replacement_condition_token_1 UInt8,
    replacement_condition_token_2 UInt8,
    replacement_condition_token_3 UInt8,
    replacement_condition_token_4 UInt8,
    replacement_condition_token_5 UInt8,
    original_available_at_us UInt64,
    correction_available_at_us UInt64,
    source_file_size UInt64,
    source_file_mtime_ns UInt64,
    build_version LowCardinality(String),
    built_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, ticker, ordinal)
{mergetree_settings_sql(args.storage_policy)}
"""


def staged_records_insert_sql(args: argparse.Namespace, source: SourceFileIdentity) -> str:
    price = "toFloat64OrZero(price)"
    encoded_price = price_int_sql(price)
    scale = scale_code_sql(price)
    valid_price = f"({price} > 0 AND {encoded_price} > 0 AND NOT {price_precision_clipped_sql(price)})"
    token_map = (
        f"(SELECT groupArray((modifier_int, toUInt8(token_id))) "
        f"FROM {quote_ident(args.database)}.{quote_ident(args.condition_reference_table)} "
        "WHERE source_family='trade_conditions' AND is_join_canonical=1)"
    )
    codes = "arrayMap(slot -> toInt16OrZero(arrayElement(splitByChar(',', conditions), slot)), range(1, 6))"
    tokens = (
        "arrayMap(code -> tupleElement(arrayFirst(item -> tupleElement(item, 1) = code, trade_token_map), 2), "
        f"{codes})"
    )
    target = f"{quote_ident(args.database)}.{quote_ident(args.correction_record_table)}"
    return f"""
INSERT INTO {target}
WITH {token_map} AS trade_token_map,
     {tokens} AS condition_tokens,
     {scale} AS encoded_scale,
     {tape_code_sql('tape')} AS encoded_tape
SELECT
    toDate({sql_string(source.source_date.isoformat())}),
    toDate(fromUnixTimestamp64Micro(toInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)), 'UTC')),
    toUInt64({source.size}),
    toUInt64({source.mtime_ns}),
    upper(ticker),
    id,
    toUInt32OrZero(sequence_number),
    toUInt8(toInt16OrZero(correction)),
    toUInt64OrZero(sip_timestamp),
    toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)),
    toUInt64OrZero(participant_timestamp),
    toUInt8(1 + bitShiftLeft(if({valid_price}, encoded_scale, 0), 1) + bitShiftLeft(encoded_tape, 3)),
    toUInt32(if({valid_price}, {encoded_price}, 0)),
    toFloat32(if(toFloat64OrZero(size) > 0, toFloat64OrZero(size), 0)),
    toUInt8OrZero(exchange),
    toUInt8(arrayElement(condition_tokens, 1)),
    toUInt8(arrayElement(condition_tokens, 2)),
    toUInt8(arrayElement(condition_tokens, 3)),
    toUInt8(arrayElement(condition_tokens, 4)),
    toUInt8(arrayElement(condition_tokens, 5)),
    now64(3, 'UTC')
FROM file({sql_string(source.path_ch)}, 'CSVWithNames', {sql_string(TRADE_SCHEMA_STRING)})
WHERE ticker != ''
  AND toUInt64OrZero(sip_timestamp) > 0
  AND toUInt32OrZero(sequence_number) > 0
  AND toInt16OrZero(correction) IN (1, 12)
SETTINGS max_threads={int(args.max_threads)}, max_memory_usage={int(args.max_memory_usage_bytes)}
"""


def _pair_cte(args: argparse.Namespace, day: dt.date) -> str:
    left = day - dt.timedelta(days=PAIR_HALO_DAYS)
    right = day + dt.timedelta(days=PAIR_HALO_DAYS + 1)
    table = f"{quote_ident(args.database)}.{quote_ident(args.correction_record_table)}"
    fields = (
        "event_date,event_meta,price_primary_int,size_primary,exchange_primary,"
        "condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5,"
        "sip_timestamp_ns,sip_timestamp_us,source_file_size,source_file_mtime_ns"
    ).split(",")
    aggregates: list[str] = []
    for correction, suffix in ((1, "01"), (12, "12")):
        for field in fields:
            aggregates.append(
                f"argMaxIf({field}, sip_timestamp_ns, correction={correction}) AS {field}_{suffix}"
            )
    aggregate_sql = ",\n        ".join(aggregates)
    return f"""
pairs AS
(
    SELECT
        ticker,
        trade_id,
        sequence_number,
        countIf(correction=1) AS count_01,
        countIf(correction=12) AS count_12,
        {aggregate_sql},
        cityHash64(ticker, trade_id, sequence_number, sip_timestamp_ns_01, sip_timestamp_ns_12) AS pair_id
    FROM {table} FINAL
    WHERE source_file_date >= toDate({sql_string(left.isoformat())})
      AND source_file_date < toDate({sql_string(right.isoformat())})
    GROUP BY ticker, trade_id, sequence_number
    HAVING count_01=1 AND count_12=1 AND sip_timestamp_ns_12 > sip_timestamp_ns_01
),
expected AS
(
    SELECT
        event_date_01 AS source_date,
        ticker,
        pair_id,
        toUInt8(1) AS phase,
        sip_timestamp_us_01 AS target_sip_timestamp_us,
        event_meta_01 AS current_event_meta,
        price_primary_int_01 AS current_price_primary_int,
        size_primary_01 AS current_size_primary,
        exchange_primary_01 AS current_exchange_primary,
        condition_token_1_01 AS current_condition_token_1,
        condition_token_2_01 AS current_condition_token_2,
        condition_token_3_01 AS current_condition_token_3,
        condition_token_4_01 AS current_condition_token_4,
        condition_token_5_01 AS current_condition_token_5,
        event_meta_12 AS replacement_event_meta,
        price_primary_int_12 AS replacement_price_primary_int,
        size_primary_12 AS replacement_size_primary,
        exchange_primary_12 AS replacement_exchange_primary,
        condition_token_1_12 AS replacement_condition_token_1,
        condition_token_2_12 AS replacement_condition_token_2,
        condition_token_3_12 AS replacement_condition_token_3,
        condition_token_4_12 AS replacement_condition_token_4,
        condition_token_5_12 AS replacement_condition_token_5,
        sip_timestamp_us_01 AS original_available_at_us,
        sip_timestamp_us_12 AS correction_available_at_us,
        source_file_size_01 AS source_file_size,
        source_file_mtime_ns_01 AS source_file_mtime_ns
    FROM pairs WHERE event_date_01=toDate({sql_string(day.isoformat())})
    UNION ALL
    SELECT
        event_date_12,
        ticker,
        pair_id,
        toUInt8(2),
        sip_timestamp_us_12,
        event_meta_12,
        price_primary_int_12,
        size_primary_12,
        exchange_primary_12,
        condition_token_1_12,
        condition_token_2_12,
        condition_token_3_12,
        condition_token_4_12,
        condition_token_5_12,
        event_meta_01,
        price_primary_int_01,
        size_primary_01,
        exchange_primary_01,
        condition_token_1_01,
        condition_token_2_01,
        condition_token_3_01,
        condition_token_4_01,
        condition_token_5_01,
        sip_timestamp_us_01,
        sip_timestamp_us_12,
        source_file_size_12,
        source_file_mtime_ns_12
    FROM pairs WHERE event_date_12=toDate({sql_string(day.isoformat())})
)
"""


def overlay_insert_sql(args: argparse.Namespace, day: dt.date) -> str:
    events = f"{quote_ident(args.database)}.{quote_ident(events_table_for_source_date(args.events_table_base, day))}"
    target = f"{quote_ident(args.database)}.{quote_ident(args.correction_overlay_table)}"
    pair_cte = _pair_cte(args, day)
    return f"""
INSERT INTO {target}
WITH
{pair_cte}
SELECT
    x.source_date,
    x.ticker,
    e.ordinal,
    x.phase,
    x.pair_id,
    x.replacement_event_meta,
    x.replacement_price_primary_int,
    x.replacement_size_primary,
    x.replacement_exchange_primary,
    x.replacement_condition_token_1,
    x.replacement_condition_token_2,
    x.replacement_condition_token_3,
    x.replacement_condition_token_4,
    x.replacement_condition_token_5,
    x.original_available_at_us,
    x.correction_available_at_us,
    x.source_file_size,
    x.source_file_mtime_ns,
    {sql_string(CORRECTION_OVERLAY_VERSION)},
    now64(3, 'UTC')
FROM {events} AS e
INNER JOIN expected AS x
    ON x.ticker=e.ticker
   AND e.sip_timestamp_us=x.target_sip_timestamp_us
   AND e.event_meta=x.current_event_meta
   AND e.price_primary_int=x.current_price_primary_int
   AND e.size_primary=x.current_size_primary
   AND e.exchange_primary=x.current_exchange_primary
   AND e.condition_token_1=x.current_condition_token_1
   AND e.condition_token_2=x.current_condition_token_2
   AND e.condition_token_3=x.current_condition_token_3
   AND e.condition_token_4=x.current_condition_token_4
   AND e.condition_token_5=x.current_condition_token_5
PREWHERE e.event_date=toDate({sql_string(day.isoformat())})
SETTINGS max_threads={int(args.max_threads)}, max_memory_usage={int(args.max_memory_usage_bytes)}
"""


def overlaid_event_source_sql(args: argparse.Namespace, day: dt.date, tickers: tuple[str, ...]) -> str:
    source_dates = (day, day + dt.timedelta(days=1))
    event_tables = tuple(dict.fromkeys(events_table_for_source_date(args.events_table_base, value) for value in source_dates))
    if len(event_tables) == 1:
        events = f"{quote_ident(args.database)}.{quote_ident(event_tables[0])}"
    else:
        pattern = "^(" + "|".join(event_tables) + ")$"
        events = f"merge({sql_string(args.database)}, {sql_string(pattern)})"
    overlay = f"{quote_ident(args.database)}.{quote_ident(args.correction_overlay_table)}"
    ticker_filter = "" if not tickers else " AND e.ticker IN (" + ", ".join(sql_string(t) for t in tickers) + ")"
    first_date = sql_string(source_dates[0].isoformat())
    last_date = sql_string((source_dates[-1] + dt.timedelta(days=1)).isoformat())
    return f"""
(
    SELECT
        e.ticker,
        e.ordinal,
        if(o.overlay_present=1,o.replacement_event_meta,e.event_meta) AS event_meta,
        e.sip_timestamp_us,
        if(o.overlay_present=1,o.replacement_price_primary_int,e.price_primary_int) AS price_primary_int,
        e.price_secondary_int,
        if(o.overlay_present=1,o.replacement_size_primary,e.size_primary) AS size_primary,
        e.size_secondary,
        if(o.overlay_present=1,o.replacement_exchange_primary,e.exchange_primary) AS exchange_primary,
        e.exchange_secondary,
        if(o.overlay_present=1,o.replacement_condition_token_1,e.condition_token_1) AS condition_token_1,
        if(o.overlay_present=1,o.replacement_condition_token_2,e.condition_token_2) AS condition_token_2,
        if(o.overlay_present=1,o.replacement_condition_token_3,e.condition_token_3) AS condition_token_3,
        if(o.overlay_present=1,o.replacement_condition_token_4,e.condition_token_4) AS condition_token_4,
        if(o.overlay_present=1,o.replacement_condition_token_5,e.condition_token_5) AS condition_token_5,
        e.event_date,
        o.phase AS trade_correction_phase
    FROM {events} AS e
    LEFT JOIN
    (
        SELECT *, toUInt8(1) AS overlay_present
        FROM {overlay} FINAL
        WHERE source_date>=toDate({first_date}) AND source_date<toDate({last_date})
    ) AS o ON o.source_date=e.event_date AND o.ticker=e.ticker AND o.ordinal=e.ordinal
    PREWHERE e.event_date>=toDate({first_date}) AND e.event_date<toDate({last_date}){ticker_filter}
)
"""


class TradeCorrectionOverlayAuthority:
    def __init__(self, client, args: argparse.Namespace) -> None:
        self.client = client
        self.args = args
        self.sources: dict[dt.date, SourceFileIdentity] = {}
        self.completed_sources: dict[dt.date, tuple[int, int]] = {}

    def ensure_tables(self) -> None:
        _execute(self.client, create_record_table_sql(self.args))
        _execute(self.client, create_record_manifest_table_sql(self.args))
        _execute(self.client, create_overlay_table_sql(self.args))
        rows = _query_rows(
            self.client,
            f"SELECT source_date,source_file_size,source_file_mtime_ns FROM "
            f"{quote_ident(self.args.database)}.{quote_ident(self.args.correction_record_manifest_table)} FINAL "
            f"WHERE status='complete' AND build_version={sql_string(CORRECTION_OVERLAY_VERSION)}",
        )
        self.completed_sources = {
            dt.date.fromisoformat(row[0]): (int(row[1]), int(row[2])) for row in rows
        }

    def load_sources(self, start: dt.date, end: dt.date) -> None:
        left = start - dt.timedelta(days=PAIR_HALO_DAYS)
        right = end + dt.timedelta(days=PAIR_HALO_DAYS)
        rows = _query_rows(
            self.client,
            f"""
SELECT source_date,argMax(trade_file_path,updated_at),argMax(trade_file_size,updated_at),argMax(trade_file_mtime_ns,updated_at)
FROM {quote_ident(self.args.database)}.{quote_ident(self.args.source_day_stats_table)}
WHERE source_date>=toDate({sql_string(left.isoformat())}) AND source_date<toDate({sql_string(right.isoformat())})
GROUP BY source_date
HAVING argMax(trade_file_size,updated_at)>0 AND argMax(trade_file_path,updated_at)!=''
ORDER BY source_date
""",
        )
        self.sources = {
            dt.date.fromisoformat(row[0]): SourceFileIdentity(
                source_date=dt.date.fromisoformat(row[0]),
                path_win=row[1],
                path_ch=_clickhouse_source_path(row[1]),
                size=int(row[2]),
                mtime_ns=int(row[3]),
            )
            for row in rows
        }

    def ensure_source_day(self, day: dt.date) -> None:
        source = self.sources.get(day)
        if source is None:
            return
        identity = (source.size, source.mtime_ns)
        if self.completed_sources.get(day) == identity:
            return
        db = quote_ident(self.args.database)
        records = quote_ident(self.args.correction_record_table)
        _execute(
            self.client,
            f"ALTER TABLE {db}.{records} DELETE WHERE source_file_date=toDate({sql_string(day.isoformat())}) "
            "SETTINGS mutations_sync=2",
        )
        _execute(self.client, staged_records_insert_sql(self.args, source))
        rows = _query_rows(
            self.client,
            f"SELECT countIf(correction=1),countIf(correction=12) FROM {db}.{records} FINAL "
            f"WHERE source_file_date=toDate({sql_string(day.isoformat())})",
        )
        count_01, count_12 = (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)
        manifest = quote_ident(self.args.correction_record_manifest_table)
        _execute(
            self.client,
            f"INSERT INTO {db}.{manifest} VALUES ("
            f"toDate({sql_string(day.isoformat())}),{sql_string(source.path_win)},toUInt64({source.size}),"
            f"toUInt64({source.mtime_ns}),{sql_string(CORRECTION_OVERLAY_VERSION)},'complete',"
            f"toUInt64({count_01}),toUInt64({count_12}),"
            f"{sql_string('staged correction records')},now64(3,'UTC'))",
        )
        self.completed_sources[day] = identity

    def prepare_day(self, day: dt.date) -> CorrectionDayStats:
        for offset in range(-PAIR_HALO_DAYS, PAIR_HALO_DAYS + 1):
            self.ensure_source_day(day + dt.timedelta(days=offset))
        db = quote_ident(self.args.database)
        records = quote_ident(self.args.correction_record_table)
        day_sql = sql_string(day.isoformat())
        record_rows = int(
            _query_rows(
                self.client,
                f"SELECT count() FROM {db}.{records} FINAL WHERE event_date=toDate({day_sql})",
            )[0][0]
        )
        pair_cte = _pair_cte(self.args, day)
        audit_rows = _query_rows(
            self.client,
            f"""
WITH {pair_cte}
SELECT
    (SELECT count() FROM expected),
    (SELECT count() FROM {db}.{records} FINAL WHERE event_date=toDate({day_sql})),
    (SELECT uniqExact(pair_id) FROM expected)
""",
        )
        expected_rows, observed_records, pair_count = map(int, audit_rows[0])
        if expected_rows != observed_records:
            raise RuntimeError(
                f"trade correction pair audit failed for {day}: staged_records={observed_records} "
                f"paired_record_sides={expected_rows}; increase pair halo or inspect duplicate/missing 01/12 records"
            )
        overlay = quote_ident(self.args.correction_overlay_table)
        _execute(
            self.client,
            f"ALTER TABLE {db}.{overlay} DELETE WHERE source_date=toDate({day_sql}) "
            "SETTINGS mutations_sync=2",
        )
        _execute(self.client, overlay_insert_sql(self.args, day))
        overlay_rows = int(
            _query_rows(
                self.client,
                f"SELECT count() FROM {db}.{overlay} FINAL WHERE source_date=toDate({day_sql}) "
                f"AND build_version={sql_string(CORRECTION_OVERLAY_VERSION)}",
            )[0][0]
        )
        if overlay_rows != expected_rows:
            raise RuntimeError(
                f"trade correction compact mapping failed for {day}: expected={expected_rows} mapped={overlay_rows}"
            )
        return CorrectionDayStats(day, record_rows, pair_count, overlay_rows)
