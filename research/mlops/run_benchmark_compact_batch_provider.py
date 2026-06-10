from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "database": "market_sip_compact",
    "quote_table": "quotes",
    "trade_table": "trades",
    "index_table": "train_2019_to_2025",
    "batch_size": 256,
    "benchmark_batches": 10,
    "events_per_chunk": 128,
    "fetch_per_kind": 256,
    "workers": 32,
    "max_sample_attempt_multiplier": 5,
    "seed": 17,
    "max_threads_per_query": 1,
    "max_memory_usage": "20G",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for compact ClickHouse batch-provider benchmark.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--quote-table", default=DEFAULTS["quote_table"])
    parser.add_argument("--trade-table", default=DEFAULTS["trade_table"])
    parser.add_argument("--index-table", default=DEFAULTS["index_table"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--benchmark-batches", type=int, default=DEFAULTS["benchmark_batches"])
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULTS["events_per_chunk"])
    parser.add_argument("--fetch-per-kind", type=int, default=DEFAULTS["fetch_per_kind"])
    parser.add_argument("--workers", type=int, default=DEFAULTS["workers"])
    parser.add_argument("--max-sample-attempt-multiplier", type=int, default=DEFAULTS["max_sample_attempt_multiplier"])
    parser.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    parser.add_argument("--max-threads-per-query", type=int, default=DEFAULTS["max_threads_per_query"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--no-strict-lossless", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script = Path(__file__).with_name("clickhouse_benchmark_compact_batch_provider.py")
    command = [
        sys.executable,
        "-u",
        str(script),
        "--database",
        args.database,
        "--quote-table",
        args.quote_table,
        "--trade-table",
        args.trade_table,
        "--index-table",
        args.index_table,
        "--batch-size",
        str(args.batch_size),
        "--benchmark-batches",
        str(args.benchmark_batches),
        "--events-per-chunk",
        str(args.events_per_chunk),
        "--fetch-per-kind",
        str(args.fetch_per_kind),
        "--workers",
        str(args.workers),
        "--max-sample-attempt-multiplier",
        str(args.max_sample_attempt_multiplier),
        "--seed",
        str(args.seed),
        "--max-threads-per-query",
        str(args.max_threads_per_query),
        "--max-memory-usage",
        args.max_memory_usage,
    ]
    if args.no_strict_lossless:
        command.append("--no-strict-lossless")
    print("Equivalent command:", flush=True)
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
