from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import signal
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v8.config import LoaderConfig
from research.news_reaction_model.v8.data import audit_prepared_dataset, month_ranges, q, qi
from research.news_reaction_model.v8.ranges import describe_ranges
from research.news_reaction_model.v8.stock_state import contract_payload, contract_sha256


REPO_ROOT = Path(__file__).resolve().parents[3]


def canonical_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), default=list))


def build_v8_manifest(config: LoaderConfig, source_representation_sha256: str) -> dict[str, Any]:
    range_contract = describe_ranges()
    manifest = canonical_json_value({
        "representation_name": config.representation_name,
        "source_v7_dataset_version": config.source_dataset_version,
        "source_v7_representation_sha256": source_representation_sha256,
        "openai_embedding_version": config.embedding_version,
        "openai_embedding_model": config.embedding_model,
        "openai_embedding_dimensions": config.openai_embedding_dim,
        "openai_text_contract": config.embedding_text_contract,
        "stock_state_contract": contract_payload(),
        "stock_state_contract_sha256": contract_sha256(),
        "range_contract": range_contract,
        "range_contract_sha256": hashlib.sha256(json.dumps(
            range_contract, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest(),
        "targets_heads_losses_and_training_contract": "unchanged_from_v7",
    })
    manifest["representation_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return manifest


def load_or_create_v8_manifest(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    expected = canonical_json_value(expected)
    if path.exists():
        persisted = canonical_json_value(json.loads(path.read_text(encoding="utf-8")))
        if persisted != expected:
            changed = sorted(
                key for key in set(persisted) | set(expected)
                if persisted.get(key) != expected.get(key)
            )
            raise RuntimeError(
                f"V8 representation manifest differs from the active ablation contract at {path}; "
                f"changed top-level fields={changed}. Use a new dataset version for a real contract change."
            )
        return persisted
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return expected


class QueryCancellationController:
    def __init__(self) -> None:
        self.run_id = "news_v8_prepare_" + uuid.uuid4().hex
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def query_id(self) -> str:
        return f"{self.run_id}_{uuid.uuid4().hex}"

    def register(self, query_id: str) -> None:
        with self._lock:
            self._active.add(query_id)

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._active.discard(query_id)

    def raise_if_cancelled(self) -> None:
        if self.stop.is_set():
            raise InterruptedError("V8 preparation was cancelled")

    def cancel(self) -> tuple[int, str]:
        self.stop.set()
        with self._lock:
            query_ids = sorted(self._active)
        if not query_ids:
            return 0, ""
        client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
        )
        try:
            client.execute(f"KILL QUERY WHERE query_id IN ({','.join(q(value) for value in query_ids)}) ASYNC")
            return len(query_ids), ""
        except Exception as exc:
            return len(query_ids), f"{type(exc).__name__}: {exc}"


class TrackedClickHouseClient:
    def __init__(self, controller: QueryCancellationController) -> None:
        self.controller = controller
        self.client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
        )

    def execute(self, sql: str) -> str:
        self.controller.raise_if_cancelled()
        query_id = self.controller.query_id()
        self.controller.register(query_id)
        try:
            return self.client.execute(sql, query_id=query_id)
        finally:
            self.controller.unregister(query_id)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader = LoaderConfig()
    parser = argparse.ArgumentParser(
        description="Build V8 by replacing only V7 TF-IDF text channels with durable OpenAI embeddings."
    )
    parser.add_argument("--start", default=loader.train_start)
    parser.add_argument("--end-exclusive", default=loader.validation_end_exclusive)
    parser.add_argument("--dataset-database", default=loader.dataset_database)
    parser.add_argument("--dataset-table", default=loader.dataset_table)
    parser.add_argument("--dataset-version", default=loader.dataset_version)
    parser.add_argument("--source-dataset-table", default=loader.source_dataset_table)
    parser.add_argument("--source-dataset-version", default=loader.source_dataset_version)
    parser.add_argument("--embedding-table", default=loader.embedding_table)
    parser.add_argument("--embedding-version", default=loader.embedding_version)
    parser.add_argument("--representation-artifact-root", default=str(loader.representation_artifact_root))
    parser.add_argument("--workers", type=int, default=loader.workers)
    parser.add_argument("--max-threads-per-query", type=int, default=loader.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=loader.max_memory_usage)
    parser.add_argument("--rebuild", action="store_true", help="Replace completed V8 months.")
    parser.add_argument("--execute", action="store_true", help="Without this flag, run a non-mutating coverage plan.")
    parser.add_argument("--status-path", default="")
    return parser.parse_args(list(argv) if argv is not None else None)


def loader_from_args(args: argparse.Namespace) -> LoaderConfig:
    return LoaderConfig(
        dataset_database=args.dataset_database,
        dataset_table=args.dataset_table,
        dataset_version=args.dataset_version,
        source_dataset_table=args.source_dataset_table,
        source_dataset_version=args.source_dataset_version,
        embedding_table=args.embedding_table,
        embedding_version=args.embedding_version,
        representation_artifact_root=Path(args.representation_artifact_root),
        workers=max(1, args.workers),
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
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
 embedding_text_sha256 FixedString(64),
 openai_embedding Array(Float32) CODEC(ZSTD(3)),
 stock_state Array(Float32) CODEC(ZSTD(3)),
 horizon_codes Array(String) CODEC(ZSTD(3)),
 return_targets Array(Array(Float32)) CODEC(ZSTD(3)),
 built_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (dataset_version, split, published_at_utc, ticker, canonical_news_id)
SETTINGS index_granularity = 8192
"""


def create_manifest_table_sql(config: LoaderConfig) -> str:
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


def population_audit_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    source = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    embeddings = f"{qi(config.dataset_database)}.{qi(config.embedding_table)}"
    return f"""
WITH
 source AS
 (
  SELECT canonical_news_id, ticker, published_at_utc, representation_name, representation_sha256
  FROM {source} FINAL
  WHERE dataset_version = {q(config.source_dataset_version)}
   AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
   AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
 ),
 embedding AS
 (
  SELECT canonical_news_id, ticker, published_at_utc, model, dimensions, text_contract, embedding
  FROM {embeddings} FINAL
  WHERE embedding_version = {q(config.embedding_version)}
 )
SELECT
 count() AS source_rows,
 uniqExact(tuple(s.canonical_news_id, s.ticker, s.published_at_utc)) AS source_unique,
 uniqExact(s.representation_sha256) AS source_representation_versions,
 any(s.representation_name) AS source_representation_name,
 any(s.representation_sha256) AS source_representation_sha256,
 countIf(length(e.embedding) > 0) AS matched_embeddings,
 countIf(length(e.embedding) > 0 AND
  (e.dimensions != {config.openai_embedding_dim}
   OR e.model != {q(config.embedding_model)}
   OR e.text_contract != {q(config.embedding_text_contract)}
   OR length(e.embedding) != {config.openai_embedding_dim}
   OR arrayExists(x -> NOT isFinite(x), e.embedding))) AS invalid_embeddings
FROM source AS s
LEFT JOIN embedding AS e USING (canonical_news_id, ticker, published_at_utc)
FORMAT JSONEachRow
"""


def population_audit(client: Any, config: LoaderConfig, start: str, end_exclusive: str) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in client.execute(population_audit_sql(config, start, end_exclusive)).splitlines()
        if line.strip()
    ]
    row = rows[0] if rows else {}
    result = {
        "source_rows": int(row.get("source_rows") or 0),
        "source_unique": int(row.get("source_unique") or 0),
        "source_representation_versions": int(row.get("source_representation_versions") or 0),
        "source_representation_name": str(row.get("source_representation_name") or ""),
        "source_representation_sha256": str(row.get("source_representation_sha256") or ""),
        "matched_embeddings": int(row.get("matched_embeddings") or 0),
        "invalid_embeddings": int(row.get("invalid_embeddings") or 0),
    }
    result["missing_embeddings"] = result["source_rows"] - result["matched_embeddings"]
    return result


def split_for(start: dt.date) -> str:
    return "train" if start < dt.date(2026, 1, 1) else "validation"


def insert_month_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    representation_sha256: str,
) -> str:
    target = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    source = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    embeddings = f"{qi(config.dataset_database)}.{qi(config.embedding_table)}"
    return f"""
INSERT INTO {target}
(
 dataset_version, split, canonical_news_id, ticker, published_at_utc, publication_session,
 representation_name, representation_sha256, embedding_text_sha256, openai_embedding,
 stock_state, horizon_codes, return_targets
)
SELECT
 {q(config.dataset_version)}, p.split, p.canonical_news_id, p.ticker, p.published_at_utc,
 p.publication_session, {q(config.representation_name)}, {q(representation_sha256)},
 e.text_sha256, e.embedding, p.stock_state, p.horizon_codes, p.return_targets
FROM {source} AS p FINAL
INNER JOIN {embeddings} AS e FINAL USING (canonical_news_id, ticker, published_at_utc)
WHERE p.dataset_version = {q(config.source_dataset_version)}
 AND e.embedding_version = {q(config.embedding_version)}
 AND e.model = {q(config.embedding_model)}
 AND e.dimensions = {config.openai_embedding_dim}
 AND e.text_contract = {q(config.embedding_text_contract)}
 AND p.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND p.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
"""


def month_count_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    representation_sha256: str,
) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(tuple(canonical_news_id, ticker, published_at_utc))
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
FORMAT TSV
"""


def source_month_count_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    return f"""
SELECT count()
FROM {table} FINAL
WHERE dataset_version = {q(config.source_dataset_version)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
"""


def completed_range_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    representation_sha256: str,
) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
SELECT status, rows
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND range_start = toDate({q(start.isoformat())})
 AND range_end_exclusive = toDate({q(end.isoformat())})
FORMAT TSV
"""


def delete_month_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> tuple[str, str]:
    target = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    manifest = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    predicate = (
        f"dataset_version = {q(config.dataset_version)} "
        f"AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC') "
        f"AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')"
    )
    manifest_predicate = (
        f"dataset_version = {q(config.dataset_version)} "
        f"AND range_start = toDate({q(start.isoformat())}) "
        f"AND range_end_exclusive = toDate({q(end.isoformat())})"
    )
    return (
        f"ALTER TABLE {target} DELETE WHERE {predicate} SETTINGS mutations_sync=2",
        f"ALTER TABLE {manifest} DELETE WHERE {manifest_predicate} SETTINGS mutations_sync=2",
    )


def record_completed_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    representation_sha256: str,
    rows: int,
) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
INSERT INTO {table}
(dataset_version, representation_sha256, range_start, range_end_exclusive, split, status, rows)
VALUES (
 {q(config.dataset_version)}, {q(representation_sha256)}, toDate({q(start.isoformat())}),
 toDate({q(end.isoformat())}), {q(split_for(start))}, 'completed', {int(rows)}
)
"""


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    config = loader_from_args(args)
    months = month_ranges(args.start, args.end_exclusive)
    raw_client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    coverage = population_audit(raw_client, config, args.start, args.end_exclusive)
    print(
        f"V8 COVERAGE | source={coverage['source_rows']:,} matched_embeddings={coverage['matched_embeddings']:,} "
        f"missing={coverage['missing_embeddings']:,} invalid={coverage['invalid_embeddings']:,}",
        flush=True,
    )
    if (
        coverage["source_rows"] == 0
        or coverage["source_unique"] != coverage["source_rows"]
        or coverage["source_representation_versions"] != 1
    ):
        raise RuntimeError(f"V7 source population is not a valid one-row-per-article authority: {coverage}")
    if not args.execute:
        print(
            f"PLAN ONLY | months={len(months)} destination={config.dataset_database}.{config.dataset_table}. "
            "No database or filesystem writes were made.",
            flush=True,
        )
        return 0
    if coverage["missing_embeddings"] or coverage["invalid_embeddings"]:
        raise RuntimeError(
            "V8 requires complete valid OpenAI coverage before materialization. "
            f"missing={coverage['missing_embeddings']:,} invalid={coverage['invalid_embeddings']:,}. "
            "Let openai_embeddings_v1 finish, run its --audit, then rerun this command. No V8 write was made."
        )

    manifest = load_or_create_v8_manifest(
        config.representation_artifact_root / "manifest.json",
        build_v8_manifest(config, coverage["source_representation_sha256"]),
    )
    representation_sha256 = str(manifest["representation_sha256"])
    status_path = (
        Path(args.status_path)
        if args.status_path
        else Path("runtime/news-reaction-model/v8/prepare/status.jsonl")
    )
    append_jsonl(status_path, {
        "event": "start",
        "stage": "openai_embedding_ablation_materialization",
        "loader": asdict(config),
        "months": len(months),
        "coverage": coverage,
        "representation_sha256": representation_sha256,
    })
    controller = QueryCancellationController()
    client = TrackedClickHouseClient(controller)
    client.execute(create_table_sql(config))
    client.execute(create_manifest_table_sql(config))

    def interrupt_handler(signum: int, _frame: Any) -> None:
        active, error = controller.cancel()
        print(
            f"INTERRUPT signal={signum}; cancellation requested for {active} active ClickHouse queries"
            + (f"; error={error}" if error else ""),
            flush=True,
        )
        raise KeyboardInterrupt

    previous_sigint = signal.signal(signal.SIGINT, interrupt_handler)
    previous_sigbreak = signal.signal(signal.SIGBREAK, interrupt_handler) if hasattr(signal, "SIGBREAK") else None

    def build_month(item: tuple[dt.date, dt.date]) -> dict[str, Any]:
        start, end = item
        local = TrackedClickHouseClient(controller)
        completed = local.execute(
            completed_range_sql(config, start, end, representation_sha256)
        ).strip().split("\t")
        if completed and completed[0] == "completed" and not args.rebuild:
            return {"month": start.strftime("%Y-%m"), "status": "skipped", "rows": int(completed[1])}
        if args.rebuild:
            for sql in delete_month_sql(config, start, end):
                local.execute(sql)
        expected = int(local.execute(source_month_count_sql(config, start, end)).strip() or "0")
        started = time.perf_counter()
        local.execute(insert_month_sql(config, start, end, representation_sha256))
        count_fields = local.execute(
            month_count_sql(config, start, end, representation_sha256)
        ).strip().split("\t")
        count = int(count_fields[0] or 0)
        unique = int(count_fields[1] or 0)
        if count != expected or unique != expected:
            raise RuntimeError(
                f"V8 month verification failed for {start:%Y-%m}: "
                f"source={expected} count={count} unique={unique}."
            )
        local.execute(record_completed_sql(config, start, end, representation_sha256, count))
        return {
            "month": start.strftime("%Y-%m"),
            "status": "completed",
            "rows": count,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    workers = max(1, min(args.workers, len(months)))
    completed_rows = 0
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="news-v8-prepare"
    )
    futures: dict[concurrent.futures.Future[dict[str, Any]], tuple[dt.date, dt.date]] = {}
    try:
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
    except (KeyboardInterrupt, InterruptedError):
        active, cancellation_error = controller.cancel()
        cancelled = sum(future.cancel() for future in futures)
        append_jsonl(status_path, {
            "event": "interrupted",
            "completed_rows": completed_rows,
            "cancelled_futures": cancelled,
            "active_queries_cancelled": active,
            "cancellation_error": cancellation_error,
        })
        executor.shutdown(wait=False, cancel_futures=True)
        return 130
    except BaseException:
        controller.cancel()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if previous_sigbreak is not None:
            signal.signal(signal.SIGBREAK, previous_sigbreak)

    audit = audit_prepared_dataset(config, args.start, args.end_exclusive)
    if audit["rows"] != coverage["source_rows"]:
        raise RuntimeError(
            f"V8 population mismatch after materialization: source={coverage['source_rows']:,} "
            f"prepared={audit['rows']:,}."
        )
    if audit["representation_sha256"] != representation_sha256:
        raise RuntimeError("Prepared V8 rows do not match the active representation manifest.")
    append_jsonl(status_path, {"event": "complete", "audit": audit})
    print(
        f"COMPLETED | rows={audit['rows']:,} representation={representation_sha256} "
        f"status={status_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
