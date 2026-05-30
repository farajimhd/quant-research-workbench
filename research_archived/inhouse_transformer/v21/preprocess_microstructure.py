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

from research.inhouse_transformer.v21.config import DataConfig  # noqa: E402


LOG_RULE = "*" * 96


def parse_args() -> argparse.Namespace:
    defaults = DataConfig()
    parser = argparse.ArgumentParser(
        description=(
            "Prebuild v21 unified 1-second quote/trade microstructure snapshots from Massive "
            "quotes_v1/trades_v1 flatfiles. The output is the Parquet cache consumed by v21 training."
        )
    )
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--cache-root", default=str(defaults.cache_root))
    parser.add_argument(
        "--start-date",
        default=defaults.train_start_date,
        help="First session date to preprocess.",
    )
    parser.add_argument(
        "--end-date",
        default=defaults.test_end_date,
        help="Last session date to preprocess. Defaults through the v21 test range.",
    )
    parser.add_argument("--tickers", default="ALL", help="Comma-separated tickers or ALL.")
    parser.add_argument("--processes", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 2)))
    parser.add_argument(
        "--polars-threads-per-process",
        type=int,
        default=2,
        help="Caps Polars worker threads inside each process to avoid CPU oversubscription.",
    )
    parser.add_argument("--session-start-hour-utc", type=int, default=defaults.session_start_hour_utc)
    parser.add_argument("--session-end-hour-utc", type=int, default=defaults.session_end_hour_utc)
    parser.add_argument(
        "--quote-size-lot-multiplier-before-2025-11-03",
        type=int,
        default=defaults.quote_size_lot_multiplier_before_2025_11_03,
    )
    parser.add_argument("--rebuild-cache", action="store_true", help="Overwrite existing cached session Parquet files.")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed session instead of continuing and reporting failures.",
    )
    parser.add_argument(
        "--manifest-name",
        default="preprocess_manifest.jsonl",
        help="JSONL progress file written under cache-root.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only list sessions and output paths.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))

    from research.inhouse_transformer.v21.data import available_sessions, cached_snapshot_path, parse_ticker_list

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
        session_start_hour_utc=args.session_start_hour_utc,
        session_end_hour_utc=args.session_end_hour_utc,
        quote_size_lot_multiplier_before_2025_11_03=args.quote_size_lot_multiplier_before_2025_11_03,
        rebuild_cache=args.rebuild_cache,
    )
    sessions = available_sessions(config.flatfiles_root, args.start_date, args.end_date)
    print(LOG_RULE)
    print("v21 microstructure preprocessing")
    print(f"flatfiles_root={config.flatfiles_root}")
    print(f"cache_root={config.cache_root}")
    print(f"sessions={sessions[0]} -> {sessions[-1]} count={len(sessions)}")
    print(f"tickers={args.tickers}")
    print(f"processes={args.processes} polars_threads_per_process={args.polars_threads_per_process}")
    print(f"rebuild_cache={args.rebuild_cache}")
    print(LOG_RULE)

    if args.dry_run:
        for session in sessions:
            print(f"{session}: {cached_snapshot_path(config, session)}")
        return

    config.cache_root.mkdir(parents=True, exist_ok=True)
    manifest_path = config.cache_root / args.manifest_name
    started = time.time()
    completed = 0
    succeeded = 0
    failed = 0

    worker_payload = {
        "config": config_to_payload(config),
        "tickers_raw": args.tickers,
        "polars_threads_per_process": args.polars_threads_per_process,
    }
    max_workers = max(1, args.processes)
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(preprocess_session_worker, session, worker_payload): session
            for session in sessions
        }
        for future in concurrent.futures.as_completed(futures):
            session = futures[future]
            completed += 1
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
            if result["status"] == "ok":
                succeeded += 1
            elif result["status"] == "skipped":
                succeeded += 1
            else:
                failed += 1
            append_jsonl(manifest_path, result)
            elapsed = max(1e-6, time.time() - started)
            print(format_progress(result, completed, len(sessions), elapsed), flush=True)
            if args.fail_fast and result["status"] == "failed":
                raise SystemExit(f"Failed preprocessing {session}: {result.get('error')}")

    elapsed = time.time() - started
    print(LOG_RULE)
    print(
        f"Done. sessions={len(sessions)} succeeded_or_skipped={succeeded} failed={failed} "
        f"elapsed_minutes={elapsed / 60.0:.2f}"
    )
    print(f"Manifest: {manifest_path}")
    print(LOG_RULE)
    if failed:
        raise SystemExit(1)


def preprocess_session_worker(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    from research.inhouse_transformer.v21.config import DataConfig
    from research.inhouse_transformer.v21.data import (
        build_sparse_one_second_snapshots,
        cached_snapshot_path,
        parse_ticker_list,
    )
    import polars as pl

    config = payload_to_config(payload["config"])
    tickers = parse_ticker_list(payload["tickers_raw"])
    output_path = cached_snapshot_path(config, session)
    if output_path.exists() and not config.rebuild_cache:
        rows = int(pl.scan_parquet(str(output_path)).select(pl.len()).collect().item())
        size_bytes = output_path.stat().st_size
        return {
            "session": session,
            "status": "skipped",
            "rows": rows,
            "size_bytes": size_bytes,
            "output_path": str(output_path),
            "elapsed_seconds": time.time() - started,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    frame = build_sparse_one_second_snapshots(config, session, tickers)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    frame.write_parquet(temp_path, compression="zstd")
    os.replace(temp_path, output_path)
    size_bytes = output_path.stat().st_size
    return {
        "session": session,
        "status": "ok",
        "rows": frame.height,
        "tickers": frame.select("ticker").n_unique() if not frame.is_empty() else 0,
        "size_bytes": size_bytes,
        "output_path": str(output_path),
        "elapsed_seconds": time.time() - started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def config_to_payload(config: DataConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key in ("flatfiles_root", "cache_root"):
        payload[key] = str(payload[key])
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
    rows = int(result.get("rows") or 0)
    size_mb = float(result.get("size_bytes") or 0) / (1024.0 * 1024.0)
    return (
        f"[{completed:,}/{total:,}] {result.get('session')} {result.get('status')} "
        f"rows={rows:,} size_mb={size_mb:.1f} "
        f"session_seconds={float(result.get('elapsed_seconds') or 0):.1f} "
        f"elapsed_minutes={elapsed / 60.0:.1f}"
    )


if __name__ == "__main__":
    main()

