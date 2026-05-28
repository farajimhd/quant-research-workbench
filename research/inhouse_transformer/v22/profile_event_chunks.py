from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
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
    parser.add_argument("--processes", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 2)))
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
    sessions = available_sessions(Path(args.flatfiles_root), args.start_date, args.end_date)
    started = time.time()
    results = []
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
            result = future.result()
            results.append(result)
            print(f"[{idx}/{len(sessions)}] {result['session']} rows={result.get('rows', 0):,}", flush=True)
    report = combine_reports(results, chunk_ms_values, caps)
    report["created_at"] = datetime.now().isoformat(timespec="seconds")
    report["elapsed_seconds"] = time.time() - started
    report["sessions"] = sessions
    report["flatfiles_root"] = args.flatfiles_root
    report["tickers"] = args.tickers
    output = Path(args.output) if args.output else Path(args.flatfiles_root) / "derived" / "event_chunks_v1" / "profile_event_chunks_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["recommendation"], indent=2), flush=True)
    print(f"Profile report: {output}", flush=True)


def profile_session_worker(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    from research.inhouse_transformer.v22.config import DataConfig
    from research.inhouse_transformer.v22.data import parse_ticker_list, read_quotes, read_trades

    clean = dict(payload["config"])
    clean["flatfiles_root"] = Path(clean["flatfiles_root"])
    clean["cache_root"] = Path(clean["cache_root"])
    clean["tickers"] = parse_ticker_list(payload["tickers_raw"])
    config = DataConfig(**clean)
    quotes = read_quotes(config, session, config.tickers)
    trades = read_trades(config, session, config.tickers)
    rows: dict[str, Any] = {"session": session, "rows": int(quotes.height + trades.height), "chunk_profiles": {}}
    for chunk_ms in payload["chunk_ms_values"]:
        rows["chunk_profiles"][str(chunk_ms)] = profile_chunk_size(quotes, trades, int(chunk_ms), payload["caps"])
    return rows


def profile_chunk_size(quotes: Any, trades: Any, chunk_ms: int, caps: list[int]) -> dict[str, Any]:
    import polars as pl

    chunk_ns = int(chunk_ms) * 1_000_000
    quote_counts = grouped_counts(quotes, chunk_ns, "quote_count")
    trade_counts = grouped_counts(trades, chunk_ns, "trade_count")
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


def grouped_counts(frame: Any, chunk_ns: int, name: str) -> Any:
    import polars as pl

    if frame.is_empty():
        return pl.DataFrame({"ticker": [], "chunk_start_ns": [], name: []})
    return (
        frame.with_columns(((pl.col("sip_timestamp") // chunk_ns) * chunk_ns).alias("chunk_start_ns"))
        .group_by(["ticker", "chunk_start_ns"])
        .agg(pl.len().alias(name))
    )


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
