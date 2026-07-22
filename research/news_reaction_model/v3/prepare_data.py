from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v3.config import LoaderConfig
from research.news_reaction_model.v3.data import month_ranges, q, qi, source_batch_sql

REPO_ROOT = Path(__file__).resolve().parents[3]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    defaults = LoaderConfig()
    parser = argparse.ArgumentParser(
        description="Materialize exact single-ticker embedding/reaction matches for news-reaction-model v3."
    )
    parser.add_argument("--start", default=defaults.train_start)
    parser.add_argument("--end-exclusive", default=defaults.validation_end_exclusive)
    parser.add_argument("--dataset-database", default=defaults.dataset_database)
    parser.add_argument("--dataset-table", default=defaults.dataset_table)
    parser.add_argument("--dataset-version", default=defaults.dataset_version)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-threads-per-query", type=int, default=defaults.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=defaults.max_memory_usage)
    parser.add_argument("--rebuild", action="store_true", help="Replace already populated months for this dataset version.")
    parser.add_argument("--execute", action="store_true", help="Create and populate the dataset. Without this flag, print a read-only plan.")
    parser.add_argument("--status-path", default="")
    return parser.parse_args(list(argv) if argv is not None else None)


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
 embedding_model LowCardinality(String),
 embedding_dim UInt16,
 chunks Array(Tuple(UInt8, Array(Float32))) CODEC(ZSTD(3)),
 horizon_codes Array(String) CODEC(ZSTD(3)),
 class_targets Array(Int8) CODEC(ZSTD(3)),
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


def split_for_month(start: dt.date) -> str:
    return "train" if start < dt.date(2026, 1, 1) else "validation"


def month_count_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id)
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
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


def completed_range_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
SELECT status, rows
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND range_start = toDate({q(start.isoformat())})
 AND range_end_exclusive = toDate({q(end.isoformat())})
ORDER BY built_at DESC
LIMIT 1
FORMAT TSV
"""


def record_completed_range_sql(config: LoaderConfig, start: dt.date, end: dt.date, rows: int) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
INSERT INTO {table}
(dataset_version, range_start, range_end_exclusive, split, status, rows, built_at)
VALUES ({q(config.dataset_version)}, toDate({q(start.isoformat())}), toDate({q(end.isoformat())}),
 {q(split_for_month(start))}, 'completed', {int(rows)}, now64(3))
"""


def insert_month_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    source = source_batch_sql(config, start, end, include_format=False, include_settings=False).strip()
    return f"""
INSERT INTO {table}
(dataset_version, split, canonical_news_id, ticker, published_at_utc, publication_session,
 embedding_model, embedding_dim, chunks, horizon_codes, class_targets, return_targets, built_at)
SELECT {q(config.dataset_version)}, {q(split_for_month(start))}, source_id, ticker, published_at_utc,
 publication_session, {q(config.embedding_model)}, toUInt16({config.embedding_dim}), chunks,
 horizon_codes, class_targets, return_targets, now64(3)
FROM
(
{source}
)
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
"""


def audit_sql(config: LoaderConfig, start: str, end: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT
 count() AS rows,
 uniqExact(canonical_news_id) AS articles,
 countIf(length(chunks) < 1 OR length(chunks) > {config.max_chunks}) AS invalid_chunks,
 countIf(length(horizon_codes) != length(class_targets) OR length(horizon_codes) != length(return_targets)) AS invalid_targets,
 countIf(split = 'train' AND published_at_utc >= toDateTime64('2026-01-01', 9, 'UTC')) AS train_leakage,
 countIf(split = 'validation' AND published_at_utc < toDateTime64('2026-01-01', 9, 'UTC')) AS validation_leakage,
 min(published_at_utc), max(published_at_utc)
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end)}, 9, 'UTC')
FORMAT JSONEachRow
"""


def parse_count(text: str) -> tuple[int, int]:
    fields = text.strip().split("\t")
    return (int(fields[0]), int(fields[1])) if len(fields) >= 2 and fields[0] else (0, 0)


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    config = LoaderConfig(
        dataset_database=args.dataset_database,
        dataset_table=args.dataset_table,
        dataset_version=args.dataset_version,
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
    )
    months = month_ranges(args.start, args.end_exclusive)
    plan = {
        "event": "plan",
        "execute": args.execute,
        "rebuild": args.rebuild,
        "range": [args.start, args.end_exclusive],
        "months": len(months),
        "workers": max(1, min(args.workers, len(months))),
        "target": f"{config.dataset_database}.{config.dataset_table}",
        "dataset_version": config.dataset_version,
        "identity": ["source_id=canonical_news_id", "ticker", "published_at_utc"],
        "single_ticker_only": True,
    }
    action = "BUILD" if args.execute else "PLAN"
    print(
        f"{action} news reaction dataset | range={args.start}..{args.end_exclusive} | "
        f"months={len(months)} | workers={plan['workers']} | version={config.dataset_version}",
        flush=True,
    )
    if not args.execute:
        print("Read-only plan complete. Add --execute to materialize missing months.", flush=True)
        return 0

    status_path = Path(args.status_path) if args.status_path else Path("runtime/news-reaction-model/v3/prepare") / f"{config.dataset_version}.jsonl"
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    client.execute(create_table_sql(config))
    client.execute(create_manifest_sql(config))
    append_jsonl(status_path, {**plan, "config": asdict(config), "started_at_utc": dt.datetime.now(dt.UTC).isoformat()})

    def build_month(item: tuple[dt.date, dt.date]) -> dict[str, object]:
        start, end = item
        local = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
        before, _ = parse_count(local.execute(month_count_sql(config, start, end)))
        manifest_fields = local.execute(completed_range_sql(config, start, end)).strip().split("\t")
        manifest_status = manifest_fields[0] if manifest_fields and manifest_fields[0] else ""
        manifest_rows = int(manifest_fields[1]) if len(manifest_fields) > 1 and manifest_fields[1] else 0
        if manifest_status == "completed" and manifest_rows == before and not args.rebuild:
            return {"event": "month", "month": start.strftime("%Y-%m"), "status": "skipped", "rows": before}
        started = time.perf_counter()
        if before:
            local.execute(delete_month_sql(config, start, end))
        local.execute(insert_month_sql(config, start, end))
        rows, articles = parse_count(local.execute(month_count_sql(config, start, end)))
        if rows != articles:
            raise RuntimeError(f"{start:%Y-%m}: expected one prepared row per article, rows={rows}, articles={articles}")
        local.execute(record_completed_range_sql(config, start, end, rows))
        return {"event": "month", "month": start.strftime("%Y-%m"), "status": "completed", "rows": rows,
                "articles": articles, "elapsed_seconds": time.perf_counter() - started}

    failures = 0
    completed = 0
    total_rows = 0
    build_started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(months)))) as pool:
        future_to_month = {pool.submit(build_month, month): month for month in months}
        for future in concurrent.futures.as_completed(future_to_month):
            month = future_to_month[future][0].strftime("%Y-%m")
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 - each month remains an atomic resumable unit
                failures += 1
                row = {"event": "month", "month": month, "status": "failed", "error": repr(exc)}
            completed += 1
            total_rows += int(row.get("rows", 0))
            append_jsonl(status_path, row)
            elapsed = time.perf_counter() - build_started
            rate = completed / max(elapsed, 1e-9)
            eta = (len(months) - completed) / max(rate, 1e-9)
            detail = f"rows={int(row.get('rows', 0)):,}"
            if row["status"] == "failed":
                detail = f"error={row['error']}"
            print(
                f"[{completed:>2}/{len(months):<2}] {month} {str(row['status']).upper():<9} "
                f"{detail} | total_rows={total_rows:,} | elapsed={elapsed / 60:.1f}m | eta={eta / 60:.1f}m",
                flush=True,
            )
    audit_text = client.execute(audit_sql(config, args.start, args.end_exclusive)).strip()
    audit = json.loads(audit_text) if audit_text else {"rows": 0}
    audit_row = {"event": "audit", **audit, "failed_months": failures}
    append_jsonl(status_path, audit_row)
    invalid = failures or int(audit.get("invalid_chunks", 0)) or int(audit.get("invalid_targets", 0)) or int(audit.get("train_leakage", 0)) or int(audit.get("validation_leakage", 0))
    status = "FAILED" if invalid else "COMPLETED"
    print(
        f"{status} | rows={int(audit.get('rows', 0)):,} | articles={int(audit.get('articles', 0)):,} | "
        f"failed_months={failures} | invalid_chunks={int(audit.get('invalid_chunks', 0))} | "
        f"invalid_targets={int(audit.get('invalid_targets', 0))} | status_log={status_path}",
        flush=True,
    )
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
