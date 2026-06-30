from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse

import numpy as np


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import DEFAULT_DATABASE, env_status_keys  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_EVENTS_TABLE = "events"
DEFAULT_CONTINUITY_TABLE = "events_ordinal_continuity"
DEFAULT_TRAIN_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_INDEX_TABLE = "validation_2026"
DEFAULT_BATCH_SIZES = "4096,8192,16384"
DEFAULT_CONTEXT_EVENTS = 128
DEFAULT_ORIGINS_PER_SPAN = 32
DEFAULT_MIN_STRIDE = 1
DEFAULT_MAX_STRIDE = 16
DEFAULT_QUERY_BUNDLE_SPANS = 64
DEFAULT_BENCHMARK_BATCHES = 5
ROW_BINARY_DTYPE = np.dtype(
    [
        ("span_id", "<u4"),
        ("ordinal", "<u8"),
        ("event_type", "u1"),
        ("sip_timestamp_us", "<u8"),
        ("price_primary_int", "<u4"),
        ("price_secondary_int", "<u4"),
        ("size_primary", "<f4"),
        ("size_secondary", "<f4"),
        ("exchange_primary", "u1"),
        ("exchange_secondary", "u1"),
        ("condition_tokens_packed", "<u8"),
    ]
)


@dataclass(frozen=True, slots=True)
class IndexRow:
    ticker: str
    first_ordinal: int
    max_valid_ordinal: int
    event_count: int


@dataclass(frozen=True, slots=True)
class Span:
    span_id: int
    ticker: str
    low_ordinal: int
    high_ordinal: int
    base_origin: int
    stride: int
    origins_per_span: int
    expected_rows: int


class PersistentClickHouseHttpClient:
    def __init__(self, base_url: str, user: str, password: str) -> None:
        parsed = parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported ClickHouse URL scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError(f"Invalid ClickHouse URL: {base_url!r}")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.path_prefix = parsed.path.rstrip("/")
        self.user = user
        self.password = password
        self._conn: http.client.HTTPConnection | http.client.HTTPSConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> http.client.HTTPConnection | http.client.HTTPSConnection:
        if self._conn is None:
            cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
            self._conn = cls(self.host, self.port, timeout=900)
        return self._conn

    def execute_bytes(self, sql: str) -> bytes:
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.user:
            headers["X-ClickHouse-User"] = self.user
        if self.password:
            headers["X-ClickHouse-Key"] = self.password
        path = (self.path_prefix or "") + "/"
        body = sql.encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                conn = self._connection()
                conn.request("POST", path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
                if response.status >= 400:
                    text = payload.decode("utf-8", errors="replace")
                    raise RuntimeError(f"ClickHouse HTTP {response.status} {response.reason}: {text}")
                return payload
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                self.close()
                if attempt == 1:
                    raise
        raise RuntimeError(f"ClickHouse request failed: {last_error!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark final events-table span batch queries for v4 training.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--continuity-table", default=DEFAULT_CONTINUITY_TABLE)
    parser.add_argument("--train-index-table", default=DEFAULT_TRAIN_INDEX_TABLE)
    parser.add_argument("--validation-index-table", default=DEFAULT_VALIDATION_INDEX_TABLE)
    parser.add_argument("--index-table", default="", help="Explicit split index table. Defaults to --train-index-table.")
    parser.add_argument("--index-source", choices=("auto", "split", "continuity"), default="auto")
    parser.add_argument("--batch-sizes", default=DEFAULT_BATCH_SIZES, help="Comma-separated batch sizes to test.")
    parser.add_argument("--context-events", type=int, default=DEFAULT_CONTEXT_EVENTS)
    parser.add_argument("--origins-per-span", type=int, default=DEFAULT_ORIGINS_PER_SPAN)
    parser.add_argument("--min-origin-stride", type=int, default=DEFAULT_MIN_STRIDE)
    parser.add_argument("--max-origin-stride", type=int, default=DEFAULT_MAX_STRIDE)
    parser.add_argument("--query-bundle-spans", type=int, default=DEFAULT_QUERY_BUNDLE_SPANS)
    parser.add_argument("--benchmark-batches", type=int, default=DEFAULT_BENCHMARK_BATCHES)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-index-rows", type=int, default=0)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "events_span_batch_benchmark"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def default_clickhouse_url() -> str:
    return (
        os.environ.get("CLICKHOUSE_URL")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL")
        or default_clickhouse_url_with_network_fallback()
        or "http://localhost:18123"
    )


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return " SETTINGS " + ", ".join(settings) if settings else ""


def parse_batch_sizes(value: str) -> list[int]:
    sizes = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not sizes:
        raise ValueError("--batch-sizes must contain at least one integer")
    return sizes


def query_scalar(client: ClickHouseHttpClient, sql: str) -> str:
    return client.execute(sql.rstrip(";") + " FORMAT TSV").strip()


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    text = query_scalar(
        client,
        f"""
SELECT count()
FROM system.tables
WHERE database = {sql_string(database)}
  AND name = {sql_string(table)}
""",
    )
    return int(text or "0") > 0


def table_count(client: ClickHouseHttpClient, database: str, table: str) -> int:
    return int(query_scalar(client, f"SELECT count() FROM {quote_ident(database)}.{quote_ident(table)}") or "0")


def load_index_rows(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[str, list[IndexRow]]:
    if args.index_source in {"auto", "split"}:
        table = args.index_table.strip() or args.train_index_table
        if table_exists(client, args.database, table):
            count = table_count(client, args.database, table)
            if count > 0:
                return f"split:{table}", load_split_index_rows(client, args, table)
            if args.index_source == "split":
                raise RuntimeError(f"Split index table {args.database}.{table} exists but has no rows.")
        elif args.index_source == "split":
            raise RuntimeError(f"Split index table {args.database}.{table} does not exist.")
    return f"continuity:{args.continuity_table}", load_continuity_index_rows(client, args)


def load_split_index_rows(client: ClickHouseHttpClient, args: argparse.Namespace, table: str) -> list[IndexRow]:
    limit = f" LIMIT {int(args.max_index_rows)}" if int(args.max_index_rows) > 0 else ""
    query = f"""
SELECT
    ticker,
    first_ordinal,
    max_valid_ordinal,
    split_event_count
FROM {quote_ident(args.database)}.{quote_ident(table)}
WHERE split_event_count >= {int(args.context_events)}
  AND max_valid_ordinal >= first_ordinal
ORDER BY ticker
{limit}
FORMAT TSV
"""
    rows = []
    for line in client.execute(query).splitlines():
        if not line:
            continue
        ticker, first_ordinal, max_valid_ordinal, event_count = line.split("\t")
        rows.append(IndexRow(ticker=ticker, first_ordinal=int(first_ordinal), max_valid_ordinal=int(max_valid_ordinal), event_count=int(event_count)))
    if not rows:
        raise RuntimeError(f"No eligible rows found in split index {args.database}.{table}")
    return rows


def load_continuity_index_rows(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[IndexRow]:
    limit = f" LIMIT {int(args.max_index_rows)}" if int(args.max_index_rows) > 0 else ""
    query = f"""
SELECT
    ticker,
    min(day_last_ordinal - day_event_count + 1) AS first_ordinal,
    max(day_last_ordinal) AS max_valid_ordinal,
    sum(day_event_count) AS event_count
FROM
(
    SELECT
        ticker,
        build_step,
        argMax(event_count, updated_at) AS day_event_count,
        argMax(last_ordinal, updated_at) AS day_last_ordinal
    FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
    GROUP BY ticker, build_step
)
GROUP BY ticker
HAVING event_count >= {int(args.context_events)}
   AND max_valid_ordinal >= first_ordinal
ORDER BY ticker
{limit}
FORMAT TSV
"""
    rows = []
    for line in client.execute(query).splitlines():
        if not line:
            continue
        ticker, first_ordinal, max_valid_ordinal, event_count = line.split("\t")
        rows.append(IndexRow(ticker=ticker, first_ordinal=int(first_ordinal), max_valid_ordinal=int(max_valid_ordinal), event_count=int(event_count)))
    if not rows:
        raise RuntimeError(f"No eligible rows found in continuity table {args.database}.{args.continuity_table}")
    return rows


def sample_spans(index_rows: list[IndexRow], *, args: argparse.Namespace, batch_size: int, rng: random.Random) -> list[Span]:
    origins_per_span = int(args.origins_per_span)
    if batch_size % origins_per_span != 0:
        raise ValueError(f"batch_size={batch_size} must be divisible by origins_per_span={origins_per_span}")
    span_count = batch_size // origins_per_span
    spans: list[Span] = []
    attempts = 0
    max_attempts = max(span_count * 100, 1000)
    while len(spans) < span_count and attempts < max_attempts:
        attempts += 1
        row = rng.choice(index_rows)
        stride = rng.randint(int(args.min_origin_stride), int(args.max_origin_stride))
        high_extra = (origins_per_span - 1) * stride
        min_base = row.first_ordinal + int(args.context_events) - 1
        max_base = row.max_valid_ordinal - high_extra
        if max_base < min_base:
            continue
        base = rng.randint(min_base, max_base)
        low = base - int(args.context_events) + 1
        high = base + high_extra
        spans.append(
            Span(
                span_id=len(spans),
                ticker=row.ticker,
                low_ordinal=low,
                high_ordinal=high,
                base_origin=base,
                stride=stride,
                origins_per_span=origins_per_span,
                expected_rows=high - low + 1,
            )
        )
    if len(spans) < span_count:
        raise RuntimeError(f"Could only sample {len(spans):,}/{span_count:,} spans after {attempts:,} attempts.")
    return spans


def span_query(args: argparse.Namespace, spans: list[Span]) -> str:
    select_parts = []
    table = f"{quote_ident(args.database)}.{quote_ident(args.events_table)}"
    for span in spans:
        select_parts.append(
            f"""
SELECT
    toUInt32({int(span.span_id)}) AS span_id,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_tokens_packed
FROM {table}
PREWHERE ticker = {sql_string(span.ticker)}
  AND ordinal >= {int(span.low_ordinal)}
  AND ordinal <= {int(span.high_ordinal)}
""".strip()
        )
    return f"""
SELECT
    span_id,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_tokens_packed
FROM
(
{" UNION ALL ".join(select_parts)}
)
ORDER BY span_id, ordinal
{query_settings(args)}
FORMAT RowBinary
"""


def fetch_span_bundle(client: PersistentClickHouseHttpClient, args: argparse.Namespace, spans: list[Span]) -> tuple[np.ndarray, float, float]:
    query_started = time.perf_counter()
    payload = client.execute_bytes(span_query(args, spans))
    query_seconds = time.perf_counter() - query_started
    parse_started = time.perf_counter()
    if len(payload) % ROW_BINARY_DTYPE.itemsize != 0:
        raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by row size {ROW_BINARY_DTYPE.itemsize}")
    rows = np.frombuffer(payload, dtype=ROW_BINARY_DTYPE).copy()
    parse_seconds = time.perf_counter() - parse_started
    return rows, query_seconds, parse_seconds


def benchmark_batch(
    client: PersistentClickHouseHttpClient,
    index_rows: list[IndexRow],
    *,
    args: argparse.Namespace,
    batch_size: int,
    rng: random.Random,
) -> dict[str, float]:
    started = time.perf_counter()
    sample_started = time.perf_counter()
    spans = sample_spans(index_rows, args=args, batch_size=batch_size, rng=rng)
    sample_seconds = time.perf_counter() - sample_started
    all_rows: list[np.ndarray] = []
    query_seconds = 0.0
    parse_seconds = 0.0
    bundle_size = max(1, int(args.query_bundle_spans))
    query_count = 0
    for offset in range(0, len(spans), bundle_size):
        bundle = spans[offset : offset + bundle_size]
        rows, query_elapsed, parse_elapsed = fetch_span_bundle(client, args, bundle)
        all_rows.append(rows)
        query_seconds += query_elapsed
        parse_seconds += parse_elapsed
        query_count += 1
    concat_started = time.perf_counter()
    rows = np.concatenate(all_rows) if all_rows else np.empty((0,), dtype=ROW_BINARY_DTYPE)
    concat_seconds = time.perf_counter() - concat_started
    expected_rows = sum(span.expected_rows for span in spans)
    stride_values = [span.stride for span in spans]
    return {
        "batch_size": float(batch_size),
        "num_spans": float(len(spans)),
        "origins_per_span": float(args.origins_per_span),
        "query_bundle_spans": float(bundle_size),
        "query_count": float(query_count),
        "rows_expected": float(expected_rows),
        "rows_received": float(rows.shape[0]),
        "query_seconds": query_seconds,
        "parse_seconds": parse_seconds,
        "concat_seconds": concat_seconds,
        "sample_seconds": sample_seconds,
        "batch_seconds": time.perf_counter() - started,
        "samples_per_second": float(batch_size) / max(time.perf_counter() - started, 1e-9),
        "rows_per_second": float(rows.shape[0]) / max(query_seconds, 1e-9),
        "stride_min": float(min(stride_values) if stride_values else 0),
        "stride_max": float(max(stride_values) if stride_values else 0),
        "stride_mean": float(statistics.fmean(stride_values) if stride_values else 0.0),
        "row_count_ok": float(rows.shape[0] == expected_rows),
    }


def summarize(size: int, profiles: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for profile in profiles for key in profile})
    out = {"batch_size": float(size), "batches": float(len(profiles))}
    for key in keys:
        values = [float(profile[key]) for profile in profiles if key in profile]
        if not values:
            continue
        out[f"{key}_mean"] = float(statistics.fmean(values))
        out[f"{key}_min"] = float(min(values))
        out[f"{key}_max"] = float(max(values))
        out[f"{key}_p50"] = float(np.quantile(np.asarray(values), 0.50))
        out[f"{key}_p95"] = float(np.quantile(np.asarray(values), 0.95))
    return out


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    batch_sizes = parse_batch_sizes(args.batch_sizes)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = "events_span_batch_benchmark_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    print("=" * 96, flush=True)
    print("Events span batch benchmark", flush=True)
    print(f"database={args.database} events_table={args.events_table}", flush=True)
    print(f"batch_sizes={batch_sizes} context_events={args.context_events} origins_per_span={args.origins_per_span}", flush=True)
    print(f"random_stride={args.min_origin_stride}..{args.max_origin_stride} query_bundle_spans={args.query_bundle_spans}", flush=True)
    print(f"settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"clickhouse_url={args.clickhouse_url} user={args.user}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)
    source, index_rows = load_index_rows(client, args)
    print(f"Loaded index source={source} tickers={len(index_rows):,}", flush=True)
    append_jsonl(report_path, {"type": "run_start", "args": vars(args), "index_source": source, "index_rows": len(index_rows)})
    if args.dry_run:
        print("dry_run=True; not querying events table.", flush=True)
        return

    binary_client = PersistentClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    rng = random.Random(args.seed)
    try:
        for size in batch_sizes:
            if size % int(args.origins_per_span) != 0:
                raise ValueError(f"batch_size={size} must be divisible by origins_per_span={args.origins_per_span}")
            print("-" * 96, flush=True)
            print(
                f"SIZE START batch_size={size:,} num_spans={size // int(args.origins_per_span):,} "
                f"origins_per_span={args.origins_per_span}",
                flush=True,
            )
            profiles = []
            for batch_index in range(1, int(args.benchmark_batches) + 1):
                profile = benchmark_batch(binary_client, index_rows, args=args, batch_size=size, rng=rng)
                profiles.append(profile)
                append_jsonl(report_path, {"type": "batch", "batch_size": size, "batch_index": batch_index, "profile": profile})
                print(
                    f"BATCH size={size:,} [{batch_index:,}/{args.benchmark_batches:,}] "
                    f"seconds={profile['batch_seconds']:.3f} query={profile['query_seconds']:.3f} "
                    f"parse={profile['parse_seconds']:.3f} rows={int(profile['rows_received']):,}/{int(profile['rows_expected']):,} "
                    f"samples_s={profile['samples_per_second']:.1f} queries={int(profile['query_count'])} "
                    f"stride_mean={profile['stride_mean']:.2f}",
                    flush=True,
                )
            summary = summarize(size, profiles)
            append_jsonl(report_path, {"type": "summary", "batch_size": size, "summary": summary})
            print(
                f"SUMMARY size={size:,} "
                f"batch_seconds_mean={summary.get('batch_seconds_mean', 0.0):.3f} "
                f"batch_seconds_p95={summary.get('batch_seconds_p95', 0.0):.3f} "
                f"query_seconds_mean={summary.get('query_seconds_mean', 0.0):.3f} "
                f"samples_per_second_mean={summary.get('samples_per_second_mean', 0.0):.1f} "
                f"rows_received_mean={summary.get('rows_received_mean', 0.0):.0f}",
                flush=True,
            )
    finally:
        binary_client.close()
    print("=" * 96, flush=True)
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
