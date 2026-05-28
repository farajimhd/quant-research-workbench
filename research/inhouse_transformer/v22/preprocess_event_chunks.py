from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.v22.config import DataConfig  # noqa: E402


LOG_RULE = "*" * 96


def parse_args() -> argparse.Namespace:
    defaults = DataConfig()
    parser = argparse.ArgumentParser(description="Prebuild v22 sparse quote/trade event chunks.")
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--cache-root", default=str(defaults.cache_root))
    parser.add_argument("--start-date", default=defaults.train_start_date)
    parser.add_argument("--end-date", default=defaults.test_end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--chunk-ms", type=int, default=defaults.chunk_ms)
    parser.add_argument("--max-quote-events", type=int, default=defaults.max_quote_events)
    parser.add_argument("--max-trade-events", type=int, default=defaults.max_trade_events)
    parser.add_argument("--max-total-events", type=int, default=defaults.max_total_events)
    parser.add_argument("--processes", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 2)))
    parser.add_argument("--polars-threads-per-process", type=int, default=2)
    parser.add_argument("--session-start-hour-utc", type=int, default=defaults.session_start_hour_utc)
    parser.add_argument("--session-end-hour-utc", type=int, default=defaults.session_end_hour_utc)
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--manifest-name", default="preprocess_event_chunks_manifest.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    from research.inhouse_transformer.v22.data import available_sessions, cached_event_chunk_path, parse_ticker_list

    config = DataConfig(
        flatfiles_root=Path(args.flatfiles_root),
        cache_root=Path(args.cache_root),
        train_start_date=args.start_date,
        train_end_date=args.end_date,
        validation_start_date=args.start_date,
        validation_end_date=args.end_date,
        test_start_date=args.start_date,
        test_end_date=args.end_date,
        tickers=parse_ticker_list(args.tickers),
        chunk_ms=args.chunk_ms,
        max_quote_events=args.max_quote_events,
        max_trade_events=args.max_trade_events,
        max_total_events=args.max_total_events,
        session_start_hour_utc=args.session_start_hour_utc,
        session_end_hour_utc=args.session_end_hour_utc,
        rebuild_cache=args.rebuild_cache,
    )
    sessions = available_sessions(config.flatfiles_root, args.start_date, args.end_date)
    print(LOG_RULE)
    print("v22 event chunk preprocessing")
    print(f"flatfiles_root={config.flatfiles_root}")
    print(f"cache_root={config.cache_root}")
    print(f"sessions={sessions[0]} -> {sessions[-1]} count={len(sessions)}")
    print(f"chunk_ms={config.chunk_ms} max_quote={config.max_quote_events} max_trade={config.max_trade_events} max_total={config.max_total_events}")
    print(f"processes={args.processes} polars_threads_per_process={args.polars_threads_per_process}")
    print(LOG_RULE)
    if args.dry_run:
        for session in sessions:
            print(f"{session}: {cached_event_chunk_path(config, session)}")
        return

    manifest_path = config.cache_root / args.manifest_name
    started = time.time()
    succeeded = 0
    failed = 0
    worker_payload = {
        "config": config_to_payload(config),
        "tickers_raw": args.tickers,
        "polars_threads_per_process": args.polars_threads_per_process,
    }
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.processes)) as executor:
        futures = {executor.submit(preprocess_session_worker, session, worker_payload): session for session in sessions}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            session = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "session": session,
                    "status": "failed",
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                }
            if result["status"] in {"ok", "skipped"}:
                succeeded += 1
            else:
                failed += 1
            append_jsonl(manifest_path, result)
            print(format_progress(result, completed, len(sessions), time.time() - started), flush=True)
            if args.fail_fast and result["status"] == "failed":
                raise SystemExit(f"Failed preprocessing {session}: {result.get('error')}")
    print(LOG_RULE)
    print(f"Done. sessions={len(sessions)} succeeded_or_skipped={succeeded} failed={failed}")
    print(f"Manifest: {manifest_path}")
    print(LOG_RULE)
    if failed:
        raise SystemExit(1)


def preprocess_session_worker(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    from research.inhouse_transformer.v22.config import DataConfig
    from research.inhouse_transformer.v22.data import (
        build_sparse_event_chunks,
        cached_event_chunk_path,
        parse_ticker_list,
    )
    import polars as pl

    config = payload_to_config(payload["config"])
    tickers = parse_ticker_list(payload["tickers_raw"])
    output_path = cached_event_chunk_path(config, session)
    if output_path.exists() and not config.rebuild_cache:
        rows = int(pl.scan_parquet(str(output_path)).select(pl.len()).collect().item())
        return result_row(session, "skipped", output_path, rows, time.time() - started)

    frame = build_sparse_event_chunks(config, session, tickers)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    frame.write_parquet(temp_path, compression="zstd")
    os.replace(temp_path, output_path)
    return result_row(session, "ok", output_path, frame.height, time.time() - started)


def result_row(session: str, status: str, output_path: Path, rows: int, elapsed: float) -> dict[str, Any]:
    return {
        "session": session,
        "status": status,
        "rows": rows,
        "size_bytes": output_path.stat().st_size if output_path.exists() else 0,
        "output_path": str(output_path),
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def config_to_payload(config: DataConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["flatfiles_root"] = str(payload["flatfiles_root"])
    payload["cache_root"] = str(payload["cache_root"])
    return payload


def payload_to_config(payload: dict[str, Any]) -> DataConfig:
    clean = dict(payload)
    clean["flatfiles_root"] = Path(clean["flatfiles_root"])
    clean["cache_root"] = Path(clean["cache_root"])
    clean["tickers"] = tuple(clean.get("tickers") or ())
    return DataConfig(**clean)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def format_progress(result: dict[str, Any], completed: int, total: int, elapsed: float) -> str:
    size_mb = float(result.get("size_bytes") or 0) / (1024.0 * 1024.0)
    return (
        f"[{completed:,}/{total:,}] {result.get('session')} {result.get('status')} "
        f"rows={int(result.get('rows') or 0):,} size_mb={size_mb:.1f} "
        f"session_seconds={float(result.get('elapsed_seconds') or 0):.1f} elapsed_minutes={elapsed / 60.0:.1f}"
    )


if __name__ == "__main__":
    main()

