from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.news_reaction_event_dictionary_v2 import (  # noqa: E402
    EVENT_DICTIONARY_VERSION,
    EVENT_RULES,
)
from pipelines.news.benzinga.news_reaction_extract import (  # noqa: E402
    LABEL_VERSION,
    calendar_month_chunks,
    date_arg,
    dt_sql,
    insert_json_rows,
    memory_bytes,
    monitored_execute,
    parse_one_json,
    table,
    table_columns,
    table_exists,
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
PIPELINE_VERSION = "news_deterministic_intelligence_v2"
RELEVANCE_VERSION = "news_ticker_relevance_rules_v2"
LANGUAGE_VERSION = "news_language_composition_v2"
SCALE_VERSION = "news_reaction_robust_scale_v2"
MODEL_VERSION = "news_reaction_empirical_bayes_v2"
PREDICTION_VERSION = "news_reaction_probability_v2"
REVIEW_VERSION = "codex_blind_review_v1"
ALLOWED_STAGES = ("extract", "scale", "train", "predict", "evaluate")


@dataclass(frozen=True, slots=True)
class MonthResult:
    start_date: str
    end_date_exclusive: str
    relevance_rows: int
    event_rows: int
    language_rows: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    reaction_rows: int
    reaction_log_loss: float | None
    reaction_brier_score: float | None
    reaction_accuracy: float | None
    language_review_rows: int
    relevance_accuracy: float | None
    language_accuracy: float | None


def compose_language(
    evidence: Iterable[tuple[str, int, float, str]],
    *,
    language_threshold: float = 0.15,
    mixed_min_mass: float = 0.35,
    mixed_ratio: float = 0.45,
) -> dict[str, Any]:
    """Compose independent positive/negative evidence after family de-correlation.

    Each tuple is (family, direction, weighted_mass, event_id). Only the strongest
    event for one family and direction contributes, matching the ClickHouse path.
    """
    strongest: dict[tuple[str, int], tuple[float, str]] = {}
    for family, direction, mass, event_id in evidence:
        if direction not in {-1, 0, 1} or mass < 0 or not math.isfinite(mass):
            raise ValueError("invalid language evidence")
        key = (family, direction)
        if key not in strongest or mass > strongest[key][0]:
            strongest[key] = (mass, event_id)
    masses = {direction: sum(value[0] for (family, item_direction), value in strongest.items() if item_direction == direction) for direction in (-1, 0, 1)}
    positive, negative = masses[1], masses[-1]
    score = (positive - negative) / max(positive + negative, 1e-9)
    mixedness = min(positive, negative) / max(max(positive, negative), 1e-9)
    if positive >= mixed_min_mass and negative >= mixed_min_mass and mixedness >= mixed_ratio:
        label = "mixed"
    elif score >= language_threshold:
        label = "positive"
    elif score <= -language_threshold:
        label = "negative"
    else:
        label = "neutral"
    return {
        "language_class": label,
        "language_score": score,
        "positive_mass": positive,
        "negative_mass": negative,
        "neutral_mass": masses[0],
        "mixedness": mixedness,
        "positive_evidence_ids": sorted(value[1] for (family, direction), value in strongest.items() if direction == 1),
        "negative_evidence_ids": sorted(value[1] for (family, direction), value in strongest.items() if direction == -1),
        "neutral_evidence_ids": sorted(value[1] for (family, direction), value in strongest.items() if direction == 0),
    }


def empirical_bayes_effect(
    class_counts: Sequence[int],
    baseline_probabilities: Sequence[float],
    *,
    prior_strength: float,
) -> tuple[tuple[float, ...], tuple[float, ...], float]:
    if len(class_counts) != 3 or len(baseline_probabilities) != 3:
        raise ValueError("three reaction classes are required")
    if prior_strength <= 0 or any(count < 0 for count in class_counts):
        raise ValueError("counts and prior strength must be valid")
    if any(probability <= 0 for probability in baseline_probabilities) or not math.isclose(sum(baseline_probabilities), 1.0):
        raise ValueError("baseline probabilities must be positive and sum to one")
    sample_count = sum(class_counts)
    reliability = sample_count / (sample_count + prior_strength)
    posterior = tuple(
        (count + prior_strength * baseline) / (sample_count + prior_strength)
        for count, baseline in zip(class_counts, baseline_probabilities, strict=True)
    )
    log_effects = tuple(
        reliability * math.log(max(posterior_probability, 1e-12) / baseline_probability)
        for posterior_probability, baseline_probability in zip(posterior, baseline_probabilities, strict=True)
    )
    return posterior, log_effects, reliability


def shrunken_robust_scale(ticker_mad: float, global_mad: float, sample_count: int, shrinkage: float, minimum: float = 1e-5) -> float:
    if min(ticker_mad, global_mad) < 0 or sample_count < 0 or shrinkage <= 0:
        raise ValueError("invalid robust-scale inputs")
    weight = sample_count / (sample_count + shrinkage)
    return max(minimum, weight * ticker_mad + (1 - weight) * global_mad)


def confusion_metrics(rows: Iterable[dict[str, Any]], labels: Sequence[str]) -> dict[str, Any]:
    counts = {(actual, predicted): 0 for actual in labels for predicted in labels}
    total = 0
    for row in rows:
        actual, predicted = str(row["actual_class"]), str(row["predicted_class"])
        if actual not in labels or predicted not in labels:
            continue
        count = int(row.get("sample_count") or 1)
        counts[(actual, predicted)] += count
        total += count
    per_class: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = counts[(label, label)]
        fp = sum(counts[(actual, label)] for actual in labels if actual != label)
        fn = sum(counts[(label, predicted)] for predicted in labels if predicted != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return {
        "sample_count": total,
        "accuracy": sum(counts[(label, label)] for label in labels) / total if total else None,
        "balanced_accuracy": sum(value["recall"] for value in per_class.values()) / len(labels) if total else None,
        "macro_f1": sum(value["f1"] for value in per_class.values()) / len(labels) if total else None,
        "per_class": per_class,
        "confusion": {f"{actual}->{predicted}": counts[(actual, predicted)] for actual in labels for predicted in labels},
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic publication-time news relevance/language intelligence and an "
            "interpretable empirical-Bayes post-news reaction model. The default is a read-only plan."
        )
    )
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2027-01-01", help="Exclusive publication bound.")
    parser.add_argument("--train-start-date", default="2019-01-01")
    parser.add_argument("--train-end-date", default="2026-01-01")
    parser.add_argument("--holdout-start-date", default="2026-01-01")
    parser.add_argument("--holdout-end-date", default="2027-01-01")
    parser.add_argument("--stages", default=",".join(ALLOWED_STAGES))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rebuild", action="store_true", help="Replace completed v2 chunks/stages.")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-threads-per-query", type=int, default=6)
    parser.add_argument("--max-memory-usage", default="16G")
    parser.add_argument("--ticker-scale-min-support", type=int, default=80)
    parser.add_argument("--scale-shrinkage", type=float, default=120.0)
    parser.add_argument("--reaction-z-threshold", type=float, default=0.5)
    parser.add_argument("--effect-min-support", type=int, default=20)
    parser.add_argument("--effect-prior-strength", type=float, default=60.0)
    parser.add_argument("--language-threshold", type=float, default=0.15)
    parser.add_argument("--mixed-min-mass", type=float, default=0.35)
    parser.add_argument("--mixed-ratio", type=float, default=0.45)
    parser.add_argument("--review-labels-csv", default="")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=2.0)
    parser.add_argument("--progress-log-lines", type=int, default=8)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--normalized-table", default="benzinga_news_normalized_v1")
    parser.add_argument("--ticker-table", default="benzinga_news_ticker_v1")
    parser.add_argument("--reaction-table", default="news_reaction_labels_v2")
    parser.add_argument("--identity-alias-table", default="news_ticker_identity_alias_v2")
    parser.add_argument("--relevance-table", default="news_ticker_relevance_v2")
    parser.add_argument("--event-dictionary-table", default="news_semantic_event_dictionary_v2")
    parser.add_argument("--event-feature-table", default="news_semantic_event_features_v2")
    parser.add_argument("--language-table", default="news_language_assessment_v2")
    parser.add_argument("--scale-table", default="news_reaction_scale_v2")
    parser.add_argument("--baseline-table", default="news_reaction_baseline_v2")
    parser.add_argument("--effect-table", default="news_reaction_event_effects_v2")
    parser.add_argument("--prediction-table", default="news_reaction_predictions_v2")
    parser.add_argument("--review-table", default="news_language_review_v1")
    parser.add_argument("--status-table", default="news_reaction_model_status_v2")
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    default_output = os.environ.get("NEWS_REACTION_V2_OUTPUT_ROOT")
    if not default_output:
        default_output = str(REPO_ROOT / "runtime" / "news_reaction_deterministic_v2")
    parser.add_argument("--output-root", default=default_output)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> tuple[str, ...]:
    start, end = date_arg(args.start_date), date_arg(args.end_date)
    train_start, train_end = date_arg(args.train_start_date), date_arg(args.train_end_date)
    holdout_start, holdout_end = date_arg(args.holdout_start_date), date_arg(args.holdout_end_date)
    if not start <= train_start < train_end <= holdout_start < holdout_end <= end:
        raise SystemExit("expected start <= training range <= holdout range <= end")
    stages = tuple(value.strip() for value in args.stages.split(",") if value.strip())
    if invalid := sorted(set(stages) - set(ALLOWED_STAGES)):
        raise SystemExit(f"unknown stages {invalid}; expected a subset of {ALLOWED_STAGES}")
    if args.workers < 1 or args.workers > 8:
        raise SystemExit("--workers must be between 1 and 8")
    if args.max_threads_per_query < 1:
        raise SystemExit("--max-threads-per-query must be positive")
    if args.ticker_scale_min_support < 1 or args.effect_min_support < 1:
        raise SystemExit("support thresholds must be positive")
    if args.scale_shrinkage <= 0 or args.effect_prior_strength <= 0:
        raise SystemExit("shrinkage and prior strength must be positive")
    if not 0 < args.reaction_z_threshold < 5:
        raise SystemExit("--reaction-z-threshold must be between zero and five")
    if not 0 < args.language_threshold < 1 or not 0 < args.mixed_ratio <= 1:
        raise SystemExit("language thresholds must be in (0, 1]")
    return stages


def policy_sql(args: argparse.Namespace) -> str:
    return f"SETTINGS storage_policy = {sql_string(args.storage_policy)}" if args.storage_policy else ""


def query_settings(args: argparse.Namespace, *, external_group_by: bool = False) -> str:
    memory = memory_bytes(str(args.max_memory_usage))
    settings = [
        f"max_threads = {int(args.max_threads_per_query)}",
        f"max_memory_usage = {memory}",
        "join_algorithm = 'grace_hash'",
    ]
    if external_group_by:
        settings.append(f"max_bytes_before_external_group_by = {max(256 * 1024**2, memory // 2)}")
    return "\nSETTINGS " + ", ".join(settings)


def ddl_statements(args: argparse.Namespace) -> tuple[str, ...]:
    p = policy_sql(args)
    db = args.database
    return (
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.identity_alias_table)}
(
 ticker LowCardinality(String), issuer_id String, issuer_name String, issuer_alias String,
 listing_id String, list_date Nullable(Date), delisted_date Nullable(Date), identity_version LowCardinality(String),
 updated_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at) ORDER BY (identity_version, ticker, issuer_id, listing_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.relevance_table)}
(
 relevance_version LowCardinality(String), canonical_news_id String, ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'), issuer_id String, issuer_name String, issuer_alias String,
 relevance_class LowCardinality(String), relevance_score Float32, ticker_mentioned UInt8, issuer_mentioned UInt8,
 direct_event_language UInt8, multi_ticker UInt8, roundup_or_analyst UInt8, evidence Array(String),
 source_text_hash String, classified_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(classified_at) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (relevance_version, ticker, published_at_utc, canonical_news_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.event_dictionary_table)}
(
 dictionary_version LowCardinality(String), event_id LowCardinality(String), canonical_event String,
 family LowCardinality(String), direction Int8, strength Float32, materiality Float32, certainty Float32,
 time_orientation LowCardinality(String), feature_role LowCardinality(String), needles Array(String),
 updated_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at) ORDER BY (dictionary_version, event_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.event_feature_table)}
(
 extraction_version LowCardinality(String), canonical_news_id String, ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'), event_id LowCardinality(String), family LowCardinality(String),
 effective_direction Int8, source_mask UInt8, negated UInt8, scoped_to_ticker UInt8,
 source_text_hash String, extracted_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(extracted_at) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (extraction_version, ticker, published_at_utc, canonical_news_id, event_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.language_table)}
(
 language_version LowCardinality(String), canonical_news_id String, ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'), relevance_class LowCardinality(String), language_class LowCardinality(String),
 language_score Float64, positive_mass Float64, negative_mass Float64, neutral_mass Float64, mixedness Float64,
 positive_evidence_ids Array(String), negative_evidence_ids Array(String), neutral_evidence_ids Array(String),
 assessed_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(assessed_at) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (language_version, ticker, published_at_utc, canonical_news_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.scale_table)}
(
 scale_version LowCardinality(String), ticker LowCardinality(String), horizon_code LowCardinality(String),
 publication_session LowCardinality(String), sample_count UInt64, global_sample_count UInt64,
 ticker_median Float64, global_median Float64, ticker_mad Float64, global_mad Float64,
 shrinkage_weight Float64, robust_scale Float64, trained_start_date Date, trained_end_date_exclusive Date,
 built_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(built_at) ORDER BY (scale_version, horizon_code, publication_session, ticker) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.baseline_table)}
(
 model_version LowCardinality(String), horizon_code LowCardinality(String), publication_session LowCardinality(String),
 sample_count UInt64, negative_probability Float64, neutral_probability Float64, positive_probability Float64,
 mean_target_z Float64, mean_high_z Float64, mean_low_z Float64,
 trained_start_date Date, trained_end_date_exclusive Date, built_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(built_at) ORDER BY (model_version, horizon_code, publication_session) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.effect_table)}
(
 model_version LowCardinality(String), event_id LowCardinality(String), family LowCardinality(String),
 horizon_code LowCardinality(String), publication_session LowCardinality(String), sample_count UInt64,
 reliability Float64, negative_probability Float64, neutral_probability Float64, positive_probability Float64,
 negative_log_effect Float64, neutral_log_effect Float64, positive_log_effect Float64,
 target_z_effect Float64, high_z_effect Float64, low_z_effect Float64,
 trained_start_date Date, trained_end_date_exclusive Date, built_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(built_at)
ORDER BY (model_version, horizon_code, publication_session, family, event_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.prediction_table)}
(
 prediction_version LowCardinality(String), canonical_news_id String, ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'), horizon_code LowCardinality(String), publication_session LowCardinality(String),
 relevance_class LowCardinality(String), language_class LowCardinality(String), language_score Float64,
 negative_probability Float64, neutral_probability Float64, positive_probability Float64,
 predicted_class LowCardinality(String), expected_target_return Float64, expected_high_return Float64,
 expected_low_return Float64, contributing_event_ids Array(String), predicted_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(predicted_at) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (prediction_version, horizon_code, ticker, published_at_utc, canonical_news_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.review_table)}
(
 review_version LowCardinality(String), review_id String, canonical_news_id String, ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'), sentiment_label LowCardinality(String), relevance_label LowCardinality(String),
 reviewer LowCardinality(String), source_sha256 String, imported_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(imported_at) ORDER BY (review_version, review_id) {p}""",
        f"""CREATE TABLE IF NOT EXISTS {table(db, args.status_table)}
(
 pipeline_version LowCardinality(String), stage LowCardinality(String), chunk_start Date, chunk_end_exclusive Date,
 status LowCardinality(String), row_count UInt64, detail String, elapsed_seconds Float64,
 updated_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (pipeline_version, stage, chunk_start, chunk_end_exclusive) {p}""",
    )


def required_sources(args: argparse.Namespace) -> dict[str, set[str]]:
    return {
        args.normalized_table: {
            "canonical_news_id", "published_at_utc", "title", "teaser", "body_text", "external_text",
            "pdf_text", "tickers", "channels", "provider_tags", "text_hash",
        },
        args.ticker_table: {"canonical_news_id", "published_at_utc", "ticker"},
        args.reaction_table: {"canonical_news_id", "ticker", "published_at_utc", "horizon_code", "publication_session", "abnormal_target_return", "abnormal_high_return", "abnormal_low_return", "quality_status"},
        "id_symbol_v1": {"listing_id", "ticker_normalized", "source_system"},
        "id_listing_v1": {"listing_id", "security_id", "list_date", "delisted_date"},
        "id_security_v1": {"security_id", "issuer_id"},
        "id_issuer_v1": {"issuer_id", "issuer_name", "issuer_name_normalized", "legal_name", "branding_name"},
    }


def ensure_sources(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    for table_name, required in required_sources(args).items():
        if not table_exists(client, args.database, table_name):
            raise SystemExit(f"required source table is missing: {args.database}.{table_name}")
        if missing := sorted(required - table_columns(client, args.database, table_name)):
            raise SystemExit(f"source table {args.database}.{table_name} is missing columns {missing}")


def ensure_targets(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    for sql in ddl_statements(args):
        client.execute(sql)


def replace_dictionary(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    target = table(args.database, args.event_dictionary_table)
    client.execute(
        f"ALTER TABLE {target} DELETE WHERE dictionary_version = {sql_string(EVENT_DICTIONARY_VERSION)} SETTINGS mutations_sync = 2"
    )
    now = dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    rows = [
        {
            "dictionary_version": EVENT_DICTIONARY_VERSION,
            "event_id": rule.event_id,
            "canonical_event": rule.canonical_event,
            "family": rule.family,
            "direction": rule.direction,
            "strength": rule.strength,
            "materiality": rule.materiality,
            "certainty": rule.certainty,
            "time_orientation": rule.time_orientation,
            "feature_role": rule.feature_role,
            "needles": list(rule.needles),
            "updated_at": now,
        }
        for rule in EVENT_RULES
    ]
    insert_json_rows(client, target, rows)


def replace_identity_aliases(client: ClickHouseHttpClient, args: argparse.Namespace, reporter: NewsReactionProgress | None) -> int:
    target = table(args.database, args.identity_alias_table)
    monitored_execute(client, f"ALTER TABLE {target} DELETE WHERE identity_version = {sql_string(RELEVANCE_VERSION)} SETTINGS mutations_sync = 2", reporter, "replace identity aliases")
    sql = f"""
INSERT INTO {target}
SELECT
 upperUTF8(sym.ticker_normalized) AS ticker,
 sec.issuer_id,
 issuer.issuer_name,
 lowerUTF8(trim(BOTH ' ' FROM replaceRegexpAll(coalesce(nullIf(issuer.branding_name, ''), nullIf(issuer.legal_name, ''), issuer.issuer_name), '(?i)\\s+(incorporated|inc\\.?|corporation|corp\\.?|company|co\\.?|limited|ltd\\.?|plc|holdings?)$', ''))) AS issuer_alias,
 listing.listing_id, listing.list_date, listing.delisted_date,
 {sql_string(RELEVANCE_VERSION)} AS identity_version, now64(6) AS updated_at
FROM {table(args.database, 'id_symbol_v1')} AS sym FINAL
INNER JOIN {table(args.database, 'id_listing_v1')} AS listing FINAL ON listing.listing_id = sym.listing_id
INNER JOIN {table(args.database, 'id_security_v1')} AS sec FINAL ON sec.security_id = listing.security_id
INNER JOIN {table(args.database, 'id_issuer_v1')} AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
WHERE sym.ticker_normalized != '' AND issuer.issuer_id != ''
""" + query_settings(args)
    monitored_execute(client, sql, reporter, "build identity aliases")
    return int(client.execute(f"SELECT count() FROM {target} FINAL WHERE identity_version = {sql_string(RELEVANCE_VERSION)}").strip() or 0)


def identity_alias_cte(args: argparse.Namespace) -> str:
    return f"""
aliases AS
(
 SELECT ticker, argMax(issuer_id, updated_at) AS issuer_id, argMax(issuer_name, updated_at) AS issuer_name,
  argMax(issuer_alias, updated_at) AS issuer_alias, min(list_date) AS list_date, max(delisted_date) AS delisted_date
 FROM {table(args.database, args.identity_alias_table)} FINAL
 WHERE identity_version = {sql_string(RELEVANCE_VERSION)}
 GROUP BY ticker
)
"""


def relevance_insert_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    return f"""
INSERT INTO {table(args.database, args.relevance_table)}
WITH
{identity_alias_cte(args)},
base AS
(
 SELECT t.canonical_news_id AS canonical_news_id, upperUTF8(t.ticker) AS ticker, t.published_at_utc AS published_at_utc,
  a.issuer_id AS issuer_id, a.issuer_name AS issuer_name, a.issuer_alias AS issuer_alias,
  lowerUTF8(concat(n.title, ' ', n.teaser)) AS head_text,
  lowerUTF8(concat(n.title, ' ', n.teaser, ' ', n.body_text, ' ', n.external_text, ' ', n.pdf_text, ' ', arrayStringConcat(n.provider_tags, ' '), ' ', arrayStringConcat(n.channels, ' '))) AS all_text,
  lowerUTF8(concat(arrayStringConcat(n.provider_tags, ' '), ' ', arrayStringConcat(n.channels, ' '))) AS metadata_text,
  length(n.tickers) > 1 AS multi_ticker,
  n.text_hash AS source_text_hash
 FROM {table(args.database, args.ticker_table)} AS t FINAL
 INNER JOIN {table(args.database, args.normalized_table)} AS n FINAL
  ON n.canonical_news_id = t.canonical_news_id AND n.published_at_utc = t.published_at_utc
 LEFT JOIN aliases AS a ON a.ticker = upperUTF8(t.ticker)
 WHERE t.published_at_utc >= {dt_sql(start.isoformat())} AND t.published_at_utc < {dt_sql(end.isoformat())}
  AND (a.list_date IS NULL OR toDate(t.published_at_utc, 'America/New_York') >= a.list_date)
  AND (a.delisted_date IS NULL OR toDate(t.published_at_utc, 'America/New_York') <= a.delisted_date)
),
flags AS
(
 SELECT canonical_news_id, ticker, published_at_utc, issuer_id, issuer_name, issuer_alias,
  head_text, all_text, metadata_text, multi_ticker, source_text_hash,
  match(all_text, concat('(^|[^a-z0-9])', regexpQuoteMeta(lowerUTF8(ticker)), '([^a-z0-9]|$)')) AS ticker_mentioned,
  length(issuer_alias) >= 4 AND position(all_text, issuer_alias) > 0 AS issuer_mentioned,
  multi_ticker OR multiSearchAny(metadata_text, ['benzinga insights','trading ideas','movers','analyst ratings','price target','top stories','why it is moving','why it''s moving','general']) > 0 AS roundup_or_analyst,
  multiSearchAny(head_text, ['reports','announces','raises','cuts','lowers','reaffirms','withdraws','acquires','merger','offering','contract','approval','clearance','trial','appoints','resigns','dividend','buyback','investigation','files for','launches','recalls']) > 0 AS direct_event_language
 FROM base
),
classified AS
(
 SELECT canonical_news_id, ticker, published_at_utc, issuer_id, issuer_name, issuer_alias,
  multi_ticker, source_text_hash, ticker_mentioned, issuer_mentioned, roundup_or_analyst, direct_event_language,
  if(NOT ticker_mentioned AND NOT issuer_mentioned, 'not_relevant',
    if(roundup_or_analyst OR multi_ticker OR NOT direct_event_language, 'ticker_related', 'company_specific')) AS relevance_class,
  greatest(0., least(1., 0.15 + 0.35 * ticker_mentioned + 0.35 * issuer_mentioned + 0.25 * direct_event_language - 0.30 * roundup_or_analyst - 0.10 * multi_ticker)) AS relevance_score
 FROM flags
)
SELECT {sql_string(RELEVANCE_VERSION)}, canonical_news_id, ticker, published_at_utc,
 coalesce(issuer_id, ''), coalesce(issuer_name, ''), coalesce(issuer_alias, ''), relevance_class, relevance_score,
 ticker_mentioned, issuer_mentioned, direct_event_language, multi_ticker, roundup_or_analyst,
 arrayFilter(x -> x != '', [if(ticker_mentioned, 'ticker_mentioned', ''), if(issuer_mentioned, 'issuer_mentioned', ''), if(direct_event_language, 'direct_issuer_event', ''), if(roundup_or_analyst, 'roundup_or_analyst', ''), if(multi_ticker, 'multiple_tickers', '')]),
 source_text_hash, now64(6)
FROM classified
""" + query_settings(args, external_group_by=True)


def event_insert_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    explicit_negative = ("clinical_endpoint_miss", "fda_rejection", "guidance_withdraw")
    explicit_sql = "[" + ",".join(sql_string(value) for value in explicit_negative) + "]"
    return f"""
INSERT INTO {table(args.database, args.event_feature_table)}
WITH
source AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker, r.published_at_utc AS published_at_utc,
  r.relevance_class AS relevance_class, r.issuer_alias AS issuer_alias,
  lowerUTF8(n.title) AS title_text, lowerUTF8(n.teaser) AS teaser_text,
  lowerUTF8(n.body_text) AS full_body,
  lowerUTF8(concat(n.external_text, ' ', n.pdf_text)) AS external_text,
  lowerUTF8(concat(arrayStringConcat(n.provider_tags, ' '), ' ', arrayStringConcat(n.channels, ' '))) AS metadata_text,
  n.text_hash AS source_text_hash
 FROM {table(args.database, args.relevance_table)} AS r FINAL
 INNER JOIN {table(args.database, args.normalized_table)} AS n FINAL ON n.canonical_news_id = r.canonical_news_id
 WHERE r.relevance_version = {sql_string(RELEVANCE_VERSION)} AND r.relevance_class != 'not_relevant'
  AND r.published_at_utc >= {dt_sql(start.isoformat())} AND r.published_at_utc < {dt_sql(end.isoformat())}
),
scoped AS
(
 SELECT canonical_news_id, ticker, published_at_utc, relevance_class, issuer_alias,
  title_text, teaser_text, full_body, external_text, metadata_text, source_text_hash,
  if(relevance_class = 'company_specific', concat(full_body, ' ', external_text),
    arrayStringConcat(arrayFilter(part ->
      match(part, concat('(^|[^a-z0-9])', regexpQuoteMeta(lowerUTF8(ticker)), '([^a-z0-9]|$)')) OR
      (length(issuer_alias) >= 4 AND position(part, issuer_alias) > 0),
      splitByRegexp('[.!?;]+', concat(full_body, ' ', external_text))), ' ')) AS scoped_body
 FROM source
),
matched AS
(
 SELECT s.canonical_news_id AS canonical_news_id, s.ticker AS ticker, s.published_at_utc AS published_at_utc,
  s.relevance_class AS relevance_class, s.title_text AS title_text, s.teaser_text AS teaser_text,
  s.scoped_body AS scoped_body, s.metadata_text AS metadata_text, s.source_text_hash AS source_text_hash,
  d.event_id AS event_id, d.family AS family, d.direction AS direction,
  (if(arrayExists(needle -> position(title_text, needle) > 0, d.needles), 1, 0)
   + if(arrayExists(needle -> position(teaser_text, needle) > 0, d.needles), 2, 0)
   + if(arrayExists(needle -> position(scoped_body, needle) > 0, d.needles), 4, 0)
   + if(arrayExists(needle -> position(metadata_text, needle) > 0, d.needles), 8, 0)) AS source_mask,
  d.direction > 0 AND d.event_id NOT IN {explicit_sql} AND
   arrayExists(needle -> position(concat(title_text, ' ', teaser_text, ' ', scoped_body), concat('not ', needle)) > 0, d.needles) AS negated
 FROM scoped AS s
 CROSS JOIN {table(args.database, args.event_dictionary_table)} AS d FINAL
 WHERE d.dictionary_version = {sql_string(EVENT_DICTIONARY_VERSION)}
)
SELECT {sql_string(EVENT_DICTIONARY_VERSION)}, canonical_news_id, ticker, published_at_utc,
 event_id, family,
 if(event_id = 'trial_adverse_event' AND arrayExists(needle -> position(concat(title_text, ' ', teaser_text, ' ', scoped_body), concat('no ', needle)) > 0, ['safety concern']), 1, if(negated, -direction, direction)),
 source_mask, negated,
 relevance_class = 'ticker_related', source_text_hash, now64(6)
FROM matched WHERE source_mask > 0
""" + query_settings(args, external_group_by=True)


def language_insert_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    return f"""
INSERT INTO {table(args.database, args.language_table)}
WITH family_evidence AS
(
 SELECT f.canonical_news_id, f.ticker, f.published_at_utc, f.family, f.effective_direction,
  argMax(f.event_id, d.strength * d.materiality * d.certainty *
    if(bitTest(f.source_mask, 0), 1.35, if(bitTest(f.source_mask, 1), 1.15, if(bitTest(f.source_mask, 2), 1., 0.9))) *
    if(d.time_orientation = 'forward', 1.25, if(d.time_orientation = 'structural', 1.10, 1.))) AS evidence_id,
  max(d.strength * d.materiality * d.certainty *
    if(bitTest(f.source_mask, 0), 1.35, if(bitTest(f.source_mask, 1), 1.15, if(bitTest(f.source_mask, 2), 1., 0.9))) *
    if(d.time_orientation = 'forward', 1.25, if(d.time_orientation = 'structural', 1.10, 1.))) AS evidence_mass
 FROM {table(args.database, args.event_feature_table)} AS f FINAL
 INNER JOIN {table(args.database, args.event_dictionary_table)} AS d FINAL ON d.event_id = f.event_id
 WHERE f.extraction_version = {sql_string(EVENT_DICTIONARY_VERSION)} AND d.dictionary_version = {sql_string(EVENT_DICTIONARY_VERSION)}
  AND f.published_at_utc >= {dt_sql(start.isoformat())} AND f.published_at_utc < {dt_sql(end.isoformat())}
 GROUP BY f.canonical_news_id, f.ticker, f.published_at_utc, f.family, f.effective_direction
),
mass AS
(
 SELECT r.canonical_news_id, r.ticker, r.published_at_utc, r.relevance_class,
  sumIf(e.evidence_mass, e.effective_direction > 0) AS positive_mass,
  sumIf(e.evidence_mass, e.effective_direction < 0) AS negative_mass,
  sumIf(e.evidence_mass, e.effective_direction = 0) AS neutral_mass,
  groupArrayIf(e.evidence_id, e.effective_direction > 0) AS positive_ids,
  groupArrayIf(e.evidence_id, e.effective_direction < 0) AS negative_ids,
  groupArrayIf(e.evidence_id, e.effective_direction = 0) AS neutral_ids
 FROM {table(args.database, args.relevance_table)} AS r FINAL
 LEFT JOIN family_evidence AS e ON e.canonical_news_id = r.canonical_news_id AND e.ticker = r.ticker
 WHERE r.relevance_version = {sql_string(RELEVANCE_VERSION)}
  AND r.published_at_utc >= {dt_sql(start.isoformat())} AND r.published_at_utc < {dt_sql(end.isoformat())}
 GROUP BY r.canonical_news_id, r.ticker, r.published_at_utc, r.relevance_class
),
scored AS
(
 SELECT *, (positive_mass - negative_mass) / greatest(positive_mass + negative_mass, 1e-9) AS score,
  least(positive_mass, negative_mass) / greatest(greatest(positive_mass, negative_mass), 1e-9) AS mixedness
 FROM mass
)
SELECT {sql_string(LANGUAGE_VERSION)}, canonical_news_id, ticker, published_at_utc, relevance_class,
 if(relevance_class = 'not_relevant', 'not_applicable',
  if(positive_mass >= {float(args.mixed_min_mass)} AND negative_mass >= {float(args.mixed_min_mass)} AND mixedness >= {float(args.mixed_ratio)}, 'mixed',
   if(score >= {float(args.language_threshold)}, 'positive', if(score <= -{float(args.language_threshold)}, 'negative', 'neutral')))) AS language_class,
 score, positive_mass, negative_mass, neutral_mass, mixedness,
 arraySort(arrayDistinct(positive_ids)), arraySort(arrayDistinct(negative_ids)), arraySort(arrayDistinct(neutral_ids)), now64(6)
FROM scored
""" + query_settings(args, external_group_by=True)


def delete_month(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> None:
    specs = (
        (args.relevance_table, "relevance_version", RELEVANCE_VERSION),
        (args.event_feature_table, "extraction_version", EVENT_DICTIONARY_VERSION),
        (args.language_table, "language_version", LANGUAGE_VERSION),
    )
    for table_name, version_column, version in specs:
        client.execute(
            f"ALTER TABLE {table(args.database, table_name)} DELETE WHERE {quote_ident(version_column)} = {sql_string(version)} "
            f"AND published_at_utc >= {dt_sql(start.isoformat())} AND published_at_utc < {dt_sql(end.isoformat())} SETTINGS mutations_sync = 2"
        )


def current_status(client: ClickHouseHttpClient, args: argparse.Namespace, stage: str, start: dt.date, end: dt.date) -> str:
    text = client.execute(f"""
SELECT argMax(status, updated_at) FROM {table(args.database, args.status_table)}
WHERE pipeline_version = {sql_string(PIPELINE_VERSION)} AND stage = {sql_string(stage)}
 AND chunk_start = toDate({sql_string(start.isoformat())}) AND chunk_end_exclusive = toDate({sql_string(end.isoformat())})
""").strip()
    return text


def record_status(client: ClickHouseHttpClient, args: argparse.Namespace, stage: str, start: dt.date, end: dt.date, status: str, rows: int, detail: str, elapsed: float) -> None:
    safe_detail = detail[:4_000]
    client.execute(
        f"INSERT INTO {table(args.database, args.status_table)} FORMAT JSONEachRow\n" +
        json.dumps({
            "pipeline_version": PIPELINE_VERSION, "stage": stage, "chunk_start": start.isoformat(),
            "chunk_end_exclusive": end.isoformat(), "status": status, "row_count": rows,
            "detail": safe_detail, "elapsed_seconds": elapsed,
            "updated_at": dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
        }, separators=(",", ":"))
    )


def count_month(client: ClickHouseHttpClient, args: argparse.Namespace, table_name: str, version_column: str, version: str, start: dt.date, end: dt.date) -> int:
    return int(client.execute(
        f"SELECT count() FROM {table(args.database, table_name)} FINAL WHERE {quote_ident(version_column)} = {sql_string(version)} "
        f"AND published_at_utc >= {dt_sql(start.isoformat())} AND published_at_utc < {dt_sql(end.isoformat())}"
    ).strip() or 0)


def process_month(args: argparse.Namespace, start: dt.date, end: dt.date) -> MonthResult:
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    started = time.perf_counter()
    if not args.rebuild and current_status(client, args, "extract", start, end) == "complete":
        return MonthResult(start.isoformat(), end.isoformat(), -1, -1, -1, 0.0)
    try:
        record_status(client, args, "extract", start, end, "running", 0, "relevance", 0.0)
        delete_month(client, args, start, end)
        client.execute(relevance_insert_sql(args, start, end))
        relevance_rows = count_month(client, args, args.relevance_table, "relevance_version", RELEVANCE_VERSION, start, end)
        record_status(client, args, "extract", start, end, "running", relevance_rows, "events", time.perf_counter() - started)
        client.execute(event_insert_sql(args, start, end))
        event_rows = count_month(client, args, args.event_feature_table, "extraction_version", EVENT_DICTIONARY_VERSION, start, end)
        record_status(client, args, "extract", start, end, "running", event_rows, "language", time.perf_counter() - started)
        client.execute(language_insert_sql(args, start, end))
        language_rows = count_month(client, args, args.language_table, "language_version", LANGUAGE_VERSION, start, end)
        if language_rows != relevance_rows:
            raise RuntimeError(f"language/relevance row mismatch: language={language_rows:,} relevance={relevance_rows:,}")
        elapsed = time.perf_counter() - started
        record_status(client, args, "extract", start, end, "complete", language_rows, f"relevance={relevance_rows};events={event_rows};language={language_rows}", elapsed)
        return MonthResult(start.isoformat(), end.isoformat(), relevance_rows, event_rows, language_rows, elapsed)
    except BaseException as exc:
        record_status(client, args, "extract", start, end, "failed", 0, repr(exc), time.perf_counter() - started)
        raise


def replace_version(client: ClickHouseHttpClient, args: argparse.Namespace, table_name: str, column: str, version: str, reporter: NewsReactionProgress | None, label: str) -> None:
    monitored_execute(client, f"ALTER TABLE {table(args.database, table_name)} DELETE WHERE {quote_ident(column)} = {sql_string(version)} SETTINGS mutations_sync = 2", reporter, label)


def scale_insert_sql(args: argparse.Namespace) -> str:
    train_start, train_end = dt_sql(args.train_start_date), dt_sql(args.train_end_date)
    z = float(args.reaction_z_threshold)
    del z  # threshold is used by model SQL; scale construction is label-independent.
    return f"""
INSERT INTO {table(args.database, args.scale_table)}
WITH clean AS
(
 SELECT r.ticker, r.horizon_code, r.publication_session, r.abnormal_target_return AS value
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 INNER JOIN {table(args.database, args.relevance_table)} AS rel FINAL
  ON rel.canonical_news_id = r.canonical_news_id AND rel.ticker = r.ticker
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND rel.relevance_version = {sql_string(RELEVANCE_VERSION)}
  AND rel.relevance_class != 'not_relevant' AND r.quality_status = 'clean' AND r.abnormal_target_return IS NOT NULL
  AND r.published_at_utc >= {train_start} AND r.published_at_utc < {train_end}
),
global_median AS
(
 SELECT horizon_code, publication_session, count() AS n, medianExact(value) AS med
 FROM clean GROUP BY horizon_code, publication_session
),
global_scale AS
(
 SELECT c.horizon_code, c.publication_session, any(g.n) AS n, any(g.med) AS med,
  greatest(medianExact(abs(c.value - g.med)) * 1.4826, 1e-5) AS mad
 FROM clean AS c INNER JOIN global_median AS g USING (horizon_code, publication_session)
 GROUP BY c.horizon_code, c.publication_session
),
ticker_median AS
(
 SELECT ticker, horizon_code, publication_session, count() AS n, medianExact(value) AS med
 FROM clean GROUP BY ticker, horizon_code, publication_session
),
ticker_scale AS
(
 SELECT c.ticker, c.horizon_code, c.publication_session, any(t.n) AS n, any(t.med) AS med,
  greatest(medianExact(abs(c.value - t.med)) * 1.4826, 1e-5) AS mad
 FROM clean AS c INNER JOIN ticker_median AS t USING (ticker, horizon_code, publication_session)
 GROUP BY c.ticker, c.horizon_code, c.publication_session
)
SELECT {sql_string(SCALE_VERSION)}, t.ticker, t.horizon_code, t.publication_session, t.n, g.n,
 t.med, g.med, t.mad, g.mad,
 if(t.n >= {int(args.ticker_scale_min_support)}, t.n / (t.n + {float(args.scale_shrinkage)}), 0.) AS w,
 greatest(w * t.mad + (1 - w) * g.mad, 1e-5), toDate({sql_string(args.train_start_date)}), toDate({sql_string(args.train_end_date)}), now64(6)
FROM ticker_scale AS t INNER JOIN global_scale AS g USING (horizon_code, publication_session)
UNION ALL
SELECT {sql_string(SCALE_VERSION)}, '*', horizon_code, publication_session, n, n, med, med, mad, mad, 0., mad,
 toDate({sql_string(args.train_start_date)}), toDate({sql_string(args.train_end_date)}), now64(6)
FROM global_scale
""" + query_settings(args, external_group_by=True)


def reaction_training_cte(args: argparse.Namespace) -> str:
    return f"""
clean AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker, r.published_at_utc AS published_at_utc,
  r.horizon_code AS horizon_code, r.publication_session AS publication_session,
  r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) AS target_z,
  r.abnormal_high_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) AS high_z,
  r.abnormal_low_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) AS low_z,
  if(target_z > {float(args.reaction_z_threshold)}, 'positive', if(target_z < -{float(args.reaction_z_threshold)}, 'negative', 'neutral')) AS actual_class
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 INNER JOIN {table(args.database, args.relevance_table)} AS rel FINAL ON rel.canonical_news_id = r.canonical_news_id AND rel.ticker = r.ticker
 LEFT JOIN {table(args.database, args.scale_table)} AS ts FINAL ON ts.scale_version = {sql_string(SCALE_VERSION)} AND ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN {table(args.database, args.scale_table)} AS gs FINAL ON gs.scale_version = {sql_string(SCALE_VERSION)} AND gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND rel.relevance_version = {sql_string(RELEVANCE_VERSION)}
  AND rel.relevance_class != 'not_relevant' AND r.quality_status = 'clean' AND r.abnormal_target_return IS NOT NULL
  AND r.published_at_utc >= {dt_sql(args.train_start_date)} AND r.published_at_utc < {dt_sql(args.train_end_date)}
)
"""


def baseline_insert_sql(args: argparse.Namespace) -> str:
    return f"""
INSERT INTO {table(args.database, args.baseline_table)}
WITH {reaction_training_cte(args)}
SELECT {sql_string(MODEL_VERSION)}, horizon_code, publication_session, count() AS n,
 (countIf(actual_class = 'negative') + 1.) / (n + 3.),
 (countIf(actual_class = 'neutral') + 1.) / (n + 3.),
 (countIf(actual_class = 'positive') + 1.) / (n + 3.),
 avg(target_z), avg(high_z), avg(low_z), toDate({sql_string(args.train_start_date)}), toDate({sql_string(args.train_end_date)}), now64(6)
FROM clean GROUP BY horizon_code, publication_session
""" + query_settings(args, external_group_by=True)


def effect_insert_sql(args: argparse.Namespace) -> str:
    prior = float(args.effect_prior_strength)
    return f"""
INSERT INTO {table(args.database, args.effect_table)}
WITH
{reaction_training_cte(args)},
event_rows AS
(
 SELECT c.*, f.event_id, f.family
 FROM clean AS c
 INNER JOIN {table(args.database, args.event_feature_table)} AS f FINAL
  ON f.canonical_news_id = c.canonical_news_id AND f.ticker = c.ticker
 WHERE f.extraction_version = {sql_string(EVENT_DICTIONARY_VERSION)}
),
stats AS
(
 SELECT event_id, family, horizon_code, publication_session, count() AS n,
  countIf(actual_class = 'negative') AS neg, countIf(actual_class = 'neutral') AS neu, countIf(actual_class = 'positive') AS pos,
  avg(target_z) AS target_mean, avg(high_z) AS high_mean, avg(low_z) AS low_mean
 FROM event_rows GROUP BY event_id, family, horizon_code, publication_session HAVING n >= {int(args.effect_min_support)}
)
SELECT {sql_string(MODEL_VERSION)}, s.event_id, s.family, s.horizon_code, s.publication_session, s.n,
 s.n / (s.n + {prior}) AS reliability,
 (s.neg + {prior} * b.negative_probability) / (s.n + {prior}) AS p_neg,
 (s.neu + {prior} * b.neutral_probability) / (s.n + {prior}) AS p_neu,
 (s.pos + {prior} * b.positive_probability) / (s.n + {prior}) AS p_pos,
 reliability * log(greatest(p_neg, 1e-9) / greatest(b.negative_probability, 1e-9)),
 reliability * log(greatest(p_neu, 1e-9) / greatest(b.neutral_probability, 1e-9)),
 reliability * log(greatest(p_pos, 1e-9) / greatest(b.positive_probability, 1e-9)),
 reliability * (s.target_mean - b.mean_target_z), reliability * (s.high_mean - b.mean_high_z), reliability * (s.low_mean - b.mean_low_z),
 toDate({sql_string(args.train_start_date)}), toDate({sql_string(args.train_end_date)}), now64(6)
FROM stats AS s INNER JOIN {table(args.database, args.baseline_table)} AS b FINAL
 ON b.model_version = {sql_string(MODEL_VERSION)} AND b.horizon_code = s.horizon_code AND b.publication_session = s.publication_session
""" + query_settings(args, external_group_by=True)


def prediction_insert_sql(args: argparse.Namespace) -> str:
    return f"""
INSERT INTO {table(args.database, args.prediction_table)}
WITH
family_candidates AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker, r.published_at_utc AS published_at_utc,
  r.horizon_code AS horizon_code, r.publication_session AS publication_session, e.family AS family,
  argMax(e.event_id, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS event_id,
  argMax(e.negative_log_effect, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS neg_effect,
  argMax(e.neutral_log_effect, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS neu_effect,
  argMax(e.positive_log_effect, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS pos_effect,
  argMax(e.target_z_effect, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS target_effect,
  argMax(e.high_z_effect, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS high_effect,
  argMax(e.low_z_effect, greatest(abs(e.negative_log_effect), abs(e.neutral_log_effect), abs(e.positive_log_effect))) AS low_effect
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 INNER JOIN {table(args.database, args.event_feature_table)} AS f FINAL ON f.canonical_news_id = r.canonical_news_id AND f.ticker = r.ticker
 INNER JOIN {table(args.database, args.effect_table)} AS e FINAL ON e.model_version = {sql_string(MODEL_VERSION)} AND e.event_id = f.event_id AND e.horizon_code = r.horizon_code AND e.publication_session = r.publication_session
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND f.extraction_version = {sql_string(EVENT_DICTIONARY_VERSION)}
  AND r.published_at_utc >= {dt_sql(args.holdout_start_date)} AND r.published_at_utc < {dt_sql(args.holdout_end_date)}
 GROUP BY r.canonical_news_id, r.ticker, r.published_at_utc, r.horizon_code, r.publication_session, e.family
),
article_effects AS
(
 SELECT canonical_news_id, ticker, published_at_utc, horizon_code, publication_session,
  sum(neg_effect) AS neg_effect, sum(neu_effect) AS neu_effect, sum(pos_effect) AS pos_effect,
  sum(target_effect) AS target_effect, sum(high_effect) AS high_effect, sum(low_effect) AS low_effect,
  groupArray(event_id) AS event_ids
 FROM family_candidates GROUP BY canonical_news_id, ticker, published_at_utc, horizon_code, publication_session
),
raw AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker, r.published_at_utc AS published_at_utc,
  r.horizon_code AS horizon_code, r.publication_session AS publication_session,
  rel.relevance_class AS relevance_class, lang.language_class AS language_class, lang.language_score AS language_score,
  log(greatest(b.negative_probability, 1e-9)) + coalesce(a.neg_effect, 0.) AS ln,
  log(greatest(b.neutral_probability, 1e-9)) + coalesce(a.neu_effect, 0.) AS lz,
  log(greatest(b.positive_probability, 1e-9)) + coalesce(a.pos_effect, 0.) AS lp,
  b.mean_target_z + coalesce(a.target_effect, 0.) AS target_z,
  b.mean_high_z + coalesce(a.high_effect, 0.) AS high_z,
  b.mean_low_z + coalesce(a.low_effect, 0.) AS low_z,
  if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) AS robust_scale,
  if(length(a.event_ids) > 0, a.event_ids, CAST([], 'Array(String)')) AS event_ids
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 INNER JOIN {table(args.database, args.relevance_table)} AS rel FINAL ON rel.relevance_version = {sql_string(RELEVANCE_VERSION)} AND rel.canonical_news_id = r.canonical_news_id AND rel.ticker = r.ticker
 INNER JOIN {table(args.database, args.language_table)} AS lang FINAL ON lang.language_version = {sql_string(LANGUAGE_VERSION)} AND lang.canonical_news_id = r.canonical_news_id AND lang.ticker = r.ticker
 INNER JOIN {table(args.database, args.baseline_table)} AS b FINAL ON b.model_version = {sql_string(MODEL_VERSION)} AND b.horizon_code = r.horizon_code AND b.publication_session = r.publication_session
 LEFT JOIN article_effects AS a ON a.canonical_news_id = r.canonical_news_id AND a.ticker = r.ticker AND a.horizon_code = r.horizon_code AND a.publication_session = r.publication_session
 LEFT JOIN {table(args.database, args.scale_table)} AS ts FINAL ON ts.scale_version = {sql_string(SCALE_VERSION)} AND ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN {table(args.database, args.scale_table)} AS gs FINAL ON gs.scale_version = {sql_string(SCALE_VERSION)} AND gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND rel.relevance_class != 'not_relevant'
  AND r.published_at_utc >= {dt_sql(args.holdout_start_date)} AND r.published_at_utc < {dt_sql(args.holdout_end_date)}
),
prob AS
(
 SELECT *, greatest(ln, lz, lp) AS max_logit,
  exp(ln - max_logit) + exp(lz - max_logit) + exp(lp - max_logit) AS denom
 FROM raw
)
SELECT {sql_string(PREDICTION_VERSION)}, canonical_news_id, ticker, published_at_utc, horizon_code, publication_session,
 relevance_class, language_class, language_score,
 exp(ln - max_logit) / denom AS p_neg, exp(lz - max_logit) / denom AS p_neu, exp(lp - max_logit) / denom AS p_pos,
 if(p_pos >= p_neg AND p_pos >= p_neu, 'positive', if(p_neg >= p_neu, 'negative', 'neutral')),
 target_z * robust_scale, greatest(high_z, target_z) * robust_scale, least(low_z, target_z) * robust_scale,
 arraySort(arrayDistinct(event_ids)), now64(6)
FROM prob
""" + query_settings(args, external_group_by=True)


def import_review_labels(client: ClickHouseHttpClient, args: argparse.Namespace, path: Path) -> int:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            sentiment = (row.get("review_sentiment") or row.get("sentiment_label") or "").strip().lower()
            relevance = (row.get("review_relevance") or row.get("relevance_label") or "").strip().lower()
            if sentiment not in {"positive", "negative", "neutral", "mixed"}:
                raise ValueError(f"invalid review sentiment {sentiment!r}")
            if relevance not in {"company_specific", "ticker_related", "not_relevant"}:
                raise ValueError(f"invalid review relevance {relevance!r}")
            rows.append({
                "review_version": REVIEW_VERSION,
                "review_id": row.get("review_id") or hashlib.sha256(f"{row.get('canonical_news_id')}|{row.get('ticker')}".encode()).hexdigest()[:24],
                "canonical_news_id": row["canonical_news_id"], "ticker": row["ticker"],
                "published_at_utc": row["published_at_utc"], "sentiment_label": sentiment,
                "relevance_label": relevance, "reviewer": "codex_blind_expert",
                "source_sha256": digest, "imported_at": dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
            })
    if len(rows) != len({row["review_id"] for row in rows}):
        raise ValueError("review CSV contains duplicate review IDs")
    target = table(args.database, args.review_table)
    client.execute(f"ALTER TABLE {target} DELETE WHERE review_version = {sql_string(REVIEW_VERSION)} SETTINGS mutations_sync = 2")
    insert_json_rows(client, target, rows)
    return len(rows)


def evaluation_sql(args: argparse.Namespace) -> str:
    return f"""
WITH actual AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker,
  r.horizon_code AS horizon_code, r.publication_session AS publication_session,
  if(r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) > {float(args.reaction_z_threshold)}, 'positive',
   if(r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) < -{float(args.reaction_z_threshold)}, 'negative', 'neutral')) AS actual_class
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 LEFT JOIN {table(args.database, args.scale_table)} AS ts FINAL ON ts.scale_version = {sql_string(SCALE_VERSION)} AND ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN {table(args.database, args.scale_table)} AS gs FINAL ON gs.scale_version = {sql_string(SCALE_VERSION)} AND gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND r.quality_status = 'clean' AND r.abnormal_target_return IS NOT NULL
  AND r.published_at_utc >= {dt_sql(args.holdout_start_date)} AND r.published_at_utc < {dt_sql(args.holdout_end_date)}
),
reaction AS
(
 SELECT count() AS n,
  avg(-log(greatest(if(a.actual_class = 'negative', p.negative_probability, if(a.actual_class = 'neutral', p.neutral_probability, p.positive_probability)), 1e-12))) AS log_loss,
  avg(pow(p.negative_probability - (a.actual_class = 'negative'), 2) + pow(p.neutral_probability - (a.actual_class = 'neutral'), 2) + pow(p.positive_probability - (a.actual_class = 'positive'), 2)) / 3 AS brier,
  avg(p.predicted_class = a.actual_class) AS accuracy
 FROM {table(args.database, args.prediction_table)} AS p FINAL INNER JOIN actual AS a
  ON a.canonical_news_id = p.canonical_news_id AND a.ticker = p.ticker AND a.horizon_code = p.horizon_code AND a.publication_session = p.publication_session
 WHERE p.prediction_version = {sql_string(PREDICTION_VERSION)}
),
review AS
(
 SELECT count() AS n, avg(r.relevance_label = rel.relevance_class) AS relevance_accuracy,
  avgIf(r.sentiment_label = lang.language_class, r.relevance_label != 'not_relevant') AS language_accuracy
 FROM {table(args.database, args.review_table)} AS r FINAL
 INNER JOIN {table(args.database, args.relevance_table)} AS rel FINAL ON rel.relevance_version = {sql_string(RELEVANCE_VERSION)} AND rel.canonical_news_id = r.canonical_news_id AND rel.ticker = r.ticker
 INNER JOIN {table(args.database, args.language_table)} AS lang FINAL ON lang.language_version = {sql_string(LANGUAGE_VERSION)} AND lang.canonical_news_id = r.canonical_news_id AND lang.ticker = r.ticker
 WHERE r.review_version = {sql_string(REVIEW_VERSION)}
)
SELECT reaction.n AS reaction_rows, reaction.log_loss AS reaction_log_loss, reaction.brier AS reaction_brier_score,
 reaction.accuracy AS reaction_accuracy, review.n AS language_review_rows,
 review.relevance_accuracy, review.language_accuracy FROM reaction CROSS JOIN review FORMAT JSONEachRow
""" + query_settings(args)


def calibration_sql(args: argparse.Namespace) -> str:
    return f"""
WITH actual AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker,
  r.horizon_code AS horizon_code, r.publication_session AS publication_session,
  if(r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) > {float(args.reaction_z_threshold)}, 'positive',
   if(r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) < -{float(args.reaction_z_threshold)}, 'negative', 'neutral')) AS actual_class
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 LEFT JOIN {table(args.database, args.scale_table)} AS ts FINAL ON ts.scale_version = {sql_string(SCALE_VERSION)} AND ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN {table(args.database, args.scale_table)} AS gs FINAL ON gs.scale_version = {sql_string(SCALE_VERSION)} AND gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND r.quality_status = 'clean' AND r.abnormal_target_return IS NOT NULL
  AND r.published_at_utc >= {dt_sql(args.holdout_start_date)} AND r.published_at_utc < {dt_sql(args.holdout_end_date)}
)
SELECT p.horizon_code, p.publication_session, p.predicted_class,
 least(9, toUInt8(floor(greatest(p.negative_probability, p.neutral_probability, p.positive_probability) * 10))) AS confidence_bin,
 count() AS sample_count, avg(greatest(p.negative_probability, p.neutral_probability, p.positive_probability)) AS mean_confidence,
 avg(p.predicted_class = a.actual_class) AS empirical_accuracy
FROM {table(args.database, args.prediction_table)} AS p FINAL INNER JOIN actual AS a
 ON a.canonical_news_id = p.canonical_news_id AND a.ticker = p.ticker AND a.horizon_code = p.horizon_code AND a.publication_session = p.publication_session
WHERE p.prediction_version = {sql_string(PREDICTION_VERSION)}
GROUP BY p.horizon_code, p.publication_session, p.predicted_class, confidence_bin
ORDER BY p.horizon_code, p.publication_session, p.predicted_class, confidence_bin FORMAT JSONEachRow
""" + query_settings(args)


def reaction_confusion_sql(args: argparse.Namespace) -> str:
    return f"""
WITH actual AS
(
 SELECT r.canonical_news_id AS canonical_news_id, r.ticker AS ticker,
  r.horizon_code AS horizon_code, r.publication_session AS publication_session,
  if(r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) > {float(args.reaction_z_threshold)}, 'positive',
   if(r.abnormal_target_return / if(ts.sample_count > 0, ts.robust_scale, gs.robust_scale) < -{float(args.reaction_z_threshold)}, 'negative', 'neutral')) AS actual_class
 FROM {table(args.database, args.reaction_table)} AS r FINAL
 LEFT JOIN {table(args.database, args.scale_table)} AS ts FINAL ON ts.scale_version = {sql_string(SCALE_VERSION)} AND ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN {table(args.database, args.scale_table)} AS gs FINAL ON gs.scale_version = {sql_string(SCALE_VERSION)} AND gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {sql_string(LABEL_VERSION)} AND r.quality_status = 'clean' AND r.abnormal_target_return IS NOT NULL
  AND r.published_at_utc >= {dt_sql(args.holdout_start_date)} AND r.published_at_utc < {dt_sql(args.holdout_end_date)}
)
SELECT p.horizon_code, p.publication_session, a.actual_class, p.predicted_class, count() AS sample_count
FROM {table(args.database, args.prediction_table)} AS p FINAL INNER JOIN actual AS a
 ON a.canonical_news_id = p.canonical_news_id AND a.ticker = p.ticker AND a.horizon_code = p.horizon_code AND a.publication_session = p.publication_session
WHERE p.prediction_version = {sql_string(PREDICTION_VERSION)}
GROUP BY p.horizon_code, p.publication_session, a.actual_class, p.predicted_class
ORDER BY p.horizon_code, p.publication_session, a.actual_class, p.predicted_class FORMAT JSONEachRow
""" + query_settings(args)


def language_confusion_sql(args: argparse.Namespace) -> str:
    return f"""
SELECT 'relevance' AS task, r.relevance_label AS actual_class, rel.relevance_class AS predicted_class, count() AS sample_count
FROM {table(args.database, args.review_table)} AS r FINAL
INNER JOIN {table(args.database, args.relevance_table)} AS rel FINAL
 ON rel.relevance_version = {sql_string(RELEVANCE_VERSION)} AND rel.canonical_news_id = r.canonical_news_id AND rel.ticker = r.ticker
WHERE r.review_version = {sql_string(REVIEW_VERSION)}
GROUP BY actual_class, predicted_class
UNION ALL
SELECT 'language' AS task, r.sentiment_label AS actual_class, lang.language_class AS predicted_class, count() AS sample_count
FROM {table(args.database, args.review_table)} AS r FINAL
INNER JOIN {table(args.database, args.language_table)} AS lang FINAL
 ON lang.language_version = {sql_string(LANGUAGE_VERSION)} AND lang.canonical_news_id = r.canonical_news_id AND lang.ticker = r.ticker
WHERE r.review_version = {sql_string(REVIEW_VERSION)} AND r.relevance_label != 'not_relevant'
GROUP BY actual_class, predicted_class
ORDER BY task, actual_class, predicted_class FORMAT JSONEachRow
""" + query_settings(args)


def json_rows(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_global_stage(client: ClickHouseHttpClient, args: argparse.Namespace, reporter: NewsReactionProgress, stage: str) -> int:
    start = date_arg(args.start_date)
    end = date_arg(args.end_date)
    reporter.stage_start(stage)
    reporter.chunk_start(stage, stage)
    began = time.perf_counter()
    if stage == "scale":
        replace_version(client, args, args.scale_table, "scale_version", SCALE_VERSION, reporter, "replace robust scales")
        monitored_execute(client, scale_insert_sql(args), reporter, "build robust scales")
        target, column, version = args.scale_table, "scale_version", SCALE_VERSION
    elif stage == "train":
        replace_version(client, args, args.baseline_table, "model_version", MODEL_VERSION, reporter, "replace baselines")
        replace_version(client, args, args.effect_table, "model_version", MODEL_VERSION, reporter, "replace event effects")
        monitored_execute(client, baseline_insert_sql(args), reporter, "train reaction baselines")
        monitored_execute(client, effect_insert_sql(args), reporter, "train empirical Bayes effects")
        target, column, version = args.effect_table, "model_version", MODEL_VERSION
    elif stage == "predict":
        replace_version(client, args, args.prediction_table, "prediction_version", PREDICTION_VERSION, reporter, "replace holdout predictions")
        monitored_execute(client, prediction_insert_sql(args), reporter, "predict holdout reactions")
        target, column, version = args.prediction_table, "prediction_version", PREDICTION_VERSION
    else:
        raise ValueError(stage)
    rows = int(client.execute(f"SELECT count() FROM {table(args.database, target)} FINAL WHERE {quote_ident(column)} = {sql_string(version)}").strip() or 0)
    record_status(client, args, stage, start, end, "complete", rows, version, time.perf_counter() - began)
    reporter.unit_done(stage, stage, status="complete", rows=rows, elapsed_seconds=time.perf_counter() - began)
    return rows


def plan_summary(client: ClickHouseHttpClient, args: argparse.Namespace, stages: Sequence[str]) -> dict[str, Any]:
    counts = parse_one_json(client.execute(f"""
SELECT
 count() AS ticker_links,
 uniqExact(canonical_news_id) AS news,
 min(published_at_utc) AS min_published_at_utc,
 max(published_at_utc) AS max_published_at_utc
FROM {table(args.database, args.ticker_table)} FINAL
WHERE published_at_utc >= {dt_sql(args.start_date)} AND published_at_utc < {dt_sql(args.end_date)} FORMAT JSONEachRow
"""))
    return {
        "execute": False,
        "stages": list(stages),
        "months": len(list(calendar_month_chunks(date_arg(args.start_date), date_arg(args.end_date)))),
        "bounded_workers": args.workers,
        "max_threads_per_query": args.max_threads_per_query,
        "source": counts,
        "versions": {
            "pipeline": PIPELINE_VERSION, "relevance": RELEVANCE_VERSION, "dictionary": EVENT_DICTIONARY_VERSION,
            "language": LANGUAGE_VERSION, "scale": SCALE_VERSION, "model": MODEL_VERSION, "prediction": PREDICTION_VERSION,
        },
        "important_contracts": [
            "publication-time relevance and language are horizon-invariant",
            "ticker-related body evidence is clause-scoped to the requested ticker or issuer",
            "one strongest event per family prevents correlated phrase double counting",
            "reaction classes use training-only robust abnormal-return scales",
            "2019-2025 trains; 2026 is untouched holdout evaluation",
            "v1 source and result tables are never mutated",
        ],
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args(argv)
    stages = validate_args(args)
    args.clickhouse_url = args.clickhouse_url or default_clickhouse_url()
    args.user = args.user or default_clickhouse_user()
    args.password = args.password or default_clickhouse_password()
    run_id = dt.datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root) / f"run_{run_id}"
    run_root.mkdir(parents=True, exist_ok=True)
    months = list(calendar_month_chunks(date_arg(args.start_date), date_arg(args.end_date)))
    totals = [("preflight", 3)]
    if "extract" in stages:
        totals.append(("extract", len(months)))
    totals.extend((stage, 1) for stage in ("scale", "train", "predict", "evaluate") if stage in stages)
    totals.append(("audit", 1))
    reporter = NewsReactionProgress(
        stage_totals=totals, run_id=run_id, run_root=str(run_root), layout=args.progress_layout,
        refresh_per_second=args.progress_refresh_per_second, log_lines=args.progress_log_lines,
    )
    started = time.perf_counter()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    results: list[MonthResult] = []
    evaluation: dict[str, Any] = {}
    try:
        with reporter:
            reporter.stage_start("preflight")
            ensure_sources(client, args)
            reporter.unit_done("preflight", "source schemas", status="complete")
            if not args.execute:
                reporter.unit_done("preflight", "v2 schemas", status="planned")
                reporter.unit_done("preflight", "execution", status="planned")
                plan = plan_summary(client, args, stages)
                write_json(run_root / "plan.json", plan)
                reporter.finish("plan_validated")
                print(json.dumps(plan, indent=2, sort_keys=True, default=str))
                return 0
            ensure_targets(client, args)
            reporter.unit_done("preflight", "v2 schemas", status="complete")
            replace_dictionary(client, args)
            alias_rows = replace_identity_aliases(client, args, reporter)
            reporter.unit_done("preflight", "dictionary and identities", status="complete", rows=alias_rows)

            if "extract" in stages:
                reporter.stage_start("extract")
                with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="news-v2") as pool:
                    futures: dict[Future[MonthResult], tuple[dt.date, dt.date]] = {
                        pool.submit(process_month, args, month_start, month_end): (month_start, month_end)
                        for month_start, month_end in months
                    }
                    for future in as_completed(futures):
                        month_start, month_end = futures[future]
                        unit = f"{month_start}:{month_end}"
                        try:
                            result = future.result()
                        except BaseException as exc:
                            reporter.unit_failed("extract", unit, exc)
                            for pending in futures:
                                pending.cancel()
                            raise
                        results.append(result)
                        skipped = result.relevance_rows < 0
                        reporter.unit_done("extract", unit, status="skipped" if skipped else "complete", rows=max(0, result.language_rows), elapsed_seconds=result.elapsed_seconds)

            for stage in ("scale", "train", "predict"):
                if stage in stages:
                    run_global_stage(client, args, reporter, stage)

            if "evaluate" in stages:
                reporter.stage_start("evaluate")
                reporter.chunk_start("evaluate", "holdout")
                if args.review_labels_csv:
                    review_rows = import_review_labels(client, args, Path(args.review_labels_csv))
                    reporter.message(f"imported locked review labels rows={review_rows:,}")
                evaluation = parse_one_json(monitored_execute(client, evaluation_sql(args), reporter, "evaluate holdout"))
                reaction_confusion = json_rows(monitored_execute(client, reaction_confusion_sql(args), reporter, "reaction confusion"))
                language_confusion = json_rows(monitored_execute(client, language_confusion_sql(args), reporter, "review confusion"))
                evaluation["reaction_classification"] = confusion_metrics(reaction_confusion, ("negative", "neutral", "positive"))
                evaluation["relevance_classification"] = confusion_metrics(
                    (row for row in language_confusion if row["task"] == "relevance"),
                    ("company_specific", "ticker_related", "not_relevant"),
                )
                evaluation["language_classification"] = confusion_metrics(
                    (row for row in language_confusion if row["task"] == "language"),
                    ("negative", "neutral", "positive", "mixed"),
                )
                calibration = json_rows(monitored_execute(client, calibration_sql(args), reporter, "build calibration report"))
                write_json(run_root / "evaluation.json", evaluation)
                write_json(run_root / "calibration.json", calibration)
                reporter.unit_done("evaluate", "holdout", status="complete", rows=int(evaluation.get("reaction_rows") or 0))

            reporter.stage_start("audit")
            manifest = {
                "run_id": run_id, "pipeline_version": PIPELINE_VERSION, "execute": True, "stages": list(stages),
                "publication_range": {"start": args.start_date, "end_exclusive": args.end_date},
                "training_range": {"start": args.train_start_date, "end_exclusive": args.train_end_date},
                "holdout_range": {"start": args.holdout_start_date, "end_exclusive": args.holdout_end_date},
                "workers": args.workers, "max_threads_per_query": args.max_threads_per_query,
                "event_rule_count": len(EVENT_RULES), "month_results": [asdict(row) for row in sorted(results, key=lambda row: row.start_date)],
                "evaluation": evaluation, "elapsed_seconds": time.perf_counter() - started,
                "secret_status": secret_status(["CLICKHOUSE_PASSWORD", "TD__DATABASE__CLICKHOUSE__PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD"]),
            }
            write_json(run_root / "manifest.json", manifest)
            reporter.unit_done("audit", "manifest", status="complete")
            reporter.finish()
        return 0
    except KeyboardInterrupt:
        reporter.interrupted()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
