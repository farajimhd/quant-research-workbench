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
from research.news_reaction_model.v5.config import LoaderConfig as V5LoaderConfig
from research.news_reaction_model.v5.data import audit_prepared_dataset as audit_v5_dataset
from research.news_reaction_model.v6.config import LoaderConfig, NumericFeatureConfig
from research.news_reaction_model.v6.data import audit_prepared_dataset, month_ranges, q, qi
from research.news_reaction_model.v6.numeric_features import (
    build_representation_manifest,
    extract_numeric_batch,
    load_representation_manifest,
    save_representation_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader, numeric = LoaderConfig(), NumericFeatureConfig()
    parser = argparse.ArgumentParser(
        description="Reuse exact V5 word/character TF-IDF rows and add publication-time structured numeric features."
    )
    parser.add_argument("--start", default=loader.train_start)
    parser.add_argument("--end-exclusive", default=loader.validation_end_exclusive)
    parser.add_argument("--dataset-database", default=loader.dataset_database)
    parser.add_argument("--dataset-table", default=loader.dataset_table)
    parser.add_argument("--dataset-version", default=loader.dataset_version)
    parser.add_argument("--source-dataset-table", default=loader.source_dataset_table)
    parser.add_argument("--source-dataset-version", default=loader.source_dataset_version)
    parser.add_argument("--representation-artifact-root", default=str(loader.representation_artifact_root))
    parser.add_argument("--v5-feature-artifact-root", default=str(loader.v5_feature_artifact_root))
    parser.add_argument("--workers", type=int, default=loader.workers)
    parser.add_argument("--query-batch-articles", type=int, default=loader.query_batch_articles)
    parser.add_argument("--insert-batch-articles", type=int, default=128)
    parser.add_argument("--numeric-vocab-size", type=int, default=numeric.vocabulary_size)
    parser.add_argument("--numeric-dense-dim", type=int, default=numeric.dense_dim)
    parser.add_argument("--numeric-max-text-chars", type=int, default=numeric.max_text_chars)
    parser.add_argument("--numeric-context-words", type=int, default=numeric.context_words)
    parser.add_argument("--numeric-max-mentions", type=int, default=numeric.max_mentions)
    parser.add_argument("--max-threads-per-query", type=int, default=loader.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=loader.max_memory_usage)
    parser.add_argument("--rebuild", action="store_true", help="Replace completed V6 months.")
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
        representation_artifact_root=Path(args.representation_artifact_root),
        v5_feature_artifact_root=Path(args.v5_feature_artifact_root),
        numeric_vocab_size=max(2, args.numeric_vocab_size),
        numeric_dense_dim=max(1, args.numeric_dense_dim),
        numeric_max_text_chars=max(1, args.numeric_max_text_chars),
        numeric_context_words=max(1, args.numeric_context_words),
        numeric_max_mentions=max(1, args.numeric_max_mentions),
        workers=max(1, args.workers),
        query_batch_articles=max(1, args.query_batch_articles),
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
    )


def numeric_config_from_args(args: argparse.Namespace) -> NumericFeatureConfig:
    return NumericFeatureConfig(
        vocabulary_size=max(2, args.numeric_vocab_size),
        dense_dim=max(1, args.numeric_dense_dim),
        max_text_chars=max(1, args.numeric_max_text_chars),
        context_words=max(1, args.numeric_context_words),
        max_mentions=max(1, args.numeric_max_mentions),
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
 numeric_ids Array(UInt32) CODEC(ZSTD(3)),
 numeric_weights Array(Float32) CODEC(ZSTD(3)),
 numeric_dense Array(Float32) CODEC(ZSTD(3)),
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
 p.word_ids, p.word_weights, p.char_ids, p.char_weights,
 p.representation_sha256 AS source_representation_sha256,
 p.horizon_codes, p.return_targets,
 n.title, n.teaser,
 substring(n.body_text, 1, {int(config.numeric_max_text_chars)}) AS body_text,
 substring(n.external_text, 1, {int(config.numeric_max_text_chars)}) AS external_text,
 substring(n.pdf_text, 1, {int(config.numeric_max_text_chars)}) AS pdf_text
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


def insert_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    source_rows: list[dict[str, Any]],
    numeric_rows: Any,
    representation_sha256: str,
    source_representation_sha256: str,
    insert_batch_articles: int,
) -> None:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    columns = (
        "dataset_version, split, canonical_news_id, ticker, published_at_utc, publication_session, "
        "representation_name, representation_sha256, word_ids, word_weights, char_ids, char_weights, "
        "numeric_ids, numeric_weights, numeric_dense, horizon_codes, return_targets"
    )
    for offset in range(0, len(source_rows), insert_batch_articles):
        rows = source_rows[offset : offset + insert_batch_articles]
        payload = []
        for local_index, row in enumerate(rows, start=offset):
            if str(row["source_representation_sha256"]) != source_representation_sha256:
                raise RuntimeError(
                    "V6 source row was not built by the frozen V5 lexical representation: "
                    f"{row['source_representation_sha256']} != {source_representation_sha256}."
                )
            payload.append(json.dumps({
                "dataset_version": config.dataset_version,
                "split": "train" if str(row["published_at_utc"]) < config.train_end_exclusive else "validation",
                "canonical_news_id": row["canonical_news_id"],
                "ticker": row["ticker"],
                "published_at_utc": row["published_at_utc"],
                "publication_session": row["publication_session"],
                "representation_name": config.representation_name,
                "representation_sha256": representation_sha256,
                "word_ids": row["word_ids"], "word_weights": row["word_weights"],
                "char_ids": row["char_ids"], "char_weights": row["char_weights"],
                "numeric_ids": numeric_rows.numeric_ids[local_index],
                "numeric_weights": numeric_rows.numeric_weights[local_index],
                "numeric_dense": numeric_rows.numeric_dense[local_index],
                "horizon_codes": row["horizon_codes"], "return_targets": row["return_targets"],
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
SELECT status, rows FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND range_start = toDate({q(start.isoformat())})
 AND range_end_exclusive = toDate({q(end.isoformat())})
LIMIT 1 FORMAT TSV
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
    split = "train" if start < dt.date.fromisoformat(config.train_end_exclusive) else "validation"
    return f"""
INSERT INTO {table}
(dataset_version, representation_sha256, range_start, range_end_exclusive, split, status, rows)
VALUES ({q(config.dataset_version)}, {q(representation_sha256)}, toDate({q(start.isoformat())}),
 toDate({q(end.isoformat())}), {q(split)}, 'completed', {int(rows)})
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
    numeric = numeric_config_from_args(args)
    months = month_ranges(args.start, args.end_exclusive)
    print(
        f"{'BUILD' if args.execute else 'PLAN'} V6 NUMERIC TF-IDF | months={len(months)} | "
        f"source={config.dataset_database}.{config.source_dataset_table} | target={config.dataset_table} | "
        f"numeric_vocab={numeric.vocabulary_size:,} dense={numeric.dense_dim}",
        flush=True,
    )
    if not args.execute:
        print("Read-only plan complete. Add --execute to reuse V5 lexical rows and materialize V6.", flush=True)
        return 0

    expected_manifest = build_representation_manifest(config, numeric)
    manifest_path = config.representation_artifact_root / "manifest.json"
    if manifest_path.exists():
        manifest = load_representation_manifest(config)
    else:
        save_representation_manifest(config, expected_manifest)
        manifest = expected_manifest
    representation_sha256 = str(manifest["representation_sha256"])
    source_representation_sha256 = str(manifest["v5_bundle_sha256"])

    v5_loader = V5LoaderConfig(
        feature_artifact_root=config.v5_feature_artifact_root,
        dataset_database=config.dataset_database,
        dataset_table=config.source_dataset_table,
        dataset_version=config.source_dataset_version,
    )
    source_audit = audit_v5_dataset(v5_loader, args.start, args.end_exclusive)
    if source_audit["representation_sha256"] != source_representation_sha256:
        raise RuntimeError(
            "V6 source table does not match the frozen V5 lexical bundle: "
            f"table={source_audit['representation_sha256']} bundle={source_representation_sha256}."
        )

    status_path = Path(args.status_path) if args.status_path else Path("runtime/news-reaction-model/v6/prepare/status.jsonl")
    append_jsonl(status_path, {
        "event": "start", "stage": "numeric_materialization", "loader": asdict(config),
        "numeric": asdict(numeric), "months": len(months), "representation_sha256": representation_sha256,
    })
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    client.execute(create_table_sql(config))
    client.execute(create_manifest_sql(config))

    def build_month(item: tuple[dt.date, dt.date]) -> dict[str, Any]:
        start, end = item
        local = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
        completed = local.execute(completed_range_sql(config, start, end, representation_sha256)).strip().split("\t")
        if completed and completed[0] == "completed" and not args.rebuild:
            return {"month": start.strftime("%Y-%m"), "status": "skipped", "rows": int(completed[1])}
        if args.rebuild:
            local.execute(delete_month_sql(config, start, end))
        rows_written = 0
        numeric_articles = 0
        started = time.perf_counter()
        for source_rows in iter_source_rows(local, config, start, end, args.query_batch_articles):
            features = extract_numeric_batch(source_rows, numeric)
            numeric_articles += sum(bool(ids) for ids in features.numeric_ids)
            insert_rows(
                local, config, source_rows, features, representation_sha256,
                source_representation_sha256, args.insert_batch_articles,
            )
            rows_written += len(source_rows)
        count, unique = parse_count(local.execute(month_count_sql(config, start, end, representation_sha256)))
        if count != rows_written or unique != rows_written:
            raise RuntimeError(
                f"V6 month verification failed for {start:%Y-%m}: wrote={rows_written} count={count} unique={unique}."
            )
        local.execute(record_completed_sql(config, start, end, representation_sha256, rows_written))
        return {
            "month": start.strftime("%Y-%m"), "status": "completed", "rows": rows_written,
            "numeric_articles": numeric_articles, "elapsed_seconds": round(time.perf_counter() - started, 3),
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
                f"rows={int(result['rows']):,} total={completed_rows:,}", flush=True,
            )

    audit = audit_prepared_dataset(config, args.start, args.end_exclusive)
    if audit["representation_sha256"] != representation_sha256:
        raise RuntimeError(
            "Prepared V6 rows do not match the active representation manifest: "
            f"table={audit['representation_sha256']} manifest={representation_sha256}."
        )
    source_rows, source_articles = parse_count(client.execute(source_range_count_sql(config, args.start, args.end_exclusive)))
    if source_rows != audit["rows"] or source_articles != audit["articles"]:
        raise RuntimeError(
            "V6 population does not exactly match V5: "
            f"source_rows={source_rows:,} source_articles={source_articles:,} "
            f"v6_rows={audit['rows']:,} v6_articles={audit['articles']:,}."
        )
    audit["source_rows"] = source_rows
    audit["source_articles"] = source_articles
    append_jsonl(status_path, {"event": "audit", **audit})
    print(
        f"COMPLETED V6 rows={completed_rows:,} numeric_coverage={audit['numeric_articles']:,}/{audit['rows']:,} "
        f"representation_sha256={representation_sha256} status={status_path}", flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
