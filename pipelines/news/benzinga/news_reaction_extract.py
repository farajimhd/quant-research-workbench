from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.news_reaction_phrase_dictionary import (  # noqa: E402
    PHRASE_DICTIONARY_VERSION,
    PHRASE_RULES,
    PhraseRule,
)
from pipelines.news.benzinga.news_reaction_progress import NewsReactionProgress  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


EASTERN = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
CALENDAR_VERSION = "xnys_pandas_market_calendars_v1"
LABEL_VERSION = "news_reaction_trade_labels_v2"
STATS_VERSION = "news_phrase_trade_reaction_stats_v2"
MULTISEARCH_NEEDLE_LIMIT = 255
HORIZONS: tuple[tuple[str, str, int], ...] = (
    ("1m", "fixed", 60),
    ("5m", "fixed", 5 * 60),
    ("10m", "fixed", 10 * 60),
    ("30m", "fixed", 30 * 60),
    ("1h", "fixed", 60 * 60),
    ("2h", "fixed", 2 * 60 * 60),
    ("3h", "fixed", 3 * 60 * 60),
    ("premarket_close", "session_boundary", 0),
    ("regular_close", "session_boundary", 0),
    ("extended_close", "session_boundary", 0),
)


@dataclass(frozen=True, slots=True)
class CalendarRow:
    calendar_date: str
    is_session: int
    current_session_date: str | None
    current_premarket_start_utc: str | None
    current_regular_open_utc: str | None
    current_regular_close_utc: str | None
    current_extended_close_utc: str | None
    next_session_date: str
    next_premarket_start_utc: str
    next_regular_open_utc: str
    next_regular_close_utc: str
    next_extended_close_utc: str
    calendar_version: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CoverageAudit:
    news_ticker_months: int
    covered_ticker_months: int
    missing_ticker_months: int
    missing_sample: tuple[str, ...]
    bar_min_date: str
    bar_max_date: str


@dataclass(frozen=True, slots=True)
class ChunkResult:
    stage: str
    start_date: str
    end_date_exclusive: str
    inserted_rows: int
    elapsed_seconds: float


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic news phrase-presence facts, causal post-news reaction labels, "
            "and phrase/reaction reference statistics in ClickHouse."
        )
    )
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2027-01-01", help="Exclusive UTC publication-date bound; defaults through the 2026 holdout year.")
    parser.add_argument("--stats-start-date", default="2019-01-01", help="Inclusive training bound for phrase probabilities.")
    parser.add_argument("--stats-end-date", default="2026-01-01", help="Exclusive training bound; 2026 labels remain held out by default.")
    parser.add_argument("--stages", default="calendar,dictionary,features,reactions,stats")
    parser.add_argument("--execute", action="store_true", help="Create and populate tables. Without this, print and validate the plan.")
    parser.add_argument("--allow-partial-bar-coverage", action="store_true", help="Permit validation runs against incomplete bars; missing labels remain explicit and are excluded from statistics.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--replace-existing", action="store_true", help="Delete the selected version/date slice before rebuilding it.")
    parser.add_argument("--feature-chunk-months", type=int, default=1)
    parser.add_argument("--reaction-chunk-days", type=int, default=1)
    parser.add_argument("--resolution-us", type=int, default=1_000_000)
    parser.add_argument("--benchmark-ticker", default="SPY")
    parser.add_argument("--active-anchor-max-age-seconds", type=int, default=60)
    parser.add_argument("--target-max-age-seconds", type=int, default=60)
    parser.add_argument("--max-threads", type=int, default=24)
    parser.add_argument("--max-memory-usage", default="0")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=2.0)
    parser.add_argument("--progress-log-lines", type=int, default=8)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--news-database", default="q_live")
    parser.add_argument("--market-database", default="market_sip_compact")
    parser.add_argument("--normalized-table", default="benzinga_news_normalized_v1")
    parser.add_argument("--ticker-table", default="benzinga_news_ticker_v1")
    parser.add_argument("--bars-table", default="intraday_base_bars_by_time_ticker")
    parser.add_argument("--calendar-table", default="news_reaction_calendar_v1")
    parser.add_argument("--dictionary-table", default="news_phrase_dictionary_v1")
    parser.add_argument("--features-table", default="news_language_features_v1")
    parser.add_argument("--reactions-table", default="news_reaction_labels_v1")
    parser.add_argument("--stats-table", default="news_phrase_reaction_stats_v1")
    parser.add_argument("--status-table", default="news_reaction_build_status_v1")
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root", default="D:/market-data/prepared/news_reaction_labels")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args(argv)
    validate_args(args)
    if not args.clickhouse_url:
        args.clickhouse_url = default_clickhouse_url()
    if not args.user:
        args.user = default_clickhouse_user()
    if not args.password:
        args.password = default_clickhouse_password()

    stages = parse_stages(args.stages)
    run_id = dt.datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    print_header(args, run_id, run_root, stages)
    feature_chunks = list(month_chunks(date_arg(args.start_date), date_arg(args.end_date), args.feature_chunk_months))
    reaction_chunks = list(day_chunks(date_arg(args.start_date), date_arg(args.end_date), args.reaction_chunk_days))
    stage_totals = [("preflight", 3 if args.execute else 2)]
    stage_totals.extend(
        (stage, len(feature_chunks) if stage == "features" else len(reaction_chunks) if stage == "reactions" else 1)
        for stage in stages
    )
    if args.execute:
        stage_totals.append(("audit", 1))
    reporter = NewsReactionProgress(
        stage_totals=stage_totals,
        run_id=run_id,
        run_root=str(run_root),
        layout=args.progress_layout,
        refresh_per_second=args.progress_refresh_per_second,
        log_lines=args.progress_log_lines,
    )
    results: list[ChunkResult] = []
    coverage: CoverageAudit | None = None
    with reporter:
        reporter.stage_start("preflight")
        ensure_sources(client, args)
        reporter.unit_done("preflight", "source schemas", status="complete")
        coverage = audit_bar_coverage(client, args, reporter)
        reporter.message(
            "trade-bar coverage "
            f"covered={coverage.covered_ticker_months:,}/{coverage.news_ticker_months:,} "
            f"missing={coverage.missing_ticker_months:,} range={coverage.bar_min_date}:{coverage.bar_max_date} "
            f"sample={','.join(coverage.missing_sample[:5]) or '-'}"
        )
        reporter.unit_done("preflight", "trade-bar coverage", status="complete")
        if coverage.missing_ticker_months and "reactions" in stages and not args.allow_partial_bar_coverage:
            raise SystemExit(
                "Reaction extraction stopped: canonical 1-second trade-bar coverage is incomplete for "
                f"{coverage.missing_ticker_months:,} news ticker-months. Build missing bars first or use "
                "--allow-partial-bar-coverage only for an explicitly partial validation run."
            )

        if not args.execute:
            reporter.message(
                f"plan validated feature_chunks={len(feature_chunks):,} reaction_chunks={len(reaction_chunks):,}; "
                "pass --execute to populate tables"
            )
            write_manifest(run_root, args, run_id, stages, coverage, [])
            reporter.finish("plan_validated")
            return 0

        ensure_target_tables(client, args)
        reporter.unit_done("preflight", "target schemas", status="complete")
        if "calendar" in stages:
            replace_calendar(client, args, reporter)
        if "dictionary" in stages:
            replace_dictionary(client, args, reporter)
        if "features" in stages:
            results.extend(run_feature_chunks(client, args, reporter, feature_chunks))
        if "reactions" in stages:
            results.extend(run_reaction_chunks(client, args, reporter, reaction_chunks))
        if "stats" in stages:
            results.append(rebuild_stats(client, args, reporter))
        reporter.stage_start("audit")
        audit_outputs(client, args, stages, reporter)
        reporter.unit_done("audit", "output integrity", status="complete")
        write_manifest(run_root, args, run_id, stages, coverage, results)
        reporter.message(f"manifest={run_root / 'news_reaction_manifest.json'}")
        reporter.finish()
    return 0


def validate_args(args: argparse.Namespace) -> None:
    start = date_arg(args.start_date)
    end = date_arg(args.end_date)
    if start >= end:
        raise SystemExit("--start-date must be before exclusive --end-date")
    stats_start = date_arg(args.stats_start_date)
    stats_end = date_arg(args.stats_end_date)
    if stats_start >= stats_end:
        raise SystemExit("--stats-start-date must be before exclusive --stats-end-date")
    if stats_start < start or stats_end > end:
        raise SystemExit("statistics training bounds must be contained inside the extracted publication range")
    if args.feature_chunk_months <= 0 or args.reaction_chunk_days <= 0:
        raise SystemExit("chunk sizes must be positive")
    if args.resolution_us <= 0:
        raise SystemExit("--resolution-us must be positive")
    if args.progress_refresh_per_second <= 0 or args.progress_log_lines <= 0:
        raise SystemExit("progress refresh rate and log-line count must be positive")


def parse_stages(value: str) -> tuple[str, ...]:
    allowed = ("calendar", "dictionary", "features", "reactions", "stats")
    stages = tuple(dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip()))
    invalid = [stage for stage in stages if stage not in allowed]
    if not stages or invalid:
        raise SystemExit(f"invalid --stages {invalid or value!r}; expected a subset of {allowed}")
    return stages


def date_arg(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"invalid ISO date {value!r}") from exc


def print_header(args: argparse.Namespace, run_id: str, run_root: Path, stages: Sequence[str]) -> None:
    print("=" * 100, flush=True)
    print("Benzinga news reaction reference build", flush=True)
    print(f"run_id={run_id} execute={args.execute} stages={','.join(stages)}", flush=True)
    print(f"publication_range=[{args.start_date},{args.end_date}) resolution_us={args.resolution_us}", flush=True)
    print(f"news_source={args.news_database}.{args.normalized_table} ticker_source={args.news_database}.{args.ticker_table}", flush=True)
    print(f"market_source={args.market_database}.{args.bars_table}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 100, flush=True)


def ensure_sources(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    required = (
        (args.news_database, args.normalized_table),
        (args.news_database, args.ticker_table),
        (args.market_database, args.bars_table),
    )
    missing = [f"{db}.{table}" for db, table in required if not table_exists(client, db, table)]
    if missing:
        raise SystemExit("required source tables are missing: " + ", ".join(missing))
    columns = table_columns(client, args.market_database, args.bars_table)
    expected = {
        "local_date", "ticker", "label_resolution_us", "bucket_index", "bar_family",
        "close", "high", "low", "first_event_timestamp_us", "last_event_timestamp_us",
    }
    if missing_columns := sorted(expected - columns):
        raise SystemExit(f"bar table is missing required columns: {missing_columns}")


def audit_bar_coverage(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    reporter: NewsReactionProgress | None = None,
) -> CoverageAudit:
    news = table(args.news_database, args.ticker_table)
    bars = table(args.market_database, args.bars_table)
    start = dt_sql(args.start_date)
    end = dt_sql(args.end_date)
    sql = f"""
WITH
news_months AS
(
    SELECT DISTINCT upperUTF8(ticker) AS ticker, toYYYYMM(toTimeZone(published_at_utc, 'America/New_York')) AS month_id
    FROM {news}
    WHERE published_at_utc >= {start} AND published_at_utc < {end}
),
bar_months AS
(
    SELECT DISTINCT upperUTF8(ticker) AS ticker, toYYYYMM(local_date) AS month_id
    FROM {bars}
    WHERE label_resolution_us = {int(args.resolution_us)}
      AND bar_family = 'trade'
      AND local_date >= addDays(toDate({sql_string(args.start_date)}), -7)
      AND local_date < addDays(toDate({sql_string(args.end_date)}), 8)
),
missing AS
(
    SELECT n.ticker, n.month_id
    FROM news_months AS n
    LEFT JOIN bar_months AS b USING (ticker, month_id)
    WHERE b.ticker = ''
)
SELECT
    (SELECT count() FROM news_months) AS news_ticker_months,
    news_ticker_months - count() AS covered_ticker_months,
    count() AS missing_ticker_months,
    arraySlice(groupArray(concat(ticker, ':', toString(month_id))), 1, 25) AS missing_sample,
    toString((SELECT min(local_date) FROM {bars} WHERE label_resolution_us = {int(args.resolution_us)} AND bar_family = 'trade')) AS bar_min_date,
    toString((SELECT max(local_date) FROM {bars} WHERE label_resolution_us = {int(args.resolution_us)} AND bar_family = 'trade')) AS bar_max_date
FROM missing
FORMAT JSONEachRow
"""
    text = monitored_execute(client, sql, reporter, "preflight trade-bar coverage")
    row = parse_one_json(text)
    return CoverageAudit(
        news_ticker_months=int(row.get("news_ticker_months") or 0),
        covered_ticker_months=int(row.get("covered_ticker_months") or 0),
        missing_ticker_months=int(row.get("missing_ticker_months") or 0),
        missing_sample=tuple(str(value) for value in row.get("missing_sample") or []),
        bar_min_date=str(row.get("bar_min_date") or ""),
        bar_max_date=str(row.get("bar_max_date") or ""),
    )


def ensure_target_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.news_database)}")
    for sql in target_table_sql(args):
        client.execute(sql)
    required_columns = {
        args.calendar_table: {"calendar_date", "current_regular_open_utc", "current_regular_close_utc", "next_session_date", "calendar_version"},
        args.dictionary_table: {"dictionary_version", "phrase_id", "canonical_phrase", "family", "direction", "strength", "feature_role", "needles"},
        args.features_table: {"extraction_version", "canonical_news_id", "published_at_utc", "phrase_id", "source_mask", "text_hash"},
        args.reactions_table: {
            "label_version", "canonical_news_id", "ticker", "published_at_utc", "horizon_code",
            "anchor_price", "target_price", "window_high_price", "window_low_price",
            "abnormal_target_return", "abnormal_high_return", "abnormal_low_return",
            "quality_status", "quality_flags", "source_revision",
        },
        args.stats_table: {
            "stats_version", "phrase_id", "horizon_code", "clean_sample_count",
            "negative_probability", "neutral_probability", "positive_probability",
            "target_return_quantiles", "high_return_quantiles", "low_return_quantiles",
        },
        args.status_table: {"stage", "version", "chunk_start", "chunk_end_exclusive", "status", "row_count"},
    }
    for table_name, required in required_columns.items():
        columns = table_columns(client, args.news_database, table_name)
        if missing := sorted(required - columns):
            raise SystemExit(f"target table {args.news_database}.{table_name} has an incompatible schema; missing {missing}")
    feature_columns = table_columns(client, args.news_database, args.features_table)
    if any("occurrence" in column.lower() for column in feature_columns):
        raise SystemExit("feature-table contract must not retain repeated phrase occurrence columns")


def target_table_sql(args: argparse.Namespace) -> tuple[str, ...]:
    policy = merge_tree_settings(args.storage_policy)
    return (
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.calendar_table)}
(
    calendar_date Date,
    is_session UInt8,
    current_session_date Nullable(Date),
    current_premarket_start_utc Nullable(DateTime64(6, 'UTC')),
    current_regular_open_utc Nullable(DateTime64(6, 'UTC')),
    current_regular_close_utc Nullable(DateTime64(6, 'UTC')),
    current_extended_close_utc Nullable(DateTime64(6, 'UTC')),
    next_session_date Date,
    next_premarket_start_utc DateTime64(6, 'UTC'),
    next_regular_open_utc DateTime64(6, 'UTC'),
    next_regular_close_utc DateTime64(6, 'UTC'),
    next_extended_close_utc DateTime64(6, 'UTC'),
    calendar_version LowCardinality(String),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (calendar_version, calendar_date)
{policy}
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.dictionary_table)}
(
    dictionary_version LowCardinality(String),
    phrase_id LowCardinality(String),
    canonical_phrase String,
    family LowCardinality(String),
    direction Int8,
    strength Float32,
    feature_role LowCardinality(String),
    needles Array(String),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (dictionary_version, phrase_id)
{policy}
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.features_table)}
(
    extraction_version LowCardinality(String),
    canonical_news_id String,
    published_at_utc DateTime64(9, 'UTC'),
    phrase_id LowCardinality(String),
    source_mask UInt8,
    text_hash String,
    extracted_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(extracted_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (extraction_version, phrase_id, published_at_utc, canonical_news_id)
{policy}
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.reactions_table)}
(
    label_version LowCardinality(String),
    canonical_news_id String,
    ticker LowCardinality(String),
    published_at_utc DateTime64(9, 'UTC'),
    available_at_utc DateTime64(9, 'UTC'),
    reaction_session_date Date,
    publication_session LowCardinality(String),
    horizon_code LowCardinality(String),
    horizon_type LowCardinality(String),
    applicable UInt8,
    target_at_utc DateTime64(6, 'UTC'),
    anchor_timestamp_utc Nullable(DateTime64(6, 'UTC')),
    anchor_price Nullable(Float64),
    anchor_basis LowCardinality(String),
    anchor_age_ms Nullable(UInt64),
    target_timestamp_utc Nullable(DateTime64(6, 'UTC')),
    target_price Nullable(Float64),
    target_basis LowCardinality(String),
    target_age_ms Nullable(UInt64),
    window_high_timestamp_utc Nullable(DateTime64(6, 'UTC')),
    window_high_price Nullable(Float64),
    window_low_timestamp_utc Nullable(DateTime64(6, 'UTC')),
    window_low_price Nullable(Float64),
    target_return Nullable(Float64),
    high_return Nullable(Float64),
    low_return Nullable(Float64),
    market_target_return Nullable(Float64),
    abnormal_target_return Nullable(Float64),
    abnormal_high_return Nullable(Float64),
    abnormal_low_return Nullable(Float64),
    reaction_bin LowCardinality(String),
    observation_count UInt64,
    overlapping_news_count UInt32,
    quality_status LowCardinality(String),
    quality_flags Array(String),
    price_resolution_us UInt64,
    calendar_version LowCardinality(String),
    source_revision String,
    finalized_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(finalized_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (label_version, horizon_code, ticker, published_at_utc, canonical_news_id)
{policy}
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.stats_table)}
(
    stats_version LowCardinality(String),
    extraction_version LowCardinality(String),
    label_version LowCardinality(String),
    phrase_id LowCardinality(String),
    horizon_code LowCardinality(String),
    publication_session LowCardinality(String),
    sample_count UInt64,
    clean_sample_count UInt64,
    negative_count UInt64,
    neutral_count UInt64,
    positive_count UInt64,
    negative_probability Float64,
    neutral_probability Float64,
    positive_probability Float64,
    mean_target_return Nullable(Float64),
    mean_high_return Nullable(Float64),
    mean_low_return Nullable(Float64),
    target_return_quantiles Array(Float64),
    high_return_quantiles Array(Float64),
    low_return_quantiles Array(Float64),
    trained_start_date Date,
    trained_end_date_exclusive Date,
    built_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(built_at)
ORDER BY (stats_version, phrase_id, horizon_code, publication_session)
{policy}
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.status_table)}
(
    stage LowCardinality(String),
    version LowCardinality(String),
    chunk_start Date,
    chunk_end_exclusive Date,
    status LowCardinality(String),
    row_count UInt64,
    elapsed_seconds Float64,
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (stage, version, chunk_start, chunk_end_exclusive)
{policy}
""",
    )


def replace_calendar(client: ClickHouseHttpClient, args: argparse.Namespace, reporter: NewsReactionProgress) -> None:
    reporter.stage_start("calendar")
    reporter.chunk_start("calendar", CALENDAR_VERSION)
    started = time.perf_counter()
    rows = build_calendar_rows(date_arg(args.start_date) - dt.timedelta(days=10), date_arg(args.end_date) + dt.timedelta(days=15))
    target = table(args.news_database, args.calendar_table)
    monitored_execute(
        client,
        f"ALTER TABLE {target} DELETE WHERE calendar_version = {sql_string(CALENDAR_VERSION)} SETTINGS mutations_sync = 2",
        reporter,
        "replace calendar",
    )
    insert_json_rows(client, target, rows)
    reporter.unit_done("calendar", CALENDAR_VERSION, status="complete", rows=len(rows), elapsed_seconds=time.perf_counter() - started)


def build_calendar_rows(start: dt.date, end_exclusive: dt.date) -> list[dict[str, Any]]:
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise SystemExit("pandas_market_calendars is required to build the XNYS reaction calendar") from exc
    schedule = mcal.get_calendar("XNYS").schedule(
        start_date=start.isoformat(),
        end_date=(end_exclusive + dt.timedelta(days=21)).isoformat(),
    )
    sessions: list[dict[str, Any]] = []
    for index, values in schedule.iterrows():
        session_date = index.date()
        regular_open = values["market_open"].to_pydatetime().astimezone(UTC)
        regular_close = values["market_close"].to_pydatetime().astimezone(UTC)
        premarket_start = dt.datetime.combine(session_date, dt.time(4, 0), EASTERN).astimezone(UTC)
        extended_close = dt.datetime.combine(session_date, dt.time(20, 0), EASTERN).astimezone(UTC)
        sessions.append({
            "session_date": session_date,
            "premarket_start": premarket_start,
            "regular_open": regular_open,
            "regular_close": regular_close,
            "extended_close": extended_close,
        })
    by_date = {value["session_date"]: value for value in sessions}
    updated_at = clickhouse_timestamp(dt.datetime.now(UTC))
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor < end_exclusive:
        current = by_date.get(cursor)
        next_session = next((value for value in sessions if value["session_date"] > cursor), None)
        if next_session is None:
            raise RuntimeError(f"calendar has no session after {cursor}")
        rows.append(asdict(CalendarRow(
            calendar_date=cursor.isoformat(),
            is_session=int(current is not None),
            current_session_date=current["session_date"].isoformat() if current else None,
            current_premarket_start_utc=clickhouse_timestamp(current["premarket_start"]) if current else None,
            current_regular_open_utc=clickhouse_timestamp(current["regular_open"]) if current else None,
            current_regular_close_utc=clickhouse_timestamp(current["regular_close"]) if current else None,
            current_extended_close_utc=clickhouse_timestamp(current["extended_close"]) if current else None,
            next_session_date=next_session["session_date"].isoformat(),
            next_premarket_start_utc=clickhouse_timestamp(next_session["premarket_start"]),
            next_regular_open_utc=clickhouse_timestamp(next_session["regular_open"]),
            next_regular_close_utc=clickhouse_timestamp(next_session["regular_close"]),
            next_extended_close_utc=clickhouse_timestamp(next_session["extended_close"]),
            calendar_version=CALENDAR_VERSION,
            updated_at=updated_at,
        )))
        cursor += dt.timedelta(days=1)
    return rows


def replace_dictionary(client: ClickHouseHttpClient, args: argparse.Namespace, reporter: NewsReactionProgress) -> None:
    reporter.stage_start("dictionary")
    reporter.chunk_start("dictionary", PHRASE_DICTIONARY_VERSION)
    started = time.perf_counter()
    target = table(args.news_database, args.dictionary_table)
    monitored_execute(
        client,
        f"ALTER TABLE {target} DELETE WHERE dictionary_version = {sql_string(PHRASE_DICTIONARY_VERSION)} SETTINGS mutations_sync = 2",
        reporter,
        "replace dictionary",
    )
    updated_at = clickhouse_timestamp(dt.datetime.now(UTC))
    rows = [
        {
            "dictionary_version": PHRASE_DICTIONARY_VERSION,
            "phrase_id": rule.phrase_id,
            "canonical_phrase": rule.canonical_phrase,
            "family": rule.family,
            "direction": rule.direction,
            "strength": rule.strength,
            "feature_role": rule.feature_role,
            "needles": list(rule.needles),
            "updated_at": updated_at,
        }
        for rule in PHRASE_RULES
    ]
    insert_json_rows(client, target, rows)
    reporter.message(f"dictionary needles={sum(len(rule.needles) for rule in PHRASE_RULES):,}")
    reporter.unit_done("dictionary", PHRASE_DICTIONARY_VERSION, status="complete", rows=len(rows), elapsed_seconds=time.perf_counter() - started)


def run_feature_chunks(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    reporter: NewsReactionProgress,
    chunks: Sequence[tuple[dt.date, dt.date]],
) -> list[ChunkResult]:
    results: list[ChunkResult] = []
    reporter.stage_start("features")
    completed = completed_chunk_keys(client, args, "features", PHRASE_DICTIONARY_VERSION, reporter) if args.resume and not args.replace_existing else set()
    for start, end in chunks:
        unit = f"{start}:{end}"
        if (start, end) in completed:
            reporter.unit_done("features", unit, status="skipped")
            continue
        reporter.chunk_start("features", unit)
        started = time.perf_counter()
        try:
            if args.replace_existing:
                delete_version_range(client, args, args.features_table, "extraction_version", PHRASE_DICTIONARY_VERSION, start, end, reporter)
            before = count_version_range(client, args, args.features_table, "extraction_version", PHRASE_DICTIONARY_VERSION, start, end, reporter)
            monitored_execute(client, feature_insert_sql(args, start, end) + settings_sql(args), reporter, f"feature insert {unit}")
            after = count_version_range(client, args, args.features_table, "extraction_version", PHRASE_DICTIONARY_VERSION, start, end, reporter)
            result = ChunkResult("features", start.isoformat(), end.isoformat(), max(0, after - before), time.perf_counter() - started)
            record_chunk(client, args, result, PHRASE_DICTIONARY_VERSION)
        except KeyboardInterrupt:
            reporter.unit_interrupted("features", unit)
            raise
        except BaseException as exc:
            reporter.unit_failed("features", unit, exc)
            raise
        results.append(result)
        reporter.unit_done("features", unit, status="complete", rows=result.inserted_rows, elapsed_seconds=result.elapsed_seconds)
    return results


def feature_insert_sql(args: argparse.Namespace, start: dt.date, end: dt.date, rules: Sequence[PhraseRule] = PHRASE_RULES) -> str:
    flattened = [(needle, rule.phrase_id) for rule in rules for needle in rule.needles]
    needles = "[" + ", ".join(sql_string(needle) for needle, _ in flattened) + "]"
    phrase_ids = "[" + ", ".join(sql_string(phrase_id) for _, phrase_id in flattened) + "]"
    needle_batches = [
        flattened[offset:offset + MULTISEARCH_NEEDLE_LIMIT]
        for offset in range(0, len(flattened), MULTISEARCH_NEEDLE_LIMIT)
    ]

    def positions(text_expression: str) -> str:
        searches = [
            "multiSearchAllPositionsCaseInsensitiveUTF8("
            f"{text_expression}, [{', '.join(sql_string(needle) for needle, _ in batch)}])"
            for batch in needle_batches
        ]
        return searches[0] if len(searches) == 1 else f"arrayConcat({', '.join(searches)})"

    source = table(args.news_database, args.normalized_table)
    target = table(args.news_database, args.features_table)
    return f"""
INSERT INTO {target}
WITH
    {needles} AS needles,
    {phrase_ids} AS phrase_ids
SELECT
    {sql_string(PHRASE_DICTIONARY_VERSION)} AS extraction_version,
    canonical_news_id,
    published_at_utc,
    phrase_id,
    toUInt8(
        if(arrayExists(i -> phrase_ids[i] = phrase_id AND title_positions[i] > 0, indexes), 1, 0)
        + if(arrayExists(i -> phrase_ids[i] = phrase_id AND body_positions[i] > 0, indexes), 2, 0)
        + if(arrayExists(i -> phrase_ids[i] = phrase_id AND tag_positions[i] > 0, indexes), 4, 0)
        + if(arrayExists(i -> phrase_ids[i] = phrase_id AND channel_positions[i] > 0, indexes), 8, 0)
    ) AS source_mask,
    text_hash,
    now64(6) AS extracted_at
FROM
(
    SELECT
        canonical_news_id,
        published_at_utc,
        text_hash,
        phrase_ids,
        arrayEnumerate(needles) AS indexes,
        {positions("ifNull(title, '')")} AS title_positions,
        {positions("concat(ifNull(teaser, ''), ' ', ifNull(body_text, ''), ' ', ifNull(external_text, ''), ' ', ifNull(pdf_text, ''))")} AS body_positions,
        {positions("arrayStringConcat(provider_tags, ' ')")} AS tag_positions,
        {positions("arrayStringConcat(channels, ' ')")} AS channel_positions,
        arrayDistinct(arrayMap(i -> phrase_ids[i], arrayFilter(i ->
            title_positions[i] > 0 OR body_positions[i] > 0 OR tag_positions[i] > 0 OR channel_positions[i] > 0,
            indexes
        ))) AS matched_phrase_ids
    FROM {source} FINAL
    WHERE published_at_utc >= {dt_sql(start.isoformat())}
      AND published_at_utc < {dt_sql(end.isoformat())}
)
ARRAY JOIN matched_phrase_ids AS phrase_id
"""


def run_reaction_chunks(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    reporter: NewsReactionProgress,
    chunks: Sequence[tuple[dt.date, dt.date]],
) -> list[ChunkResult]:
    results: list[ChunkResult] = []
    reporter.stage_start("reactions")
    completed = completed_chunk_keys(client, args, "reactions", LABEL_VERSION, reporter) if args.resume and not args.replace_existing else set()
    for start, end in chunks:
        unit = f"{start}:{end}"
        if (start, end) in completed:
            reporter.unit_done("reactions", unit, status="skipped")
            continue
        reporter.chunk_start("reactions", unit)
        started = time.perf_counter()
        try:
            if args.replace_existing:
                delete_version_range(client, args, args.reactions_table, "label_version", LABEL_VERSION, start, end, reporter)
            before = count_version_range(client, args, args.reactions_table, "label_version", LABEL_VERSION, start, end, reporter)
            monitored_execute(
                client,
                reaction_insert_sql(args, start, end) + settings_sql(args, experimental_join=True),
                reporter,
                f"reaction insert {unit}",
            )
            after = count_version_range(client, args, args.reactions_table, "label_version", LABEL_VERSION, start, end, reporter)
            result = ChunkResult("reactions", start.isoformat(), end.isoformat(), max(0, after - before), time.perf_counter() - started)
            record_chunk(client, args, result, LABEL_VERSION)
        except KeyboardInterrupt:
            reporter.unit_interrupted("reactions", unit)
            raise
        except BaseException as exc:
            reporter.unit_failed("reactions", unit, exc)
            raise
        results.append(result)
        reporter.unit_done("reactions", unit, status="complete", rows=result.inserted_rows, elapsed_seconds=result.elapsed_seconds)
    return results


def reaction_insert_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    ticker_source = table(args.news_database, args.ticker_table)
    normalized = table(args.news_database, args.normalized_table)
    bars = table(args.market_database, args.bars_table)
    calendar_table = table(args.news_database, args.calendar_table)
    target = table(args.news_database, args.reactions_table)
    lookback = start - dt.timedelta(days=8)
    lookahead = end + dt.timedelta(days=8)
    fixed_tuples = [
        f"tuple({sql_string(code)}, {sql_string(kind)}, toUInt64(pub_us + {seconds * 1_000_000}), toUInt8(publication_session != 'closed' AND pub_us + {seconds * 1_000_000} <= extended_close_us))"
        for code, kind, seconds in HORIZONS if kind == "fixed"
    ]
    boundary_tuples = [
        "tuple('premarket_close', 'session_boundary', regular_open_us, toUInt8(pub_us < regular_open_us))",
        "tuple('regular_close', 'session_boundary', regular_close_us, toUInt8(pub_us < regular_close_us))",
        "tuple('extended_close', 'session_boundary', extended_close_us, toUInt8(pub_us < extended_close_us))",
    ]
    horizon_array = "[" + ",\n            ".join(fixed_tuples + boundary_tuples) + "]"
    source_revision = f"concat('bars:', toString({int(args.resolution_us)}), ':', toString(max_bar_built_at))"
    return f"""
INSERT INTO {target}
WITH
updates AS
(
    SELECT
        upperUTF8(ticker) AS ticker,
        local_date,
        bucket_index,
        max(toNullable(close)) AS trade_close,
        max(toNullable(high)) AS trade_high,
        min(toNullable(low)) AS trade_low,
        min(first_event_timestamp_us) AS first_trade_timestamp_us,
        max(last_event_timestamp_us) AS last_trade_timestamp_us,
        max(built_at) AS max_bar_built_at
    FROM {bars}
    WHERE label_resolution_us = {int(args.resolution_us)}
      AND bar_family = 'trade'
      AND local_date >= toDate({sql_string(lookback.isoformat())})
      AND local_date < toDate({sql_string(lookahead.isoformat())})
      AND (ticker = {sql_string(args.benchmark_ticker.upper())} OR upperUTF8(ticker) IN
      (
          SELECT DISTINCT upperUTF8(ticker)
          FROM {ticker_source}
          WHERE published_at_utc >= {dt_sql(start.isoformat())}
            AND published_at_utc < {dt_sql(end.isoformat())}
      ))
    GROUP BY ticker, local_date, bucket_index
),
points AS
(
    SELECT
        ticker,
        first_trade_timestamp_us,
        last_trade_timestamp_us,
        trade_close AS price,
        trade_high,
        trade_low,
        'trade_close' AS price_basis,
        max_bar_built_at
    FROM updates
    WHERE isNotNull(trade_close)
      AND isNotNull(trade_high)
      AND isNotNull(trade_low)
      AND first_trade_timestamp_us > 0
      AND last_trade_timestamp_us >= first_trade_timestamp_us
    ORDER BY ticker, last_trade_timestamp_us
),
news_base AS
(
    SELECT
        t.canonical_news_id AS canonical_news_id,
        upperUTF8(t.ticker) AS ticker,
        t.published_at_utc AS published_at_utc,
        n.downloaded_at_utc AS available_at_utc,
        toUInt64(toUnixTimestamp64Micro(t.published_at_utc)) AS pub_us,
        toDate(toTimeZone(t.published_at_utc, 'America/New_York')) AS local_publication_date,
        c.is_session AS is_session,
        if(c.is_session = 1 AND t.published_at_utc < c.current_extended_close_utc, assumeNotNull(c.current_session_date), c.next_session_date) AS reaction_session_date,
        if(c.is_session = 1 AND t.published_at_utc < c.current_extended_close_utc, assumeNotNull(c.current_premarket_start_utc), c.next_premarket_start_utc) AS premarket_start_utc,
        if(c.is_session = 1 AND t.published_at_utc < c.current_extended_close_utc, assumeNotNull(c.current_regular_open_utc), c.next_regular_open_utc) AS regular_open_utc,
        if(c.is_session = 1 AND t.published_at_utc < c.current_extended_close_utc, assumeNotNull(c.current_regular_close_utc), c.next_regular_close_utc) AS regular_close_utc,
        if(c.is_session = 1 AND t.published_at_utc < c.current_extended_close_utc, assumeNotNull(c.current_extended_close_utc), c.next_extended_close_utc) AS extended_close_utc
    FROM (SELECT * FROM {ticker_source} FINAL) AS t
    INNER JOIN (SELECT * FROM {normalized} FINAL) AS n ON n.canonical_news_id = t.canonical_news_id
    INNER JOIN
    (
        SELECT * FROM {calendar_table} FINAL
        WHERE calendar_version = {sql_string(CALENDAR_VERSION)}
    ) AS c
        ON c.calendar_date = toDate(toTimeZone(t.published_at_utc, 'America/New_York'))
    WHERE t.published_at_utc >= {dt_sql(start.isoformat())}
      AND t.published_at_utc < {dt_sql(end.isoformat())}
),
news_resolved AS
(
    SELECT
        canonical_news_id,
        ticker,
        published_at_utc,
        available_at_utc,
        pub_us,
        local_publication_date,
        is_session,
        reaction_session_date,
        premarket_start_utc,
        regular_open_utc,
        regular_close_utc,
        extended_close_utc,
        toUInt64(toUnixTimestamp64Micro(premarket_start_utc)) AS premarket_start_us,
        toUInt64(toUnixTimestamp64Micro(regular_open_utc)) AS regular_open_us,
        toUInt64(toUnixTimestamp64Micro(regular_close_utc)) AS regular_close_us,
        toUInt64(toUnixTimestamp64Micro(extended_close_utc)) AS extended_close_us,
        multiIf(
            published_at_utc >= premarket_start_utc AND published_at_utc < regular_open_utc, 'premarket',
            published_at_utc >= regular_open_utc AND published_at_utc < regular_close_utc, 'regular',
            published_at_utc >= regular_close_utc AND published_at_utc < extended_close_utc, 'afterhours',
            'closed'
        ) AS publication_session
    FROM news_base
),
windows AS
(
    SELECT
        *,
        tupleElement(horizon, 1) AS horizon_code,
        tupleElement(horizon, 2) AS horizon_type,
        tupleElement(horizon, 3) AS target_us,
        tupleElement(horizon, 4) AS applicable
    FROM news_resolved
    ARRAY JOIN {horizon_array} AS horizon
),
instrument_windows AS
(
    SELECT
        *,
        tupleElement(instrument, 1) AS instrument_role,
        tupleElement(instrument, 2) AS instrument_ticker
    FROM windows
    ARRAY JOIN [tuple('asset', ticker), tuple('market', {sql_string(args.benchmark_ticker.upper())})] AS instrument
),
anchored AS
(
    SELECT
        w.*,
        p.last_trade_timestamp_us AS anchor_ts_us,
        p.price AS anchor_price,
        p.price_basis AS anchor_basis,
        p.max_bar_built_at AS anchor_bar_built_at
    FROM instrument_windows AS w
    ASOF LEFT JOIN points AS p
      ON w.instrument_ticker = p.ticker
     AND w.pub_us >= p.last_trade_timestamp_us + toUInt64(1)
),
instrument_metrics AS
(
    SELECT
        a.canonical_news_id,
        a.ticker,
        a.published_at_utc,
        a.available_at_utc,
        a.reaction_session_date,
        a.publication_session,
        a.horizon_code,
        a.horizon_type,
        a.applicable,
        a.target_us,
        a.instrument_role,
        a.anchor_ts_us,
        a.anchor_price,
        a.anchor_basis,
        argMaxIf(toNullable(p.price), p.last_trade_timestamp_us, a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS target_price,
        argMaxIf(toNullable(p.last_trade_timestamp_us), p.last_trade_timestamp_us, a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS target_ts_us,
        argMaxIf(toNullable(p.price_basis), p.last_trade_timestamp_us, a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS target_basis,
        maxIf(toNullable(p.trade_high), a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS high_price,
        argMaxIf(toNullable(p.last_trade_timestamp_us), p.trade_high, a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS high_ts_us,
        minIf(toNullable(p.trade_low), a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS low_price,
        argMinIf(toNullable(p.last_trade_timestamp_us), p.trade_low, a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS low_ts_us,
        countIf(a.applicable = 1 AND p.first_trade_timestamp_us > a.pub_us AND p.last_trade_timestamp_us <= a.target_us) AS observation_count,
        max(greatest(a.anchor_bar_built_at, p.max_bar_built_at)) AS max_bar_built_at
    FROM anchored AS a
    LEFT JOIN points AS p
      ON p.ticker = a.instrument_ticker
     AND p.first_trade_timestamp_us > a.pub_us
     AND p.last_trade_timestamp_us <= a.target_us
    GROUP BY
        a.canonical_news_id, a.ticker, a.published_at_utc, a.available_at_utc,
        a.reaction_session_date, a.publication_session, a.horizon_code, a.horizon_type,
        a.applicable, a.target_us, a.pub_us, a.instrument_role, a.anchor_ts_us,
        a.anchor_price, a.anchor_basis
),
asset_metrics AS
(
    SELECT * FROM instrument_metrics WHERE instrument_role = 'asset'
),
market_metrics AS
(
    SELECT * FROM instrument_metrics WHERE instrument_role = 'market'
),
overlaps AS
(
    SELECT
        w.canonical_news_id,
        w.ticker,
        w.horizon_code,
        toUInt32(countIf(o.canonical_news_id != w.canonical_news_id)) AS overlapping_news_count
    FROM windows AS w
    LEFT JOIN {ticker_source} AS o
      ON upperUTF8(o.ticker) = w.ticker
     AND o.published_at_utc > w.published_at_utc
     AND o.published_at_utc <= fromUnixTimestamp64Micro(toInt64(w.target_us))
    GROUP BY w.canonical_news_id, w.ticker, w.horizon_code
),
final_rows AS
(
    SELECT
        a.canonical_news_id AS canonical_news_id,
        a.ticker AS ticker,
        a.published_at_utc AS published_at_utc,
        a.available_at_utc AS available_at_utc,
        a.reaction_session_date AS reaction_session_date,
        a.publication_session AS publication_session,
        a.horizon_code AS horizon_code,
        a.horizon_type AS horizon_type,
        a.applicable AS applicable,
        a.target_us AS target_us,
        a.anchor_ts_us AS anchor_ts_us,
        a.anchor_price AS anchor_price,
        a.anchor_basis AS anchor_basis,
        a.target_price AS target_price,
        a.target_ts_us AS target_ts_us,
        a.target_basis AS target_basis,
        a.high_price AS high_price,
        a.high_ts_us AS high_ts_us,
        a.low_price AS low_price,
        a.low_ts_us AS low_ts_us,
        a.observation_count AS observation_count,
        a.max_bar_built_at AS max_bar_built_at,
        m.anchor_price AS market_anchor_price,
        m.target_price AS market_target_price,
        m.high_price AS market_high_price,
        m.low_price AS market_low_price,
        o.overlapping_news_count,
        if(isNotNull(a.anchor_price) AND a.anchor_price > 0 AND isNotNull(a.target_price), a.target_price / a.anchor_price - 1.0, toNullable(NULL)) AS target_return,
        if(isNotNull(a.anchor_price) AND a.anchor_price > 0 AND isNotNull(a.high_price), a.high_price / a.anchor_price - 1.0, toNullable(NULL)) AS high_return,
        if(isNotNull(a.anchor_price) AND a.anchor_price > 0 AND isNotNull(a.low_price), a.low_price / a.anchor_price - 1.0, toNullable(NULL)) AS low_return,
        if(isNotNull(m.anchor_price) AND m.anchor_price > 0 AND isNotNull(m.target_price), m.target_price / m.anchor_price - 1.0, toNullable(NULL)) AS market_target_return,
        target_return - market_target_return AS abnormal_target_return,
        high_return - if(isNotNull(m.anchor_price) AND m.anchor_price > 0 AND isNotNull(m.high_price), m.high_price / m.anchor_price - 1.0, toNullable(NULL)) AS abnormal_high_return,
        low_return - if(isNotNull(m.anchor_price) AND m.anchor_price > 0 AND isNotNull(m.low_price), m.low_price / m.anchor_price - 1.0, toNullable(NULL)) AS abnormal_low_return
    FROM asset_metrics AS a
    LEFT JOIN market_metrics AS m
      ON m.canonical_news_id = a.canonical_news_id
     AND m.ticker = a.ticker
     AND m.horizon_code = a.horizon_code
    LEFT JOIN overlaps AS o
      ON o.canonical_news_id = a.canonical_news_id
     AND o.ticker = a.ticker
     AND o.horizon_code = a.horizon_code
)
SELECT
    {sql_string(LABEL_VERSION)} AS label_version,
    canonical_news_id,
    ticker,
    published_at_utc,
    available_at_utc,
    reaction_session_date,
    publication_session,
    horizon_code,
    horizon_type,
    applicable,
    fromUnixTimestamp64Micro(toInt64(target_us)) AS target_at_utc,
    if(anchor_ts_us > 0, fromUnixTimestamp64Micro(toInt64(anchor_ts_us)), NULL) AS anchor_timestamp_utc,
    anchor_price,
    ifNull(anchor_basis, 'missing') AS anchor_basis,
    if(anchor_ts_us > 0, toUInt64((toUnixTimestamp64Micro(published_at_utc) - toInt64(anchor_ts_us)) / 1000), NULL) AS anchor_age_ms,
    if(isNotNull(target_ts_us), fromUnixTimestamp64Micro(toInt64(target_ts_us)), NULL) AS target_timestamp_utc,
    target_price,
    ifNull(target_basis, 'missing') AS target_basis,
    if(isNotNull(target_ts_us), toUInt64((toInt64(target_us) - toInt64(target_ts_us)) / 1000), NULL) AS target_age_ms,
    if(isNotNull(high_ts_us), fromUnixTimestamp64Micro(toInt64(high_ts_us)), NULL) AS window_high_timestamp_utc,
    high_price AS window_high_price,
    if(isNotNull(low_ts_us), fromUnixTimestamp64Micro(toInt64(low_ts_us)), NULL) AS window_low_timestamp_utc,
    low_price AS window_low_price,
    target_return,
    high_return,
    low_return,
    market_target_return,
    abnormal_target_return,
    abnormal_high_return,
    abnormal_low_return,
    multiIf(
        isNull(abnormal_target_return), 'unavailable',
        abnormal_target_return <= -0.10, 'le_-10pct',
        abnormal_target_return <= -0.05, '-10_to_-5pct',
        abnormal_target_return <= -0.02, '-5_to_-2pct',
        abnormal_target_return <= -0.005, '-2_to_-0.5pct',
        abnormal_target_return < 0.005, '-0.5_to_0.5pct',
        abnormal_target_return < 0.02, '0.5_to_2pct',
        abnormal_target_return < 0.05, '2_to_5pct',
        abnormal_target_return < 0.10, '5_to_10pct',
        'ge_10pct'
    ) AS reaction_bin,
    observation_count,
    ifNull(overlapping_news_count, 0) AS overlapping_news_count,
    multiIf(
        applicable = 0, 'not_applicable',
        isNull(anchor_price), 'missing_anchor',
        observation_count = 0 OR isNull(target_price), 'missing_target',
        publication_session != 'closed' AND (toUnixTimestamp64Micro(published_at_utc) - toInt64(anchor_ts_us)) > {int(args.active_anchor_max_age_seconds) * 1_000_000}, 'stale_anchor',
        (toInt64(target_us) - toInt64(target_ts_us)) > {int(args.target_max_age_seconds) * 1_000_000}, 'stale_target',
        overlapping_news_count > 0, 'overlapping_news',
        'clean'
    ) AS quality_status,
    arrayFilter(value -> notEmpty(value), [
        if(applicable = 0, 'horizon_not_applicable', ''),
        if(isNull(anchor_price), 'missing_anchor', ''),
        if(observation_count = 0 OR isNull(target_price), 'missing_target', ''),
        if(publication_session != 'closed' AND isNotNull(anchor_ts_us) AND (toUnixTimestamp64Micro(published_at_utc) - toInt64(anchor_ts_us)) > {int(args.active_anchor_max_age_seconds) * 1_000_000}, 'stale_active_anchor', ''),
        if(isNotNull(target_ts_us) AND (toInt64(target_us) - toInt64(target_ts_us)) > {int(args.target_max_age_seconds) * 1_000_000}, 'stale_target', ''),
        if(overlapping_news_count > 0, 'overlapping_ticker_news', '')
    ]) AS quality_flags,
    toUInt64({int(args.resolution_us)}) AS price_resolution_us,
    {sql_string(CALENDAR_VERSION)} AS calendar_version,
    {source_revision} AS source_revision,
    now64(6) AS finalized_at
FROM final_rows
"""


def rebuild_stats(client: ClickHouseHttpClient, args: argparse.Namespace, reporter: NewsReactionProgress) -> ChunkResult:
    reporter.stage_start("stats")
    reporter.chunk_start("stats", STATS_VERSION)
    target = table(args.news_database, args.stats_table)
    started = time.perf_counter()
    try:
        monitored_execute(
            client,
            f"ALTER TABLE {target} DELETE WHERE stats_version = {sql_string(STATS_VERSION)} SETTINGS mutations_sync = 2",
            reporter,
            "replace phrase statistics",
        )
        monitored_execute(client, stats_insert_sql(args) + settings_sql(args), reporter, "build phrase statistics")
        rows = int(monitored_execute(
            client,
            f"SELECT count() FROM {target} FINAL WHERE stats_version = {sql_string(STATS_VERSION)}",
            reporter,
            "count phrase statistics",
        ).strip() or 0)
        result = ChunkResult("stats", args.start_date, args.end_date, rows, time.perf_counter() - started)
    except KeyboardInterrupt:
        reporter.unit_interrupted("stats", STATS_VERSION)
        raise
    except BaseException as exc:
        reporter.unit_failed("stats", STATS_VERSION, exc)
        raise
    reporter.unit_done("stats", STATS_VERSION, status="complete", rows=rows, elapsed_seconds=result.elapsed_seconds)
    return result


def stats_insert_sql(args: argparse.Namespace) -> str:
    features = table(args.news_database, args.features_table)
    reactions = table(args.news_database, args.reactions_table)
    dictionary = table(args.news_database, args.dictionary_table)
    target = table(args.news_database, args.stats_table)
    return f"""
INSERT INTO {target}
SELECT
    {sql_string(STATS_VERSION)} AS stats_version,
    {sql_string(PHRASE_DICTIONARY_VERSION)} AS extraction_version,
    {sql_string(LABEL_VERSION)} AS label_version,
    f.phrase_id,
    r.horizon_code,
    r.publication_session,
    count() AS sample_count,
    countIf(r.quality_status = 'clean') AS clean_sample_count,
    countIf(r.quality_status = 'clean' AND r.abnormal_target_return < -0.005) AS negative_count,
    countIf(r.quality_status = 'clean' AND r.abnormal_target_return >= -0.005 AND r.abnormal_target_return <= 0.005) AS neutral_count,
    countIf(r.quality_status = 'clean' AND r.abnormal_target_return > 0.005) AS positive_count,
    (negative_count + 1.0) / (clean_sample_count + 3.0) AS negative_probability,
    (neutral_count + 1.0) / (clean_sample_count + 3.0) AS neutral_probability,
    (positive_count + 1.0) / (clean_sample_count + 3.0) AS positive_probability,
    avgIf(r.abnormal_target_return, r.quality_status = 'clean') AS mean_target_return,
    avgIf(r.abnormal_high_return, r.quality_status = 'clean') AS mean_high_return,
    avgIf(r.abnormal_low_return, r.quality_status = 'clean') AS mean_low_return,
    quantilesTDigestIf(0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)(r.abnormal_target_return, r.quality_status = 'clean') AS target_return_quantiles,
    quantilesTDigestIf(0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)(r.abnormal_high_return, r.quality_status = 'clean') AS high_return_quantiles,
    quantilesTDigestIf(0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)(r.abnormal_low_return, r.quality_status = 'clean') AS low_return_quantiles,
    toDate({sql_string(args.stats_start_date)}) AS trained_start_date,
    toDate({sql_string(args.stats_end_date)}) AS trained_end_date_exclusive,
    now64(6) AS built_at
FROM (SELECT * FROM {features} FINAL) AS f
INNER JOIN (SELECT * FROM {reactions} FINAL) AS r
  ON r.canonical_news_id = f.canonical_news_id
INNER JOIN (SELECT * FROM {dictionary} FINAL) AS d
  ON d.dictionary_version = f.extraction_version
 AND d.phrase_id = f.phrase_id
WHERE f.extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)}
  AND r.label_version = {sql_string(LABEL_VERSION)}
  AND d.feature_role != 'observed_reaction'
  AND r.published_at_utc >= {dt_sql(args.stats_start_date)}
  AND r.published_at_utc < {dt_sql(args.stats_end_date)}
GROUP BY f.phrase_id, r.horizon_code, r.publication_session
HAVING countIf(r.quality_status = 'clean') > 0
"""


def audit_outputs(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    stages: Sequence[str],
    reporter: NewsReactionProgress | None = None,
) -> None:
    checks: dict[str, str] = {}
    if "features" in stages:
        checks["features"] = monitored_execute(
            client,
            f"SELECT concat(toString(count()), '\\t', toString(uniqExact(tuple(canonical_news_id, phrase_id))), '\\t', toString(count() - uniqExact(tuple(canonical_news_id, phrase_id)))) FROM {table(args.news_database, args.features_table)} FINAL WHERE extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)} AND published_at_utc >= {dt_sql(args.start_date)} AND published_at_utc < {dt_sql(args.end_date)}",
            reporter,
            "audit language features",
        ).strip()
    if "reactions" in stages:
        checks["reactions"] = monitored_execute(
            client,
            f"SELECT concat(toString(count()), '\\t', toString(uniqExact(tuple(canonical_news_id, ticker, horizon_code))), '\\t', toString(countIf(quality_status = 'clean')), '\\t', toString(countIf(quality_status = 'missing_anchor')), '\\t', toString(countIf(quality_status = 'missing_target'))) FROM {table(args.news_database, args.reactions_table)} FINAL WHERE label_version = {sql_string(LABEL_VERSION)} AND published_at_utc >= {dt_sql(args.start_date)} AND published_at_utc < {dt_sql(args.end_date)}",
            reporter,
            "audit reaction labels",
        ).strip()
    if reporter is not None:
        reporter.message("output audit " + json.dumps(checks, sort_keys=True))
    else:
        print("output_audit=" + json.dumps(checks, sort_keys=True), flush=True)
    if "features" in checks and int(checks["features"].split("\\t")[-1]) != 0:
        raise RuntimeError("feature table contains duplicate article/phrase presence rows")
    if "reactions" in checks:
        values = checks["reactions"].split("\\t")
        if values[0] != values[1]:
            raise RuntimeError("reaction table contains duplicate news/ticker/horizon rows")


def chunk_completed(client: ClickHouseHttpClient, args: argparse.Namespace, stage: str, version: str, start: dt.date, end: dt.date) -> bool:
    if not table_exists(client, args.news_database, args.status_table):
        return False
    sql = f"""
SELECT count()
FROM {table(args.news_database, args.status_table)} FINAL
WHERE stage = {sql_string(stage)}
  AND version = {sql_string(version)}
  AND chunk_start = toDate({sql_string(start.isoformat())})
  AND chunk_end_exclusive = toDate({sql_string(end.isoformat())})
  AND status = 'completed'
"""
    return int(client.execute(sql).strip() or 0) > 0


def completed_chunk_keys(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    stage: str,
    version: str,
    reporter: NewsReactionProgress | None = None,
) -> set[tuple[dt.date, dt.date]]:
    if not table_exists(client, args.news_database, args.status_table):
        return set()
    sql = f"""
SELECT chunk_start, chunk_end_exclusive
FROM {table(args.news_database, args.status_table)} FINAL
WHERE stage = {sql_string(stage)}
  AND version = {sql_string(version)}
  AND status = 'completed'
FORMAT TSV
"""
    text = monitored_execute(client, sql, reporter, f"load {stage} checkpoints")
    completed: set[tuple[dt.date, dt.date]] = set()
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) == 2:
            completed.add((date_arg(fields[0]), date_arg(fields[1])))
    return completed


def record_chunk(client: ClickHouseHttpClient, args: argparse.Namespace, result: ChunkResult, version: str) -> None:
    row = {
        "stage": result.stage,
        "version": version,
        "chunk_start": result.start_date,
        "chunk_end_exclusive": result.end_date_exclusive,
        "status": "completed",
        "row_count": result.inserted_rows,
        "elapsed_seconds": result.elapsed_seconds,
        "updated_at": clickhouse_timestamp(dt.datetime.now(UTC)),
    }
    insert_json_rows(client, table(args.news_database, args.status_table), [row])


def delete_version_range(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    target_table: str,
    version_column: str,
    version: str,
    start: dt.date,
    end: dt.date,
    reporter: NewsReactionProgress | None = None,
) -> None:
    monitored_execute(
        client,
        f"ALTER TABLE {table(args.news_database, target_table)} DELETE WHERE {quote_ident(version_column)} = {sql_string(version)} "
        f"AND published_at_utc >= {dt_sql(start.isoformat())} AND published_at_utc < {dt_sql(end.isoformat())} SETTINGS mutations_sync = 2",
        reporter,
        f"delete {target_table} {start}:{end}",
    )


def count_version_range(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    target_table: str,
    version_column: str,
    version: str,
    start: dt.date,
    end: dt.date,
    reporter: NewsReactionProgress | None = None,
) -> int:
    sql = (
        f"SELECT count() FROM {table(args.news_database, target_table)} FINAL WHERE {quote_ident(version_column)} = {sql_string(version)} "
        f"AND published_at_utc >= {dt_sql(start.isoformat())} AND published_at_utc < {dt_sql(end.isoformat())}"
    )
    return int(monitored_execute(client, sql, reporter, f"count {target_table} {start}:{end}").strip() or 0)


def month_chunks(start: dt.date, end: dt.date, months: int) -> Iterable[tuple[dt.date, dt.date]]:
    cursor = start
    while cursor < end:
        month_index = cursor.year * 12 + cursor.month - 1 + months
        next_month = dt.date(month_index // 12, month_index % 12 + 1, 1)
        chunk_end = min(end, next_month)
        yield cursor, chunk_end
        cursor = chunk_end


def day_chunks(start: dt.date, end: dt.date, days: int) -> Iterable[tuple[dt.date, dt.date]]:
    cursor = start
    delta = dt.timedelta(days=days)
    while cursor < end:
        chunk_end = min(end, cursor + delta)
        yield cursor, chunk_end
        cursor = chunk_end


def insert_json_rows(client: ClickHouseHttpClient, target: str, rows: Sequence[dict[str, Any]], batch_size: int = 5_000) -> None:
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        payload = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in batch)
        client.execute(f"INSERT INTO {target} FORMAT JSONEachRow\n{payload}")


def table_exists(client: ClickHouseHttpClient, database: str, table_name: str) -> bool:
    return int(client.execute(
        f"SELECT count() FROM system.tables WHERE database = {sql_string(database)} AND name = {sql_string(table_name)}"
    ).strip() or 0) > 0


def table_columns(client: ClickHouseHttpClient, database: str, table_name: str) -> set[str]:
    text = client.execute(
        f"SELECT name FROM system.columns WHERE database = {sql_string(database)} AND table = {sql_string(table_name)} FORMAT TSV"
    )
    return {line.strip() for line in text.splitlines() if line.strip()}


def query_one_json(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    return parse_one_json(client.execute(sql))


def parse_one_json(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.strip():
            return json.loads(line)
    raise RuntimeError("query returned no rows")


def monitored_execute(
    client: ClickHouseHttpClient,
    sql: str,
    reporter: NewsReactionProgress | None,
    label: str,
) -> str:
    if reporter is None:
        return client.execute(sql)
    query_id = "news-reaction-" + uuid.uuid4().hex
    reporter.query_start(label, query_id)
    try:
        result = client.execute(sql, query_id=query_id)
    except KeyboardInterrupt:
        reporter.interrupted()
        try:
            client.execute(f"KILL QUERY WHERE query_id = {sql_string(query_id)} ASYNC")
        except Exception as kill_exc:  # noqa: BLE001
            reporter.message(f"WARN query cancellation failed query_id={query_id}: {kill_exc}")
        raise
    except BaseException as exc:
        reporter.query_failed(label, exc)
        raise
    reporter.query_done(label)
    return result


def settings_sql(args: argparse.Namespace, *, experimental_join: bool = False) -> str:
    settings = [f"max_threads = {int(args.max_threads)}"]
    if str(args.max_memory_usage) not in {"", "0"}:
        settings.append(f"max_memory_usage = {memory_bytes(args.max_memory_usage)}")
    if experimental_join:
        settings.append("allow_experimental_join_condition = 1")
        settings.append("join_algorithm = 'hash'")
    return "\nSETTINGS " + ", ".join(settings)


def memory_bytes(value: str) -> int:
    text = value.strip().upper()
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    if text.isdigit():
        return int(text)
    suffix = text[-1]
    if suffix not in multipliers:
        raise SystemExit(f"invalid memory size {value!r}")
    return int(float(text[:-1]) * multipliers[suffix])


def table(database: str, table_name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(table_name)}"


def dt_sql(value: str) -> str:
    return f"toDateTime64({sql_string(value + ' 00:00:00')}, 9, 'UTC')"


def merge_tree_settings(storage_policy: str) -> str:
    return f"SETTINGS storage_policy = {sql_string(storage_policy)}" if storage_policy else ""


def clickhouse_timestamp(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def write_manifest(
    run_root: Path,
    args: argparse.Namespace,
    run_id: str,
    stages: Sequence[str],
    coverage: CoverageAudit,
    results: Sequence[ChunkResult],
) -> None:
    manifest = {
        "run_id": run_id,
        "execute": bool(args.execute),
        "stages": list(stages),
        "publication_range": {"start": args.start_date, "end_exclusive": args.end_date},
        "statistics_training_range": {"start": args.stats_start_date, "end_exclusive": args.stats_end_date},
        "versions": {
            "calendar": CALENDAR_VERSION,
            "dictionary": PHRASE_DICTIONARY_VERSION,
            "labels": LABEL_VERSION,
            "stats": STATS_VERSION,
        },
        "sources": {
            "news": f"{args.news_database}.{args.normalized_table}",
            "ticker": f"{args.news_database}.{args.ticker_table}",
            "bars": f"{args.market_database}.{args.bars_table}",
        },
        "targets": {
            "calendar": f"{args.news_database}.{args.calendar_table}",
            "dictionary": f"{args.news_database}.{args.dictionary_table}",
            "features": f"{args.news_database}.{args.features_table}",
            "reactions": f"{args.news_database}.{args.reactions_table}",
            "stats": f"{args.news_database}.{args.stats_table}",
        },
        "coverage": asdict(coverage),
        "results": [asdict(result) for result in results],
        "secret_status": secret_status(["CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD"]),
    }
    (run_root / "news_reaction_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
