from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "database": "market_sip_compact",
    "quote_table": "quotes",
    "ordinal_table": "quotes_ordinal_benchmark",
    "tickers": "AAPL,MSFT,NVDA,TSLA,AMD,SPY,QQQ",
    "start_date": "2026-05-15",
    "end_date": "2026-05-15",
    "batch_size": 256,
    "benchmark_batches": 10,
    "events_per_sample": 128,
    "workers": 32,
    "seed": 17,
    "max_threads_per_query": 1,
    "max_memory_usage": "20G",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for quote ordinal-index query benchmark.")
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--quote-table", default=DEFAULTS["quote_table"])
    parser.add_argument("--ordinal-table", default=DEFAULTS["ordinal_table"])
    parser.add_argument("--tickers", default=DEFAULTS["tickers"])
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--benchmark-batches", type=int, default=DEFAULTS["benchmark_batches"])
    parser.add_argument("--events-per-sample", type=int, default=DEFAULTS["events_per_sample"])
    parser.add_argument("--workers", type=int, default=DEFAULTS["workers"])
    parser.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    parser.add_argument("--max-threads-per-query", type=int, default=DEFAULTS["max_threads_per_query"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script = Path(__file__).with_name("clickhouse_benchmark_quote_ordinal_query.py")
    command = [
        sys.executable,
        "-u",
        str(script),
        "--database",
        args.database,
        "--quote-table",
        args.quote_table,
        "--ordinal-table",
        args.ordinal_table,
        "--tickers",
        args.tickers,
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--batch-size",
        str(args.batch_size),
        "--benchmark-batches",
        str(args.benchmark_batches),
        "--events-per-sample",
        str(args.events_per_sample),
        "--workers",
        str(args.workers),
        "--seed",
        str(args.seed),
        "--max-threads-per-query",
        str(args.max_threads_per_query),
        "--max-memory-usage",
        args.max_memory_usage,
    ]
    if args.no_build:
        command.append("--no-build")
    if args.rebuild:
        command.append("--rebuild")
    if args.storage_policy:
        command.extend(["--storage-policy", args.storage_policy])
    print("Equivalent command:", flush=True)
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
