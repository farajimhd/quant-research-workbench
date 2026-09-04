#!/usr/bin/env python3
"""Persist bounded 1-second EMA/MACD warm-up seeds for historical sessions."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


@dataclass
class CampaignState:
    total: int
    active: dict[str, str] = field(default_factory=dict)
    completed: int = 0
    failed: int = 0
    insufficient: int = 0
    cache_hits: int = 0
    fetched_events: int = 0
    errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Warm and persist canonical 1s indicator history from imported events."
    )
    parser.add_argument("--session-date", required=True, help="Target exchange date, YYYY-MM-DD")
    parser.add_argument("--qmd-history-url", default="http://127.0.0.1:8801")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--required-bars", type=int, default=200)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument(
        "--runtime-root",
        default=r"D:\TradingML\runtimes\qmd_history_gateway\indicator-warmup-campaigns",
    )
    parser.add_argument("--no-ui", action="store_true", help="Emit line-oriented JSON progress")
    return parser.parse_args()


def request_json(url: str, *, payload: dict[str, Any] | None = None, timeout: float = 90) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def session_start_utc(session_date: date) -> str:
    return datetime.combine(session_date, clock_time(4, 0), tzinfo=NEW_YORK).astimezone(UTC).isoformat()


def load_tickers(base_url: str, session_date: date, explicit: list[str]) -> list[str]:
    if explicit:
        tickers = explicit
    else:
        query = urllib.parse.urlencode({"as_of": session_date.isoformat()})
        response = request_json(f"{base_url.rstrip('/')}/universe/tradable?{query}")
        tickers = list(response.get("tickers") or [])
    return sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})


def warm_one(
    base_url: str,
    ticker: str,
    session_start: str,
    required_bars: int,
    state: CampaignState,
) -> dict[str, Any]:
    with state.lock:
        state.active[ticker] = "requesting ordinal ranges"
    payload = {
        "ticker": ticker,
        "timeframe": "1s",
        "session_start": session_start,
        "required_bars": required_bars,
    }
    last_error = ""
    for attempt in range(1, 4):
        try:
            result = request_json(
                f"{base_url.rstrip('/')}/materialize/indicator-warmup",
                payload=payload,
                timeout=180,
            )
            result["ticker"] = ticker
            return result
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            detail = exc.read().decode("utf-8", errors="replace") if isinstance(exc, urllib.error.HTTPError) else str(exc)
            last_error = f"attempt {attempt}/3: {detail[:500]}"
            with state.lock:
                state.active[ticker] = last_error
            non_retryable_contract = any(
                marker in detail
                for marker in (
                    "execution-clock coverage incomplete",
                    "insufficient_history",
                    "invalid indicator warm-up",
                )
            )
            retryable = (
                not non_retryable_contract
                and (not isinstance(exc, urllib.error.HTTPError) or exc.code in {429, 502, 503, 504})
            )
            if attempt < 3 and retryable:
                time.sleep(min(4.0, 0.5 * 2 ** (attempt - 1)))
            else:
                break
    raise RuntimeError(last_error)


def write_manifest(path: Path, session_date: str, state: CampaignState, results: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with state.lock:
        payload = {
            "schema_version": 1,
            "session_date": session_date,
            "updated_at": datetime.now(UTC).isoformat(),
            "counts": {
                "total": state.total,
                "completed": state.completed,
                "insufficient": state.insufficient,
                "failed": state.failed,
                "cache_hits": state.cache_hits,
                "fetched_events": state.fetched_events,
            },
            "results": results,
        }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "bar_count": len(result.get("bars") or []),
        "required_bars": int(result.get("required_bars") or 0),
        "cache_hit": bool(result.get("cache_hit")),
        "fetched_events": int(result.get("fetched_events") or 0),
        "fetched_ordinal_ranges": int(result.get("fetched_ordinal_ranges") or 0),
        "authority_start": result.get("authority_start"),
        "source_revision": dict(result.get("source_revision") or {}),
    }


def render(state: CampaignState, started: float):
    from rich.console import Group
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    from rich.table import Table

    with state.lock:
        done = state.completed + state.insufficient + state.failed
        active = list(state.active.items())[:8]
        errors = list(state.errors[-4:])
        counters = (state.completed, state.insufficient, state.failed, state.cache_hits, state.fetched_events)
    progress = Progress(
        TextColumn("[bold]Tickers"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        expand=True,
    )
    progress.add_task("warm", total=state.total, completed=done)
    facts = Table.grid(expand=True)
    facts.add_column()
    facts.add_column()
    facts.add_column()
    facts.add_row(
        f"[green]Ready {counters[0]:,}[/green]",
        f"[yellow]Insufficient {counters[1]:,}[/yellow]",
        f"[red]Failed {counters[2]:,}[/red]",
    )
    facts.add_row(
        f"Cache hits {counters[3]:,}",
        f"Fetched trades {counters[4]:,}",
        f"Elapsed {time.monotonic() - started:,.1f}s",
    )
    activity = Table("Ticker", "Current operation", box=None, expand=True)
    for ticker, detail in active:
        activity.add_row(ticker, detail)
    if not active:
        activity.add_row("—", "waiting")
    sections: list[Any] = [progress, facts, Panel(activity, title="Active workers", border_style="cyan")]
    if errors:
        sections.append(Panel("\n".join(errors), title="Current failures", border_style="red"))
    return Group(*sections)


def main() -> int:
    args = parse_args()
    target_date = date.fromisoformat(args.session_date)
    workers = max(1, min(64, args.workers))
    required_bars = max(1, min(10_000, args.required_bars))
    tickers = load_tickers(args.qmd_history_url, target_date, args.ticker)
    if not tickers:
        raise RuntimeError("The point-in-time tradable universe is empty")
    state = CampaignState(total=len(tickers))
    started = time.monotonic()
    results: dict[str, Any] = {}
    manifest = Path(args.runtime_root) / f"indicator-warmup-{target_date.isoformat()}-1s.json"
    start_utc = session_start_utc(target_date)

    live = None
    if not args.no_ui:
        from rich.live import Live

        live = Live(render(state, started), refresh_per_second=4, transient=False)
        live.start()
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="indicator-warmup") as pool:
            ticker_iter = iter(tickers)
            pending: dict[Future[dict[str, Any]], str] = {}

            def submit_next() -> bool:
                ticker = next(ticker_iter, None)
                if ticker is None:
                    return False
                future = pool.submit(
                    warm_one,
                    args.qmd_history_url,
                    ticker,
                    start_utc,
                    required_bars,
                    state,
                )
                pending[future] = ticker
                return True

            for _ in range(min(len(tickers), workers * 2)):
                submit_next()
            results_since_manifest = 0
            last_manifest_at = time.monotonic()
            while pending:
                finished, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for future in finished:
                    ticker = pending.pop(future)
                    with state.lock:
                        state.active.pop(ticker, None)
                    try:
                        result = future.result()
                        results[ticker] = result_summary(result)
                        with state.lock:
                            if result.get("status") == "ready":
                                state.completed += 1
                            else:
                                state.insufficient += 1
                            state.cache_hits += int(bool(result.get("cache_hit")))
                            state.fetched_events += int(result.get("fetched_events") or 0)
                    except Exception as exc:  # one ticker must not abort the campaign
                        message = f"{ticker}: {exc}"
                        results[ticker] = {"status": "failed", "error": str(exc)}
                        with state.lock:
                            state.failed += 1
                            state.errors.append(message[:700])
                    results_since_manifest += 1
                    submit_next()
                now = time.monotonic()
                if results_since_manifest and (results_since_manifest >= 25 or now - last_manifest_at >= 2.0):
                    write_manifest(manifest, target_date.isoformat(), state, results)
                    results_since_manifest = 0
                    last_manifest_at = now
                if live is not None:
                    live.update(render(state, started), refresh=True)
                elif finished:
                    print(json.dumps({"completed": state.completed, "failed": state.failed, "insufficient": state.insufficient, "total": state.total}))
    except KeyboardInterrupt:
        if live is not None:
            live.stop()
        write_manifest(manifest, target_date.isoformat(), state, results)
        print(f"Interrupted safely; resumable manifest: {manifest}")
        return 130
    finally:
        if live is not None:
            live.update(render(state, started), refresh=True)
            live.stop()
    write_manifest(manifest, target_date.isoformat(), state, results)
    print(f"Manifest: {manifest}")
    return 1 if state.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
