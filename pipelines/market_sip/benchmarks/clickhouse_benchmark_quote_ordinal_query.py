from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.benchmarks.clickhouse_benchmark_compact_batch_provider import (  # noqa: E402
    DEFAULT_EVENTS_PER_CHUNK,
    PersistentClickHouseHttpClient,
    normalize_reject_reason,
    thread_client,
)
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


DEFAULT_ORDINAL_TABLE = "quotes_ordinal_benchmark"
DEFAULT_TICKERS = "AAPL,MSFT,NVDA,TSLA,AMD,SPY,QQQ"
DEFAULT_START_DATE = "2026-05-15"
DEFAULT_END_DATE = "2026-05-15"
DEFAULT_BATCH_SIZE = 256
DEFAULT_BENCHMARK_BATCHES = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark nearest-N quote lookup against a small ordinal-index table."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--ordinal-table", default=DEFAULT_ORDINAL_TABLE)
    parser.add_argument("--tickers", default=DEFAULT_TICKERS)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--build", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--benchmark-batches", type=int, default=DEFAULT_BENCHMARK_BATCHES)
    parser.add_argument("--events-per-sample", type=int, default=DEFAULT_EVENTS_PER_CHUNK)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-memory-usage", default="20G")
    parser.add_argument("--max-threads-per-query", type=int, default=1)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "quote_ordinal_query_benchmark"))
    return parser.parse_args()


def parse_tickers(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if args.max_threads_per_query > 0:
        settings.append(f"max_threads = {int(args.max_threads_per_query)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return " SETTINGS " + ", ".join(settings) if settings else ""


def ensure_ordinal_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    table = f"{quote_ident(args.database)}.{quote_ident(args.ordinal_table)}"
    if args.rebuild:
        print(f"DROP {table}", flush=True)
        client.execute(f"DROP TABLE IF EXISTS {table} SYNC")
    storage = f"storage_policy = {sql_string(args.storage_policy)}" if args.storage_policy.strip() else ""
    create_settings = f"SETTINGS {storage}" if storage else ""
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table}
(
    ticker LowCardinality(String),
    ordinal UInt64,
    sip_timestamp_us UInt64,
    sequence_number UInt32,
    bid_price_int UInt32,
    ask_price_int UInt32,
    bid_size UInt32,
    ask_size UInt32,
    bid_exchange UInt8,
    ask_exchange UInt8,
    quote_flags UInt8,
    conditions LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY cityHash64(ticker) % 16
ORDER BY (ticker, ordinal)
{create_settings}
"""
    )


def existing_ordinal_rows(client: ClickHouseHttpClient, args: argparse.Namespace) -> int:
    table = f"{quote_ident(args.database)}.{quote_ident(args.ordinal_table)}"
    text = client.query_tsv(f"SELECT count() FROM {table}").strip()
    return int(text or 0)


def build_ordinal_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    ensure_ordinal_table(client, args)
    if not args.rebuild and existing_ordinal_rows(client, args) > 0:
        print(f"Ordinal benchmark table already has rows; skipping build. Use --rebuild to recreate.", flush=True)
        return
    tickers = parse_tickers(args.tickers)
    if not tickers:
        raise ValueError("--tickers must contain at least one ticker")
    ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers)
    table = f"{quote_ident(args.database)}.{quote_ident(args.ordinal_table)}"
    source = f"{quote_ident(args.database)}.{quote_ident(args.quote_table)}"
    print(
        f"BUILD {table} tickers={tickers} date_range={args.start_date}->{args.end_date}",
        flush=True,
    )
    started = time.perf_counter()
    client.execute(
        f"""
INSERT INTO {table}
SELECT
    ticker,
    row_number() OVER (PARTITION BY ticker ORDER BY sip_timestamp_us ASC, sequence_number ASC) AS ordinal,
    sip_timestamp_us,
    sequence_number,
    bid_price_int,
    ask_price_int,
    bid_size,
    ask_size,
    bid_exchange,
    ask_exchange,
    quote_flags,
    conditions
FROM {source}
PREWHERE ticker IN ({ticker_sql})
  AND event_date >= toDate({sql_string(args.start_date)})
  AND event_date <= toDate({sql_string(args.end_date)})
WHERE sip_timestamp_us > 0
  AND sequence_number > 0
"""
    )
    rows = existing_ordinal_rows(client, args)
    print(f"BUILD DONE rows={rows:,} seconds={time.perf_counter() - started:.3f}", flush=True)


def load_ordinal_ranges(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    table = f"{quote_ident(args.database)}.{quote_ident(args.ordinal_table)}"
    query = f"""
SELECT
    ticker,
    count() AS event_count,
    min(ordinal) AS min_ordinal,
    max(ordinal) AS max_ordinal
FROM {table}
GROUP BY ticker
HAVING event_count >= {int(args.events_per_sample)}
ORDER BY ticker
"""
    rows: list[dict[str, Any]] = []
    for line in client.query_tsv(query).splitlines():
        ticker, event_count, min_ordinal, max_ordinal = line.split("\t")
        rows.append(
            {
                "ticker": ticker,
                "event_count": int(event_count),
                "min_ordinal": int(min_ordinal),
                "max_ordinal": int(max_ordinal),
            }
        )
    if not rows:
        raise RuntimeError(f"No eligible tickers found in {table}")
    return rows


def sample_origins(ranges: list[dict[str, Any]], *, batch_size: int, events_per_sample: int, rng: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sample_id in range(batch_size):
        row = rng.choice(ranges)
        min_origin = max(int(row["min_ordinal"]) + events_per_sample - 1, int(row["min_ordinal"]))
        max_origin = int(row["max_ordinal"])
        origin = rng.randint(min_origin, max_origin)
        out.append({"sample_id": sample_id, "ticker": row["ticker"], "origin_ordinal": origin})
    return out


def ordinal_query(args: argparse.Namespace, sample: dict[str, Any]) -> str:
    low = int(sample["origin_ordinal"]) - int(args.events_per_sample) + 1
    high = int(sample["origin_ordinal"])
    return f"""
SELECT
    ticker,
    ordinal,
    sip_timestamp_us,
    sequence_number,
    bid_price_int,
    ask_price_int,
    bid_size,
    ask_size,
    bid_exchange,
    ask_exchange,
    quote_flags,
    conditions
FROM {quote_ident(args.database)}.{quote_ident(args.ordinal_table)}
PREWHERE ticker = {sql_string(str(sample["ticker"]))}
  AND ordinal >= {low}
  AND ordinal <= {high}
ORDER BY ordinal ASC
LIMIT {int(args.events_per_sample)}
{query_settings(args)}
"""


def fetch_ordinal_count(sample: dict[str, Any], *, args: argparse.Namespace) -> dict[str, Any]:
    client: PersistentClickHouseHttpClient = thread_client(args)
    started = time.perf_counter()
    try:
        text = client.query_tsv(ordinal_query(args, sample))
    except Exception as exc:  # noqa: BLE001
        return {
            "accepted": False,
            "rows": 0,
            "query_seconds": time.perf_counter() - started,
            "reject_reason": normalize_reject_reason("query_error", exc),
        }
    rows = 0 if not text else sum(1 for line in text.splitlines() if line)
    return {
        "accepted": rows >= int(args.events_per_sample),
        "rows": rows,
        "query_seconds": time.perf_counter() - started,
        "reject_reason": "" if rows >= int(args.events_per_sample) else "not_enough_rows",
    }


def build_query_batch(ranges: list[dict[str, Any]], *, args: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    started = time.perf_counter()
    workers = max(1, min(int(args.workers), int(args.batch_size)))
    origins = sample_origins(ranges, batch_size=int(args.batch_size), events_per_sample=int(args.events_per_sample), rng=rng)
    fetch_started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_ordinal_count, sample, args=args) for sample in origins]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "accepted": False,
                        "rows": 0,
                        "query_seconds": 0.0,
                        "reject_reason": normalize_reject_reason("worker_error", exc),
                    }
                )
    fetch_seconds = time.perf_counter() - fetch_started
    accepted = [item for item in results if item["accepted"]]
    rejected = [item for item in results if not item["accepted"]]
    rows = [float(item["rows"]) for item in results]
    query_seconds = [float(item["query_seconds"]) for item in results]
    reject_counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item["reject_reason"])
        reject_counts[reason] = reject_counts.get(reason, 0) + 1
    profile = {
        "data/batch_build_seconds": time.perf_counter() - started,
        "data/fetch_wall_seconds": fetch_seconds,
        "data/query_sum_seconds": float(sum(query_seconds)),
        "data/requested": float(args.batch_size),
        "data/accepted": float(len(accepted)),
        "data/rejected": float(len(rejected)),
        "data/query_requests": float(len(results)),
        "data/query_errors": float(sum(1 for item in rejected if str(item["reject_reason"]).startswith("query_error"))),
        "data/accept_pct": 100.0 * len(accepted) / max(1, len(results)),
        "data/rows_mean": float(np.mean(rows)) if rows else 0.0,
        "data/query_seconds_p50": float(np.quantile(np.asarray(query_seconds), 0.50)) if query_seconds else 0.0,
        "data/query_seconds_p95": float(np.quantile(np.asarray(query_seconds), 0.95)) if query_seconds else 0.0,
        "data/workers": float(workers),
        "data/events_per_sample": float(args.events_per_sample),
    }
    return {"profile": profile, "reject_counts": reject_counts}


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def summarize_profiles(profiles: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for profile in profiles for key in profile})
    out: dict[str, float] = {}
    for key in keys:
        values = [float(profile[key]) for profile in profiles if key in profile]
        out[f"{key}/mean"] = float(np.mean(values)) if values else 0.0
        out[f"{key}/p50"] = percentile(values, 0.50)
        out[f"{key}/p95"] = percentile(values, 0.95)
    accepted = [float(profile.get("data/accepted", 0.0)) for profile in profiles]
    seconds = [float(profile.get("data/batch_build_seconds", 0.0)) for profile in profiles]
    total_seconds = sum(seconds)
    out["throughput/accepted_samples_per_second"] = sum(accepted) / max(total_seconds, 1e-9)
    out["throughput/query_requests_per_second"] = sum(float(profile.get("data/query_requests", 0.0)) for profile in profiles) / max(total_seconds, 1e-9)
    return out


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    output_root = Path(args.output_root_win)
    run_id = "quote_ordinal_query_benchmark_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    rng = random.Random(args.seed)
    print("=" * 96, flush=True)
    print("Quote ordinal-index query benchmark", flush=True)
    print(f"database={args.database} quote_table={args.quote_table} ordinal_table={args.ordinal_table}", flush=True)
    print(f"tickers={args.tickers} date_range={args.start_date}->{args.end_date}", flush=True)
    print(f"build={args.build} rebuild={args.rebuild}", flush=True)
    print(f"batch_size={args.batch_size} batches={args.benchmark_batches} events_per_sample={args.events_per_sample} workers={args.workers}", flush=True)
    print(f"settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)
    if args.build:
        build_ordinal_table(client, args)
    ranges = load_ordinal_ranges(client, args)
    print(f"Loaded ordinal ticker ranges={len(ranges):,}", flush=True)
    append_jsonl(
        report_path,
        {
            "type": "run_start",
            "run_id": run_id,
            "args": vars(args),
            "ranges": ranges,
            "secret_status": secret_status(env_status_keys()),
            "loaded_env_files": [str(path) for path in loaded_env_files],
        },
    )
    profiles: list[dict[str, float]] = []
    reject_totals: dict[str, int] = {}
    for batch_index in range(1, int(args.benchmark_batches) + 1):
        batch = build_query_batch(ranges, args=args, rng=rng)
        profile = batch["profile"]
        profiles.append(profile)
        for reason, count in batch["reject_counts"].items():
            reject_totals[reason] = reject_totals.get(reason, 0) + int(count)
        append_jsonl(report_path, {"type": "batch", "batch_index": batch_index, "profile": profile, "reject_counts": batch["reject_counts"]})
        print(
            f"BATCH [{batch_index:,}/{args.benchmark_batches:,}] "
            f"seconds={profile['data/batch_build_seconds']:.3f} "
            f"accepted={int(profile['data/accepted']):,}/{args.batch_size:,} "
            f"fetch_wall={profile['data/fetch_wall_seconds']:.3f} "
            f"query_p50={profile['data/query_seconds_p50']:.4f} "
            f"query_p95={profile['data/query_seconds_p95']:.4f}",
            flush=True,
        )
    summary = summarize_profiles(profiles)
    append_jsonl(report_path, {"type": "summary", "summary": summary, "reject_totals": reject_totals})
    print("=" * 96, flush=True)
    print(f"SUMMARY batches={len(profiles):,} reject_totals={reject_totals}", flush=True)
    for key in sorted(summary):
        print(f"{key}={summary[key]:.6f}", flush=True)
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
