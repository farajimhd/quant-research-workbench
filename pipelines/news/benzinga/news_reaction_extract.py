from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
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
LABEL_VERSION = "news_reaction_event_labels_v3"
STATS_VERSION = "news_phrase_event_reaction_stats_v3"
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
    source_tables: tuple[str, ...]
    missing_source_tables: tuple[str, ...]
    event_min_date: str
    event_max_date: str
    event_rows: int


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
    parser.add_argument("--allow-partial-event-coverage", action="store_true", help="Permit an explicitly partial development build when canonical yearly event tables are missing.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--replace-existing", action="store_true", help="Delete the selected version/date slice before rebuilding it.")
    parser.add_argument("--feature-chunk-months", type=int, default=1)
    parser.add_argument("--reaction-chunk-days", type=int, default=1)
    parser.add_argument("--reaction-workers", type=int, default=4, help="Bounded number of independent ClickHouse day-chunk queries.")
    parser.add_argument("--reaction-ticker-shards", type=int, default=32, help="Deterministic ticker shards used to build one shared exact-event cache per publication month.")
    parser.add_argument("--reaction-links-per-shard", type=int, default=100, help="Target news-ticker links per reaction insert shard; sparse days use fewer queries.")
    parser.add_argument("--reaction-max-news-shards", type=int, default=64, help="Hard bound on reaction insert shards for a single publication day.")
    parser.add_argument("--benchmark-ticker", default="SPY")
    parser.add_argument("--active-anchor-max-age-seconds", type=int, default=60)
    parser.add_argument("--target-max-age-seconds", type=int, default=60)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="24G", help="Total reaction-query memory budget; divided across concurrent workers.")
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
    parser.add_argument("--events-table", default="events", help="Canonical compact event table base; yearly tables use the events_YYYY convention.")
    parser.add_argument("--condition-reference-table", default="event_condition_token_reference")
    parser.add_argument("--calendar-table", default="news_reaction_calendar_v1")
    parser.add_argument("--dictionary-table", default="news_phrase_dictionary_v1")
    parser.add_argument("--features-table", default="news_language_features_v1")
    parser.add_argument("--reactions-table", default="news_reaction_labels_v2")
    parser.add_argument("--stats-table", default="news_phrase_reaction_stats_v2")
    parser.add_argument("--status-table", default="news_reaction_build_status_v1")
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root", default="D:/market-data/prepared/news_reaction_labels")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args(argv)
    stages = parse_stages(args.stages)
    validate_args(args, stages)
    if not args.clickhouse_url:
        args.clickhouse_url = default_clickhouse_url()
    if not args.user:
        args.user = default_clickhouse_user()
    if not args.password:
        args.password = default_clickhouse_password()

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
        coverage = audit_event_coverage(client, args, reporter)
        args.available_event_tables = coverage.source_tables
        reporter.message(
            "compact-event coverage "
            f"tables={len(coverage.source_tables):,} missing={len(coverage.missing_source_tables):,} "
            f"range={coverage.event_min_date}:{coverage.event_max_date} rows={coverage.event_rows:,}"
        )
        reporter.unit_done("preflight", "compact-event coverage", status="complete")
        if coverage.missing_source_tables and "reactions" in stages and not args.allow_partial_event_coverage:
            raise SystemExit(
                "Reaction extraction stopped: canonical compact event tables are missing: "
                + ", ".join(coverage.missing_source_tables)
                + ". Repair event coverage or use --allow-partial-event-coverage only for an explicit development run."
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


def validate_args(args: argparse.Namespace, stages: Sequence[str] | None = None) -> None:
    selected_stages = tuple(stages) if stages is not None else parse_stages(args.stages)
    start = date_arg(args.start_date)
    end = date_arg(args.end_date)
    if start >= end:
        raise SystemExit("--start-date must be before exclusive --end-date")
    if "stats" in selected_stages:
        stats_start = date_arg(args.stats_start_date)
        stats_end = date_arg(args.stats_end_date)
        if stats_start >= stats_end:
            raise SystemExit("--stats-start-date must be before exclusive --stats-end-date")
        if stats_start < start or stats_end > end:
            raise SystemExit("statistics training bounds must be contained inside the extracted publication range")
    if args.feature_chunk_months <= 0 or args.reaction_chunk_days <= 0:
        raise SystemExit("chunk sizes must be positive")
    if (
        args.reaction_workers <= 0
        or args.reaction_ticker_shards <= 0
        or args.reaction_links_per_shard <= 0
        or args.reaction_max_news_shards <= 0
    ):
        raise SystemExit("reaction workers, ticker shards, links per shard, and maximum news shards must be positive")
    if str(args.max_memory_usage) not in {"", "0"}:
        memory_bytes(str(args.max_memory_usage))
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


def events_table_uses_year_suffix(table_name: str) -> bool:
    return not re.search(r"_\d{4}$", str(table_name))


def event_table_for_year(base_table: str, year: int) -> str:
    return f"{base_table}_{year}" if events_table_uses_year_suffix(base_table) else base_table


def event_authority_bounds(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    """Return the event authority implied by the configured publication range.

    Chunk lookback/lookahead windows may cross a year boundary, but must not
    expand the required market dataset beyond this build's explicit authority.
    Missing observations at the true edges remain visible as missing labels.
    """
    return date_arg(args.start_date), date_arg(args.end_date)


def expected_event_tables(args: argparse.Namespace) -> tuple[str, ...]:
    start, end = event_authority_bounds(args)
    if not events_table_uses_year_suffix(args.events_table):
        return (str(args.events_table),)
    last_year = (end - dt.timedelta(days=1)).year
    return tuple(event_table_for_year(args.events_table, year) for year in range(start.year, last_year + 1))


def existing_event_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(name for name in expected_event_tables(args) if table_exists(client, args.market_database, name))


def event_source_table(
    args: argparse.Namespace,
    first_date: dt.date,
    last_exclusive: dt.date,
    *,
    tables: Sequence[str] | None = None,
) -> str:
    authority_start, authority_end = event_authority_bounds(args)
    first_date = max(first_date, authority_start)
    last_exclusive = min(last_exclusive, authority_end)
    if last_exclusive <= first_date:
        raise ValueError(
            f"event request [{first_date},{last_exclusive}) is outside configured authority "
            f"[{authority_start},{authority_end})"
        )
    available = tuple(tables or getattr(args, "available_event_tables", ()) or ())
    if available and events_table_uses_year_suffix(args.events_table):
        first_year = first_date.year
        last_year = (last_exclusive - dt.timedelta(days=1)).year
        available = tuple(
            name for name in available
            if (match := re.search(r"_(\d{4})$", name)) and first_year <= int(match.group(1)) <= last_year
        )
    if not available:
        if not events_table_uses_year_suffix(args.events_table):
            available = (str(args.events_table),)
        else:
            available = tuple(
                event_table_for_year(args.events_table, year)
                for year in range(first_date.year, (last_exclusive - dt.timedelta(days=1)).year + 1)
            )
    if len(available) == 1:
        return table(args.market_database, available[0])
    pattern = "^(" + "|".join(re.escape(name) for name in available) + ")$"
    return f"merge({sql_string(args.market_database)}, {sql_string(pattern)})"


def print_header(args: argparse.Namespace, run_id: str, run_root: Path, stages: Sequence[str]) -> None:
    print("=" * 100, flush=True)
    print("Benzinga news reaction reference build", flush=True)
    print(f"run_id={run_id} execute={args.execute} stages={','.join(stages)}", flush=True)
    print(f"publication_range=[{args.start_date},{args.end_date}) reaction_workers={args.reaction_workers}", flush=True)
    print(f"news_source={args.news_database}.{args.normalized_table} ticker_source={args.news_database}.{args.ticker_table}", flush=True)
    event_source_name = f"{args.events_table}_YYYY" if events_table_uses_year_suffix(args.events_table) else str(args.events_table)
    print(f"market_source={args.market_database}.{event_source_name} exact compact events", flush=True)
    print(f"run_root={run_root}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 100, flush=True)


def ensure_sources(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    required = (
        (args.news_database, args.normalized_table),
        (args.news_database, args.ticker_table),
        (args.market_database, args.condition_reference_table),
    )
    missing = [f"{db}.{table}" for db, table in required if not table_exists(client, db, table)]
    if missing:
        raise SystemExit("required source tables are missing: " + ", ".join(missing))
    for source_table in existing_event_tables(client, args):
        columns = table_columns(client, args.market_database, source_table)
        expected = {
            "ticker", "ordinal", "event_meta", "sip_timestamp_us", "price_primary_int",
            "size_primary", "condition_token_1", "condition_token_2", "condition_token_3",
            "condition_token_4", "condition_token_5", "event_date",
        }
        if missing_columns := sorted(expected - columns):
            raise SystemExit(f"event table {args.market_database}.{source_table} is missing required columns: {missing_columns}")


def audit_event_coverage(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    reporter: NewsReactionProgress | None = None,
) -> CoverageAudit:
    expected = expected_event_tables(args)
    existing = tuple(name for name in expected if table_exists(client, args.market_database, name))
    if not existing:
        return CoverageAudit((), expected, "", "", 0)
    text = monitored_execute(client, event_coverage_sql(args, existing), reporter, "preflight compact-event coverage")
    row = parse_one_json(text)
    populated = tuple(sorted(str(name) for name in row.get("populated_tables") or ()))
    missing = tuple(name for name in expected if name not in populated)
    event_rows = int(row.get("event_rows") or 0)
    return CoverageAudit(
        source_tables=populated,
        missing_source_tables=missing,
        event_min_date=str(row.get("event_min_date") or "") if event_rows else "",
        event_max_date=str(row.get("event_max_date") or "") if event_rows else "",
        event_rows=event_rows,
    )


def event_coverage_sql(args: argparse.Namespace, existing: Sequence[str]) -> str:
    names = ", ".join(sql_string(name) for name in existing)
    return f"""
SELECT
    toString(min(min_date)) AS event_min_date,
    toString(max(max_date)) AS event_max_date,
    sum(rows) AS event_rows,
    arraySort(groupUniqArray(table)) AS populated_tables
FROM system.parts
WHERE active
  AND database = {sql_string(args.market_database)}
  AND table IN ({names})
FORMAT JSONEachRow
"""


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
            "quality_status", "quality_flags", "source_revision", "observation_count",
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
    text_sources = (
        ("ifNull(title, '')", 1),
        ("concat(ifNull(teaser, ''), ' ', ifNull(body_text, ''), ' ', ifNull(external_text, ''), ' ', ifNull(pdf_text, ''))", 2),
        ("arrayStringConcat(provider_tags, ' ')", 4),
        ("arrayStringConcat(channels, ' ')", 8),
    )

    def has_any(text_expression: str, rule: PhraseRule) -> str:
        needles = ", ".join(sql_string(needle) for needle in rule.needles)
        if len(rule.needles) == 1:
            return f"positionCaseInsensitiveUTF8({text_expression}, {needles}) > 0"
        return f"multiSearchAnyCaseInsensitiveUTF8({text_expression}, [{needles}])"

    phrase_matches = []
    for rule in rules:
        mask = " + ".join(
            f"if({has_any(text_expression, rule)}, {bit}, 0)"
            for text_expression, bit in text_sources
        )
        phrase_matches.append(f"tuple({sql_string(rule.phrase_id)}, toUInt8({mask}))")
    phrase_match_array = "[\n            " + ",\n            ".join(phrase_matches) + "\n        ]"

    source = table(args.news_database, args.normalized_table)
    target = table(args.news_database, args.features_table)
    return f"""
INSERT INTO {target}
SELECT
    {sql_string(PHRASE_DICTIONARY_VERSION)} AS extraction_version,
    canonical_news_id,
    published_at_utc,
    phrase_match.1 AS phrase_id,
    phrase_match.2 AS source_mask,
    text_hash,
    now64(6) AS extracted_at
FROM {source} FINAL
ARRAY JOIN arrayFilter(match -> match.2 > 0, {phrase_match_array}) AS phrase_match
WHERE published_at_utc >= {dt_sql(start.isoformat())}
  AND published_at_utc < {dt_sql(end.isoformat())}
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
    pending: list[tuple[dt.date, dt.date]] = []
    for start, end in chunks:
        unit = f"{start}:{end}"
        if (start, end) in completed:
            reporter.unit_done("reactions", unit, status="skipped")
            continue
        if args.replace_existing:
            delete_version_range(client, args, args.reactions_table, "label_version", LABEL_VERSION, start, end, reporter)
        pending.append((start, end))

    if not pending:
        return results
    total_memory = memory_bytes(str(args.max_memory_usage)) if str(args.max_memory_usage) not in {"", "0"} else 0
    for month_start, month_end in calendar_month_chunks(pending[0][0], pending[-1][1]):
        month_pending = [(start, end) for start, end in pending if month_start <= start < month_end]
        if not month_pending:
            continue
        cache_start = min(start for start, _end in month_pending)
        cache_end = max(end for _start, end in month_pending)
        link_counts = reaction_link_counts(client, args, cache_start, cache_end)
        active: list[tuple[dt.date, dt.date, int]] = []
        for start, end in month_pending:
            link_count = sum(
                count for publication_date, count in link_counts.items()
                if start <= publication_date < end
            )
            unit = f"{start}:{end}"
            if link_count == 0:
                reporter.chunk_start("reactions", unit)
                result = complete_empty_reaction_chunk(client, args, start, end)
                results.append(result)
                reporter.unit_done("reactions", unit, status="complete", rows=0, elapsed_seconds=result.elapsed_seconds)
            else:
                active.append((start, end, link_count))
        if not active:
            continue

        cache_table_name = "_news_reaction_event_cache_month_" + uuid.uuid4().hex[-24:]
        cache_target = table(args.news_database, cache_table_name)
        cache_query_prefix = "news-reaction-cache-" + uuid.uuid4().hex
        reporter.message(
            f"building monthly exact-event cache month={month_start:%Y-%m} "
            f"ticker_shards={args.reaction_ticker_shards} active_chunks={len(active)}"
        )
        primary_error: BaseException | None = None
        try:
            build_month_event_cache(
                client,
                args,
                cache_start,
                cache_end,
                cache_table_name,
                query_id_prefix=cache_query_prefix,
                query_threads=int(args.max_threads),
                query_memory=total_memory,
            )
            workers = min(int(args.reaction_workers), len(active))
            query_threads = max(1, int(args.max_threads) // workers)
            query_memory = max(256 * 1024**2, total_memory // workers) if total_memory else 0
            reporter.message(
                f"reaction month={month_start:%Y-%m} workers={workers} "
                f"clickhouse_threads_per_worker={query_threads} "
                f"memory_per_worker={query_memory if query_memory else 'server_default'}"
            )
            futures: dict[concurrent.futures.Future[ChunkResult], tuple[dt.date, dt.date, str]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="news-reaction") as pool:
                for start, end, link_count in active:
                    unit = f"{start}:{end}"
                    reporter.chunk_start("reactions", unit)
                    query_id = "news-reaction-" + uuid.uuid4().hex
                    future = pool.submit(
                        execute_reaction_chunk,
                        args,
                        start,
                        end,
                        query_threads,
                        query_memory,
                        query_id,
                        cache_table_name,
                        reaction_news_shard_count(args, link_count),
                        reporter,
                    )
                    futures[future] = (start, end, query_id)
                try:
                    for future in concurrent.futures.as_completed(futures):
                        start, end, _query_id = futures[future]
                        unit = f"{start}:{end}"
                        try:
                            result = future.result()
                        except BaseException as exc:
                            reporter.unit_failed("reactions", unit, exc)
                            for outstanding in futures:
                                outstanding.cancel()
                            cancel_reaction_queries(client, [query_id for _, _, query_id in futures.values()], reporter)
                            raise
                        results.append(result)
                        reporter.unit_done("reactions", unit, status="complete", rows=result.inserted_rows, elapsed_seconds=result.elapsed_seconds)
                except KeyboardInterrupt:
                    for future, (start, end, _query_id) in futures.items():
                        if not future.done():
                            future.cancel()
                            reporter.unit_interrupted("reactions", f"{start}:{end}")
                    cancel_reaction_queries(client, [query_id for _, _, query_id in futures.values()], reporter)
                    raise
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cancel_reaction_queries(client, [cache_query_prefix], reporter)
            try:
                client.execute(f"DROP TABLE IF EXISTS {cache_target} SYNC", query_id=f"{cache_query_prefix}_drop")
            except Exception as cleanup_exc:  # noqa: BLE001
                if primary_error is None:
                    raise
                primary_error.add_note(f"monthly event-cache cleanup also failed: {cleanup_exc}")
    return sorted(results, key=lambda item: item.start_date)


def reaction_link_counts(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
) -> dict[dt.date, int]:
    source = table(args.news_database, args.ticker_table)
    rows = client.execute(f"""
SELECT toDate(published_at_utc) AS publication_date, count()
FROM {source}
WHERE published_at_utc >= {dt_sql(start.isoformat())}
  AND published_at_utc < {dt_sql(end.isoformat())}
  AND ticker != ''
GROUP BY publication_date
ORDER BY publication_date
FORMAT TabSeparated
""")
    result: dict[dt.date, int] = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        publication_date, count = line.split("\t", 1)
        result[dt.date.fromisoformat(publication_date)] = int(count)
    return result


def reaction_news_shard_count(args: argparse.Namespace, link_count: int) -> int:
    return min(
        int(args.reaction_max_news_shards),
        max(1, (int(link_count) + int(args.reaction_links_per_shard) - 1) // int(args.reaction_links_per_shard)),
    )


def complete_empty_reaction_chunk(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
) -> ChunkResult:
    started = time.perf_counter()
    target = table(args.news_database, args.reactions_table)
    client.execute(
        f"ALTER TABLE {target} DELETE WHERE label_version = {sql_string(LABEL_VERSION)} "
        f"AND published_at_utc >= {dt_sql(start.isoformat())} "
        f"AND published_at_utc < {dt_sql(end.isoformat())} SETTINGS mutations_sync = 2"
    )
    result = ChunkResult("reactions", start.isoformat(), end.isoformat(), 0, time.perf_counter() - started)
    record_chunk(client, args, result, LABEL_VERSION)
    return result


def build_month_event_cache(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
    cache_table_name: str,
    *,
    query_id_prefix: str,
    query_threads: int,
    query_memory: int,
) -> None:
    cache_target = table(args.news_database, cache_table_name)
    client.execute(f"DROP TABLE IF EXISTS {cache_target} SYNC", query_id=f"{query_id_prefix}_reset")
    for shard_index in range(int(args.reaction_ticker_shards)):
        client.execute(
            event_cache_create_sql(
                args,
                start,
                end,
                cache_table_name,
                ticker_shard_index=shard_index,
                ticker_shard_count=int(args.reaction_ticker_shards),
                create_table=shard_index == 0,
                include_benchmark=shard_index == 0,
            )
            + settings_sql(
                args,
                max_threads=query_threads,
                max_memory_usage=query_memory,
                external_group_by=True,
            ),
            query_id=f"{query_id_prefix}_{shard_index:03d}",
        )


def execute_reaction_chunk(
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
    query_threads: int,
    query_memory: int,
    query_id: str,
    cache_table_name: str,
    news_shard_count: int,
    reporter: NewsReactionProgress | None = None,
) -> ChunkResult:
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    started = time.perf_counter()
    target = table(args.news_database, args.reactions_table)
    active_shard_count = news_shard_count
    attempt = 0
    while True:
        client.execute(
            f"ALTER TABLE {target} DELETE WHERE label_version = {sql_string(LABEL_VERSION)} "
            f"AND published_at_utc >= {dt_sql(start.isoformat())} "
            f"AND published_at_utc < {dt_sql(end.isoformat())} SETTINGS mutations_sync = 2",
            query_id=f"{query_id}_attempt_{attempt:02d}_reset",
        )
        try:
            for shard_index in range(active_shard_count):
                client.execute(
                    reaction_insert_sql(
                        args,
                        start,
                        end,
                        ticker_shard_index=shard_index,
                        ticker_shard_count=active_shard_count,
                        event_cache_table_name=cache_table_name,
                    )
                    + settings_sql(
                        args,
                        experimental_join=True,
                        max_threads=query_threads,
                        max_memory_usage=query_memory,
                    ),
                    query_id=f"{query_id}_attempt_{attempt:02d}_shard_{shard_index:03d}",
                )
        except RuntimeError as exc:
            if not is_clickhouse_memory_limit(exc) or active_shard_count >= int(args.reaction_max_news_shards):
                raise
            next_shard_count = min(int(args.reaction_max_news_shards), active_shard_count * 2)
            if reporter is not None:
                reporter.message(
                    f"reaction memory retry chunk={start}:{end} "
                    f"news_shards={active_shard_count}->{next_shard_count}"
                )
            active_shard_count = next_shard_count
            attempt += 1
            continue
        break
    after = count_version_range(client, args, args.reactions_table, "label_version", LABEL_VERSION, start, end)
    result = ChunkResult("reactions", start.isoformat(), end.isoformat(), after, time.perf_counter() - started)
    record_chunk(client, args, result, LABEL_VERSION)
    return result


def is_clickhouse_memory_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "memory_limit_exceeded" in text or "memory limit exceeded" in text


def cancel_reaction_queries(
    client: ClickHouseHttpClient,
    query_ids: Sequence[str],
    reporter: NewsReactionProgress | None = None,
) -> None:
    if not query_ids:
        return
    values = ", ".join(sql_string(query_id) for query_id in query_ids)
    try:
        client.execute(f"KILL QUERY WHERE arrayExists(prefix -> startsWith(query_id, prefix), [{values}]) ASYNC")
    except Exception as exc:  # noqa: BLE001
        if reporter is not None:
            reporter.message(f"WARN concurrent query cancellation failed: {exc}")


def reaction_insert_sql(
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
    *,
    ticker_shard_index: int | None = None,
    ticker_shard_count: int | None = None,
    event_ticker_shard_index: int | None = None,
    event_ticker_shard_count: int | None = None,
    include_benchmark: bool = True,
    event_cache_table_name: str | None = None,
) -> str:
    if (ticker_shard_index is None) != (ticker_shard_count is None):
        raise ValueError("ticker shard index and count must be provided together")
    if ticker_shard_count is not None and not (0 <= int(ticker_shard_index) < int(ticker_shard_count)):
        raise ValueError("ticker shard index must be inside the configured shard count")
    if (event_ticker_shard_index is None) != (event_ticker_shard_count is None):
        raise ValueError("event ticker shard index and count must be provided together")
    if event_ticker_shard_count is not None and not (
        0 <= int(event_ticker_shard_index) < int(event_ticker_shard_count)
    ):
        raise ValueError("event ticker shard index must be inside the configured shard count")
    ticker_shard_sql = ""
    ticker_shard_t_sql = ""
    if ticker_shard_count is not None:
        ticker_shard_sql = (
            f" AND cityHash64(canonical_news_id, upperUTF8(ticker)) % toUInt64({int(ticker_shard_count)}) "
            f"= toUInt64({int(ticker_shard_index)})"
        )
        ticker_shard_t_sql = (
            f" AND cityHash64(t.canonical_news_id, upperUTF8(t.ticker)) % toUInt64({int(ticker_shard_count)}) "
            f"= toUInt64({int(ticker_shard_index)})"
        )
    event_ticker_shard_sql = ""
    if event_ticker_shard_count is not None:
        event_ticker_shard_sql = (
            f" AND cityHash64(upperUTF8(ticker)) % toUInt64({int(event_ticker_shard_count)}) "
            f"= toUInt64({int(event_ticker_shard_index)})"
        )
    ticker_source = table(args.news_database, args.ticker_table)
    normalized = table(args.news_database, args.normalized_table)
    calendar_table = table(args.news_database, args.calendar_table)
    target = table(args.news_database, args.reactions_table)
    lookback = start - dt.timedelta(days=8)
    lookahead = end + dt.timedelta(days=8)
    authority_start, authority_end = event_authority_bounds(args)
    event_start = max(lookback, authority_start)
    event_end = min(lookahead, authority_end)
    events = event_source_table(args, lookback, lookahead)
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
    source_name = f"{args.events_table}_YYYY" if events_table_uses_year_suffix(args.events_table) else str(args.events_table)
    source_revision = sql_string(f"compact_events_exact_v1:{args.market_database}.{source_name}")
    condition_reference = table(args.market_database, args.condition_reference_table)
    benchmark_event_prefix = f"ticker = {sql_string(args.benchmark_ticker.upper())} OR " if include_benchmark else ""
    sql = f"""
INSERT INTO {target}
WITH
    (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference} WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND update_last = 1) AS update_last_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference} WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND update_high_low = 1) AS update_high_low_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference} WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND update_last = 1 AND update_high_low = 1) AS fully_price_eligible_tokens,
    (SELECT any(toUInt8(token_id)) FROM {condition_reference} WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND modifier_int = 12) AS form_t_token,
updates AS
(
    SELECT
        ticker AS ticker,
        event_date,
        toNullable(toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) = 2, 10000.0, 100.0)) AS trade_close,
        trade_close AS trade_high,
        trade_close AS trade_low,
        toUInt64(sip_timestamp_us) AS first_trade_timestamp_us,
        toUInt64(sip_timestamp_us) AS last_trade_timestamp_us,
        condition_token_1,
        condition_token_2,
        condition_token_3,
        condition_token_4,
        condition_token_5,
        toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us), 'UTC'), 'America/New_York') AS local_timestamp,
        toUInt8(toHour(local_timestamp) < 9 OR (toHour(local_timestamp) = 9 AND toMinute(local_timestamp) < 30) OR toHour(local_timestamp) >= 16) AS is_extended_hours
    FROM {events}
    PREWHERE event_date >= toDate({sql_string(event_start.isoformat())})
      AND event_date < toDate({sql_string(event_end.isoformat())})
    WHERE bitAnd(event_meta, 1) = 1
      AND sip_timestamp_us > 0
      AND ordinal > 0
      AND price_primary_int > 0
      AND size_primary > 0
      -- Canonical SIP tickers are already uppercase. Keep this predicate on the
      -- raw ORDER BY key so ClickHouse can prune symbols before decoding events.
      AND ({benchmark_event_prefix}ticker IN
      (
          SELECT DISTINCT upperUTF8(ticker)
          FROM {ticker_source}
          WHERE published_at_utc >= {dt_sql(start.isoformat())}
            AND published_at_utc < {dt_sql(end.isoformat())}
            AND upperUTF8(ticker) != {sql_string(args.benchmark_ticker.upper())}
            {ticker_shard_sql}
            {event_ticker_shard_sql}
      ))
),
points AS
(
    SELECT
        ticker,
        event_date,
        first_trade_timestamp_us,
        last_trade_timestamp_us,
        trade_close AS price,
        trade_high,
        trade_low,
        'eligible_trade_event' AS price_basis,
        update_last,
        update_high_low
    FROM
    (
        SELECT
            *,
            arrayFilter(token -> token != 0, [condition_token_1, condition_token_2, condition_token_3, condition_token_4, condition_token_5]) AS condition_tokens,
            toUInt8(
                empty(condition_tokens)
                OR if(
                    is_extended_hours AND has(condition_tokens, form_t_token)
                        AND arrayAll(token -> token = form_t_token OR has(fully_price_eligible_tokens, token), condition_tokens),
                    1,
                    arrayAll(token -> has(update_last_tokens, token), condition_tokens)
                )
            ) AS update_last,
            toUInt8(
                empty(condition_tokens)
                OR if(
                    is_extended_hours AND has(condition_tokens, form_t_token)
                        AND arrayAll(token -> token = form_t_token OR has(fully_price_eligible_tokens, token), condition_tokens),
                    1,
                    arrayAll(token -> has(update_high_low_tokens, token), condition_tokens)
                )
            ) AS update_high_low
        FROM updates
    )
    WHERE isNotNull(trade_close)
      AND isNotNull(trade_high)
      AND isNotNull(trade_low)
      AND first_trade_timestamp_us > 0
      AND last_trade_timestamp_us >= first_trade_timestamp_us
),
point_arrays AS
(
    SELECT
        ticker,
        event_date,
        arraySort(event -> tupleElement(event, 1), groupArrayIf(
            tuple(last_trade_timestamp_us, price, update_last, update_high_low),
            update_last = 1 OR update_high_low = 1
        )) AS eligible_events
    FROM points
    WHERE event_date IN (SELECT window_event_date FROM window_event_dates)
    GROUP BY ticker, event_date
),
prior_anchor_points AS
(
    SELECT
        ticker,
        argMaxIf(
            tuple(last_trade_timestamp_us, price),
            last_trade_timestamp_us,
            update_last = 1
            AND last_trade_timestamp_us < toUInt64(toUnixTimestamp64Micro({dt_sql(start.isoformat())}))
        ) AS prior_anchor_event
    FROM points
    GROUP BY ticker
),
market_prior_anchor AS
(
    SELECT prior_anchor_event AS market_prior_anchor_event
    FROM prior_anchor_points
    WHERE ticker = {sql_string(args.benchmark_ticker.upper())}
),
market_all_events AS
(
    SELECT
        arraySort(event -> tupleElement(event, 1), groupArrayArray(eligible_events)) AS all_market_events
    FROM point_arrays
    WHERE ticker = {sql_string(args.benchmark_ticker.upper())}
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
      {ticker_shard_t_sql}
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
        toUInt8(1) AS market_asof_key,
        tupleElement(horizon, 1) AS horizon_code,
        tupleElement(horizon, 2) AS horizon_type,
        tupleElement(horizon, 3) AS target_us,
        tupleElement(horizon, 4) AS applicable
    FROM news_resolved
    ARRAY JOIN {horizon_array} AS horizon
),
window_event_dates AS
(
    SELECT DISTINCT
        addDays(toDate(published_at_utc), day_offset) AS window_event_date
    FROM windows
    ARRAY JOIN range(toUInt32(greatest(0, dateDiff('day', toDate(published_at_utc), toDate(fromUnixTimestamp64Micro(toInt64(target_us)))))) + 1) AS day_offset
),
news_event_bounds AS
(
    SELECT
        canonical_news_id,
        ticker,
        any(published_at_utc) AS published_at_utc,
        any(available_at_utc) AS available_at_utc,
        any(reaction_session_date) AS reaction_session_date,
        any(publication_session) AS publication_session,
        any(pub_us) AS pub_us,
        max(target_us) AS max_target_us
    FROM windows
    GROUP BY canonical_news_id, ticker
),
asset_event_sets AS
(
    SELECT
        joined.canonical_news_id,
        joined.ticker,
        any(joined.published_at_utc) AS published_at_utc,
        any(joined.available_at_utc) AS available_at_utc,
        any(joined.reaction_session_date) AS reaction_session_date,
        any(joined.publication_session) AS publication_session,
        any(joined.pub_us) AS pub_us,
        any(joined.prior_anchor_event) AS prior_anchor_event,
        arraySort(event -> tupleElement(event, 1), groupArrayArray(joined.daily_events)) AS all_events
    FROM
    (
        SELECT
            day_window.canonical_news_id AS canonical_news_id,
            day_window.ticker AS ticker,
            day_window.published_at_utc AS published_at_utc,
            day_window.available_at_utc AS available_at_utc,
            day_window.reaction_session_date AS reaction_session_date,
            day_window.publication_session AS publication_session,
            day_window.pub_us AS pub_us,
            prior.prior_anchor_event AS prior_anchor_event,
            arrayFilter(event -> tupleElement(event, 1) <= day_window.max_target_us, p.eligible_events) AS daily_events
        FROM
        (
            SELECT
                anchored.canonical_news_id AS canonical_news_id,
                anchored.ticker AS ticker,
                anchored.published_at_utc AS published_at_utc,
                anchored.available_at_utc AS available_at_utc,
                anchored.reaction_session_date AS reaction_session_date,
                anchored.publication_session AS publication_session,
                anchored.pub_us AS pub_us,
                anchored.max_target_us AS max_target_us,
                addDays(toDate(anchored.published_at_utc), day_offset) AS window_event_date
            FROM news_event_bounds AS anchored
            ARRAY JOIN range(toUInt32(greatest(0, dateDiff('day', toDate(anchored.published_at_utc), toDate(fromUnixTimestamp64Micro(toInt64(anchored.max_target_us)))))) + 1) AS day_offset
        ) AS day_window
        LEFT JOIN point_arrays AS p
          ON day_window.ticker = p.ticker
         AND day_window.window_event_date = p.event_date
        LEFT JOIN prior_anchor_points AS prior
          ON day_window.ticker = prior.ticker
    ) AS joined
    GROUP BY joined.canonical_news_id, joined.ticker
),
asset_event_windows AS
(
    SELECT
        event_set.canonical_news_id,
        event_set.ticker,
        event_set.published_at_utc,
        event_set.available_at_utc,
        event_set.reaction_session_date,
        event_set.publication_session,
        event_set.pub_us,
        window.horizon_code,
        window.horizon_type,
        window.applicable,
        window.target_us,
        event_set.prior_anchor_event,
        event_set.all_events
    FROM asset_event_sets AS event_set
    INNER JOIN windows AS window
      ON event_set.canonical_news_id = window.canonical_news_id
     AND event_set.ticker = window.ticker
),
asset_metrics AS
(
    SELECT
        canonical_news_id,
        ticker,
        published_at_utc,
        available_at_utc,
        reaction_session_date,
        publication_session,
        horizon_code,
        horizon_type,
        applicable,
        target_us,
        arrayLast(event -> tupleElement(event, 3) = 1 AND tupleElement(event, 1) < pub_us AND tupleElement(event, 1) <= target_us, all_events) AS current_anchor_event,
        if(tupleElement(current_anchor_event, 1) > 0, current_anchor_event, tuple(tupleElement(prior_anchor_event, 1), tupleElement(prior_anchor_event, 2), toUInt8(1), toUInt8(1))) AS anchor_event,
        if(tupleElement(anchor_event, 1) > 0, toNullable(tupleElement(anchor_event, 1)), toNullable(NULL)) AS anchor_ts_us,
        if(tupleElement(anchor_event, 1) > 0, toNullable(tupleElement(anchor_event, 2)), toNullable(NULL)) AS anchor_price,
        if(anchor_ts_us > 0, toNullable('eligible_trade_event'), toNullable(NULL)) AS anchor_basis,
        arrayLast(event -> applicable = 1 AND tupleElement(event, 3) = 1 AND tupleElement(event, 1) > pub_us AND tupleElement(event, 1) <= target_us, all_events) AS target_event,
        if(tupleElement(target_event, 1) > 0, toNullable(tupleElement(target_event, 2)), toNullable(NULL)) AS target_price,
        if(tupleElement(target_event, 1) > 0, toNullable(tupleElement(target_event, 1)), toNullable(NULL)) AS target_ts_us,
        if(tupleElement(target_event, 1) > 0, toNullable('eligible_trade_event'), toNullable(NULL)) AS target_basis,
        arrayMax(event -> if(applicable = 1 AND tupleElement(event, 4) = 1 AND tupleElement(event, 1) > pub_us AND tupleElement(event, 1) <= target_us, tuple(tupleElement(event, 2), tupleElement(event, 1)), tuple(toFloat64('-inf'), toUInt64(0))), all_events) AS high_event,
        if(tupleElement(high_event, 2) > 0, toNullable(tupleElement(high_event, 1)), toNullable(NULL)) AS high_price,
        if(tupleElement(high_event, 2) > 0, toNullable(tupleElement(high_event, 2)), toNullable(NULL)) AS high_ts_us,
        arrayMin(event -> if(applicable = 1 AND tupleElement(event, 4) = 1 AND tupleElement(event, 1) > pub_us AND tupleElement(event, 1) <= target_us, tuple(tupleElement(event, 2), tupleElement(event, 1)), tuple(toFloat64('inf'), toUInt64(0))), all_events) AS low_event,
        if(tupleElement(low_event, 2) > 0, toNullable(tupleElement(low_event, 1)), toNullable(NULL)) AS low_price,
        if(tupleElement(low_event, 2) > 0, toNullable(tupleElement(low_event, 2)), toNullable(NULL)) AS low_ts_us,
        toUInt64(arrayCount(event -> applicable = 1 AND (tupleElement(event, 3) = 1 OR tupleElement(event, 4) = 1) AND tupleElement(event, 1) > pub_us AND tupleElement(event, 1) <= target_us, all_events)) AS observation_count,
        arrayLast(event -> tupleElement(event, 3) = 1 AND tupleElement(event, 1) < pub_us, market_events.all_market_events) AS current_market_anchor_event,
        if(tupleElement(current_market_anchor_event, 1) > 0, current_market_anchor_event, tuple(tupleElement(market_prior.market_prior_anchor_event, 1), tupleElement(market_prior.market_prior_anchor_event, 2), toUInt8(1), toUInt8(1))) AS market_anchor_event,
        if(tupleElement(market_anchor_event, 1) > 0, toNullable(tupleElement(market_anchor_event, 2)), toNullable(NULL)) AS market_anchor_price,
        arrayLast(event -> applicable = 1 AND tupleElement(event, 3) = 1 AND tupleElement(event, 1) > pub_us AND tupleElement(event, 1) <= target_us, market_events.all_market_events) AS market_target_event,
        if(tupleElement(market_target_event, 1) > 0, toNullable(tupleElement(market_target_event, 2)), toNullable(NULL)) AS market_target_price,
        arrayLast(event -> tupleElement(event, 3) = 1 AND tupleElement(event, 1) <= high_ts_us, market_events.all_market_events) AS aligned_market_high_event,
        arrayLast(event -> tupleElement(event, 3) = 1 AND tupleElement(event, 1) <= low_ts_us, market_events.all_market_events) AS aligned_market_low_event,
        if(tupleElement(aligned_market_high_event, 1) > 0, toNullable(tupleElement(aligned_market_high_event, 2)), toNullable(NULL)) AS aligned_market_high_price,
        if(tupleElement(aligned_market_low_event, 1) > 0, toNullable(tupleElement(aligned_market_low_event, 2)), toNullable(NULL)) AS aligned_market_low_price
    FROM asset_event_windows AS a
    CROSS JOIN market_all_events AS market_events
    CROSS JOIN market_prior_anchor AS market_prior
),
overlaps AS
(
    SELECT
        w.canonical_news_id,
        w.ticker,
        w.horizon_code,
        toUInt32(countIf(
            o.canonical_news_id != w.canonical_news_id
            AND o.published_at_utc > w.published_at_utc
            AND o.published_at_utc <= fromUnixTimestamp64Micro(toInt64(w.target_us))
        )) AS overlapping_news_count
    FROM windows AS w
    LEFT JOIN
    (
        SELECT * FROM {ticker_source}
        WHERE published_at_utc >= {dt_sql(lookback.isoformat())}
          AND published_at_utc < {dt_sql(lookahead.isoformat())}
    ) AS o
      ON upperUTF8(o.ticker) = w.ticker
    GROUP BY w.canonical_news_id, w.ticker, w.horizon_code
),
final_rows AS
(
    SELECT
        canonical_news_id,
        ticker,
        published_at_utc,
        available_at_utc,
        reaction_session_date,
        publication_session,
        horizon_code,
        horizon_type,
        applicable,
        target_us,
        anchor_ts_us,
        anchor_price,
        anchor_basis,
        target_price,
        target_ts_us,
        target_basis,
        high_price,
        high_ts_us,
        low_price,
        low_ts_us,
        observation_count,
        market_anchor_price,
        market_target_price,
        ifNull(o.overlapping_news_count, toUInt32(0)) AS overlap_count,
        if(isNotNull(anchor_price) AND anchor_price > 0 AND isNotNull(target_price), target_price / anchor_price - 1.0, toNullable(NULL)) AS target_return,
        if(isNotNull(anchor_price) AND anchor_price > 0 AND isNotNull(high_price), high_price / anchor_price - 1.0, toNullable(NULL)) AS high_return,
        if(isNotNull(anchor_price) AND anchor_price > 0 AND isNotNull(low_price), low_price / anchor_price - 1.0, toNullable(NULL)) AS low_return,
        if(isNotNull(market_anchor_price) AND market_anchor_price > 0 AND isNotNull(market_target_price), market_target_price / market_anchor_price - 1.0, toNullable(NULL)) AS market_target_return,
        target_return - market_target_return AS abnormal_target_return,
        high_return - if(isNotNull(market_anchor_price) AND market_anchor_price > 0 AND isNotNull(aligned_market_high_price), aligned_market_high_price / market_anchor_price - 1.0, toNullable(NULL)) AS abnormal_high_return,
        low_return - if(isNotNull(market_anchor_price) AND market_anchor_price > 0 AND isNotNull(aligned_market_low_price), aligned_market_low_price / market_anchor_price - 1.0, toNullable(NULL)) AS abnormal_low_return
    FROM asset_metrics
    LEFT JOIN overlaps AS o USING (canonical_news_id, ticker, horizon_code)
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
    overlap_count AS overlapping_news_count,
    multiIf(
        applicable = 0, 'not_applicable',
        isNull(anchor_price), 'missing_anchor',
        observation_count = 0 OR isNull(target_price), 'missing_target',
        publication_session != 'closed' AND (toUnixTimestamp64Micro(published_at_utc) - toInt64(anchor_ts_us)) > {int(args.active_anchor_max_age_seconds) * 1_000_000}, 'stale_anchor',
        (toInt64(target_us) - toInt64(target_ts_us)) > {int(args.target_max_age_seconds) * 1_000_000}, 'stale_target',
        isNull(market_anchor_price) OR isNull(market_target_price), 'missing_market_reference',
        overlap_count > 0, 'overlapping_news',
        'clean'
    ) AS quality_status,
    arrayFilter(value -> notEmpty(value), [
        if(applicable = 0, 'horizon_not_applicable', ''),
        if(isNull(anchor_price), 'missing_anchor', ''),
        if(observation_count = 0 OR isNull(target_price), 'missing_target', ''),
        if(publication_session != 'closed' AND isNotNull(anchor_ts_us) AND (toUnixTimestamp64Micro(published_at_utc) - toInt64(anchor_ts_us)) > {int(args.active_anchor_max_age_seconds) * 1_000_000}, 'stale_active_anchor', ''),
        if(isNotNull(target_ts_us) AND (toInt64(target_us) - toInt64(target_ts_us)) > {int(args.target_max_age_seconds) * 1_000_000}, 'stale_target', ''),
        if(isNull(market_anchor_price) OR isNull(market_target_price), 'missing_market_reference', ''),
        if(overlap_count > 0, 'overlapping_ticker_news', '')
    ]) AS quality_flags,
    {sql_string(CALENDAR_VERSION)} AS calendar_version,
    {source_revision} AS source_revision,
    now64(6) AS finalized_at
FROM final_rows
"""
    if event_cache_table_name:
        cached_point_ctes = f"""active_cache_tickers AS
(
    SELECT DISTINCT upperUTF8(ticker) AS ticker
    FROM {ticker_source}
    WHERE published_at_utc >= {dt_sql(start.isoformat())}
      AND published_at_utc < {dt_sql(end.isoformat())}
      AND upperUTF8(ticker) != {sql_string(args.benchmark_ticker.upper())}
      {ticker_shard_sql}
    UNION ALL
    SELECT {sql_string(args.benchmark_ticker.upper())} AS ticker
),
point_arrays AS
(
    SELECT ticker, event_date, eligible_events
    FROM {table(args.news_database, event_cache_table_name)}
    WHERE (
        (ticker = {sql_string(args.benchmark_ticker.upper())}
         AND event_date >= toDate({sql_string(start.isoformat())})
         AND event_date < toDate({sql_string(lookahead.isoformat())}))
        OR
        (ticker != {sql_string(args.benchmark_ticker.upper())}
         AND event_date IN (SELECT window_event_date FROM window_event_dates))
    )
      AND ticker IN (SELECT ticker FROM active_cache_tickers)
),
market_all_events AS
(
    SELECT arraySort(event -> tupleElement(event, 1), groupArrayArray(eligible_events)) AS all_market_events
    FROM point_arrays
    WHERE ticker = {sql_string(args.benchmark_ticker.upper())}
),
prior_anchor_points AS
(
    SELECT
        ticker,
        arrayLast(event -> tupleElement(event, 3) = 1, arraySort(
            event -> tupleElement(event, 1),
            groupArrayArray(eligible_events)
        )) AS prior_anchor_event
    FROM {table(args.news_database, event_cache_table_name)}
    WHERE event_date < toDate({sql_string(start.isoformat())})
      AND ticker IN (SELECT ticker FROM active_cache_tickers)
    GROUP BY ticker
),
market_prior_anchor AS
(
    SELECT prior_anchor_event AS market_prior_anchor_event
    FROM prior_anchor_points
    WHERE ticker = {sql_string(args.benchmark_ticker.upper())}
),
"""
        point_start = sql.index("point_arrays AS\n(")
        news_start = sql.index("news_base AS\n(", point_start)
        sql = sql[:point_start] + sql[news_start:]
        asset_sets_start = sql.index("asset_event_sets AS\n(")
        sql = sql[:asset_sets_start] + cached_point_ctes + sql[asset_sets_start:]
    return sql


def event_cache_create_sql(
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
    cache_table_name: str,
    *,
    ticker_shard_index: int,
    ticker_shard_count: int,
    create_table: bool = True,
    include_benchmark: bool = True,
) -> str:
    """Materialize one ticker shard in a shared monthly compact-event cache."""
    raw_sql = reaction_insert_sql(
        args,
        start,
        end,
        event_ticker_shard_index=ticker_shard_index,
        event_ticker_shard_count=ticker_shard_count,
        include_benchmark=include_benchmark,
    )
    with_body = raw_sql.split("\nWITH\n", 1)[1]
    event_ctes = with_body.split("news_base AS\n(", 1)[0]
    event_ctes = event_ctes.replace(
        "    WHERE event_date IN (SELECT window_event_date FROM window_event_dates)\n",
        f"    WHERE event_date >= toDate({sql_string(start.isoformat())})\n",
    )
    cache_target = table(args.news_database, cache_table_name)
    destination = (
        f"CREATE TABLE {cache_target}\nENGINE = MergeTree\nORDER BY (ticker, event_date)\nAS"
        if create_table
        else f"INSERT INTO {cache_target}"
    )
    return f"""
{destination}
WITH
{event_ctes}cache_rows AS
(
    SELECT ticker, event_date, eligible_events
    FROM point_arrays
    UNION ALL
    SELECT
        ticker,
        addDays(toDate({sql_string(start.isoformat())}), -1) AS event_date,
        [tuple(tupleElement(prior_anchor_event, 1), tupleElement(prior_anchor_event, 2), toUInt8(1), toUInt8(1))] AS eligible_events
    FROM prior_anchor_points
    WHERE tupleElement(prior_anchor_event, 1) > 0
)
SELECT ticker, event_date, eligible_events
FROM cache_rows
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


def calendar_month_chunks(start: dt.date, end: dt.date) -> Iterable[tuple[dt.date, dt.date]]:
    cursor = start.replace(day=1)
    while cursor < end:
        if cursor.month == 12:
            chunk_end = dt.date(cursor.year + 1, 1, 1)
        else:
            chunk_end = dt.date(cursor.year, cursor.month + 1, 1)
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


def settings_sql(
    args: argparse.Namespace,
    *,
    experimental_join: bool = False,
    max_threads: int | None = None,
    max_memory_usage: int | None = None,
    external_group_by: bool = False,
) -> str:
    settings = [f"max_threads = {int(max_threads if max_threads is not None else args.max_threads)}"]
    memory_limit = max_memory_usage if max_memory_usage is not None else (
        memory_bytes(str(args.max_memory_usage)) if str(args.max_memory_usage) not in {"", "0"} else 0
    )
    if memory_limit:
        settings.append(f"max_memory_usage = {int(memory_limit)}")
        if external_group_by:
            settings.append(f"max_bytes_before_external_group_by = {max(256 * 1024**2, int(memory_limit) // 2)}")
    if experimental_join:
        settings.append("allow_experimental_join_condition = 1")
        settings.append("join_algorithm = 'hash'")
        # Array-valued cache rows can otherwise make JoiningTransform request
        # multi-GiB blocks. Bound the join without weakening event semantics.
        settings.append("max_block_size = 1024")
        settings.append("max_joined_block_size_rows = 1024")
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
            "events": f"{args.market_database}." + (f"{args.events_table}_YYYY" if events_table_uses_year_suffix(args.events_table) else str(args.events_table)),
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
