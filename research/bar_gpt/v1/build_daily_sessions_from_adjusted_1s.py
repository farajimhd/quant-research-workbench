from __future__ import annotations

import argparse
import datetime as dt
import os
import time
import uuid
from pathlib import Path

from pipelines.market_sip.events.clickhouse_build_daily_session_bars import (
    SessionBuildReporter,
    _query_rows,
    create_manifest_table_sql,
    create_target_table_sql,
    date_chunks,
    query_settings,
    validate_schema,
)
from pipelines.market_sip.events.session_bar_contract import (
    FEATURE_NAMES,
    FEATURE_SPECS,
    SESSION_BAR_FEATURE_VERSION,
    SESSION_BAR_SCHEMA_VERSION,
    session_table_columns,
)
from research.bar_gpt.v1.cohort import (
    BAR_GPT_ADJUSTED_1S_TABLE,
    BAR_GPT_ADJUSTED_SIP_DAILY_MANIFEST_TABLE,
    BAR_GPT_ADJUSTED_SIP_DAILY_TABLE,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files


BUILD_VERSION = "bar_gpt_daily_sessions_sip_adjusted_v3"
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_daily_sessions_sip_adjusted")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll certified split-adjusted BarGPT 1s geometry into daily sessions.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="auto")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--source-table", default=BAR_GPT_ADJUSTED_1S_TABLE)
    parser.add_argument("--target-table", default=BAR_GPT_ADJUSTED_SIP_DAILY_TABLE)
    parser.add_argument("--manifest-table", default=BAR_GPT_ADJUSTED_SIP_DAILY_MANIFEST_TABLE)
    parser.add_argument("--identity-database", default="q_live")
    parser.add_argument("--symbol-interval-table", default="id_symbol_interval_v1")
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--allow-empty-storage-policy", action="store_true")
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--replace-range", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-threads", type=int, default=16)
    parser.add_argument("--max-memory-usage", default="64G")
    parser.add_argument("--max-bytes-before-external-group-by", default="16G")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(argv)


def _reduce_expression(name: str, reducer: str, validity: str) -> str:
    value = f"s.{quote_ident(name)}"
    valid = "1" if validity == "always" else f"s.{quote_ident(validity)} > 0"
    if reducer == "sum":
        return f"sum({value}) AS {quote_ident(name)}"
    if reducer == "max":
        return f"maxIf({value}, {valid}) AS {quote_ident(name)}" if validity != "always" else f"max({value}) AS {quote_ident(name)}"
    if reducer == "min":
        return f"minIf({value}, {valid}) AS {quote_ident(name)}"
    if reducer == "first":
        return f"argMinIf({value}, s.bar_start_us, {valid}) AS {quote_ident(name)}"
    if reducer == "last":
        return f"argMaxIf({value}, s.bar_start_us, {valid}) AS {quote_ident(name)}"
    raise ValueError(f"unsupported reducer {reducer!r}")


def insert_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    reductions = ",\n        ".join(_reduce_expression(spec.name, spec.reducer, spec.validity) for spec in FEATURE_SPECS)
    feature_select = ",\n    ".join(f"a.{quote_ident(name)}" for name in FEATURE_NAMES)
    columns = ",\n    ".join(quote_ident(name) for name, _ in session_table_columns())
    identity_db = quote_ident(args.identity_database)
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.target_table)}
(
    {columns}
)
WITH
source AS
(
    SELECT *,
           toTimeZone(fromUnixTimestamp64Micro(bar_start_us, 'UTC'), 'America/New_York') AS local_ts,
           dateDiff('second', toStartOfDay(local_ts), local_ts) AS local_second,
           multiIf(local_second < 34200, 'premarket', local_second < 57600, 'regular', 'after_hours') AS session_kind
    FROM
    (
        SELECT * FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} FINAL
        PREWHERE local_date >= toDate({sql_string(start.isoformat())}) AND local_date < toDate({sql_string(end.isoformat())})
    ) AS raw
    WHERE local_second >= 14400 AND local_second < 72000
),
aggregated AS
(
    SELECT
        local_date AS session_date,
        session_kind,
        ticker AS canonical_ticker,
        source_ticker,
        min(source_first_ordinal) AS source_first_ordinal,
        max(source_last_ordinal) AS source_last_ordinal,
        min(source_first_timestamp_us) AS source_first_timestamp_us,
        max(source_last_timestamp_us) AS source_last_timestamp_us,
        any(adjustment_asof_date) AS adjustment_asof_date,
        any(split_schedule_sha256) AS split_schedule_sha256,
        {reductions}
    FROM source AS s
    GROUP BY session_date, session_kind, canonical_ticker, source_ticker
),
active_symbols AS
(
    SELECT session_date, canonical_ticker, source_ticker,
           any(adjustment_asof_date) AS adjustment_asof_date,
           any(split_schedule_sha256) AS split_schedule_sha256
    FROM aggregated
    GROUP BY session_date, canonical_ticker, source_ticker
),
session_grid AS
(
    SELECT session_date, canonical_ticker, source_ticker, adjustment_asof_date, split_schedule_sha256, session_kind
    FROM active_symbols
    ARRAY JOIN ['premarket', 'regular', 'after_hours'] AS session_kind
),
intervals AS
(
    SELECT provider_entity_key, security_id, listing_id, ticker_normalized,
           valid_from_date, valid_to_date_exclusive, is_current, observed_at_utc
    FROM {identity_db}.{quote_ident(args.symbol_interval_table)} FINAL
    WHERE is_deleted=0 AND mapping_status='mapped'
),
identity_starts AS
(
    SELECT ticker_normalized,valid_from_date,
           if(uniqExact(provider_entity_key)=1 OR countIf(is_current=1)=1,1,uniqExact(provider_entity_key)) AS resolved_count,
           argMax(security_id,tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_security_id,
           argMax(listing_id,tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_listing_id,
           argMax(valid_to_date_exclusive,tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_valid_to_date_exclusive
    FROM intervals
    GROUP BY ticker_normalized,valid_from_date
),
identity_matches AS
(
    SELECT a.session_date, a.source_ticker,
           if(i.valid_from_date<=a.session_date AND (i.resolved_valid_to_date_exclusive IS NULL OR a.session_date<i.resolved_valid_to_date_exclusive),
              i.resolved_count,0) AS match_count,
           if(match_count>0,i.resolved_security_id,'') AS security_id,
           if(match_count>0,i.resolved_listing_id,'') AS listing_id
    FROM active_symbols AS a
    ASOF LEFT JOIN identity_starts AS i
      ON i.ticker_normalized=a.source_ticker AND a.session_date>=i.valid_from_date
)
SELECT
    toUInt16({SESSION_BAR_SCHEMA_VERSION}) AS schema_version,
    {sql_string(SESSION_BAR_FEATURE_VERSION)} AS feature_version,
    g.session_date,
    g.session_kind,
    g.source_ticker,
    g.canonical_ticker,
    if(m.match_count=1, nullIf(m.security_id, ''), CAST(NULL, 'Nullable(String)')) AS security_id,
    if(m.match_count=1, nullIf(m.listing_id, ''), CAST(NULL, 'Nullable(String)')) AS listing_id,
    multiIf(m.match_count=0, 'canonical_from_adjusted_1s', m.match_count=1, 'mapped', 'ambiguous_source_ticker') AS identity_status,
    'bar_gpt_1s_split_adjusted_v2_rollup' AS source_contract,
    toUInt8(1) AS adjusted,
    g.adjustment_asof_date,
    g.split_schedule_sha256,
    toUInt64(toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(g.session_date), multiIf(g.session_kind='premarket',' 04:00:00',g.session_kind='regular',' 09:30:00',' 16:00:00')), 6, 'America/New_York'), 'UTC'))) AS bar_start_us,
    toUInt64(toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(g.session_date), multiIf(g.session_kind='premarket',' 09:30:00',g.session_kind='regular',' 16:00:00',' 20:00:00')), 6, 'America/New_York'), 'UTC'))) AS bar_end_us,
    bar_end_us AS available_at_us,
    a.source_first_ordinal,
    a.source_last_ordinal,
    a.source_first_timestamp_us,
    a.source_last_timestamp_us,
    {feature_select},
    now64(3, 'UTC') AS built_at
FROM session_grid AS g
ANY LEFT JOIN aggregated AS a
    ON a.session_date=g.session_date AND a.session_kind=g.session_kind
   AND a.canonical_ticker=g.canonical_ticker AND a.source_ticker=g.source_ticker
ANY LEFT JOIN identity_matches AS m
    ON m.source_ticker=g.source_ticker AND m.session_date=g.session_date
{query_settings(args)}
"""


def resolve_range(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    start = dt.date.fromisoformat(args.start_date)
    if args.end_date == "auto":
        rows = _query_rows(client, f"SELECT addDays(max(local_date),1) FROM {quote_ident(args.database)}.{quote_ident(args.source_table)}")
        if not rows or rows[0][0] in {"", "\\N", "1970-01-01"}:
            raise RuntimeError("adjusted one-second source has no coverage")
        end = dt.date.fromisoformat(rows[0][0])
    else:
        end = dt.date.fromisoformat(args.end_date)
    if end <= start:
        raise ValueError("end-date must be later than start-date")
    return start, end


def _unit(start: dt.date, end: dt.date) -> str:
    return f"adjusted:{start.isoformat()}__{end.isoformat()}"


def _write_manifest(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date, status: str,
                    *, rows: int = 0, events: int = 0, mapped: int = 0, unmapped: int = 0, message: str = "") -> None:
    client.execute(f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.manifest_table)} VALUES
({sql_string(args.target_table)}, {sql_string(_unit(start,end))}, toDate({sql_string(start.isoformat())}),
 toDate({sql_string(end.isoformat())}), {sql_string(status)}, {sql_string(BUILD_VERSION)},
 {sql_string(SESSION_BAR_FEATURE_VERSION)}, toUInt64({rows}), toUInt64({events}), toUInt64({mapped}),
 toUInt64({unmapped}), {sql_string(message)}, now64(3,'UTC'))
""")


def _completed(client: ClickHouseHttpClient, args: argparse.Namespace) -> set[str]:
    return {row[0] for row in _query_rows(client, f"""
SELECT unit_id FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name={sql_string(args.target_table)} AND status='complete'
  AND build_version={sql_string(BUILD_VERSION)} AND feature_version={sql_string(SESSION_BAR_FEATURE_VERSION)}
""")}


def build_chunk(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> tuple[int, int, int, int, float]:
    began = time.perf_counter()
    _write_manifest(client, args, start, end, "started")
    if args.replace_range:
        client.execute(
            f"ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.target_table)} DELETE "
            f"WHERE session_date>=toDate({sql_string(start.isoformat())}) AND session_date<toDate({sql_string(end.isoformat())})"
            + query_settings(args, mutation=True)
        )
    query_id = f"bargpt_daily_adjusted_{start}_{uuid.uuid4().hex}"
    try:
        client.execute(insert_sql(args, start, end), query_id=query_id)
    except KeyboardInterrupt:
        try:
            client.execute(f"KILL QUERY WHERE query_id={sql_string(query_id)} ASYNC")
        except Exception:
            pass
        raise
    source = _query_rows(client, f"""
SELECT sum(source_event_count) FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} FINAL
WHERE local_date>=toDate({sql_string(start.isoformat())}) AND local_date<toDate({sql_string(end.isoformat())})
""")
    expected_events = int(source[0][0] or 0)
    audit = _query_rows(client, f"""
SELECT count(),uniqExact(tuple(source_ticker,session_date,session_kind)),sum(source_event_count),
       countIf(identity_status='mapped'),countIf(identity_status!='mapped'),countIf(adjusted!=1),
       uniqExact(tuple(adjustment_asof_date,split_schedule_sha256)),countIf(available_at_us!=bar_end_us)
FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE session_date>=toDate({sql_string(start.isoformat())}) AND session_date<toDate({sql_string(end.isoformat())})
""")[0]
    rows, unique, events, mapped, unmapped, bad_adjusted, bases, bad_available = (int(value or 0) for value in audit)
    expected_bases = 1 if rows else 0
    if rows != unique or events != expected_events or bad_adjusted or bases != expected_bases or bad_available:
        raise RuntimeError(
            f"adjusted daily chunk audit failed [{start},{end}): rows={rows}/{unique} events={events}/{expected_events} "
            f"bad_adjusted={bad_adjusted} bases={bases} bad_available={bad_available}"
        )
    elapsed = time.perf_counter() - began
    _write_manifest(client, args, start, end, "complete", rows=rows, events=events, mapped=mapped, unmapped=unmapped,
                    message=f"certified from adjusted one-second authority in {elapsed:.3f}s")
    return rows, events, mapped, unmapped, elapsed


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args(argv)
    if not args.storage_policy and not args.allow_empty_storage_policy:
        raise RuntimeError("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required")
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, args.clickhouse_password)
    start, end = resolve_range(client, args)
    chunks = date_chunks(start, end, args.chunk_days)
    if not args.execute:
        print(create_target_table_sql(args))
        print(create_manifest_table_sql(args))
        print(insert_sql(args, chunks[0][0], chunks[0][1]))
        print(f"PLAN source={args.database}.{args.source_table} range=[{start},{end}) chunks={len(chunks)}")
        return 0
    args.runtime_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.runtime_root / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "build.jsonl"
    client.execute(create_target_table_sql(args))
    client.execute(create_manifest_table_sql(args))
    validate_schema(client, args)
    done = _completed(client, args)
    with SessionBuildReporter(args, len(chunks), report_path) as reporter:
        reporter.event("build_started", message=f"adjusted one-second rollup range=[{start},{end})")
        for left, right in chunks:
            reporter.current = f"[{left}, {right})"
            if _unit(left, right) in done:
                reporter.skipped += 1
                reporter.event("chunk_skipped", message=f"already certified [{left},{right})")
                continue
            rows, events, mapped, unmapped, seconds = build_chunk(client, args, left, right)
            reporter.completed += 1
            reporter.rows += rows
            reporter.source_events += events
            reporter.message = f"certified {rows:,} rows from {events:,} events in {seconds:.1f}s"
            reporter.event("chunk_complete", rows=rows, source_events=events, mapped_rows=mapped,
                           unmapped_rows=unmapped, seconds=seconds, message=reporter.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
