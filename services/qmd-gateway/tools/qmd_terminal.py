#!/usr/bin/env python
"""Rich terminal monitor for qmd-gateway.

The monitor is intentionally a separate process. It reads the gateway's local
HTTP endpoints and never sits in the websocket ingest or ClickHouse write path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


try:
    from rich import box
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
except Exception:  # pragma: no cover - fallback is for environments without rich.
    box = None
    Group = None
    Live = None
    Panel = None
    Table = None


@dataclass
class PollState:
    base_url: str
    errors: list[str] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    prior_metrics: dict[str, Any] = field(default_factory=dict)
    prior_poll_ts: float = 0.0
    rates: dict[str, float] = field(default_factory=dict)
    updated_at: datetime | None = None


def main() -> int:
    args = parse_args()
    state = PollState(base_url=args.base_url.rstrip("/"))
    watch = [item.strip().upper() for item in args.watch.split(",") if item.strip()]
    if Live is None:
        return run_plain(args, state, watch)
    refresh = max(0.25, float(args.refresh_seconds))
    with Live(
        render_dashboard(state, watch),
        auto_refresh=False,
        screen=not args.no_screen,
        transient=False,
        vertical_overflow="crop",
    ) as live:
        while True:
            poll_once(state, watch, args.event_limit, args.timeout_seconds)
            live.update(render_dashboard(state, watch), refresh=True)
            time.sleep(refresh)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a live qmd-gateway Rich terminal dashboard.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8795", help="qmd-gateway HTTP base URL.")
    parser.add_argument("--watch", default="AAPL,NVDA,TSLA", help="Comma-separated tickers for compact event samples.")
    parser.add_argument("--event-limit", type=int, default=6, help="Recent compact events per watched ticker.")
    parser.add_argument("--refresh-seconds", type=float, default=1.0, help="Dashboard refresh interval.")
    parser.add_argument("--timeout-seconds", type=float, default=2.0, help="HTTP request timeout.")
    parser.add_argument("--no-screen", action="store_true", help="Do not use Rich alternate-screen mode.")
    return parser.parse_args()


def run_plain(args: argparse.Namespace, state: PollState, watch: list[str]) -> int:
    print("rich is not installed; falling back to plain qmd monitor output.", flush=True)
    while True:
        poll_once(state, watch, args.event_limit, args.timeout_seconds)
        payload = {
            "updated_at": state.updated_at.isoformat() if state.updated_at else "",
            "health": state.health,
            "metrics": state.metrics,
            "rates": state.rates,
            "errors": state.errors[-5:],
        }
        print(json.dumps(payload, sort_keys=True), flush=True)
        time.sleep(max(0.25, float(args.refresh_seconds)))


def poll_once(state: PollState, watch: list[str], event_limit: int, timeout: float) -> None:
    now = time.perf_counter()
    state.updated_at = datetime.now(UTC)
    state.health = get_json(state, "/health", timeout) or {}
    metrics = get_json(state, "/metrics", timeout) or {}
    update_rates(state, metrics, now)
    state.metrics = metrics
    state.samples = {}
    for ticker in watch:
        rows = get_json(state, f"/snapshot/compact-events/{ticker}?limit={max(1, event_limit)}", timeout)
        state.samples[ticker] = rows if isinstance(rows, list) else []


def get_json(state: PollState, path: str, timeout: float) -> Any:
    url = state.base_url + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        message = f"{datetime.now().strftime('%H:%M:%S')} {path}: {exc}"
        state.errors.append(message)
        state.errors = state.errors[-20:]
        return None


def update_rates(state: PollState, metrics: dict[str, Any], now: float) -> None:
    elapsed = now - state.prior_poll_ts if state.prior_poll_ts else 0.0
    rate_keys = [
        "ingest_events",
        "ingest_trades",
        "ingest_quotes",
        "compact_events_emitted",
        "compact_events_persisted",
        "bar_rows_emitted",
        "scanner_candidates_emitted",
    ]
    rates: dict[str, float] = {}
    if elapsed > 0:
        for key in rate_keys:
            current = as_float(metrics.get(key))
            prior = as_float(state.prior_metrics.get(key))
            rates[key + "_per_sec"] = max(0.0, (current - prior) / elapsed)
    state.rates = rates
    state.prior_metrics = dict(metrics)
    state.prior_poll_ts = now


def render_dashboard(state: PollState, watch: list[str]) -> Any:
    return Group(
        render_connection(state),
        render_ingest(state),
        render_queues(state),
        render_compact_events(state),
        render_recent_events(state, watch),
        render_messages(state),
    )


def render_connection(state: PollState) -> Any:
    health = state.health or {}
    metrics = state.metrics or {}
    config = health.get("config") if isinstance(health.get("config"), dict) else {}
    status = str(health.get("status") or "waiting")
    color = "green" if status == "running" else "yellow"
    table = kv_table()
    table.add_row("status", status)
    table.add_row("session", str(health.get("session_phase") or "-"))
    table.add_row("subscriptions", ", ".join(health.get("subscriptions") or []))
    table.add_row("bind", str(config.get("bind") or "-"))
    table.add_row("database", str(config.get("clickhouse_database") or "-"))
    table.add_row("uptime", format_ms(metrics.get("process_uptime_ms")))
    table.add_row("updated", state.updated_at.strftime("%H:%M:%S") if state.updated_at else "-")
    return Panel(table, title="QMD Gateway", box=box.ROUNDED, border_style=color, padding=(0, 1))


def render_ingest(state: PollState) -> Any:
    metrics = state.metrics or {}
    table = kv_table()
    table.add_row("events/sec", format_rate(state.rates.get("ingest_events_per_sec")))
    table.add_row("trades/sec", format_rate(state.rates.get("ingest_trades_per_sec")))
    table.add_row("quotes/sec", format_rate(state.rates.get("ingest_quotes_per_sec")))
    table.add_row("total events", format_int(metrics.get("ingest_events")))
    table.add_row("lag", format_ms(metrics.get("last_event_lag_ms")))
    table.add_row("last event", str(metrics.get("last_event_ts") or "-"))
    table.add_row("connect failures", format_int(metrics.get("massive_connect_failures")))
    table.add_row("disconnects", format_int(metrics.get("massive_disconnects")))
    table.add_row("parse failures", format_int(metrics.get("parse_failures")))
    return Panel(table, title="Ingest", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def render_queues(state: PollState) -> Any:
    metrics = state.metrics or {}
    table = kv_table()
    drops = [
        ("event broadcast", "events_broadcast_dropped"),
        ("compact queue", "compact_event_queue_dropped"),
        ("compact broadcast", "compact_event_broadcast_dropped"),
        ("bar events", "bar_events_dropped"),
        ("bar writer", "bar_rows_writer_dropped"),
        ("bar indicator", "bar_rows_indicator_dropped"),
        ("bar scanner", "bar_rows_scanner_dropped"),
        ("indicator events", "indicator_events_dropped"),
        ("raw clickhouse", "clickhouse_events_dropped"),
    ]
    total = 0
    for label, key in drops:
        value = int(as_float(metrics.get(key)))
        total += value
        table.add_row(label, format_int(value))
    color = "green" if total == 0 else "red"
    return Panel(table, title="Queues And Drops", box=box.ROUNDED, border_style=color, padding=(0, 1))


def render_compact_events(state: PollState) -> Any:
    metrics = state.metrics or {}
    table = kv_table()
    table.add_row("emitted/sec", format_rate(state.rates.get("compact_events_emitted_per_sec")))
    table.add_row("persisted/sec", format_rate(state.rates.get("compact_events_persisted_per_sec")))
    table.add_row("emitted", format_int(metrics.get("compact_events_emitted")))
    table.add_row("persisted", format_int(metrics.get("compact_events_persisted")))
    table.add_row("reorder pending", format_int(metrics.get("compact_events_reorder_pending")))
    table.add_row("reorder buffered", format_int(metrics.get("compact_events_reorder_buffered")))
    table.add_row("reorder flushed", format_int(metrics.get("compact_events_reorder_flushed")))
    table.add_row("forced flushes", format_int(metrics.get("compact_event_reorder_forced_flushes")))
    table.add_row("late arrivals", format_int(metrics.get("compact_event_reorder_late_arrivals")))
    table.add_row("rejected", format_int(metrics.get("compact_event_rejected")))
    return Panel(table, title="Compact Events", box=box.ROUNDED, border_style="magenta", padding=(0, 1))


def render_recent_events(state: PollState, watch: list[str]) -> Any:
    table = Table(box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("Ticker", width=8)
    table.add_column("Type", width=5)
    table.add_column("Ordinal", justify="right", width=10)
    table.add_column("SIP us", justify="right", width=18)
    table.add_column("Seq", justify="right", width=12)
    table.add_column("Primary", justify="right", width=12)
    table.add_column("Size", justify="right", width=10)
    for ticker in watch:
        rows = state.samples.get(ticker, [])
        for row in rows[-3:]:
            event_type = "Q" if int(as_float(row.get("event_type"))) == 0 else "T"
            table.add_row(
                ticker,
                event_type,
                format_int(row.get("ordinal")),
                format_int(row.get("sip_timestamp_us")),
                format_int(row.get("source_sequence")),
                format_int(row.get("price_primary_int")),
                format_int(row.get("size_primary")),
            )
    return Panel(table, title="Recent Compact Events", box=box.ROUNDED, border_style="blue", padding=(0, 1))


def render_messages(state: PollState) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Message")
    if state.errors:
        for message in state.errors[-6:]:
            table.add_row(message)
        color = "red"
    else:
        table.add_row("No monitor errors.")
        color = "green"
    return Panel(table, title="Messages", box=box.ROUNDED, border_style=color, padding=(0, 1))


def kv_table() -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Metric", justify="right", style="bold")
    table.add_column("Value", justify="left")
    return table


def as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_int(value: Any) -> str:
    return f"{int(as_float(value)):,}"


def format_rate(value: Any) -> str:
    return f"{as_float(value):,.1f}/s"


def format_ms(value: Any) -> str:
    number = as_float(value)
    if number <= 0:
        return "-"
    if number >= 60_000:
        return f"{number / 60_000:,.1f}m"
    if number >= 1_000:
        return f"{number / 1_000:,.1f}s"
    return f"{number:,.0f}ms"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nqmd terminal stopped.", file=sys.stderr)
        raise SystemExit(130)
