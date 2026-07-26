from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import tracemalloc
from pathlib import Path

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v16.config import LoaderConfig
from research.news_reaction_model.v16.market_data import load_day_market_data


REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark and validate one representative V16 bounded event-reader session."
    )
    parser.add_argument("--session-date", default="2026-07-10")
    parser.add_argument("--max-threads", type=int, default=4)
    parser.add_argument("--max-memory-usage", default="16G")
    parser.add_argument(
        "--output",
        default=r"D:\market-data\prepared\news_reaction_model\v16\reader_benchmark.json",
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    session_date = dt.date.fromisoformat(args.session_date)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    config = LoaderConfig(
        market_max_threads=max(1, int(args.max_threads)),
        market_max_memory_usage=str(args.max_memory_usage),
    )
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    tracemalloc.start()
    started = time.perf_counter()
    data = load_day_market_data(client, config, session_date)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = {
        "status": "complete",
        "session_date": session_date.isoformat(),
        "ticker_count": data.ticker_count,
        "minute_row_count": data.minute_row_count,
        "elapsed_seconds": elapsed,
        "rows_per_second": data.minute_row_count / max(elapsed, 1e-9),
        "python_peak_bytes": peak,
        "max_threads": config.market_max_threads,
        "max_memory_usage": config.market_max_memory_usage,
        "contract": "completed_minute_state_from_exact_events_v2_ordinal",
    }
    if data.ticker_count <= 0 or data.minute_row_count <= 0:
        raise RuntimeError(f"V16 reader benchmark returned no market state: {result}")
    print("V16 READER BENCHMARK " + json.dumps(result, sort_keys=True), flush=True)
    if not args.no_write and str(args.output).strip():
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
