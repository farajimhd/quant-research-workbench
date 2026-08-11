from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import select
import socket
import sys
import tempfile
import time
import urllib.parse
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\qmd_validation")


def get_json(url: str, timeout: float) -> tuple[dict[str, Any], int, float]:
    started = time.perf_counter()
    deadline = time.monotonic() + timeout
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("load validation supports explicit http URLs only")
    port = parsed.port or 80
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    request = (
        f"GET {target} HTTP/1.1\r\nHost: {parsed.hostname}:{port}\r\n"
        "Accept: application/json\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    chunks: list[bytes] = []
    with socket.create_connection((parsed.hostname, port), timeout=timeout) as connection:
        connection.sendall(request)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"request exceeded {timeout:.1f}s wall-clock deadline")
            readable, _, _ = select.select([connection], [], [], remaining)
            if not readable:
                raise TimeoutError(f"request exceeded {timeout:.1f}s wall-clock deadline")
            chunk = connection.recv(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    headers, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError("HTTP response omitted headers")
    status_line = headers.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    parts = status_line.split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if status != 200:
        raise RuntimeError(f"HTTP {status or 'invalid'}")
    if b"transfer-encoding: chunked" in headers.lower():
        body = decode_chunked_body(body)
    elapsed_ms = (time.perf_counter() - started) * 1_000
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")
    return payload, len(body), elapsed_ms


def decode_chunked_body(body: bytes) -> bytes:
    decoded = bytearray()
    remaining = body
    while remaining:
        size_line, separator, remaining = remaining.partition(b"\r\n")
        if not separator:
            raise RuntimeError("invalid chunked response")
        size = int(size_line.split(b";", 1)[0], 16)
        if size == 0:
            return bytes(decoded)
        if len(remaining) < size + 2:
            raise RuntimeError("truncated chunked response")
        decoded.extend(remaining[:size])
        remaining = remaining[size + 2 :]
    raise RuntimeError("chunked response omitted terminator")


def validate_scanner(payload: dict[str, Any]) -> None:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("scanner omitted rows")
    if int(payload.get("row_count") or 0) != len(rows):
        raise ValueError("scanner row_count does not match rows")


def validate_watchlists(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("watchlists"), list):
        raise ValueError("Watchlist runtime omitted watchlists")
    if not isinstance(payload.get("computation_requirements"), dict):
        raise ValueError("Watchlist runtime omitted computation requirements")


def validate_chart(payload: dict[str, Any]) -> None:
    bars = payload.get("bars")
    if not isinstance(bars, dict):
        raise ValueError("Canvas chart omitted bars")
    if not isinstance(bars.get("history"), list):
        raise ValueError("Canvas chart omitted bounded bar history")
    if payload.get("source") != "qmd-gateway":
        raise ValueError("Canvas chart source authority drifted")


def validate_planner(payload: dict[str, Any]) -> None:
    if int(payload.get("schema_version") or 0) < 1:
        raise ValueError("computation planner omitted schema version")
    if not isinstance(payload.get("authorities"), dict):
        raise ValueError("computation planner omitted authorities")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def bounded_health(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(payload.get("metrics") or {})
    lanes = list(dict(payload.get("operational") or {}).get("lanes") or [])
    return {
        "status": payload.get("status"),
        "running": payload.get("running"),
        "events_received": metrics.get("events_received"),
        "symbols_seen": metrics.get("symbols_seen"),
        "lanes": {
            str(row.get("key") or "unknown"): {
                key: row.get(key)
                for key in (
                    "state",
                    "pending_rows",
                    "max_pending_rows",
                    "successful_rows",
                    "failures",
                    "consecutive_failures",
                )
            }
            for row in lanes
            if isinstance(row, dict)
        },
    }


def write_report(root: Path, report: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = root / f"application_read_load_{suffix}.json"
    fd, temporary = tempfile.mkstemp(prefix=".read-load-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run bounded concurrent read load through application Scanner, Watchlist, Canvas, and planner APIs."
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--qmd-url", default="http://127.0.0.1:8795")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-p95-ms", type=float, default=5_000.0)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    if not 5 <= args.duration_seconds <= 900:
        print("read load FAILED  duration must be between 5 and 900 seconds", file=sys.stderr)
        return 1
    if not 1 <= args.concurrency <= 64:
        print("read load FAILED  concurrency must be between 1 and 64", file=sys.stderr)
        return 1

    backend = args.backend_url.rstrip("/")
    ticker = args.ticker.strip().upper()
    targets: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
        "scanner": (
            f"{backend}/api/real-live-trading/scanner?row_limit=250",
            validate_scanner,
        ),
        "watchlists": (
            f"{backend}/api/market-discovery/watchlists/runtime",
            validate_watchlists,
        ),
        "canvas_chart": (
            f"{backend}/api/trading/canvas-live-chart?symbol={ticker}&timeframe=1m&row_limit=500",
            validate_chart,
        ),
        "computation_planner": (
            f"{backend}/api/system/computation-requirements",
            validate_planner,
        ),
    }
    captured_at = datetime.now(UTC)
    try:
        backend_before, _, _ = get_json(f"{backend}/api/health", args.timeout_seconds)
        qmd_before, _, _ = get_json(
            f"{args.qmd_url.rstrip('/')}/health", args.timeout_seconds
        )
    except Exception as exc:
        print(f"read load FAILED  readiness preflight: {exc}", file=sys.stderr)
        return 1

    latencies: dict[str, list[float]] = defaultdict(list)
    response_bytes: dict[str, list[int]] = defaultdict(list)
    errors: dict[str, list[str]] = defaultdict(list)
    deadline = time.monotonic() + args.duration_seconds

    def exercise(name: str) -> tuple[str, float, int, str]:
        url, validator = targets[name]
        try:
            payload, size, elapsed = get_json(url, args.timeout_seconds)
            validator(payload)
            return name, elapsed, size, ""
        except Exception as exc:
            return name, 0.0, 0, str(exc)[:500]

    names = tuple(targets)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures: dict[concurrent.futures.Future[tuple[str, float, int, str]], str] = {}
        for index in range(args.concurrency):
            name = names[index % len(names)]
            futures[executor.submit(exercise, name)] = name
        while futures:
            done, _ = concurrent.futures.wait(
                futures,
                timeout=0.5,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                scheduled_name = futures.pop(future)
                name, elapsed, size, error = future.result()
                if error:
                    if len(errors[name]) < 25:
                        errors[name].append(error)
                else:
                    latencies[name].append(elapsed)
                    response_bytes[name].append(size)
                if time.monotonic() < deadline:
                    futures[executor.submit(exercise, scheduled_name)] = scheduled_name

    try:
        backend_after, _, _ = get_json(f"{backend}/api/health", args.timeout_seconds)
        qmd_after, _, _ = get_json(
            f"{args.qmd_url.rstrip('/')}/health", args.timeout_seconds
        )
    except Exception as exc:
        backend_after = {}
        qmd_after = {}
        errors["health"].append(str(exc)[:500])

    routes = {}
    failures = []
    for name in names:
        values = latencies[name]
        p95 = percentile(values, 0.95)
        routes[name] = {
            "success_count": len(values),
            "error_count": len(errors[name]),
            "errors": errors[name],
            "latency_ms": {
                "p50": percentile(values, 0.50),
                "p95": p95,
                "max": round(max(values), 3) if values else None,
            },
            "response_bytes": {
                "maximum": max(response_bytes[name]) if response_bytes[name] else None,
            },
        }
        if errors[name] or not values:
            failures.append(f"{name} had {len(errors[name])} errors and {len(values)} successes")
        if p95 is not None and p95 > args.maximum_p95_ms:
            failures.append(
                f"{name} p95 {p95:.1f} ms exceeds {args.maximum_p95_ms:.1f} ms"
            )
    if errors["health"]:
        failures.append("post-load health could not be read")
    report = {
        "schema_version": 1,
        "captured_at": captured_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not failures else "failed",
        "profile": {
            "duration_seconds": args.duration_seconds,
            "concurrency": args.concurrency,
            "maximum_p95_ms": args.maximum_p95_ms,
            "ticker": ticker,
        },
        "routes": routes,
        "health": {
            "backend_before": {"status": backend_before.get("status")},
            "backend_after": {"status": backend_after.get("status")},
            "qmd_before": bounded_health(qmd_before),
            "qmd_after": bounded_health(qmd_after),
        },
        "failures": failures,
    }
    destination = write_report(args.output_root, report)
    total = sum(row["success_count"] for row in routes.values())
    print(
        f"application read load {report['status'].upper()}  "
        f"requests={total} concurrency={args.concurrency} duration={args.duration_seconds:.0f}s"
    )
    for name, row in routes.items():
        print(
            f"{name:<20} ok={row['success_count']:<4} err={row['error_count']:<3} "
            f"p50={row['latency_ms']['p50']}ms p95={row['latency_ms']['p95']}ms"
        )
    print(f"report               {destination}")
    for failure in failures:
        print(f"failure  {failure}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
