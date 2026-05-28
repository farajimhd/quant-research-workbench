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


DEFAULT_CHUNK_MS = (100, 250, 500, 1000)
DEFAULT_CAPS = (64, 128, 256, 512)


def parse_args() -> argparse.Namespace:
    defaults = DataConfig()
    parser = argparse.ArgumentParser(description="Profile quote/trade event counts per fixed-time chunk for v22.")
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--start-date", default=defaults.train_start_date)
    parser.add_argument("--end-date", default=defaults.validation_end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--chunk-ms", default=",".join(str(value) for value in DEFAULT_CHUNK_MS))
    parser.add_argument("--caps", default=",".join(str(value) for value in DEFAULT_CAPS))
    parser.add_argument(
        "--max-profile-sessions",
        type=int,
        default=4,
        help="Maximum quote/trade sessions to profile. Use 0 to profile every available session.",
    )
    parser.add_argument("--processes", type=int, default=max(1, min(2, (os.cpu_count() or 4) // 2)))
    parser.add_argument("--polars-threads-per-process", type=int, default=2)
    parser.add_argument("--session-start-hour-utc", type=int, default=defaults.session_start_hour_utc)
    parser.add_argument("--session-end-hour-utc", type=int, default=defaults.session_end_hour_utc)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    from research.inhouse_transformer.v22.data import available_sessions, parse_ticker_list

    chunk_ms_values = parse_int_list(args.chunk_ms)
    caps = parse_int_list(args.caps)
    config_payload = asdict(
        DataConfig(
            flatfiles_root=Path(args.flatfiles_root),
            train_start_date=args.start_date,
            train_end_date=args.end_date,
            tickers=parse_ticker_list(args.tickers),
            session_start_hour_utc=args.session_start_hour_utc,
            session_end_hour_utc=args.session_end_hour_utc,
        )
    )
    config_payload["flatfiles_root"] = str(config_payload["flatfiles_root"])
    config_payload["cache_root"] = str(config_payload["cache_root"])
    available = available_sessions(Path(args.flatfiles_root), args.start_date, args.end_date)
    sessions = available[: args.max_profile_sessions] if args.max_profile_sessions > 0 else available
    if not sessions:
        raise SystemExit(
            f"No sessions found under {args.flatfiles_root} from {args.start_date} to {args.end_date}."
        )
    if args.max_profile_sessions > 0 and len(available) > len(sessions):
        print(
            f"Profiling first {len(sessions)} of {len(available)} available sessions. "
            "Set --max-profile-sessions 0 to profile all sessions.",
            flush=True,
        )
    started = time.time()
    results = []
    failures = []
    worker_payload = {
        "config": config_payload,
        "tickers_raw": args.tickers,
        "chunk_ms_values": chunk_ms_values,
        "caps": caps,
        "polars_threads_per_process": args.polars_threads_per_process,
    }
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.processes)) as executor:
        futures = {executor.submit(profile_session_worker, session, worker_payload): session for session in sessions}
        for idx, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            session = futures[future]
            try:
                result = future.result()
            except BaseException:
                result = {
                    "session": session,
                    "rows": 0,
                    "chunk_profiles": {},
                    "error": traceback.format_exc(),
                }
            if result.get("error"):
                failures.append(result)
                print(f"[{idx}/{len(sessions)}] {result['session']} FAILED", flush=True)
                print(result["error"], flush=True)
                continue
            results.append(result)
            print(f"[{idx}/{len(sessions)}] {result['session']} rows={result.get('rows', 0):,}", flush=True)
    if not results:
        failure_preview = "\n\n".join(
            f"{failure['session']}:\n{failure.get('error', '')}" for failure in failures[:5]
        )
        raise SystemExit(f"All profile sessions failed. First failures:\n{failure_preview}")
    report = combine_reports(results, chunk_ms_values, caps)
    report["created_at"] = datetime.now().isoformat(timespec="seconds")
    report["elapsed_seconds"] = time.time() - started
    report["sessions"] = sessions
    report["available_sessions"] = available
    report["max_profile_sessions"] = args.max_profile_sessions
    report["successful_sessions"] = [result["session"] for result in results]
    report["failed_sessions"] = failures
    report["flatfiles_root"] = args.flatfiles_root
    report["tickers"] = args.tickers
    output = Path(args.output) if args.output else Path(args.flatfiles_root) / "derived" / "event_chunks_v1" / "profile_event_chunks_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["recommendation"], indent=2), flush=True)
    print(f"Profile report: {output}", flush=True)


def profile_session_worker(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
        from research.inhouse_transformer.v22.config import DataConfig
        from research.inhouse_transformer.v22.data import parse_ticker_list

        clean = dict(payload["config"])
        clean["flatfiles_root"] = Path(clean["flatfiles_root"])
        clean["cache_root"] = Path(clean["cache_root"])
        clean["tickers"] = parse_ticker_list(payload["tickers_raw"])
        config = DataConfig(**clean)
        rows: dict[str, Any] = {"session": session, "rows": 0, "chunk_profiles": {}}
        for chunk_ms in payload["chunk_ms_values"]:
            quote_counts = event_counts(config, session, "quotes", int(chunk_ms), "quote_count")
            trade_counts = event_counts(config, session, "trades", int(chunk_ms), "trade_count")
            if rows["rows"] <= 0:
                rows["rows"] = int(quote_counts.get_column("quote_count").sum() or 0) + int(
                    trade_counts.get_column("trade_count").sum() or 0
                )
            rows["chunk_profiles"][str(chunk_ms)] = profile_chunk_counts(quote_counts, trade_counts, payload["caps"])
        return rows
    except KeyboardInterrupt:
        raise
    except BaseException:
        return {
            "session": session,
            "rows": 0,
            "chunk_profiles": {},
            "error": traceback.format_exc(),
        }


def event_counts(config: Any, session: str, kind: str, chunk_ms: int, count_name: str) -> Any:
    import polars as pl
    from research.inhouse_transformer.v22.data import (
        NANOSECONDS_PER_SECOND,
        collect_lazy,
        find_flatfile,
        header_columns,
        uses_all_tickers,
    )

    path = find_flatfile(config.flatfiles_root, kind, session)
    if path is None:
        raise FileNotFoundError(f"Missing {kind} flatfile for {session} under {config.flatfiles_root}.")
    names = header_columns(path)
    missing = sorted({"ticker", "sip_timestamp"} - names)
    if missing:
        raise SystemExit(f"{kind} flatfile {path} is missing required columns for profiling: {missing}")
    chunk_ns = int(chunk_ms) * 1_000_000
    day_ns = pl.col("sip_timestamp").cast(pl.Int64, strict=False) % (24 * 3600 * NANOSECONDS_PER_SECOND)
    start_ns = config.session_start_hour_utc * 3600 * NANOSECONDS_PER_SECOND
    end_ns = config.session_end_hour_utc * 3600 * NANOSECONDS_PER_SECOND
    scan = pl.scan_csv(str(path), infer_schema_length=0, ignore_errors=True).select(["ticker", "sip_timestamp"])
    if config.tickers and not uses_all_tickers(config.tickers):
        scan = scan.filter(pl.col("ticker").str.to_uppercase().is_in(list(config.tickers)))
    return (
        scan.filter((day_ns >= start_ns) & (day_ns < end_ns))
        .with_columns(
            pl.col("ticker").cast(pl.String).str.to_uppercase(),
            pl.col("sip_timestamp").cast(pl.Int64, strict=False),
        )
        .filter(pl.col("sip_timestamp").is_not_null())
        .with_columns(((pl.col("sip_timestamp") // chunk_ns) * chunk_ns).alias("chunk_start_ns"))
        .group_by(["ticker", "chunk_start_ns"])
        .agg(pl.len().alias(count_name))
        .pipe(collect_lazy)
    )


def profile_chunk_counts(quote_counts: Any, trade_counts: Any, caps: list[int]) -> dict[str, Any]:
    import polars as pl

    if quote_counts.is_empty() and trade_counts.is_empty():
        return {}
    counts = quote_counts.join(trade_counts, on=["ticker", "chunk_start_ns"], how="full", coalesce=True).with_columns(
        pl.col("quote_count").fill_null(0),
        pl.col("trade_count").fill_null(0),
    ).with_columns((pl.col("quote_count") + pl.col("trade_count")).alias("total_count"))
    total_chunks = counts.height
    by_ticker = counts.group_by("ticker").agg(
        pl.sum("quote_count").alias("quote_count"),
        pl.sum("trade_count").alias("trade_count"),
        pl.sum("total_count").alias("total_count"),
    )
    return {
        "total_chunks_with_events": total_chunks,
        "quote_events_per_chunk": quantiles(counts.get_column("quote_count").to_numpy()),
        "trade_events_per_chunk": quantiles(counts.get_column("trade_count").to_numpy()),
        "total_events_per_chunk": quantiles(counts.get_column("total_count").to_numpy()),
        "overflow_rates": {
            str(cap): float((counts.get_column("total_count").to_numpy() > cap).mean() * 100.0)
            for cap in caps
        },
        "top_tickers": by_ticker.sort("total_count", descending=True).head(50).to_dicts(),
    }


def quantiles(values: Any) -> dict[str, float]:
    import numpy as np

    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return {key: 0.0 for key in ("p50", "p90", "p95", "p99", "max", "mean")}
    return {
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
    }


def combine_reports(results: list[dict[str, Any]], chunk_ms_values: list[int], caps: list[int]) -> dict[str, Any]:
    combined: dict[str, Any] = {"chunk_profiles": {}, "session_reports": results}
    best = None
    for chunk_ms in chunk_ms_values:
        total_chunks = sum((result["chunk_profiles"].get(str(chunk_ms)) or {}).get("total_chunks_with_events", 0) for result in results)
        p99_values = [
            (result["chunk_profiles"].get(str(chunk_ms)) or {}).get("total_events_per_chunk", {}).get("p99", 0.0)
            for result in results
        ]
        max_values = [
            (result["chunk_profiles"].get(str(chunk_ms)) or {}).get("total_events_per_chunk", {}).get("max", 0.0)
            for result in results
        ]
        overflow = {
            str(cap): weighted_overflow(results, chunk_ms, cap)
            for cap in caps
        }
        combined["chunk_profiles"][str(chunk_ms)] = {
            "total_chunks_with_events": total_chunks,
            "mean_session_p99_total_events": sum(p99_values) / max(1, len(p99_values)),
            "max_total_events": max(max_values) if max_values else 0.0,
            "overflow_rates_pct": overflow,
        }
        for cap in caps:
            overflow_rate = overflow[str(cap)]
            score = overflow_rate + (chunk_ms / 1000.0)
            if overflow_rate <= 1.0 and (best is None or score < best["score"]):
                best = {"chunk_ms": chunk_ms, "max_total_events": cap, "score": score, "overflow_pct": overflow_rate}
    if best is None:
        best = {"chunk_ms": 250, "max_total_events": 128, "score": None, "overflow_pct": None}
    best["max_quote_events"] = max(1, int(best["max_total_events"] * 0.75))
    best["max_trade_events"] = max(1, int(best["max_total_events"] * 0.5))
    combined["recommendation"] = best
    return combined


def weighted_overflow(results: list[dict[str, Any]], chunk_ms: int, cap: int) -> float:
    weighted = 0.0
    total = 0.0
    for result in results:
        profile = result["chunk_profiles"].get(str(chunk_ms)) or {}
        chunks = float(profile.get("total_chunks_with_events", 0))
        rate = float((profile.get("overflow_rates") or {}).get(str(cap), 0.0))
        weighted += chunks * rate
        total += chunks
    return weighted / max(1.0, total)


def parse_int_list(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


if __name__ == "__main__":
    main()
