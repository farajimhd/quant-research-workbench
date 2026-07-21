from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.news_reaction_extract import (  # noqa: E402
    LABEL_VERSION,
    date_arg,
    dt_sql,
    events_table_uses_year_suffix,
    event_table_for_year,
    monitored_execute,
    parse_one_json,
    table,
    table_exists,
)
from pipelines.news.benzinga.news_reaction_phrase_dictionary import (  # noqa: E402
    PHRASE_DICTIONARY_VERSION,
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


UTC = dt.timezone.utc
EASTERN = ZoneInfo("America/New_York")
FINALIZER_VERSION = "news_reaction_finalizer_v1"
QUALITY_VERSION = "news_reaction_quality_overlay_v1"
ROBUST_STATS_VERSION = "news_phrase_event_reaction_stats_v4"
PREDICTION_VERSION = "news_phrase_probability_classifier_v1"


@dataclass(frozen=True, slots=True)
class SourceWatermarks:
    captured_at_utc: str
    news_max_published_at_utc: str
    news_max_updated_at_utc: str
    ticker_max_updated_at_utc: str
    event_max_timestamp_utc: str
    event_max_date: str
    event_table: str
    news_settled_end_exclusive: str
    event_complete_end_exclusive: str
    stable_end_exclusive: str


@dataclass(frozen=True, slots=True)
class RepairUnit:
    stage: str
    start_date: str
    end_date_exclusive: str
    source_rows: int
    output_rows: int
    checkpoint_status: str
    reasons: tuple[str, ...]

    @property
    def days(self) -> int:
        return (date_arg(self.end_date_exclusive) - date_arg(self.start_date)).days


@dataclass(frozen=True, slots=True)
class FinalizationSummary:
    status: str
    execute: bool
    watermarks: SourceWatermarks
    feature_repairs: int
    reaction_repairs: int
    repair_days: int
    certified_feature_chunks: int
    certified_reaction_chunks: int
    quality_rows: int
    stats_rows: int
    holdout_predictions: int
    review_rows: int
    elapsed_seconds: float


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize the deterministic news/reaction reference: certify source watermarks, "
            "repair stale slices, exclude corporate-action contamination, rebuild robust "
            "2019-2025 statistics, and evaluate the stable 2026 holdout."
        )
    )
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2027-01-01", help="Exclusive requested publication bound.")
    parser.add_argument("--stats-start-date", default="2019-01-01")
    parser.add_argument("--stats-end-date", default="2026-01-01")
    parser.add_argument("--holdout-start-date", default="2026-01-01")
    parser.add_argument("--holdout-end-date", default="2027-01-01")
    parser.add_argument("--news-settle-hours", type=int, default=48)
    parser.add_argument("--review-sample-size", type=int, default=750)
    parser.add_argument("--minimum-phrase-support", type=int, default=30)
    parser.add_argument("--prediction-threshold", type=float, default=0.05)
    parser.add_argument("--return-outlier-absolute", type=float, default=2.0)
    parser.add_argument("--trim-lower", type=float, default=0.01)
    parser.add_argument("--trim-upper", type=float, default=0.99)
    parser.add_argument("--max-repair-days", type=int, default=62)
    parser.add_argument("--allow-large-repair", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--skip-repair", action="store_true")
    parser.add_argument("--keep-provisional-tail", action="store_true")
    parser.add_argument("--stages", default="watermarks,repair,quality,stats,evaluate")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=2.0)
    parser.add_argument("--progress-log-lines", type=int, default=8)
    parser.add_argument("--reaction-workers", type=int, default=4)
    parser.add_argument("--reaction-ticker-shards", type=int, default=32)
    parser.add_argument("--reaction-links-per-shard", type=int, default=100)
    parser.add_argument("--reaction-max-news-shards", type=int, default=64)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="24G")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--news-database", default="q_live")
    parser.add_argument("--market-database", default="market_sip_compact")
    parser.add_argument("--normalized-table", default="benzinga_news_normalized_v1")
    parser.add_argument("--ticker-table", default="benzinga_news_ticker_v1")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--calendar-table", default="news_reaction_calendar_v1")
    parser.add_argument("--features-table", default="news_language_features_v1")
    parser.add_argument("--reactions-table", default="news_reaction_labels_v2")
    parser.add_argument("--status-table", default="news_reaction_build_status_v1")
    parser.add_argument("--split-table", default="market_stock_split_v1")
    parser.add_argument("--quality-table", default="news_reaction_quality_overlay_v1")
    parser.add_argument("--robust-stats-table", default="news_phrase_reaction_stats_v3")
    parser.add_argument("--finalization-table", default="news_reaction_finalization_state_v1")
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root", default="D:/market-data/prepared/news_reaction_labels")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> tuple[str, ...]:
    start = date_arg(args.start_date)
    end = date_arg(args.end_date)
    stats_start = date_arg(args.stats_start_date)
    stats_end = date_arg(args.stats_end_date)
    holdout_start = date_arg(args.holdout_start_date)
    holdout_end = date_arg(args.holdout_end_date)
    if not start < end:
        raise SystemExit("--start-date must be before --end-date")
    if not start <= stats_start < stats_end <= holdout_start < holdout_end <= end:
        raise SystemExit("expected start <= stats range <= holdout range <= end, with non-overlapping train/holdout bounds")
    if args.news_settle_hours < 0:
        raise SystemExit("--news-settle-hours must be non-negative")
    if args.review_sample_size < 0:
        raise SystemExit("--review-sample-size must be non-negative")
    if args.minimum_phrase_support < 1:
        raise SystemExit("--minimum-phrase-support must be positive")
    if not 0 < args.prediction_threshold < 1:
        raise SystemExit("--prediction-threshold must be between zero and one")
    if not 0 <= args.trim_lower < args.trim_upper <= 1:
        raise SystemExit("trim bounds must satisfy 0 <= lower < upper <= 1")
    allowed = ("watermarks", "repair", "quality", "stats", "evaluate")
    stages = tuple(item.strip() for item in args.stages.split(",") if item.strip())
    invalid = sorted(set(stages) - set(allowed))
    if invalid:
        raise SystemExit(f"unknown stages {invalid}; expected a subset of {allowed}")
    if "evaluate" in stages and "stats" not in stages and not args.execute:
        # A dry run can inspect existing statistics; execution validation happens after schema preflight.
        pass
    return stages


def main(argv: Sequence[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args(argv)
    stages = validate_args(args)
    args.clickhouse_url = args.clickhouse_url or default_clickhouse_url()
    args.user = args.user or default_clickhouse_user()
    args.password = args.password or default_clickhouse_password()

    run_id = dt.datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root) / f"finalize_{run_id}"
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    totals = [("preflight", 4), ("watermarks", 1)]
    if "repair" in stages:
        totals.append(("repair", 3))
    if "quality" in stages:
        totals.append(("quality", 2))
    if "stats" in stages:
        totals.append(("stats", 2))
    if "evaluate" in stages:
        totals.append(("evaluate", 3))
    totals.append(("audit", 1))
    reporter = NewsReactionProgress(
        stage_totals=totals,
        run_id=run_id,
        run_root=str(run_root),
        layout=args.progress_layout,
        refresh_per_second=args.progress_refresh_per_second,
        log_lines=args.progress_log_lines,
    )
    started = time.perf_counter()
    with reporter:
        reporter.stage_start("preflight")
        ensure_sources(client, args)
        reporter.unit_done("preflight", "source schemas", status="complete")
        if args.execute:
            ensure_finalization_tables(client, args)
            reporter.unit_done("preflight", "finalization schemas", status="complete")
        else:
            reporter.unit_done("preflight", "finalization schemas", status="planned")
        reporter.message("secret_status=" + json.dumps(secret_status(["CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD"]), sort_keys=True))
        reporter.unit_done("preflight", "secret presence", status="complete")

        reporter.stage_start("watermarks")
        watermarks = load_source_watermarks(client, args)
        validate_generated_sql(client, args, watermarks, stages, reporter)
        reporter.message(
            "stable publication coverage "
            f"requested=[{args.start_date},{args.end_date}) "
            f"settled_end={watermarks.news_settled_end_exclusive} "
            f"event_end={watermarks.event_complete_end_exclusive} "
            f"stable_end={watermarks.stable_end_exclusive}"
        )
        reporter.unit_done("watermarks", "source authority", status="complete")
        reporter.unit_done("preflight", "stable source boundary", status="complete")

        feature_repairs = load_feature_repairs(client, args, watermarks)
        reaction_repairs = load_reaction_repairs(client, args, watermarks)
        repair_days = sum(item.days for item in feature_repairs + reaction_repairs)
        write_json(run_root / "repair_plan.json", {
            "watermarks": asdict(watermarks),
            "feature_repairs": [asdict(item) for item in feature_repairs],
            "reaction_repairs": [asdict(item) for item in reaction_repairs],
        })
        reporter.message(
            f"repair plan feature_chunks={len(feature_repairs):,} reaction_days={len(reaction_repairs):,} "
            f"summed_stage_days={repair_days:,}"
        )
        if repair_days > args.max_repair_days and not args.allow_large_repair:
            raise SystemExit(
                f"repair plan covers {repair_days} summed stage-days, above --max-repair-days={args.max_repair_days}; "
                "inspect repair_plan.json, then rerun with --allow-large-repair only if the scope is expected"
            )

        if not args.execute:
            summary = FinalizationSummary(
                status="plan_validated",
                execute=False,
                watermarks=watermarks,
                feature_repairs=len(feature_repairs),
                reaction_repairs=len(reaction_repairs),
                repair_days=repair_days,
                certified_feature_chunks=0,
                certified_reaction_chunks=0,
                quality_rows=0,
                stats_rows=0,
                holdout_predictions=0,
                review_rows=0,
                elapsed_seconds=time.perf_counter() - started,
            )
            write_json(run_root / "news_reaction_finalization_summary.json", asdict(summary))
            reporter.message(f"plan={run_root / 'repair_plan.json'}")
            reporter.finish("plan_validated")
            return 0

        if "repair" in stages:
            reporter.stage_start("repair")
            if not args.keep_provisional_tail:
                purge_provisional_tail(client, args, watermarks, reporter)
            reporter.unit_done("repair", "provisional tail", status="preserved" if args.keep_provisional_tail else "removed")
            if not args.skip_repair:
                run_repairs(args, feature_repairs, reaction_repairs, reporter)
            reporter.unit_done("repair", "stale source slices", status="skipped" if args.skip_repair else "complete")
            post_feature = load_feature_repairs(client, args, watermarks)
            post_reaction = load_reaction_repairs(client, args, watermarks)
            if post_feature or post_reaction:
                raise RuntimeError(
                    f"repair verification failed: feature_chunks={len(post_feature)} reaction_days={len(post_reaction)}; "
                    "finalization state was not advanced"
                )
            reporter.unit_done("repair", "repair verification", status="complete")

        quality_rows = 0
        if "quality" in stages:
            reporter.stage_start("quality")
            quality_rows = rebuild_quality_overlay(client, args, watermarks, reporter)
            reporter.unit_done("quality", "corporate-action overlay", status="complete", rows=quality_rows)
            audit_quality_overlay(client, args, watermarks)
            reporter.unit_done("quality", "quality integrity", status="complete")

        stats_rows = 0
        if "stats" in stages:
            reporter.stage_start("stats")
            stats_rows = rebuild_robust_stats(client, args, reporter)
            reporter.unit_done("stats", "robust phrase statistics", status="complete", rows=stats_rows)
            audit_robust_stats(client, args)
            reporter.unit_done("stats", "statistics integrity", status="complete")

        evaluation: dict[str, Any] = {}
        review_rows = 0
        if "evaluate" in stages:
            reporter.stage_start("evaluate")
            evaluation = evaluate_holdout(client, args, watermarks)
            write_json(run_root / "holdout_evaluation.json", evaluation)
            reporter.unit_done("evaluate", "2026 holdout", status="complete", rows=int(evaluation.get("prediction_count") or 0))
            review_rows = write_review_sample(client, args, watermarks, run_root / "human_review_sample.csv")
            reporter.unit_done("evaluate", "stratified review sample", status="complete", rows=review_rows)
            write_json(run_root / "human_review_instructions.json", review_instructions(args))
            reporter.unit_done("evaluate", "review contract", status="complete")

        reporter.stage_start("audit")
        remaining_feature_repairs = load_feature_repairs(client, args, watermarks)
        remaining_reaction_repairs = load_reaction_repairs(client, args, watermarks)
        if remaining_feature_repairs or remaining_reaction_repairs:
            raise RuntimeError(
                "outputs cannot be certified while source-alignment repairs remain: "
                f"feature_chunks={len(remaining_feature_repairs)} reaction_days={len(remaining_reaction_repairs)}"
            )
        audit = audit_all(client, args, watermarks)
        write_json(run_root / "final_audit.json", audit)
        certify_chunks(client, args, watermarks, audit)
        reporter.unit_done("audit", "certified outputs", status="complete")

        summary = FinalizationSummary(
            status="completed",
            execute=True,
            watermarks=watermarks,
            feature_repairs=len(feature_repairs),
            reaction_repairs=len(reaction_repairs),
            repair_days=repair_days,
            certified_feature_chunks=int(audit["feature_chunks"]),
            certified_reaction_chunks=int(audit["reaction_chunks"]),
            quality_rows=quality_rows,
            stats_rows=stats_rows,
            holdout_predictions=int(evaluation.get("prediction_count") or 0),
            review_rows=review_rows,
            elapsed_seconds=time.perf_counter() - started,
        )
        write_json(run_root / "news_reaction_finalization_summary.json", asdict(summary))
        reporter.message(f"summary={run_root / 'news_reaction_finalization_summary.json'}")
        reporter.finish()
    return 0


def ensure_sources(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    required = (
        (args.news_database, args.normalized_table),
        (args.news_database, args.ticker_table),
        (args.news_database, args.calendar_table),
        (args.news_database, args.features_table),
        (args.news_database, args.reactions_table),
        (args.news_database, args.status_table),
        (args.news_database, args.split_table),
    )
    missing = [f"{database}.{name}" for database, name in required if not table_exists(client, database, name)]
    if missing:
        raise SystemExit("required tables are missing: " + ", ".join(missing))


def merge_tree_settings(storage_policy: str) -> str:
    return f"SETTINGS index_granularity = 8192, storage_policy = {sql_string(storage_policy)}" if storage_policy else "SETTINGS index_granularity = 8192"


def ensure_finalization_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    policy = merge_tree_settings(args.storage_policy)
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.news_database)}")
    for sql in (
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.quality_table)}
(
    quality_version LowCardinality(String),
    label_version LowCardinality(String),
    canonical_news_id String,
    ticker LowCardinality(String),
    horizon_code LowCardinality(String),
    published_at_utc DateTime64(9, 'UTC'),
    corporate_action_overlap UInt8,
    corporate_action_ids Array(String),
    return_outlier UInt8,
    eligible_for_statistics UInt8,
    exclusion_reasons Array(String),
    built_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (quality_version, label_version, horizon_code, ticker, published_at_utc, canonical_news_id)
{policy}
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.robust_stats_table)}
(
    stats_version LowCardinality(String),
    extraction_version LowCardinality(String),
    label_version LowCardinality(String),
    quality_version LowCardinality(String),
    phrase_id LowCardinality(String),
    horizon_code LowCardinality(String),
    publication_session LowCardinality(String),
    sample_count UInt64,
    eligible_sample_count UInt64,
    corporate_action_excluded_count UInt64,
    return_outlier_excluded_count UInt64,
    negative_count UInt64,
    neutral_count UInt64,
    positive_count UInt64,
    negative_probability Float64,
    neutral_probability Float64,
    positive_probability Float64,
    mean_target_return Nullable(Float64),
    trimmed_mean_target_return Nullable(Float64),
    median_target_return Nullable(Float64),
    mean_high_return Nullable(Float64),
    trimmed_mean_high_return Nullable(Float64),
    median_high_return Nullable(Float64),
    mean_low_return Nullable(Float64),
    trimmed_mean_low_return Nullable(Float64),
    median_low_return Nullable(Float64),
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
CREATE TABLE IF NOT EXISTS {table(args.news_database, args.finalization_table)}
(
    finalizer_version LowCardinality(String),
    stage LowCardinality(String),
    version LowCardinality(String),
    chunk_start Date,
    chunk_end_exclusive Date,
    source_rows UInt64,
    output_rows UInt64,
    source_max_updated_at_utc DateTime64(9, 'UTC'),
    event_max_timestamp_utc DateTime64(6, 'UTC'),
    source_signature String,
    status LowCardinality(String),
    audit_json String,
    certified_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(certified_at)
ORDER BY (finalizer_version, stage, version, chunk_start, chunk_end_exclusive)
{policy}
""",
    ):
        client.execute(sql)


def latest_event_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[str, dt.date]:
    start = date_arg(args.start_date)
    end = date_arg(args.end_date)
    candidates = (
        [str(args.events_table)]
        if not events_table_uses_year_suffix(args.events_table)
        else [event_table_for_year(args.events_table, year) for year in range(start.year, end.year + 1)]
    )
    existing = [name for name in candidates if table_exists(client, args.market_database, name)]
    if not existing:
        raise SystemExit("no canonical compact event table exists inside the requested authority")
    rows = client.execute(f"""
SELECT table, max(max_date) AS max_date
FROM system.parts
WHERE active AND database = {sql_string(args.market_database)}
  AND table IN ({', '.join(sql_string(name) for name in existing)})
GROUP BY table
ORDER BY max_date DESC
LIMIT 1
FORMAT TSV
""").strip().split("\t")
    if len(rows) != 2:
        raise RuntimeError("could not determine the latest populated compact-event table")
    return rows[0], date_arg(rows[1])


def load_source_watermarks(client: ClickHouseHttpClient, args: argparse.Namespace) -> SourceWatermarks:
    event_table, event_date = latest_event_table(client, args)
    event_row = parse_one_json(client.execute(f"""
SELECT
    max(sip_timestamp_us) AS max_us,
    toString(fromUnixTimestamp64Micro(toInt64(max_us))) AS event_max_timestamp_utc
FROM {table(args.market_database, event_table)}
PREWHERE event_date = toDate({sql_string(event_date.isoformat())})
WHERE sip_timestamp_us > 0
FORMAT JSONEachRow
"""))
    if int(event_row.get("max_us") or 0) <= 0:
        raise RuntimeError(f"latest event partition {event_table}:{event_date} contains no valid SIP timestamp")
    event_max = parse_clickhouse_datetime(str(event_row["event_max_timestamp_utc"]))
    news_row = parse_one_json(client.execute(f"""
SELECT
    toString(max(published_at_utc)) AS news_max_published_at_utc,
    toString(max(updated_at_utc)) AS news_max_updated_at_utc
FROM {table(args.news_database, args.normalized_table)} FINAL
WHERE published_at_utc >= {dt_sql(args.start_date)}
  AND published_at_utc < {dt_sql(args.end_date)}
FORMAT JSONEachRow
"""))
    ticker_row = parse_one_json(client.execute(f"""
SELECT toString(max(updated_at_utc)) AS ticker_max_updated_at_utc
FROM {table(args.news_database, args.ticker_table)} FINAL
WHERE published_at_utc >= {dt_sql(args.start_date)}
  AND published_at_utc < {dt_sql(args.end_date)}
FORMAT JSONEachRow
"""))
    event_complete_text = client.execute(f"""
SELECT toString(addDays(max(calendar_date), 1))
FROM {table(args.news_database, args.calendar_table)} FINAL
WHERE calendar_version != ''
  AND calendar_date >= toDate({sql_string(args.start_date)})
  AND calendar_date < toDate({sql_string(args.end_date)})
  AND if(is_session = 1, current_extended_close_utc, next_extended_close_utc)
      <= toDateTime64({sql_string(clickhouse_timestamp(event_max))}, 6, 'UTC')
""").strip()
    if not event_complete_text:
        raise RuntimeError("event watermark does not finalize any requested publication date")
    requested_end = date_arg(args.end_date)
    settled_end = min(
        requested_end,
        (dt.datetime.now(UTC) - dt.timedelta(hours=args.news_settle_hours)).date(),
    )
    event_complete_end = min(requested_end, date_arg(event_complete_text))
    stable_end = min(requested_end, settled_end, event_complete_end)
    if stable_end <= date_arg(args.start_date):
        raise RuntimeError("source watermarks do not provide a non-empty stable publication range")
    return SourceWatermarks(
        captured_at_utc=clickhouse_timestamp(dt.datetime.now(UTC)),
        news_max_published_at_utc=str(news_row.get("news_max_published_at_utc") or ""),
        news_max_updated_at_utc=str(news_row.get("news_max_updated_at_utc") or ""),
        ticker_max_updated_at_utc=str(ticker_row.get("ticker_max_updated_at_utc") or ""),
        event_max_timestamp_utc=clickhouse_timestamp(event_max),
        event_max_date=event_date.isoformat(),
        event_table=event_table,
        news_settled_end_exclusive=settled_end.isoformat(),
        event_complete_end_exclusive=event_complete_end.isoformat(),
        stable_end_exclusive=stable_end.isoformat(),
    )


def validate_generated_sql(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    watermarks: SourceWatermarks,
    stages: Sequence[str],
    reporter: NewsReactionProgress,
) -> None:
    """Parse every generated query whose referenced tables currently exist.

    Repair-plan queries are executed immediately after this check. Derived-stage
    queries are syntax-checked before any mutation; queries depending on a new
    table are checked after `--execute` creates the versioned schemas.
    """
    statements: list[tuple[str, str]] = []
    if "quality" in stages:
        statements.append(("quality overlay", select_body(quality_overlay_insert_sql(args, watermarks))))
    if "stats" in stages and table_exists(client, args.news_database, args.quality_table):
        statements.append(("robust statistics", select_body(robust_stats_insert_sql(args))))
    if "evaluate" in stages and table_exists(client, args.news_database, args.quality_table) and table_exists(client, args.news_database, args.robust_stats_table):
        statements.append(("holdout prediction", "WITH\n" + prediction_ctes(args, watermarks) + "\nSELECT count() FROM predictions"))
    for label, statement in statements:
        monitored_execute(client, "EXPLAIN SYNTAX " + statement, reporter, f"parse {label}")


def select_body(insert_sql: str) -> str:
    marker = "\nWITH\n"
    if marker not in insert_sql:
        raise ValueError("generated INSERT statement has no WITH body")
    return "WITH\n" + insert_sql.split(marker, 1)[1]


def load_feature_repairs(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks) -> list[RepairUnit]:
    rows = query_json_rows(client, feature_repair_sql(args, watermarks))
    return [repair_unit_from_row("features", row) for row in rows if row.get("reasons")]


def feature_repair_sql(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    return f"""
WITH
source AS
(
    SELECT
        toStartOfMonth(toDate(published_at_utc)) AS chunk_start,
        least(addMonths(chunk_start, 1), toDate({sql_string(watermarks.stable_end_exclusive)})) AS chunk_end_exclusive,
        count() AS source_rows,
        max(updated_at_utc) AS source_max_updated,
        groupBitXor(cityHash64(canonical_news_id, text_hash, toString(updated_at_utc))) AS source_hash
    FROM {table(args.news_database, args.normalized_table)} FINAL
    WHERE published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start, chunk_end_exclusive
),
features AS
(
    SELECT
        toStartOfMonth(toDate(published_at_utc)) AS chunk_start,
        least(addMonths(chunk_start, 1), toDate({sql_string(watermarks.stable_end_exclusive)})) AS chunk_end_exclusive,
        count() AS output_rows,
        countIf(f.text_hash != n.text_hash) AS stale_text_rows
    FROM (SELECT * FROM {table(args.news_database, args.features_table)} FINAL) AS f
    INNER JOIN (SELECT * FROM {table(args.news_database, args.normalized_table)} FINAL) AS n USING (canonical_news_id)
    WHERE f.extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)}
      AND f.published_at_utc >= {dt_sql(args.start_date)}
      AND f.published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start, chunk_end_exclusive
),
checkpoints AS
(
    SELECT
        chunk_start,
        chunk_end_exclusive,
        argMax(status, updated_at) AS checkpoint_status,
        max(updated_at) AS checkpoint_updated
    FROM {table(args.news_database, args.status_table)} FINAL
    WHERE stage = 'features' AND version = {sql_string(PHRASE_DICTIONARY_VERSION)}
    GROUP BY chunk_start, chunk_end_exclusive
)
SELECT
    source.chunk_start AS chunk_start,
    source.chunk_end_exclusive AS chunk_end_exclusive,
    source.source_rows AS source_rows,
    ifNull(features.output_rows, 0) AS output_rows,
    ifNull(checkpoints.checkpoint_status, 'missing') AS checkpoint_status,
    arrayFilter(x -> x != '', [
        if(ifNull(checkpoints.checkpoint_status, 'missing') != 'completed', 'missing_checkpoint', ''),
        if(ifNull(checkpoints.checkpoint_updated, toDateTime64(0, 6, 'UTC')) < source.source_max_updated, 'source_newer_than_checkpoint', ''),
        if(ifNull(features.stale_text_rows, 0) > 0, 'text_hash_changed', '')
    ]) AS reasons,
    toString(source.source_max_updated) AS source_max_updated,
    toString(source.source_hash) AS source_signature
FROM source
LEFT JOIN features USING (chunk_start, chunk_end_exclusive)
LEFT JOIN checkpoints USING (chunk_start, chunk_end_exclusive)
ORDER BY chunk_start
FORMAT JSONEachRow
"""


def load_reaction_repairs(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks) -> list[RepairUnit]:
    rows = query_json_rows(client, reaction_repair_sql(args, watermarks))
    return [repair_unit_from_row("reactions", row) for row in rows if row.get("reasons")]


def reaction_repair_sql(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    return f"""
WITH
source AS
(
    SELECT
        toDate(t.published_at_utc) AS chunk_start,
        addDays(chunk_start, 1) AS chunk_end_exclusive,
        uniqExact(tuple(t.canonical_news_id, upperUTF8(t.ticker))) AS source_pairs,
        max(greatest(t.updated_at_utc, n.updated_at_utc)) AS source_max_updated,
        groupBitXor(cityHash64(t.canonical_news_id, upperUTF8(t.ticker), t.text_hash, toString(t.updated_at_utc), toString(n.updated_at_utc))) AS source_hash
    FROM (SELECT * FROM {table(args.news_database, args.ticker_table)} FINAL) AS t
    INNER JOIN (SELECT * FROM {table(args.news_database, args.normalized_table)} FINAL) AS n USING (canonical_news_id)
    WHERE t.ticker != ''
      AND t.published_at_utc >= {dt_sql(args.start_date)}
      AND t.published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start, chunk_end_exclusive
),
outputs AS
(
    SELECT
        toDate(published_at_utc) AS chunk_start,
        addDays(chunk_start, 1) AS chunk_end_exclusive,
        uniqExact(tuple(canonical_news_id, ticker, horizon_code)) AS output_rows
    FROM {table(args.news_database, args.reactions_table)} FINAL
    WHERE label_version = {sql_string(LABEL_VERSION)}
      AND published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start, chunk_end_exclusive
),
checkpoints AS
(
    SELECT
        chunk_start,
        chunk_end_exclusive,
        argMax(status, updated_at) AS checkpoint_status,
        max(updated_at) AS checkpoint_updated
    FROM {table(args.news_database, args.status_table)} FINAL
    WHERE stage = 'reactions' AND version = {sql_string(LABEL_VERSION)}
    GROUP BY chunk_start, chunk_end_exclusive
)
SELECT
    source.chunk_start AS chunk_start,
    source.chunk_end_exclusive AS chunk_end_exclusive,
    source.source_pairs AS source_rows,
    ifNull(outputs.output_rows, 0) AS output_rows,
    ifNull(checkpoints.checkpoint_status, 'missing') AS checkpoint_status,
    arrayFilter(x -> x != '', [
        if(ifNull(checkpoints.checkpoint_status, 'missing') != 'completed', 'missing_checkpoint', ''),
        if(ifNull(checkpoints.checkpoint_updated, toDateTime64(0, 6, 'UTC')) < source.source_max_updated, 'source_newer_than_checkpoint', ''),
        if(ifNull(outputs.output_rows, 0) != source.source_pairs * 10, 'label_count_mismatch', '')
    ]) AS reasons,
    toString(source.source_max_updated) AS source_max_updated,
    toString(source.source_hash) AS source_signature
FROM source
LEFT JOIN outputs USING (chunk_start, chunk_end_exclusive)
LEFT JOIN checkpoints USING (chunk_start, chunk_end_exclusive)
ORDER BY chunk_start
FORMAT JSONEachRow
"""


def repair_unit_from_row(stage: str, row: dict[str, Any]) -> RepairUnit:
    return RepairUnit(
        stage=stage,
        start_date=str(row["chunk_start"]),
        end_date_exclusive=str(row["chunk_end_exclusive"]),
        source_rows=int(row.get("source_rows") or 0),
        output_rows=int(row.get("output_rows") or 0),
        checkpoint_status=str(row.get("checkpoint_status") or "missing"),
        reasons=tuple(str(item) for item in row.get("reasons") or ()),
    )


def purge_provisional_tail(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    watermarks: SourceWatermarks,
    reporter: NewsReactionProgress,
) -> None:
    stable = watermarks.stable_end_exclusive
    for target, version_column, version in (
        (args.features_table, "extraction_version", PHRASE_DICTIONARY_VERSION),
        (args.reactions_table, "label_version", LABEL_VERSION),
    ):
        reporter.message(f"removing provisional {target} rows at or after {stable}")
        monitored_execute(
            client,
            f"ALTER TABLE {table(args.news_database, target)} DELETE WHERE {quote_ident(version_column)} = {sql_string(version)} "
            f"AND published_at_utc >= {dt_sql(stable)} AND published_at_utc < {dt_sql(args.end_date)} SETTINGS mutations_sync = 2",
            reporter,
            f"remove provisional {target}",
        )
    monitored_execute(client, f"""
ALTER TABLE {table(args.news_database, args.status_table)} DELETE WHERE
    ((stage = 'features' AND version = {sql_string(PHRASE_DICTIONARY_VERSION)})
      OR (stage = 'reactions' AND version = {sql_string(LABEL_VERSION)}))
    AND chunk_end_exclusive > toDate({sql_string(stable)})
SETTINGS mutations_sync = 2
""", reporter, "remove provisional checkpoints")


def run_repairs(
    args: argparse.Namespace,
    feature_repairs: Sequence[RepairUnit],
    reaction_repairs: Sequence[RepairUnit],
    reporter: NewsReactionProgress,
) -> None:
    for stage, units in (("features", feature_repairs), ("reactions", reaction_repairs)):
        for start, end in merge_repair_ranges(units):
            reporter.message(f"repairing {stage} [{start},{end})")
            command = extractor_command(args, stage, start, end)
            child_env = os.environ.copy()
            child_env.update(
                {
                    "CLICKHOUSE_URL": str(args.clickhouse_url),
                    "CLICKHOUSE_USER": str(args.user),
                    "CLICKHOUSE_PASSWORD": str(args.password),
                }
            )
            completed = subprocess.run(command, cwd=REPO_ROOT, env=child_env, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"{stage} repair failed with exit code {completed.returncode}: {subprocess.list2cmdline(command)}")


def merge_repair_ranges(units: Sequence[RepairUnit]) -> list[tuple[str, str]]:
    ordered = sorted((date_arg(unit.start_date), date_arg(unit.end_date_exclusive)) for unit in units)
    merged: list[tuple[dt.date, dt.date]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [(start.isoformat(), end.isoformat()) for start, end in merged]


def extractor_command(args: argparse.Namespace, stage: str, start: str, end: str) -> list[str]:
    script = REPO_ROOT / "pipelines" / "news" / "benzinga" / "news_reaction_extract.py"
    command = [
        sys.executable,
        str(script),
        "--execute",
        "--replace-existing",
        "--start-date", start,
        "--end-date", end,
        "--stats-start-date", start,
        "--stats-end-date", end,
        "--stages", stage,
        "--news-database", args.news_database,
        "--market-database", args.market_database,
        "--normalized-table", args.normalized_table,
        "--ticker-table", args.ticker_table,
        "--events-table", args.events_table,
        "--calendar-table", args.calendar_table,
        "--features-table", args.features_table,
        "--reactions-table", args.reactions_table,
        "--status-table", args.status_table,
        "--reaction-workers", str(args.reaction_workers),
        "--reaction-ticker-shards", str(args.reaction_ticker_shards),
        "--reaction-links-per-shard", str(args.reaction_links_per_shard),
        "--reaction-max-news-shards", str(args.reaction_max_news_shards),
        "--max-threads", str(args.max_threads),
        "--max-memory-usage", str(args.max_memory_usage),
        "--progress-layout", args.progress_layout,
        "--progress-refresh-per-second", str(args.progress_refresh_per_second),
        "--progress-log-lines", str(args.progress_log_lines),
        "--output-root", args.output_root,
    ]
    if args.storage_policy:
        command.extend(("--storage-policy", args.storage_policy))
    return command


def quality_overlay_insert_sql(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    return f"""
INSERT INTO {table(args.news_database, args.quality_table)}
WITH
splits AS
(
    SELECT
        upperUTF8(provider_ticker) AS ticker,
        execution_date,
        groupUniqArray(stock_split_id) AS split_ids
    FROM {table(args.news_database, args.split_table)} FINAL
    WHERE provider_ticker != ''
      AND execution_date >= toDate({sql_string(args.start_date)})
      AND execution_date < toDate({sql_string(watermarks.stable_end_exclusive)})
      AND split_from > 0 AND split_to > 0 AND split_from != split_to
    GROUP BY ticker, execution_date
),
joined AS
(
    SELECT
        r.*,
        arrayDistinct(arrayFlatten(groupArray(if(
            s.execution_date BETWEEN toDate(ifNull(r.anchor_timestamp_utc, r.published_at_utc)) AND toDate(r.target_at_utc),
            s.split_ids,
            []
        )))) AS corporate_action_ids
    FROM (SELECT * FROM {table(args.news_database, args.reactions_table)} FINAL) AS r
    LEFT JOIN splits AS s ON s.ticker = upperUTF8(r.ticker)
    WHERE r.label_version = {sql_string(LABEL_VERSION)}
      AND r.published_at_utc >= {dt_sql(args.start_date)}
      AND r.published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY ALL
)
SELECT
    {sql_string(QUALITY_VERSION)} AS quality_version,
    label_version,
    canonical_news_id,
    ticker,
    horizon_code,
    published_at_utc,
    toUInt8(notEmpty(corporate_action_ids)) AS corporate_action_overlap,
    corporate_action_ids,
    toUInt8(
        abs(ifNull(abnormal_target_return, 0.0)) > {float(args.return_outlier_absolute)}
        OR abs(ifNull(abnormal_high_return, 0.0)) > {float(args.return_outlier_absolute)}
        OR abs(ifNull(abnormal_low_return, 0.0)) > {float(args.return_outlier_absolute)}
    ) AS return_outlier,
    toUInt8(quality_status = 'clean' AND corporate_action_overlap = 0 AND return_outlier = 0) AS eligible_for_statistics,
    arrayFilter(x -> x != '', [
        if(quality_status != 'clean', concat('base_quality:', quality_status), ''),
        if(corporate_action_overlap = 1, 'corporate_action_overlap', ''),
        if(return_outlier = 1, 'extreme_return_outlier', '')
    ]) AS exclusion_reasons,
    now64(6) AS built_at
FROM joined
"""


def rebuild_quality_overlay(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks, reporter: NewsReactionProgress) -> int:
    target = table(args.news_database, args.quality_table)
    monitored_execute(client, f"ALTER TABLE {target} DELETE WHERE quality_version = {sql_string(QUALITY_VERSION)} SETTINGS mutations_sync = 2", reporter, "replace quality overlay")
    reporter.message("building split-aware statistical eligibility from finalized reaction rows")
    monitored_execute(client, quality_overlay_insert_sql(args, watermarks), reporter, "build quality overlay")
    return int(monitored_execute(client, f"SELECT count() FROM {target} FINAL WHERE quality_version = {sql_string(QUALITY_VERSION)}", reporter, "count quality overlay").strip() or 0)


def audit_quality_overlay(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks) -> None:
    row = parse_one_json(client.execute(f"""
SELECT
    count() AS overlay_rows,
    uniqExact(tuple(canonical_news_id, ticker, horizon_code)) AS unique_rows,
    countIf(corporate_action_overlap = 1) AS corporate_action_rows,
    countIf(return_outlier = 1) AS outlier_rows,
    countIf(eligible_for_statistics = 1 AND notEmpty(exclusion_reasons)) AS contradictory_rows
FROM {table(args.news_database, args.quality_table)} FINAL
WHERE quality_version = {sql_string(QUALITY_VERSION)}
  AND published_at_utc >= {dt_sql(args.start_date)}
  AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
FORMAT JSONEachRow
"""))
    if int(row["overlay_rows"]) != int(row["unique_rows"]):
        raise RuntimeError("quality overlay contains duplicate news/ticker/horizon rows")
    if int(row["contradictory_rows"]) != 0:
        raise RuntimeError("quality overlay marks excluded rows eligible")


def robust_stats_insert_sql(args: argparse.Namespace) -> str:
    return f"""
INSERT INTO {table(args.news_database, args.robust_stats_table)}
WITH
joined AS
(
    SELECT
        f.phrase_id AS phrase_id,
        r.horizon_code AS horizon_code,
        r.publication_session AS publication_session,
        r.abnormal_target_return AS abnormal_target_return,
        r.abnormal_high_return AS abnormal_high_return,
        r.abnormal_low_return AS abnormal_low_return,
        q.eligible_for_statistics AS eligible_for_statistics,
        q.corporate_action_overlap AS corporate_action_overlap,
        q.return_outlier AS return_outlier
    FROM (SELECT * FROM {table(args.news_database, args.features_table)} FINAL) AS f
    INNER JOIN (SELECT * FROM {table(args.news_database, args.reactions_table)} FINAL) AS r USING (canonical_news_id)
    INNER JOIN (SELECT * FROM {table(args.news_database, args.quality_table)} FINAL) AS q
      ON q.label_version = r.label_version
     AND q.canonical_news_id = r.canonical_news_id
     AND q.ticker = r.ticker
     AND q.horizon_code = r.horizon_code
    INNER JOIN (SELECT * FROM {table(args.news_database, 'news_phrase_dictionary_v1')} FINAL) AS d
      ON d.dictionary_version = f.extraction_version AND d.phrase_id = f.phrase_id
    WHERE f.extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)}
      AND r.label_version = {sql_string(LABEL_VERSION)}
      AND q.quality_version = {sql_string(QUALITY_VERSION)}
      AND d.feature_role != 'observed_reaction'
      AND r.published_at_utc >= {dt_sql(args.stats_start_date)}
      AND r.published_at_utc < {dt_sql(args.stats_end_date)}
),
bounds AS
(
    SELECT
        phrase_id,
        horizon_code,
        publication_session,
        quantileTDigestIf({float(args.trim_lower)})(abnormal_target_return, eligible_for_statistics = 1) AS target_lower,
        quantileTDigestIf({float(args.trim_upper)})(abnormal_target_return, eligible_for_statistics = 1) AS target_upper,
        quantileTDigestIf({float(args.trim_lower)})(abnormal_high_return, eligible_for_statistics = 1) AS high_lower,
        quantileTDigestIf({float(args.trim_upper)})(abnormal_high_return, eligible_for_statistics = 1) AS high_upper,
        quantileTDigestIf({float(args.trim_lower)})(abnormal_low_return, eligible_for_statistics = 1) AS low_lower,
        quantileTDigestIf({float(args.trim_upper)})(abnormal_low_return, eligible_for_statistics = 1) AS low_upper
    FROM joined
    GROUP BY phrase_id, horizon_code, publication_session
)
SELECT
    {sql_string(ROBUST_STATS_VERSION)} AS stats_version,
    {sql_string(PHRASE_DICTIONARY_VERSION)} AS extraction_version,
    {sql_string(LABEL_VERSION)} AS label_version,
    {sql_string(QUALITY_VERSION)} AS quality_version,
    j.phrase_id,
    j.horizon_code,
    j.publication_session,
    count() AS sample_count,
    countIf(eligible_for_statistics = 1) AS eligible_sample_count,
    countIf(corporate_action_overlap = 1) AS corporate_action_excluded_count,
    countIf(return_outlier = 1) AS return_outlier_excluded_count,
    countIf(eligible_for_statistics = 1 AND abnormal_target_return < -0.005) AS negative_count,
    countIf(eligible_for_statistics = 1 AND abnormal_target_return >= -0.005 AND abnormal_target_return <= 0.005) AS neutral_count,
    countIf(eligible_for_statistics = 1 AND abnormal_target_return > 0.005) AS positive_count,
    (negative_count + 1.0) / (eligible_sample_count + 3.0) AS negative_probability,
    (neutral_count + 1.0) / (eligible_sample_count + 3.0) AS neutral_probability,
    (positive_count + 1.0) / (eligible_sample_count + 3.0) AS positive_probability,
    avgIf(abnormal_target_return, eligible_for_statistics = 1) AS mean_target_return,
    avgIf(abnormal_target_return, eligible_for_statistics = 1 AND abnormal_target_return BETWEEN b.target_lower AND b.target_upper) AS trimmed_mean_target_return,
    quantileTDigestIf(0.5)(abnormal_target_return, eligible_for_statistics = 1) AS median_target_return,
    avgIf(abnormal_high_return, eligible_for_statistics = 1) AS mean_high_return,
    avgIf(abnormal_high_return, eligible_for_statistics = 1 AND abnormal_high_return BETWEEN b.high_lower AND b.high_upper) AS trimmed_mean_high_return,
    quantileTDigestIf(0.5)(abnormal_high_return, eligible_for_statistics = 1) AS median_high_return,
    avgIf(abnormal_low_return, eligible_for_statistics = 1) AS mean_low_return,
    avgIf(abnormal_low_return, eligible_for_statistics = 1 AND abnormal_low_return BETWEEN b.low_lower AND b.low_upper) AS trimmed_mean_low_return,
    quantileTDigestIf(0.5)(abnormal_low_return, eligible_for_statistics = 1) AS median_low_return,
    quantilesTDigestIf(0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)(abnormal_target_return, eligible_for_statistics = 1) AS target_return_quantiles,
    quantilesTDigestIf(0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)(abnormal_high_return, eligible_for_statistics = 1) AS high_return_quantiles,
    quantilesTDigestIf(0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)(abnormal_low_return, eligible_for_statistics = 1) AS low_return_quantiles,
    toDate({sql_string(args.stats_start_date)}) AS trained_start_date,
    toDate({sql_string(args.stats_end_date)}) AS trained_end_date_exclusive,
    now64(6) AS built_at
FROM joined AS j
INNER JOIN bounds AS b USING (phrase_id, horizon_code, publication_session)
GROUP BY j.phrase_id, j.horizon_code, j.publication_session, b.target_lower, b.target_upper, b.high_lower, b.high_upper, b.low_lower, b.low_upper
HAVING eligible_sample_count > 0
"""


def rebuild_robust_stats(client: ClickHouseHttpClient, args: argparse.Namespace, reporter: NewsReactionProgress) -> int:
    target = table(args.news_database, args.robust_stats_table)
    monitored_execute(client, f"ALTER TABLE {target} DELETE WHERE stats_version = {sql_string(ROBUST_STATS_VERSION)} SETTINGS mutations_sync = 2", reporter, "replace robust statistics")
    reporter.message("building 2019-2025 split-aware probabilities and trimmed reaction distributions")
    monitored_execute(client, robust_stats_insert_sql(args), reporter, "build robust statistics")
    return int(monitored_execute(client, f"SELECT count() FROM {target} FINAL WHERE stats_version = {sql_string(ROBUST_STATS_VERSION)}", reporter, "count robust statistics").strip() or 0)


def audit_robust_stats(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    row = parse_one_json(client.execute(f"""
SELECT
    count() AS rows,
    uniqExact(tuple(phrase_id, horizon_code, publication_session)) AS unique_rows,
    countIf(abs(negative_probability + neutral_probability + positive_probability - 1.0) > 1e-9) AS invalid_probability_rows,
    countIf(trained_end_date_exclusive > toDate({sql_string(args.holdout_start_date)})) AS leaked_rows
FROM {table(args.news_database, args.robust_stats_table)} FINAL
WHERE stats_version = {sql_string(ROBUST_STATS_VERSION)}
FORMAT JSONEachRow
"""))
    if int(row["rows"]) != int(row["unique_rows"]):
        raise RuntimeError("robust statistics contain duplicate phrase/horizon/session rows")
    if int(row["invalid_probability_rows"]) or int(row["leaked_rows"]):
        raise RuntimeError("robust statistics failed probability or holdout-leakage audit")


def prediction_ctes(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    holdout_end = min(date_arg(args.holdout_end_date), date_arg(watermarks.stable_end_exclusive)).isoformat()
    return f"""
eligible AS
(
    SELECT
        r.canonical_news_id AS canonical_news_id,
        r.ticker AS ticker,
        r.published_at_utc AS published_at_utc,
        r.horizon_code AS horizon_code,
        r.publication_session AS publication_session,
        r.abnormal_target_return AS abnormal_target_return,
        f.phrase_id AS phrase_id,
        s.eligible_sample_count AS eligible_sample_count,
        s.negative_probability AS negative_probability,
        s.neutral_probability AS neutral_probability,
        s.positive_probability AS positive_probability
    FROM (SELECT * FROM {table(args.news_database, args.reactions_table)} FINAL) AS r
    INNER JOIN (SELECT * FROM {table(args.news_database, args.quality_table)} FINAL) AS q
      ON q.label_version = r.label_version AND q.canonical_news_id = r.canonical_news_id
     AND q.ticker = r.ticker AND q.horizon_code = r.horizon_code
    INNER JOIN (SELECT * FROM {table(args.news_database, args.features_table)} FINAL) AS f
      ON f.canonical_news_id = r.canonical_news_id
    INNER JOIN (SELECT * FROM {table(args.news_database, args.robust_stats_table)} FINAL) AS s
      ON s.phrase_id = f.phrase_id AND s.horizon_code = r.horizon_code
     AND s.publication_session = r.publication_session
    WHERE r.label_version = {sql_string(LABEL_VERSION)}
      AND q.quality_version = {sql_string(QUALITY_VERSION)}
      AND q.eligible_for_statistics = 1
      AND f.extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)}
      AND s.stats_version = {sql_string(ROBUST_STATS_VERSION)}
      AND s.eligible_sample_count >= {int(args.minimum_phrase_support)}
      AND r.published_at_utc >= {dt_sql(args.holdout_start_date)}
      AND r.published_at_utc < {dt_sql(holdout_end)}
),
predictions AS
(
    SELECT
        canonical_news_id,
        ticker,
        published_at_utc,
        horizon_code,
        publication_session,
        any(abnormal_target_return) AS abnormal_target_return,
        groupUniqArray(phrase_id) AS phrase_ids,
        sum((positive_probability - negative_probability) * sqrt(toFloat64(least(eligible_sample_count, 10000))))
            / nullIf(sum(sqrt(toFloat64(least(eligible_sample_count, 10000)))), 0.0) AS sentiment_score,
        multiIf(
            sentiment_score > {float(args.prediction_threshold)}, 'positive',
            sentiment_score < {-float(args.prediction_threshold)}, 'negative',
            'neutral'
        ) AS predicted_class,
        multiIf(
            abnormal_target_return > 0.005, 'positive',
            abnormal_target_return < -0.005, 'negative',
            'neutral'
        ) AS actual_class
    FROM eligible
    GROUP BY canonical_news_id, ticker, published_at_utc, horizon_code, publication_session
)
"""


def evaluate_holdout(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks) -> dict[str, Any]:
    holdout_end = min(date_arg(args.holdout_end_date), date_arg(watermarks.stable_end_exclusive)).isoformat()
    eligible_holdout_labels = int(client.execute(f"""
SELECT count()
FROM (SELECT * FROM {table(args.news_database, args.reactions_table)} FINAL) AS r
INNER JOIN (SELECT * FROM {table(args.news_database, args.quality_table)} FINAL) AS q
  ON q.label_version = r.label_version AND q.canonical_news_id = r.canonical_news_id
 AND q.ticker = r.ticker AND q.horizon_code = r.horizon_code
WHERE r.label_version = {sql_string(LABEL_VERSION)}
  AND q.quality_version = {sql_string(QUALITY_VERSION)}
  AND q.eligible_for_statistics = 1
  AND r.published_at_utc >= {dt_sql(args.holdout_start_date)}
  AND r.published_at_utc < {dt_sql(holdout_end)}
""").strip() or 0)
    rows = query_json_rows(client, f"""
WITH
{prediction_ctes(args, watermarks)}
SELECT
    horizon_code,
    publication_session,
    count() AS prediction_count,
    countIf(predicted_class = actual_class) AS correct_count,
    correct_count / prediction_count AS accuracy,
    countIf(actual_class = 'negative') AS actual_negative,
    countIf(actual_class = 'neutral') AS actual_neutral,
    countIf(actual_class = 'positive') AS actual_positive,
    countIf(predicted_class = 'negative') AS predicted_negative,
    countIf(predicted_class = 'neutral') AS predicted_neutral,
    countIf(predicted_class = 'positive') AS predicted_positive,
    countIf(actual_class = 'negative' AND predicted_class = 'negative') AS negative_negative,
    countIf(actual_class = 'negative' AND predicted_class = 'neutral') AS negative_neutral,
    countIf(actual_class = 'negative' AND predicted_class = 'positive') AS negative_positive,
    countIf(actual_class = 'neutral' AND predicted_class = 'negative') AS neutral_negative,
    countIf(actual_class = 'neutral' AND predicted_class = 'neutral') AS neutral_neutral,
    countIf(actual_class = 'neutral' AND predicted_class = 'positive') AS neutral_positive,
    countIf(actual_class = 'positive' AND predicted_class = 'negative') AS positive_negative,
    countIf(actual_class = 'positive' AND predicted_class = 'neutral') AS positive_neutral,
    countIf(actual_class = 'positive' AND predicted_class = 'positive') AS positive_positive,
    avg(abs(sentiment_score)) AS mean_confidence,
    avg(abnormal_target_return) AS mean_actual_return
FROM predictions
GROUP BY horizon_code, publication_session
ORDER BY horizon_code, publication_session
FORMAT JSONEachRow
""")
    total = sum(int(row["prediction_count"]) for row in rows)
    correct = sum(int(row["correct_count"]) for row in rows)
    confusion = {
        actual: {
            predicted: sum(int(row[f"{actual}_{predicted}"]) for row in rows)
            for predicted in ("negative", "neutral", "positive")
        }
        for actual in ("negative", "neutral", "positive")
    }
    classification = classification_metrics(confusion)
    actual_totals = [sum(confusion[label].values()) for label in ("negative", "neutral", "positive")]
    majority_baseline_accuracy = max(actual_totals) / total if total else None
    return {
        "prediction_version": PREDICTION_VERSION,
        "stats_version": ROBUST_STATS_VERSION,
        "holdout_start_date": args.holdout_start_date,
        "holdout_end_date_exclusive": min(date_arg(args.holdout_end_date), date_arg(watermarks.stable_end_exclusive)).isoformat(),
        "prediction_count": total,
        "eligible_holdout_label_count": eligible_holdout_labels,
        "prediction_coverage": (total / eligible_holdout_labels) if eligible_holdout_labels else None,
        "correct_count": correct,
        "accuracy": (correct / total) if total else None,
        "majority_baseline_accuracy": majority_baseline_accuracy,
        "accuracy_lift_over_majority": ((correct / total) - majority_baseline_accuracy) if total and majority_baseline_accuracy is not None else None,
        "balanced_accuracy": classification["balanced_accuracy"],
        "macro_f1": classification["macro_f1"],
        "confusion_matrix": confusion,
        "minimum_phrase_support": args.minimum_phrase_support,
        "prediction_threshold": args.prediction_threshold,
        "groups": rows,
    }


def write_review_sample(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    watermarks: SourceWatermarks,
    path: Path,
) -> int:
    if args.review_sample_size == 0:
        path.write_text("", encoding="utf-8")
        return 0
    rows = query_json_rows(client, review_sample_sql(args, watermarks))
    review_ids = {row["review_id"] for row in rows}
    article_keys = {(row["canonical_news_id"], row["ticker"]) for row in rows}
    if len(review_ids) != len(rows) or len(article_keys) != len(rows):
        raise RuntimeError("human review sample contains duplicate review or news/ticker identities")
    if any(not row.get("hidden_answers") for row in rows):
        raise RuntimeError("human review sample contains an article without hidden horizon answers")
    fieldnames = [
        "review_id", "canonical_news_id", "ticker", "published_at_utc",
        "title", "teaser", "body_excerpt", "provider_tags", "channels",
        "reviewer_sentiment", "reviewer_relevance", "reviewer_notes",
    ]
    answer_fields = [
        "review_id", "canonical_news_id", "ticker", "published_at_utc",
        "horizon_code", "publication_session", "phrase_ids", "sentiment_score",
        "predicted_class", "actual_class", "abnormal_target_return",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(row.get(key), ensure_ascii=False)
                if isinstance(row.get(key), list)
                else row.get(key, "")
                for key in fieldnames
            })
    answer_path = path.with_name(path.stem + "_answer_key.csv")
    answer_keys: set[tuple[str, str]] = set()
    with answer_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=answer_fields)
        writer.writeheader()
        for row in rows:
            common = {
                "review_id": row["review_id"],
                "canonical_news_id": row["canonical_news_id"],
                "ticker": row["ticker"],
                "published_at_utc": row["published_at_utc"],
            }
            for hidden in row.get("hidden_answers", []):
                answer_key = (row["review_id"], str(hidden[0]))
                if answer_key in answer_keys:
                    raise RuntimeError(f"duplicate hidden review/horizon answer: {answer_key}")
                answer_keys.add(answer_key)
                answer = {
                    **common,
                    "horizon_code": hidden[0],
                    "publication_session": hidden[1],
                    "phrase_ids": json.dumps(hidden[2], ensure_ascii=False),
                    "sentiment_score": hidden[3],
                    "predicted_class": hidden[4],
                    "actual_class": hidden[5],
                    "abnormal_target_return": hidden[6],
                }
                writer.writerow(answer)
    return len(rows)


def review_sample_sql(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    """Select one deterministic, fully blinded review row per news/ticker pair."""
    horizon_priority = (
        "indexOf(['1m', '5m', '10m', '30m', '1h', '2h', '3h', "
        "'premarket_close', 'regular_close', 'extended_close'], horizon_code)"
    )
    return f"""
WITH
{prediction_ctes(args, watermarks)},
article_candidates AS
(
    SELECT
        canonical_news_id,
        ticker,
        any(published_at_utc) AS published_at_utc,
        any(publication_session) AS publication_session,
        argMin(
            predicted_class,
            {horizon_priority}
        ) AS stratification_predicted_class,
        argMin(
            actual_class,
            {horizon_priority}
        ) AS stratification_actual_class
    FROM predictions
    GROUP BY canonical_news_id, ticker
),
ranked_articles AS
(
    SELECT
        *,
        row_number() OVER (
            PARTITION BY publication_session, stratification_predicted_class, stratification_actual_class
            ORDER BY cityHash64(canonical_news_id, ticker)
        ) AS stratum_rank
    FROM article_candidates
),
selected_articles AS
(
    SELECT *
    FROM ranked_articles
    ORDER BY stratum_rank, cityHash64(canonical_news_id, ticker)
    LIMIT {int(args.review_sample_size)}
)
SELECT
    hex(sipHash128(s.canonical_news_id, s.ticker)) AS review_id,
    s.canonical_news_id AS canonical_news_id,
    s.ticker AS ticker,
    toString(s.published_at_utc) AS published_at_utc,
    n.title AS title,
    n.teaser AS teaser,
    leftUTF8(n.normalized_full_text, 2000) AS body_excerpt,
    n.provider_tags AS provider_tags,
    n.channels AS channels,
    arraySort(groupArray(tuple(
        p.horizon_code,
        p.publication_session,
        p.phrase_ids,
        p.sentiment_score,
        p.predicted_class,
        p.actual_class,
        p.abnormal_target_return
    ))) AS hidden_answers,
    '' AS reviewer_sentiment,
    '' AS reviewer_relevance,
    '' AS reviewer_notes
FROM selected_articles AS s
INNER JOIN (SELECT * FROM {table(args.news_database, args.normalized_table)} FINAL) AS n
  ON n.canonical_news_id = s.canonical_news_id
INNER JOIN predictions AS p
  ON p.canonical_news_id = s.canonical_news_id AND p.ticker = s.ticker
GROUP BY
    s.canonical_news_id,
    s.ticker,
    s.published_at_utc,
    n.title,
    n.teaser,
    n.normalized_full_text,
    n.provider_tags,
    n.channels
ORDER BY cityHash64(review_id)
FORMAT JSONEachRow
"""


def review_instructions(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "purpose": "Human review of a deterministic, phrase-probability news sentiment classifier.",
        "sample_unit": "one unique canonical news/ticker pair",
        "model_outputs_hidden": True,
        "future_price_reaction_hidden": True,
        "answer_key_policy": "Do not open the answer key until all reviewer labels are locked.",
        "reviewer_sentiment_values": ["negative", "neutral", "positive", "mixed", "unclear"],
        "reviewer_relevance_values": ["company_specific", "ticker_related", "not_relevant"],
        "guidance": [
            "Judge language available at publication time, not the later price reaction.",
            "Do not inspect phrase rules, model scores, predictions, or the answer key while labeling.",
            "Use mixed when independent positive and negative components are both material.",
            "Use unclear when the excerpt is insufficient; do not infer missing article text.",
        ],
        "sample_size_requested": args.review_sample_size,
    }


def classification_metrics(confusion: dict[str, dict[str, int]]) -> dict[str, float | None]:
    labels = ("negative", "neutral", "positive")
    recalls: list[float] = []
    f1_scores: list[float] = []
    for label in labels:
        true_positive = confusion[label][label]
        actual_total = sum(confusion[label].values())
        predicted_total = sum(confusion[actual][label] for actual in labels)
        recall = true_positive / actual_total if actual_total else None
        precision = true_positive / predicted_total if predicted_total else None
        if recall is not None:
            recalls.append(recall)
        if precision is not None and recall is not None:
            f1_scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return {
        "balanced_accuracy": sum(recalls) / len(recalls) if recalls else None,
        "macro_f1": sum(f1_scores) / len(f1_scores) if f1_scores else None,
    }


def audit_all(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks) -> dict[str, Any]:
    row = parse_one_json(client.execute(f"""
WITH
expected AS
(
    SELECT uniqExact(tuple(t.canonical_news_id, upperUTF8(t.ticker))) * 10 AS expected_labels
    FROM (SELECT * FROM {table(args.news_database, args.ticker_table)} FINAL) AS t
    INNER JOIN (SELECT canonical_news_id FROM {table(args.news_database, args.normalized_table)} FINAL) AS n USING (canonical_news_id)
    WHERE t.ticker != '' AND t.published_at_utc >= {dt_sql(args.start_date)}
      AND t.published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
),
actual AS
(
    SELECT
        count() AS label_rows,
        uniqExact(tuple(canonical_news_id, ticker, horizon_code)) AS unique_label_rows,
        countIf(anchor_timestamp_utc >= published_at_utc) AS noncausal_anchors,
        countIf(target_timestamp_utc <= published_at_utc) AS noncausal_targets,
        countIf(target_timestamp_utc > target_at_utc) AS targets_after_boundary,
        countIf(window_high_price < window_low_price) AS invalid_extrema
    FROM {table(args.news_database, args.reactions_table)} FINAL
    WHERE label_version = {sql_string(LABEL_VERSION)}
      AND published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
),
features AS
(
    SELECT
        count() AS feature_rows,
        uniqExact(tuple(canonical_news_id, phrase_id)) AS unique_feature_rows
    FROM {table(args.news_database, args.features_table)} FINAL
    WHERE extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)}
      AND published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
),
feature_chunks AS
(
    SELECT count() AS feature_chunk_count FROM
    (
        SELECT toStartOfMonth(toDate(published_at_utc))
        FROM {table(args.news_database, args.normalized_table)} FINAL
        WHERE published_at_utc >= {dt_sql(args.start_date)}
          AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
        GROUP BY 1
    )
),
reaction_chunks AS
(
    SELECT count() AS reaction_chunk_count FROM
    (
        SELECT toDate(published_at_utc)
        FROM {table(args.news_database, args.ticker_table)} FINAL
        WHERE ticker != '' AND published_at_utc >= {dt_sql(args.start_date)}
          AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
        GROUP BY 1
    )
)
SELECT
    expected_labels,
    label_rows,
    unique_label_rows,
    noncausal_anchors,
    noncausal_targets,
    targets_after_boundary,
    invalid_extrema,
    feature_rows,
    unique_feature_rows,
    feature_chunk_count,
    reaction_chunk_count
FROM expected CROSS JOIN actual CROSS JOIN features CROSS JOIN feature_chunks CROSS JOIN reaction_chunks
FORMAT JSONEachRow
"""))
    integer_keys = (
        "expected_labels", "label_rows", "unique_label_rows", "noncausal_anchors", "noncausal_targets",
        "targets_after_boundary", "invalid_extrema", "feature_rows", "unique_feature_rows",
    )
    parsed = {key: int(row.get(key) or 0) for key in integer_keys}
    parsed["feature_chunks"] = int(row.get("feature_chunk_count") or 0)
    parsed["reaction_chunks"] = int(row.get("reaction_chunk_count") or 0)
    if parsed["expected_labels"] != parsed["unique_label_rows"]:
        raise RuntimeError(f"reaction coverage mismatch expected={parsed['expected_labels']} actual={parsed['unique_label_rows']}")
    if parsed["label_rows"] != parsed["unique_label_rows"]:
        raise RuntimeError("reaction output contains duplicate keys")
    if parsed["feature_rows"] != parsed["unique_feature_rows"]:
        raise RuntimeError("feature output contains duplicate article/phrase rows")
    if any(parsed[key] for key in ("noncausal_anchors", "noncausal_targets", "targets_after_boundary", "invalid_extrema")):
        raise RuntimeError("causal reaction invariants failed: " + json.dumps(parsed, sort_keys=True))
    return parsed


def certify_chunks(client: ClickHouseHttpClient, args: argparse.Namespace, watermarks: SourceWatermarks, audit: dict[str, Any]) -> None:
    target = table(args.news_database, args.finalization_table)
    client.execute(f"ALTER TABLE {target} DELETE WHERE finalizer_version = {sql_string(FINALIZER_VERSION)} SETTINGS mutations_sync = 2")
    common = {
        "event_max": watermarks.event_max_timestamp_utc,
        "audit_json": json.dumps(audit, sort_keys=True, separators=(",", ":")),
    }
    feature_rows = query_json_rows(client, feature_certification_sql(args, watermarks))
    reaction_rows = query_json_rows(client, reaction_certification_sql(args, watermarks))
    validate_certification_rows(feature_rows, reaction_rows, audit)
    rows = [
        {
            "finalizer_version": FINALIZER_VERSION,
            "stage": stage,
            "version": PHRASE_DICTIONARY_VERSION if stage == "features" else LABEL_VERSION,
            "chunk_start": row["chunk_start"],
            "chunk_end_exclusive": row["chunk_end_exclusive"],
            "source_rows": int(row["source_rows"]),
            "output_rows": int(row["output_rows"]),
            "source_max_updated_at_utc": row["source_max_updated"],
            "event_max_timestamp_utc": common["event_max"],
            "source_signature": str(row["source_signature"]),
            "status": "certified",
            "audit_json": common["audit_json"],
            "certified_at": clickhouse_timestamp(dt.datetime.now(UTC)),
        }
        for stage, stage_rows in (("features", feature_rows), ("reactions", reaction_rows))
        for row in stage_rows
    ]
    insert_json_rows(client, target, rows)


def validate_certification_rows(
    feature_rows: Sequence[dict[str, Any]],
    reaction_rows: Sequence[dict[str, Any]],
    audit: dict[str, Any],
) -> None:
    """Refuse certification when per-chunk metadata disagrees with the final audit."""
    checks = (
        (
            "features",
            len(feature_rows),
            sum(int(row["output_rows"]) for row in feature_rows),
            int(audit["feature_chunks"]),
            int(audit["feature_rows"]),
        ),
        (
            "reactions",
            len(reaction_rows),
            sum(int(row["output_rows"]) for row in reaction_rows),
            int(audit["reaction_chunks"]),
            int(audit["unique_label_rows"]),
        ),
    )
    for stage, actual_chunks, actual_rows, expected_chunks, expected_rows in checks:
        if actual_chunks != expected_chunks or actual_rows != expected_rows:
            raise RuntimeError(
                f"{stage} certification metadata disagrees with final audit: "
                f"chunks={actual_chunks}/{expected_chunks} rows={actual_rows}/{expected_rows}"
            )


def feature_certification_sql(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    return f"""
WITH source AS
(
    SELECT
        toStartOfMonth(toDate(published_at_utc)) AS chunk_start,
        least(addMonths(chunk_start, 1), toDate({sql_string(watermarks.stable_end_exclusive)})) AS chunk_end_exclusive,
        uniqExact(canonical_news_id) AS source_rows,
        toString(max(updated_at_utc)) AS source_max_updated,
        toString(groupBitXor(cityHash64(canonical_news_id, text_hash, toString(updated_at_utc)))) AS source_signature
    FROM {table(args.news_database, args.normalized_table)} FINAL
    WHERE published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start, chunk_end_exclusive
),
outputs AS
(
    SELECT
        toStartOfMonth(toDate(published_at_utc)) AS chunk_start,
        uniqExact(tuple(canonical_news_id, phrase_id)) AS output_rows
    FROM {table(args.news_database, args.features_table)} FINAL
    WHERE extraction_version = {sql_string(PHRASE_DICTIONARY_VERSION)}
      AND published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start
)
SELECT
    source.chunk_start AS chunk_start,
    source.chunk_end_exclusive AS chunk_end_exclusive,
    source.source_rows AS source_rows,
    ifNull(outputs.output_rows, toUInt64(0)) AS output_rows,
    source.source_max_updated AS source_max_updated,
    source.source_signature AS source_signature
FROM source
LEFT JOIN outputs USING (chunk_start)
ORDER BY source.chunk_start
FORMAT JSONEachRow
"""


def reaction_certification_sql(args: argparse.Namespace, watermarks: SourceWatermarks) -> str:
    return f"""
WITH source AS
(
    SELECT
        toDate(t.published_at_utc) AS chunk_start,
        addDays(chunk_start, 1) AS chunk_end_exclusive,
        uniqExact(tuple(t.canonical_news_id, upperUTF8(t.ticker))) AS source_rows,
        max(greatest(t.updated_at_utc, n.updated_at_utc)) AS source_max_updated,
        groupBitXor(cityHash64(t.canonical_news_id, upperUTF8(t.ticker), t.text_hash, toString(t.updated_at_utc), toString(n.updated_at_utc))) AS source_signature
    FROM (SELECT * FROM {table(args.news_database, args.ticker_table)} FINAL) AS t
    INNER JOIN (SELECT * FROM {table(args.news_database, args.normalized_table)} FINAL) AS n USING (canonical_news_id)
    WHERE t.ticker != '' AND t.published_at_utc >= {dt_sql(args.start_date)}
      AND t.published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start, chunk_end_exclusive
), outputs AS
(
    SELECT toDate(published_at_utc) AS chunk_start, uniqExact(tuple(canonical_news_id, ticker, horizon_code)) AS output_rows
    FROM {table(args.news_database, args.reactions_table)} FINAL
    WHERE label_version = {sql_string(LABEL_VERSION)} AND published_at_utc >= {dt_sql(args.start_date)}
      AND published_at_utc < {dt_sql(watermarks.stable_end_exclusive)}
    GROUP BY chunk_start
)
SELECT source.*, outputs.output_rows
FROM source INNER JOIN outputs USING (chunk_start)
ORDER BY chunk_start
FORMAT JSONEachRow
"""


def query_json_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def insert_json_rows(client: ClickHouseHttpClient, target: str, rows: Sequence[dict[str, Any]], batch_size: int = 2_000) -> None:
    for offset in range(0, len(rows), batch_size):
        payload = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows[offset : offset + batch_size])
        client.execute(f"INSERT INTO {target} FORMAT JSONEachRow\n{payload}")


def parse_clickhouse_datetime(value: str) -> dt.datetime:
    text = value.strip().replace(" ", "T")
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def clickhouse_timestamp(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
