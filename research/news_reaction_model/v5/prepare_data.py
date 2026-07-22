from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v5.config import FeatureConfig, LoaderConfig
from research.news_reaction_model.v5.data import audit_prepared_dataset, month_ranges, q, qi
from research.news_reaction_model.v5.text_features import SparseTfidfBundle, load_bundle, save_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader, features = LoaderConfig(), FeatureConfig()
    parser = argparse.ArgumentParser(
        description="Fit bounded train-only sparse TF-IDF vocabularies and materialize V5 with exact V4 labels."
    )
    parser.add_argument("--start", default=loader.train_start)
    parser.add_argument("--end-exclusive", default=loader.validation_end_exclusive)
    parser.add_argument("--dataset-database", default=loader.dataset_database)
    parser.add_argument("--dataset-table", default=loader.dataset_table)
    parser.add_argument("--dataset-version", default=loader.dataset_version)
    parser.add_argument("--source-dataset-table", default=loader.source_dataset_table)
    parser.add_argument("--source-dataset-version", default=loader.source_dataset_version)
    parser.add_argument("--feature-artifact-root", default=str(loader.feature_artifact_root))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--query-batch-articles", type=int, default=features.fit_query_batch_articles)
    parser.add_argument("--insert-batch-articles", type=int, default=128)
    parser.add_argument("--word-vocab-size", type=int, default=features.word_vocab_size)
    parser.add_argument("--char-vocab-size", type=int, default=features.char_vocab_size)
    parser.add_argument("--hash-buckets", type=int, default=features.hash_buckets)
    parser.add_argument("--max-text-chars", type=int, default=features.max_text_chars)
    parser.add_argument("--char-text-chars", type=int, default=features.char_text_chars)
    parser.add_argument("--max-threads-per-query", type=int, default=loader.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=loader.max_memory_usage)
    parser.add_argument("--rebuild", action="store_true", help="Replace completed V5 feature months.")
    parser.add_argument("--refit-features", action="store_true", help="Refit train-only sparse vocabularies and IDF; requires --rebuild.")
    parser.add_argument("--execute", action="store_true", help="Without this flag, print the non-mutating plan.")
    parser.add_argument("--status-path", default="")
    return parser.parse_args(list(argv) if argv is not None else None)


def loader_from_args(args: argparse.Namespace) -> LoaderConfig:
    return LoaderConfig(
        dataset_database=args.dataset_database,
        dataset_table=args.dataset_table,
        dataset_version=args.dataset_version,
        source_dataset_table=args.source_dataset_table,
        source_dataset_version=args.source_dataset_version,
        feature_artifact_root=Path(args.feature_artifact_root),
        word_vocab_size=max(2, args.word_vocab_size),
        char_vocab_size=max(2, args.char_vocab_size),
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
    )


def feature_config_from_args(args: argparse.Namespace) -> FeatureConfig:
    return FeatureConfig(
        word_vocab_size=max(2, args.word_vocab_size),
        char_vocab_size=max(2, args.char_vocab_size),
        hash_buckets=max(2, args.hash_buckets),
        max_text_chars=max(1, args.max_text_chars),
        char_text_chars=max(1, args.char_text_chars),
        fit_query_batch_articles=max(1, args.query_batch_articles),
    )


def create_table_sql(config: LoaderConfig) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
CREATE TABLE IF NOT EXISTS {table}
(
 dataset_version LowCardinality(String),
 split LowCardinality(String),
 canonical_news_id String,
 ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'),
 publication_session LowCardinality(String),
 representation_name LowCardinality(String),
 representation_sha256 FixedString(64),
 word_ids Array(UInt32) CODEC(ZSTD(3)),
 word_weights Array(Float32) CODEC(ZSTD(3)),
 char_ids Array(UInt32) CODEC(ZSTD(3)),
 char_weights Array(Float32) CODEC(ZSTD(3)),
 horizon_codes Array(String) CODEC(ZSTD(3)),
 return_targets Array(Array(Float32)) CODEC(ZSTD(3)),
 built_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (dataset_version, split, published_at_utc, ticker, canonical_news_id)
SETTINGS index_granularity = 8192
"""


def create_manifest_sql(config: LoaderConfig) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
CREATE TABLE IF NOT EXISTS {table}
(
 dataset_version LowCardinality(String),
 representation_sha256 FixedString(64),
 range_start Date,
 range_end_exclusive Date,
 split LowCardinality(String),
 status LowCardinality(String),
 rows UInt64,
 built_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(built_at)
ORDER BY (dataset_version, range_start, range_end_exclusive)
SETTINGS index_granularity = 8192
"""


def split_for_date(value: dt.date) -> str:
    return "train" if value < dt.date(2026, 1, 1) else "validation"


def source_rows_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    cursor_timestamp: str = "1970-01-01",
    cursor_ticker: str = "",
    cursor_id: str = "",
    limit: int = 4096,
) -> str:
    source = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    news = f"{qi(config.news_database)}.{qi(config.normalized_news_table)}"
    return f"""
SELECT
 p.canonical_news_id, p.ticker, p.published_at_utc, p.publication_session,
 p.horizon_codes, p.return_targets,
 n.provider, n.title, n.teaser,
 substring(n.body_text, 1, 12000) AS body_text,
 substring(n.external_text, 1, 12000) AS external_text,
 substring(n.pdf_text, 1, 12000) AS pdf_text,
 arrayStringConcat(n.channels, ',') AS channels,
 arrayStringConcat(n.provider_tags, ',') AS provider_tags
FROM {source} AS p FINAL
ANY INNER JOIN {news} AS n FINAL ON n.canonical_news_id = p.canonical_news_id
WHERE p.dataset_version = {q(config.source_dataset_version)}
 AND p.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND p.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 AND (p.published_at_utc, p.ticker, p.canonical_news_id) >
     (toDateTime64({q(cursor_timestamp)}, 9, 'UTC'), {q(cursor_ticker)}, {q(cursor_id)})
ORDER BY p.published_at_utc, p.ticker, p.canonical_news_id
LIMIT {int(limit)}
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def iter_source_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    batch_articles: int,
) -> Iterator[list[dict[str, Any]]]:
    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
    while True:
        text = client.execute(source_rows_sql(
            config, start, end, cursor_timestamp, cursor_ticker, cursor_id, batch_articles,
        ))
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not rows:
            break
        yield rows
        cursor_timestamp = str(rows[-1]["published_at_utc"])
        cursor_ticker = str(rows[-1]["ticker"])
        cursor_id = str(rows[-1]["canonical_news_id"])
        if len(rows) < batch_articles:
            break


def iter_training_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    batch_articles: int,
) -> Iterator[list[dict[str, Any]]]:
    for month_start, month_end in month_ranges(config.train_start, config.train_end_exclusive):
        for batch in iter_source_rows(client, config, month_start, month_end, batch_articles):
            yield batch


def insert_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    source_rows: list[dict[str, Any]],
    features: Any,
    representation_sha256: str,
    insert_batch_articles: int,
) -> None:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    columns = (
        "dataset_version, split, canonical_news_id, ticker, published_at_utc, publication_session, "
        "representation_name, representation_sha256, word_ids, word_weights, char_ids, char_weights, "
        "horizon_codes, return_targets"
    )
    for offset in range(0, len(source_rows), insert_batch_articles):
        rows = source_rows[offset : offset + insert_batch_articles]
        payload = []
        for local_index, row in enumerate(rows, start=offset):
            payload.append(json.dumps({
                "dataset_version": config.dataset_version,
                "split": "train" if str(row["published_at_utc"]) < config.train_end_exclusive else "validation",
                "canonical_news_id": row["canonical_news_id"],
                "ticker": row["ticker"],
                "published_at_utc": row["published_at_utc"],
                "publication_session": row["publication_session"],
                "representation_name": config.representation_name,
                "representation_sha256": representation_sha256,
                "word_ids": features.word_ids[local_index],
                "word_weights": features.word_weights[local_index],
                "char_ids": features.char_ids[local_index],
                "char_weights": features.char_weights[local_index],
                "horizon_codes": row["horizon_codes"],
                "return_targets": row["return_targets"],
            }, separators=(",", ":"), allow_nan=False))
        client.execute(f"INSERT INTO {table} ({columns}) FORMAT JSONEachRow\n" + "\n".join(payload))


def month_count_sql(config: LoaderConfig, start: dt.date, end: dt.date, representation_sha256: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id)
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
FORMAT TSV
"""


def source_range_count_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id)
FROM {table} FINAL
WHERE dataset_version = {q(config.source_dataset_version)}
 AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
FORMAT TSV
"""


def completed_range_sql(config: LoaderConfig, start: dt.date, end: dt.date, representation_sha256: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
SELECT status, rows
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND range_start = toDate({q(start.isoformat())})
 AND range_end_exclusive = toDate({q(end.isoformat())})
LIMIT 1
FORMAT TSV
"""


def delete_month_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
ALTER TABLE {table} DELETE
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
SETTINGS mutations_sync = 2
"""


def record_completed_sql(config: LoaderConfig, start: dt.date, end: dt.date, representation_sha256: str, rows: int) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
INSERT INTO {table}
(dataset_version, representation_sha256, range_start, range_end_exclusive, split, status, rows)
VALUES ({q(config.dataset_version)}, {q(representation_sha256)}, toDate({q(start.isoformat())}),
 toDate({q(end.isoformat())}), {q(split_for_date(start))}, 'completed', {int(rows)})
"""


def parse_count(text: str) -> tuple[int, int]:
    fields = text.strip().split("\t")
    return (int(fields[0]), int(fields[1])) if len(fields) >= 2 and fields[0] else (0, 0)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    config = loader_from_args(args)
    feature_config = feature_config_from_args(args)
    if feature_config.hash_buckets < max(feature_config.word_vocab_size, feature_config.char_vocab_size):
        raise ValueError("--hash-buckets must be at least as large as both sparse vocabularies.")
    if args.refit_features and not args.rebuild:
        raise ValueError("--refit-features requires --rebuild so one dataset version cannot mix representations.")
    if args.refit_features and (
        args.start != config.train_start or args.end_exclusive != config.validation_end_exclusive
    ):
        raise ValueError(
            "--refit-features must rebuild the complete 2019-2026 V5 range; partial refits would mix "
            "representation checksums in one dataset version."
        )
    months = month_ranges(args.start, args.end_exclusive)
    print(
        f"{'BUILD' if args.execute else 'PLAN'} V5 TF-IDF | months={len(months)} | "
        f"source={config.dataset_database}.{config.source_dataset_table} | target={config.dataset_table} | "
        f"features={config.feature_artifact_root}",
        flush=True,
    )
    if not args.execute:
        print("Read-only plan complete. Add --execute to fit missing train-only artifacts and materialize V5.", flush=True)
        return 0
    status_path = Path(args.status_path) if args.status_path else Path("runtime/news-reaction-model/v5/prepare/status.jsonl")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    artifacts_exist = (config.feature_artifact_root / "manifest.json").exists()
    append_jsonl(status_path, {
        "event": "start", "stage": "feature_fit" if args.refit_features or not artifacts_exist else "materialize",
        "loader": asdict(config), "features": asdict(feature_config), "months": len(months),
    })
    if args.refit_features or not artifacts_exist:
        print("FIT sparse word/character TF-IDF from bounded 2019-2025 batches", flush=True)
        bundle = SparseTfidfBundle.fit(
            iter_training_rows(client, config, args.query_batch_articles), feature_config
        )
        manifest = save_bundle(bundle, config)
        append_jsonl(status_path, {
            "event": "feature_fit_completed", "articles": bundle.training_documents,
            "representation_sha256": manifest["bundle_sha256"],
        })
    else:
        bundle, manifest = load_bundle(config)
    representation_sha256 = str(manifest["bundle_sha256"])
    client.execute(create_table_sql(config))
    client.execute(create_manifest_sql(config))
    append_jsonl(status_path, {
        "event": "materialization_started", "representation_sha256": representation_sha256,
        "months": len(months),
    })

    def build_month(item: tuple[dt.date, dt.date]) -> dict[str, Any]:
        start, end = item
        local = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
        completed = local.execute(completed_range_sql(config, start, end, representation_sha256)).strip().split("\t")
        if completed and completed[0] == "completed" and not args.rebuild:
            return {"month": start.strftime("%Y-%m"), "status": "skipped", "rows": int(completed[1])}
        if args.rebuild:
            local.execute(delete_month_sql(config, start, end))
        rows_written = 0
        started = time.perf_counter()
        for source_rows in iter_source_rows(local, config, start, end, args.query_batch_articles):
            feature_rows = bundle.transform(source_rows)
            insert_rows(local, config, source_rows, feature_rows, representation_sha256, args.insert_batch_articles)
            rows_written += len(source_rows)
        count, unique = parse_count(local.execute(month_count_sql(config, start, end, representation_sha256)))
        if count != rows_written or unique != rows_written:
            raise RuntimeError(
                f"V5 month verification failed for {start:%Y-%m}: wrote={rows_written} count={count} unique={unique}."
            )
        local.execute(record_completed_sql(config, start, end, representation_sha256, rows_written))
        return {
            "month": start.strftime("%Y-%m"), "status": "completed", "rows": rows_written,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    workers = max(1, min(args.workers, len(months)))
    completed_rows = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(build_month, month): month for month in months}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            completed_rows += int(result["rows"])
            append_jsonl(status_path, {"event": "month", **result})
            print(
                f"[{index}/{len(months)}] {result['month']} {str(result['status']).upper()} "
                f"rows={int(result['rows']):,} total={completed_rows:,}",
                flush=True,
            )
    audit = audit_prepared_dataset(config, args.start, args.end_exclusive)
    if audit["representation_sha256"] != representation_sha256:
        raise RuntimeError(
            "Prepared V5 rows do not match the active frozen feature bundle: "
            f"table={audit['representation_sha256']} bundle={representation_sha256}."
        )
    source_rows, source_articles = parse_count(client.execute(source_range_count_sql(config, args.start, args.end_exclusive)))
    if source_rows != audit["rows"] or source_articles != audit["articles"]:
        raise RuntimeError(
            "V5 population does not exactly match the authoritative V4 population: "
            f"source_rows={source_rows:,} source_articles={source_articles:,} "
            f"v5_rows={audit['rows']:,} v5_articles={audit['articles']:,}."
        )
    audit["source_rows"] = source_rows
    audit["source_articles"] = source_articles
    append_jsonl(status_path, {"event": "audit", **audit})
    print(
        f"COMPLETED V5 TF-IDF rows={completed_rows:,} representation_sha256={representation_sha256} "
        f"audited_articles={audit['articles']:,} status={status_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
