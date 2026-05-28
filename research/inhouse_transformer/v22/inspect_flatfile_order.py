from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_FLATFILES_ROOT = Path("D:/market-data/flatfiles/us_stocks_sip")
DEFAULT_KINDS = ("quotes", "trades")
ORDER_NAMES = (
    "ticker_time_sequence",
    "time_ticker_sequence",
    "time_sequence",
    "time_only",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream raw SIP quote/trade CSV.GZ files and report whether their physical row order is useful "
            "for memory-efficient preprocessing."
        )
    )
    parser.add_argument("--flatfiles-root", default=str(DEFAULT_FLATFILES_ROOT))
    parser.add_argument("--start-date", default="2025-11-01")
    parser.add_argument("--end-date", default="2025-11-30")
    parser.add_argument("--kinds", default=",".join(DEFAULT_KINDS), help="Comma list: quotes,trades")
    parser.add_argument("--max-files-per-kind", type=int, default=2, help="Use 0 to scan every file in range.")
    parser.add_argument("--progress-rows", type=int, default=1_000_000)
    parser.add_argument("--progress-seconds", type=float, default=10.0)
    parser.add_argument("--max-violations", type=int, default=5)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    flatfiles_root = Path(args.flatfiles_root)
    kinds = tuple(clean for clean in (item.strip().lower() for item in args.kinds.split(",")) if clean)
    sessions = session_range(args.start_date, args.end_date)
    report: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "flatfiles_root": str(flatfiles_root),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "kinds": kinds,
        "max_files_per_kind": args.max_files_per_kind,
        "files": [],
    }
    print(
        f"Inspecting flatfile order root={flatfiles_root} dates={args.start_date}->{args.end_date} "
        f"kinds={','.join(kinds)} max_files_per_kind={args.max_files_per_kind}",
        flush=True,
    )
    for kind in kinds:
        paths = find_files(flatfiles_root, kind, sessions)
        if args.max_files_per_kind > 0:
            paths = paths[: args.max_files_per_kind]
        if not paths:
            print(f"{kind}: no files found", flush=True)
            continue
        for index, path in enumerate(paths, start=1):
            print("=" * 96, flush=True)
            print(f"{kind} [{index}/{len(paths)}] {path}", flush=True)
            result = inspect_file(
                path,
                kind=kind,
                progress_rows=max(1, args.progress_rows),
                progress_seconds=max(1.0, args.progress_seconds),
                max_violations=max(1, args.max_violations),
            )
            report["files"].append(result)
            print_summary(result)
    output = Path(args.output) if args.output else flatfiles_root / "derived" / "flatfile_order_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print("=" * 96, flush=True)
    print(f"Order report: {output}", flush=True)


def inspect_file(
    path: Path,
    *,
    kind: str,
    progress_rows: int,
    progress_seconds: float,
    max_violations: int,
) -> dict[str, Any]:
    started = time.time()
    compressed_size = path.stat().st_size
    result: dict[str, Any] = {
        "kind": kind,
        "path": str(path),
        "compressed_size_bytes": compressed_size,
        "rows": 0,
        "bad_rows": 0,
        "unique_tickers": 0,
        "ticker_switches": 0,
        "ticker_contiguous": True,
        "first_repeated_ticker_after_leave": None,
        "min_sip_timestamp": None,
        "max_sip_timestamp": None,
        "orders": {
            name: {"sorted": True, "violation_count": 0, "first_violations": []}
            for name in ORDER_NAMES
        },
        "per_ticker_time_sequence": {"sorted": True, "violation_count": 0, "first_violations": []},
    }
    previous_keys: dict[str, tuple[Any, ...] | None] = {name: None for name in ORDER_NAMES}
    previous_ticker_keys: dict[str, tuple[int, int]] = {}
    seen_tickers: set[str] = set()
    closed_tickers: set[str] = set()
    current_ticker: str | None = None
    last_progress_rows = 0
    last_progress_time = started
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            result["elapsed_seconds"] = time.time() - started
            result["error"] = "empty file"
            return result
        indexes = required_indexes(header, path)
        for raw_row in reader:
            result["rows"] += 1
            try:
                ticker = raw_row[indexes["ticker"]].strip().upper()
                ts = int(raw_row[indexes["sip_timestamp"]])
                seq = int(raw_row[indexes["sequence_number"]]) if indexes["sequence_number"] is not None else 0
            except (IndexError, TypeError, ValueError):
                result["bad_rows"] += 1
                continue
            if not ticker:
                result["bad_rows"] += 1
                continue
            update_timestamp_bounds(result, ts)
            seen_tickers.add(ticker)
            update_ticker_contiguity(result, ticker, seen_tickers, closed_tickers, current_ticker)
            if ticker != current_ticker:
                if current_ticker is not None:
                    closed_tickers.add(current_ticker)
                current_ticker = ticker
            check_order(
                result["orders"]["ticker_time_sequence"],
                previous_keys,
                "ticker_time_sequence",
                (ticker, ts, seq),
                result["rows"],
                ticker,
                ts,
                seq,
                max_violations,
            )
            check_order(
                result["orders"]["time_ticker_sequence"],
                previous_keys,
                "time_ticker_sequence",
                (ts, ticker, seq),
                result["rows"],
                ticker,
                ts,
                seq,
                max_violations,
            )
            check_order(
                result["orders"]["time_sequence"],
                previous_keys,
                "time_sequence",
                (ts, seq),
                result["rows"],
                ticker,
                ts,
                seq,
                max_violations,
            )
            check_order(
                result["orders"]["time_only"],
                previous_keys,
                "time_only",
                (ts,),
                result["rows"],
                ticker,
                ts,
                seq,
                max_violations,
            )
            previous_for_ticker = previous_ticker_keys.get(ticker)
            current_for_ticker = (ts, seq)
            if previous_for_ticker is not None and current_for_ticker < previous_for_ticker:
                add_violation(
                    result["per_ticker_time_sequence"],
                    result["rows"],
                    ticker,
                    ts,
                    seq,
                    previous_for_ticker,
                    max_violations,
                )
            previous_ticker_keys[ticker] = current_for_ticker
            now = time.time()
            if result["rows"] - last_progress_rows >= progress_rows or now - last_progress_time >= progress_seconds:
                print_progress(result, started, compressed_size)
                last_progress_rows = result["rows"]
                last_progress_time = now
    result["unique_tickers"] = len(seen_tickers)
    result["elapsed_seconds"] = time.time() - started
    result["rows_per_second"] = result["rows"] / max(1e-9, result["elapsed_seconds"])
    return result


def required_indexes(header: list[str], path: Path) -> dict[str, int | None]:
    names = {name.strip(): index for index, name in enumerate(header)}
    missing = sorted({"ticker", "sip_timestamp"} - set(names))
    if missing:
        raise SystemExit(f"{path} is missing required columns: {missing}")
    return {
        "ticker": names["ticker"],
        "sip_timestamp": names["sip_timestamp"],
        "sequence_number": names.get("sequence_number"),
    }


def update_timestamp_bounds(result: dict[str, Any], ts: int) -> None:
    result["min_sip_timestamp"] = ts if result["min_sip_timestamp"] is None else min(result["min_sip_timestamp"], ts)
    result["max_sip_timestamp"] = ts if result["max_sip_timestamp"] is None else max(result["max_sip_timestamp"], ts)


def update_ticker_contiguity(
    result: dict[str, Any],
    ticker: str,
    seen_tickers: set[str],
    closed_tickers: set[str],
    current_ticker: str | None,
) -> None:
    if ticker == current_ticker:
        return
    if current_ticker is not None:
        result["ticker_switches"] += 1
    if ticker in closed_tickers:
        result["ticker_contiguous"] = False
        if result["first_repeated_ticker_after_leave"] is None:
            result["first_repeated_ticker_after_leave"] = {
                "row": result["rows"],
                "ticker": ticker,
            }


def check_order(
    order_result: dict[str, Any],
    previous_keys: dict[str, tuple[Any, ...] | None],
    order_name: str,
    key: tuple[Any, ...],
    row_number: int,
    ticker: str,
    ts: int,
    seq: int,
    max_violations: int,
) -> None:
    previous = previous_keys[order_name]
    if previous is not None and key < previous:
        add_violation(order_result, row_number, ticker, ts, seq, previous, max_violations)
    previous_keys[order_name] = key


def add_violation(
    order_result: dict[str, Any],
    row_number: int,
    ticker: str,
    ts: int,
    seq: int,
    previous_key: tuple[Any, ...],
    max_violations: int,
) -> None:
    order_result["sorted"] = False
    order_result["violation_count"] += 1
    if len(order_result["first_violations"]) < max_violations:
        order_result["first_violations"].append(
            {
                "row": row_number,
                "ticker": ticker,
                "sip_timestamp": ts,
                "sequence_number": seq,
                "previous_key": list(previous_key),
            }
        )


def print_progress(result: dict[str, Any], started: float, compressed_size: int) -> None:
    elapsed = time.time() - started
    rate = result["rows"] / max(1e-9, elapsed)
    mib = compressed_size / (1024.0 * 1024.0)
    order_flags = " ".join(
        f"{name}={'OK' if value['sorted'] else 'BAD'}"
        for name, value in result["orders"].items()
    )
    print(
        f"{Path(result['path']).name} rows={result['rows']:,} bad={result['bad_rows']:,} "
        f"rate={rate:,.0f}/s compressed_mib={mib:,.1f} contiguous={result['ticker_contiguous']} "
        f"{order_flags}",
        flush=True,
    )


def print_summary(result: dict[str, Any]) -> None:
    print(
        f"SUMMARY {Path(result['path']).name}: rows={result['rows']:,} bad={result['bad_rows']:,} "
        f"tickers={result['unique_tickers']:,} switches={result['ticker_switches']:,} "
        f"contiguous={result['ticker_contiguous']} elapsed={result.get('elapsed_seconds', 0.0):.1f}s",
        flush=True,
    )
    for name, value in result["orders"].items():
        print(f"  {name}: sorted={value['sorted']} violations={value['violation_count']:,}", flush=True)
    per_ticker = result["per_ticker_time_sequence"]
    print(
        f"  per_ticker_time_sequence: sorted={per_ticker['sorted']} "
        f"violations={per_ticker['violation_count']:,}",
        flush=True,
    )


def find_files(flatfiles_root: Path, kind: str, sessions: list[str]) -> list[Path]:
    folder = "quotes_v1" if kind in {"quote", "quotes"} else "trades_v1"
    found = []
    for session in sessions:
        year = session[:4]
        month = session[5:7]
        path = flatfiles_root / folder / year / month / f"{session}.csv.gz"
        if path.exists():
            found.append(path)
    return found


def session_range(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    sessions = []
    while current <= final:
        sessions.append(current.isoformat())
        current += timedelta(days=1)
    return sessions


if __name__ == "__main__":
    main()
