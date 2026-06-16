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
    DEFAULT_INDEX_TABLE,
    DEFAULT_REFERENCE_DIR,
    IndexRow,
    OriginSample,
    PersistentClickHouseHttpClient,
    load_index,
    normalize_reject_reason,
    query_settings,
    sample_origins,
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


DEFAULT_BATCH_SIZE = 256
DEFAULT_BENCHMARK_BATCHES = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark ClickHouse-only nearest-N quote event lookups for sampled batch origins."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--index-table", default=DEFAULT_INDEX_TABLE)
    parser.add_argument("--reference-dir", default=str(DEFAULT_REFERENCE_DIR))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--benchmark-batches", type=int, default=DEFAULT_BENCHMARK_BATCHES)
    parser.add_argument("--events-per-sample", type=int, default=DEFAULT_EVENTS_PER_CHUNK)
    parser.add_argument("--lookback-us", type=int, default=0)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--max-sample-attempt-multiplier", type=int, default=5)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-memory-usage", default="20G")
    parser.add_argument("--max-threads-per-query", type=int, default=1)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "quote_nearest_query_benchmark"))
    parser.add_argument("--limit-index-tickers", type=int, default=0)
    return parser.parse_args()


def nearest_quote_query(args: argparse.Namespace, sample: OriginSample) -> str:
    lower_bound_sql = ""
    if int(args.lookback_us) > 0:
        lower_bound = max(0, int(sample.origin_timestamp_us) - int(args.lookback_us))
        lower_bound_sql = f"  AND sip_timestamp_us >= {lower_bound}\n"
    return f"""
SELECT
    ticker,
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
FROM {quote_ident(args.database)}.{quote_ident(args.quote_table)}
PREWHERE ticker = {sql_string(sample.ticker)}
  AND sip_timestamp_us <= {int(sample.origin_timestamp_us)}
{lower_bound_sql.rstrip()}
WHERE sip_timestamp_us > 0
  AND sequence_number > 0
ORDER BY sip_timestamp_us DESC, sequence_number DESC
LIMIT {int(args.events_per_sample)}
{query_settings(args)}
"""


def fetch_nearest_quote_count(
    sample: OriginSample,
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    client: PersistentClickHouseHttpClient = thread_client(args)
    started = time.perf_counter()
    try:
        text = client.query_tsv(nearest_quote_query(args, sample))
    except Exception as exc:  # noqa: BLE001
        return {
            "sample_id": sample.sample_id,
            "ticker": sample.ticker,
            "accepted": False,
            "rows": 0,
            "query_seconds": time.perf_counter() - started,
            "reject_reason": normalize_reject_reason("query_error", exc),
        }
    rows = 0 if not text else sum(1 for line in text.splitlines() if line)
    return {
        "sample_id": sample.sample_id,
        "ticker": sample.ticker,
        "accepted": rows >= int(args.events_per_sample),
        "rows": rows,
        "query_seconds": time.perf_counter() - started,
        "reject_reason": "" if rows >= int(args.events_per_sample) else "not_enough_rows",
    }


def build_query_batch(
    index_rows: list[IndexRow],
    *,
    args: argparse.Namespace,
    rng: random.Random,
) -> dict[str, Any]:
    batch_started = time.perf_counter()
    workers = max(1, min(int(args.workers), int(args.batch_size)))
    max_attempts = max(int(args.batch_size), int(args.batch_size) * max(1, int(args.max_sample_attempt_multiplier)))
    attempted = 0
    next_sample_id = 0
    sample_seconds = 0.0
    fetch_seconds = 0.0
    results: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        while len(accepted) < args.batch_size and attempted < max_attempts:
            remaining_needed = int(args.batch_size) - len(accepted)
            remaining_attempts = max_attempts - attempted
            draw_count = min(max(workers, remaining_needed), remaining_attempts)
            sample_started = time.perf_counter()
            origins = sample_origins(index_rows, batch_size=draw_count, rng=rng)
            origins = [
                OriginSample(sample_id=next_sample_id + index, ticker=sample.ticker, origin_timestamp_us=sample.origin_timestamp_us)
                for index, sample in enumerate(origins)
            ]
            next_sample_id += draw_count
            attempted += draw_count
            sample_seconds += time.perf_counter() - sample_started
            fetch_started = time.perf_counter()
            futures = [executor.submit(fetch_nearest_quote_count, sample, args=args) for sample in origins]
            round_results: list[dict[str, Any]] = []
            for future in concurrent.futures.as_completed(futures):
                try:
                    round_results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    round_results.append(
                        {
                            "sample_id": -1,
                            "ticker": "",
                            "accepted": False,
                            "rows": 0,
                            "query_seconds": 0.0,
                            "reject_reason": normalize_reject_reason("worker_error", exc),
                        }
                    )
            fetch_seconds += time.perf_counter() - fetch_started
            results.extend(round_results)
            accepted.extend(item for item in round_results if item["accepted"])
    accepted = sorted(accepted, key=lambda item: int(item["sample_id"]))[: int(args.batch_size)]
    rejected = [item for item in results if not item["accepted"]]
    reject_counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item["reject_reason"])
        reject_counts[reason] = reject_counts.get(reason, 0) + 1
    rows = [float(item["rows"]) for item in results]
    query_seconds = [float(item["query_seconds"]) for item in results]
    profile = {
        "data/batch_build_seconds": time.perf_counter() - batch_started,
        "data/sample_select_seconds": sample_seconds,
        "data/fetch_wall_seconds": fetch_seconds,
        "data/query_sum_seconds": float(sum(query_seconds)),
        "data/requested": float(args.batch_size),
        "data/attempted": float(attempted),
        "data/accepted": float(len(accepted)),
        "data/rejected": float(len(rejected)),
        "data/query_requests": float(attempted),
        "data/query_errors": float(sum(1 for item in rejected if str(item["reject_reason"]).startswith("query_error"))),
        "data/accept_pct": 100.0 * len(accepted) / max(1, len(results)),
        "data/rows_mean": float(np.mean(rows)) if rows else 0.0,
        "data/rows_p50": float(np.quantile(np.asarray(rows), 0.50)) if rows else 0.0,
        "data/rows_p95": float(np.quantile(np.asarray(rows), 0.95)) if rows else 0.0,
        "data/query_seconds_p50": float(np.quantile(np.asarray(query_seconds), 0.50)) if query_seconds else 0.0,
        "data/query_seconds_p95": float(np.quantile(np.asarray(query_seconds), 0.95)) if query_seconds else 0.0,
        "data/workers": float(workers),
        "data/events_per_sample": float(args.events_per_sample),
        "data/lookback_us": float(args.lookback_us),
    }
    return {
        "profile": profile,
        "reject_counts": reject_counts,
        "accepted_tickers_preview": [str(item["ticker"]) for item in accepted[:10]],
    }


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
    total_accepted = sum(accepted)
    total_seconds = sum(seconds)
    out["throughput/accepted_samples_per_second"] = total_accepted / max(total_seconds, 1e-9)
    out["throughput/query_requests_per_second"] = sum(float(profile.get("data/query_requests", 0.0)) for profile in profiles) / max(total_seconds, 1e-9)
    return out


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    args.events_per_chunk = args.events_per_sample
    output_root = Path(args.output_root_win)
    run_id = "quote_nearest_query_benchmark_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    rng = random.Random(args.seed)
    print("=" * 96, flush=True)
    print("Quote-only ClickHouse nearest-N query benchmark", flush=True)
    print(f"database={args.database} quote_table={args.quote_table}", flush=True)
    print(f"index_table={args.index_table} batch_size={args.batch_size} batches={args.benchmark_batches}", flush=True)
    print(f"events_per_sample={args.events_per_sample} lookback_us={args.lookback_us} workers={args.workers}", flush=True)
    print(f"settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)
    started = time.perf_counter()
    index_rows = load_index(client, args)
    print(f"Loaded index tickers={len(index_rows):,} seconds={time.perf_counter() - started:.3f}", flush=True)
    append_jsonl(
        report_path,
        {
            "type": "run_start",
            "run_id": run_id,
            "args": vars(args),
            "secret_status": secret_status(env_status_keys()),
            "loaded_env_files": [str(path) for path in loaded_env_files],
        },
    )
    profiles: list[dict[str, float]] = []
    reject_totals: dict[str, int] = {}
    for batch_index in range(1, int(args.benchmark_batches) + 1):
        batch = build_query_batch(index_rows, args=args, rng=rng)
        profile = batch["profile"]
        profiles.append(profile)
        for reason, count in batch["reject_counts"].items():
            reject_totals[reason] = reject_totals.get(reason, 0) + int(count)
        append_jsonl(
            report_path,
            {
                "type": "batch",
                "batch_index": batch_index,
                "profile": profile,
                "reject_counts": batch["reject_counts"],
                "accepted_tickers_preview": batch["accepted_tickers_preview"],
            },
        )
        print(
            f"BATCH [{batch_index:,}/{args.benchmark_batches:,}] "
            f"seconds={profile['data/batch_build_seconds']:.3f} "
            f"accepted={int(profile['data/accepted']):,}/{args.batch_size:,} "
            f"accept_pct={profile['data/accept_pct']:.1f} "
            f"fetch_wall={profile['data/fetch_wall_seconds']:.3f} "
            f"query_sum={profile['data/query_sum_seconds']:.3f} "
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
