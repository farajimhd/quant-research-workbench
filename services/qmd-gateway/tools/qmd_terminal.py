#!/usr/bin/env python
"""Rich terminal monitor for qmd-gateway.

The monitor is intentionally a separate process. It reads the gateway's local
HTTP endpoints and never sits in the websocket ingest or ClickHouse write path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.gateway_core.market_calendar import MassiveMarketHoursClient  # noqa: E402

try:
    from rich import box
    from rich.columns import Columns
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except Exception:  # pragma: no cover - fallback is for environments without rich.
    box = None
    Columns = None
    Group = None
    Live = None
    Panel = None
    Table = None
    Text = None


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


@dataclass
class PollState:
    base_url: str
    errors: list[str] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    maintenance: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    market_status: dict[str, Any] = field(default_factory=dict)
    market_status_error: str = ""
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    prior_metrics: dict[str, Any] = field(default_factory=dict)
    prior_poll_ts: float = 0.0
    last_market_status_poll: float = 0.0
    rates: dict[str, float] = field(default_factory=dict)
    refresh_seconds: float = 1.0
    updated_at: datetime | None = None


def main() -> int:
    args = parse_args()
    state = PollState(
        base_url=args.base_url.rstrip("/"),
        refresh_seconds=max(0.25, float(args.refresh_seconds)),
    )
    watch = [item.strip().upper() for item in args.watch.split(",") if item.strip()]
    if Live is None:
        return run_plain(args, state, watch)
    with Live(
        render_dashboard(state, watch),
        auto_refresh=False,
        screen=not args.no_screen,
        transient=False,
        vertical_overflow="crop",
        refresh_per_second=4,
    ) as live:
        while True:
            poll_once(
                state,
                watch,
                args.event_limit,
                args.timeout_seconds,
                args.market_status_seconds,
                args.disable_market_status,
            )
            live.update(render_dashboard(state, watch), refresh=True)
            time.sleep(state.refresh_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a live qmd-gateway Rich terminal dashboard.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8795", help="qmd-gateway HTTP base URL.")
    parser.add_argument("--watch", default="AAPL,NVDA,TSLA", help="Comma-separated tickers for compact event samples.")
    parser.add_argument("--event-limit", type=int, default=6, help="Recent compact events per watched ticker.")
    parser.add_argument("--refresh-seconds", type=float, default=1.0, help="Dashboard refresh interval.")
    parser.add_argument("--timeout-seconds", type=float, default=2.0, help="HTTP request timeout.")
    parser.add_argument(
        "--market-status-seconds",
        type=float,
        default=10.0,
        help="Massive market-status polling interval for the terminal.",
    )
    parser.add_argument("--disable-market-status", action="store_true", help="Do not call Massive market status.")
    parser.add_argument("--no-screen", action="store_true", help="Do not use Rich alternate-screen mode.")
    return parser.parse_args()


def run_plain(args: argparse.Namespace, state: PollState, watch: list[str]) -> int:
    print("rich is not installed; falling back to plain qmd monitor output.", flush=True)
    while True:
        poll_once(
            state,
            watch,
            args.event_limit,
            args.timeout_seconds,
            args.market_status_seconds,
            args.disable_market_status,
        )
        payload = {
            "updated_at": state.updated_at.isoformat() if state.updated_at else "",
            "health": state.health,
            "metrics": state.metrics,
            "maintenance": state.maintenance,
            "coverage": state.coverage,
            "market_status": state.market_status,
            "rates": state.rates,
            "errors": state.errors[-5:],
        }
        print(json.dumps(payload, sort_keys=True), flush=True)
        time.sleep(state.refresh_seconds)


def poll_once(
    state: PollState,
    watch: list[str],
    event_limit: int,
    timeout: float,
    market_status_seconds: float,
    disable_market_status: bool,
) -> None:
    now = time.perf_counter()
    state.updated_at = datetime.now(UTC)
    state.health = get_json(state, "/health", timeout) or {}
    metrics = get_json(state, "/metrics", timeout) or {}
    state.maintenance = get_json(state, "/snapshot/maintenance", timeout) or {}
    state.coverage = get_json(state, "/snapshot/coverage?limit=8", timeout) or {}
    update_rates(state, metrics, now)
    state.metrics = metrics
    if not disable_market_status and now - state.last_market_status_poll >= max(1.0, market_status_seconds):
        state.market_status = get_market_status(state, timeout) or {}
        state.last_market_status_poll = now
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


def get_market_status(state: PollState, timeout: float) -> dict[str, Any] | None:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not api_key:
        state.market_status_error = "MASSIVE_API_KEY missing in terminal environment"
        return None
    try:
        snapshot = MassiveMarketHoursClient.from_env(
            service_prefix="QMD",
            api_key=api_key,
            refresh_seconds=max(1.0, timeout),
        ).snapshot(force=True)
        state.market_status_error = snapshot.error
        return {
            "market": snapshot.market or snapshot.session,
            "earlyHours": snapshot.early_hours,
            "afterHours": snapshot.after_hours,
            "serverTime": snapshot.server_time,
            "source": snapshot.source,
            "reason": snapshot.reason,
            "holidayStatus": snapshot.holiday_status,
            "holidayName": snapshot.holiday_name,
            "activeCollectionWindow": snapshot.active_collection_window,
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, RuntimeError) as exc:
        state.market_status_error = str(exc)
        state.errors.append(f"{datetime.now().strftime('%H:%M:%S')} market-status: {exc}")
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
        "live_market_state_events_emitted",
        "live_market_state_events_persisted",
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
    _, terminal_height = shutil.get_terminal_size((120, 40))
    compact = terminal_height < 38
    messages = render_messages(state, limit=4 if compact else 6)
    if compact:
        return Group(
            render_header(state),
            render_current_operation(state),
            messages,
            Columns(
                [render_dependencies(state), render_runtime(state)],
                equal=True,
                expand=True,
            ),
            render_maintenance_progress(state),
        )
    return Group(
        render_header(state),
        render_current_operation(state),
        messages,
        Columns(
            [render_dependencies(state), render_runtime(state)],
            equal=True,
            expand=True,
        ),
        render_maintenance_progress(state),
        render_maintenance(state),
        Columns(
            [render_backpressure(state), render_compact_events(state)],
            equal=True,
            expand=True,
        ),
        render_recent_events(state, watch),
    )


def render_header(state: PollState) -> Any:
    health = state.health or {}
    metrics = state.metrics or {}
    config = health.get("config") if isinstance(health.get("config"), dict) else {}
    status = str(health.get("status") or "waiting")
    status_style = status_color(status)
    now = state.updated_at or datetime.now(UTC)
    left = Text.assemble(
        ("Python QMD Gateway  ", "bold"),
        (status.upper(), f"bold {status_style}"),
        "\n",
        ("bind ", "dim"),
        str(config.get("bind") or "-"),
        ("  db ", "dim"),
        str(config.get("clickhouse_database") or "-"),
        ("  session ", "dim"),
        str(health.get("session_phase") or "-"),
    )
    right = Text.assemble(
        ("UTC ", "dim"),
        clock(now),
        ("   ET ", "dim"),
        clock(now.astimezone(EASTERN)),
        ("   VAN ", "dim"),
        clock(now.astimezone(VANCOUVER)),
        "\n",
        ("poll ", "dim"),
        f"{state.refresh_seconds:.1f}s",
        ("   uptime ", "dim"),
        format_ms(metrics.get("process_uptime_ms")),
        ("   market ", "dim"),
        market_label(state),
    )
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(justify="right", ratio=1)
    grid.add_row(left, right)
    return Panel(grid, box=box.ROUNDED, border_style=status_style, padding=(0, 1))


def render_current_operation(state: PollState) -> Any:
    health = state.health or {}
    metrics = state.metrics or {}
    config = health.get("config") if isinstance(health.get("config"), dict) else {}
    rows = [
        ("Phase", str(health.get("session_phase") or "-")),
        ("Market", market_detail(state)),
        ("Subscriptions", ", ".join(health.get("subscriptions") or []) or "-"),
        ("Message", operation_message(state)),
        (
            "Historical",
            "enabled" if config.get("historical_flatfile_update_enabled") else "disabled",
        ),
        ("Last event lag", format_ms(metrics.get("last_event_lag_ms"))),
    ]
    return Panel(detail_table(rows), title="Current Operation", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def render_dependencies(state: PollState) -> Any:
    health = state.health or {}
    config = health.get("config") if isinstance(health.get("config"), dict) else {}
    market_ok = bool(state.market_status) and not state.market_status_error
    rows = [
        ("Local API", ok_label(bool(health)), state.base_url),
        ("Massive API key", ok_label(bool(config.get("api_key_present"))), "required for websocket and status"),
        ("Massive market status", ok_label(market_ok), state.market_status_error or market_label(state)),
        (
            "ClickHouse",
            ok_label(bool(config.get("clickhouse_url"))),
            f"{config.get('clickhouse_url') or '-'} db={config.get('clickhouse_database') or '-'}",
        ),
        (
            "Historical source",
            ok_label(bool(config.get("historical_clickhouse_database"))),
            str(config.get("historical_clickhouse_database") or "-"),
        ),
    ]
    return Panel(status_table(rows), title="Dependencies", box=box.ROUNDED, border_style="green", padding=(0, 1))


def render_runtime(state: PollState) -> Any:
    metrics = state.metrics or {}
    rows = [
        ("Status", str((state.health or {}).get("status") or "waiting"), state.updated_at.strftime("%Y-%m-%d %H:%M:%S") if state.updated_at else "-"),
        ("Events", format_int(metrics.get("ingest_events")), format_rate(state.rates.get("ingest_events_per_sec"))),
        ("Trades", format_int(metrics.get("ingest_trades")), format_rate(state.rates.get("ingest_trades_per_sec"))),
        ("Quotes", format_int(metrics.get("ingest_quotes")), format_rate(state.rates.get("ingest_quotes_per_sec"))),
        ("Compact written", format_int(metrics.get("compact_events_persisted")), format_rate(state.rates.get("compact_events_persisted_per_sec"))),
        ("Bars emitted", format_int(metrics.get("bar_rows_emitted")), format_rate(state.rates.get("bar_rows_emitted_per_sec"))),
        ("Scanner rows", format_int(metrics.get("scanner_candidates_emitted")), format_rate(state.rates.get("scanner_candidates_emitted_per_sec"))),
        ("Live state events", format_int(metrics.get("live_market_state_events_emitted")), format_rate(state.rates.get("live_market_state_events_emitted_per_sec"))),
        ("Live state persisted", format_int(metrics.get("live_market_state_events_persisted")), format_rate(state.rates.get("live_market_state_events_persisted_per_sec"))),
        ("Gap fill runs", format_int(metrics.get("gap_fill_runs")), f"failures={format_int(metrics.get('gap_fill_failures'))}"),
    ]
    return Panel(metric_table(rows), title="Runtime", box=box.ROUNDED, border_style="yellow", padding=(0, 1))


def render_maintenance_progress(state: PollState) -> Any:
    maintenance = state.maintenance if isinstance(state.maintenance, dict) else {}
    total_jobs = int(as_float(maintenance.get("total_jobs")))
    completed_jobs = int(as_float(maintenance.get("completed_jobs")))
    total_symbols = int(as_float(maintenance.get("total_symbols")))
    completed_symbols = int(as_float(maintenance.get("completed_symbols")))
    active = bool(maintenance.get("active"))
    status = str(maintenance.get("status") or "idle")
    color = "cyan" if active else ("green" if status in {"idle", "up_to_date", "repair_completed"} else status_color(status))
    active_symbols = maintenance.get("active_symbols") if isinstance(maintenance.get("active_symbols"), list) else []
    current_interval = "-".join(
        item
        for item in [
            short_time(maintenance.get("current_interval_start_utc")),
            short_time(maintenance.get("current_interval_end_utc")),
        ]
        if item and item != "-"
    ) or "-"
    rows = [
        ("State", status_text(status), "active" if active else "idle"),
        ("Mode", str(maintenance.get("mode") or "-"), str(maintenance.get("phase") or "-")),
        ("Jobs", progress_bar(completed_jobs, total_jobs), f"{format_int(completed_jobs)}/{format_int(total_jobs)}"),
        ("Symbols", progress_bar(completed_symbols, total_symbols), f"{format_int(completed_symbols)}/{format_int(total_symbols)}"),
        ("Rows", format_int(maintenance.get("rows_written")), f"errors={format_int(maintenance.get('errors'))} page_limit={format_int(maintenance.get('page_limited_symbols'))}"),
        ("Current", str(maintenance.get("current_symbol") or "-"), current_interval),
        ("Active", ", ".join(str(item) for item in active_symbols[:8]) or "-", str(maintenance.get("current_interval_reason") or "-")),
        ("Message", str(maintenance.get("message") or "-"), updated_label(maintenance.get("updated_at_utc"))),
    ]
    table = metric_table(
        rows,
        value_heading="Value",
        last_heading="Detail",
        metric_width=12,
        value_width=None,
        last_width=None,
        value_ratio=3,
        last_ratio=2,
        value_no_wrap=False,
        last_no_wrap=False,
        value_overflow="fold",
        last_overflow="fold",
        value_justify="left",
        last_justify="left",
    )
    return Panel(table, title="Maintenance Progress", box=box.ROUNDED, border_style=color, padding=(0, 1))


def render_maintenance(state: PollState) -> Any:
    rows = coverage_rows(state)
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Kind", style="bold cyan", ratio=2)
    table.add_column("Status", width=20, no_wrap=True)
    table.add_column("Window", ratio=3)
    table.add_column("Action", ratio=2)
    table.add_column("Details", ratio=4, overflow="fold")
    if not rows:
        error = state.coverage.get("error") if isinstance(state.coverage, dict) else ""
        table.add_row("-", "waiting", "-", "-", str(error or "No coverage rows returned yet."))
    for row in rows:
        summary = parse_summary(row.get("summary_json"))
        details = summary.get("message") or summary.get("latest_historical_event_date") or ""
        if summary.get("target_end_date"):
            details = f"{details} -> target {summary.get('target_end_date')}".strip()
        table.add_row(
            str(row.get("coverage_kind") or "-"),
            status_text(str(row.get("status") or "-")),
            f"{short_time(row.get('start_ts_utc'))} to {short_time(row.get('end_ts_utc'))}",
            str(row.get("action") or "-"),
            details or f"rows={format_int(row.get('rows_written'))}",
        )
    return Panel(table, title="Maintenance Coverage", box=box.ROUNDED, border_style="green", padding=(0, 1))


def render_backpressure(state: PollState) -> Any:
    metrics = state.metrics or {}
    rows = [
        ("Event broadcast", "events_broadcast_dropped"),
        ("Compact writer queue", "compact_event_queue_dropped"),
        ("Compact broadcast", "compact_event_broadcast_dropped"),
        ("Bar input queue", "bar_events_dropped"),
        ("Bar writer queue", "bar_rows_writer_dropped"),
        ("Bar indicator queue", "bar_rows_indicator_dropped"),
        ("Bar scanner queue", "bar_rows_scanner_dropped"),
        ("Indicator queue", "indicator_events_dropped"),
        ("Live state broadcast", "live_market_state_broadcast_dropped"),
        ("Live state insert failures", "live_market_state_persist_failures"),
        ("Raw CH queue", "clickhouse_events_dropped"),
    ]
    total = sum(int(as_float(metrics.get(key))) for _, key in rows)
    table_rows = [(label, format_int(metrics.get(key)), "0 expected") for label, key in rows]
    color = "green" if total == 0 else "red"
    return Panel(metric_table(table_rows, value_heading="Count", last_heading="Target"), title="Queue Health", box=box.ROUNDED, border_style=color, padding=(0, 1))


def render_compact_events(state: PollState) -> Any:
    metrics = state.metrics or {}
    rows = [
        ("Emitted", format_int(metrics.get("compact_events_emitted")), format_rate(state.rates.get("compact_events_emitted_per_sec"))),
        ("Persisted", format_int(metrics.get("compact_events_persisted")), format_rate(state.rates.get("compact_events_persisted_per_sec"))),
        ("Reorder pending", format_int(metrics.get("compact_events_reorder_pending")), "-"),
        ("Reorder buffered", format_int(metrics.get("compact_events_reorder_buffered")), "-"),
        ("Reorder flushed", format_int(metrics.get("compact_events_reorder_flushed")), "-"),
        ("Forced flushes", format_int(metrics.get("compact_event_reorder_forced_flushes")), "-"),
        ("Late arrivals", format_int(metrics.get("compact_event_reorder_late_arrivals")), "-"),
        ("Rejected", format_int(metrics.get("compact_event_rejected")), "-"),
    ]
    return Panel(metric_table(rows), title="Compact Events", box=box.ROUNDED, border_style="magenta", padding=(0, 1))


def render_recent_events(state: PollState, watch: list[str]) -> Any:
    table = Table(box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("ET", width=8, no_wrap=True)
    table.add_column("VAN", width=8, no_wrap=True)
    table.add_column("Ticker", width=8, no_wrap=True)
    table.add_column("Type", width=5, no_wrap=True)
    table.add_column("Ordinal", justify="right", width=10, no_wrap=True)
    table.add_column("SIP", width=19, no_wrap=True)
    table.add_column("Seq", justify="right", width=12, no_wrap=True)
    table.add_column("Primary", justify="right", width=12, no_wrap=True)
    table.add_column("Size", justify="right", width=10, no_wrap=True)
    added = 0
    for ticker in watch:
        rows = state.samples.get(ticker, [])
        for row in rows[-3:]:
            event_type = "Q" if (int(as_float(row.get("event_meta"))) & 1) == 0 else "T"
            event_dt = us_to_dt(row.get("sip_timestamp_us"))
            table.add_row(
                clock(event_dt.astimezone(EASTERN)) if event_dt else "-",
                clock(event_dt.astimezone(VANCOUVER)) if event_dt else "-",
                ticker,
                event_type,
                format_int(row.get("ordinal")),
                short_time(row.get("sip_timestamp_us")),
                format_int(row.get("source_sequence")),
                format_int(row.get("price_primary_int")),
                format_int(row.get("size_primary")),
            )
            added += 1
    if added == 0:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-")
    return Panel(table, title=f"Recent Compact Events ({added})", box=box.ROUNDED, border_style="blue", padding=(0, 1))


def render_messages(state: PollState, *, limit: int = 6) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Message", overflow="fold")
    if state.errors:
        for message in state.errors[-max(1, limit):]:
            table.add_row(message)
        color = "red"
    else:
        table.add_row("No monitor errors.")
        color = "green"
    return Panel(table, title="Messages", box=box.ROUNDED, border_style=color, padding=(0, 1))


def detail_table(rows: list[tuple[str, str]]) -> Any:
    table = Table.grid(expand=True)
    table.add_column("Label", width=16, style="bold cyan")
    table.add_column("Value", ratio=1, overflow="fold")
    for label, value in rows:
        table.add_row(label, value)
    return table


def status_table(rows: list[tuple[str, str, str]]) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Check", style="bold cyan", width=20)
    table.add_column("Result", width=8, no_wrap=True)
    table.add_column("Details", ratio=1, overflow="fold")
    for check, result, details in rows:
        table.add_row(check, result, details)
    return table


def metric_table(
    rows: list[tuple[str, str, str]],
    value_heading: str = "Total",
    last_heading: str = "Last",
    metric_width: int = 22,
    value_width: int | None = 14,
    last_width: int | None = 16,
    value_ratio: int | None = None,
    last_ratio: int | None = None,
    value_no_wrap: bool = True,
    last_no_wrap: bool = True,
    value_overflow: str = "ellipsis",
    last_overflow: str = "ellipsis",
    value_justify: str = "right",
    last_justify: str = "right",
) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Metric", style="bold cyan", width=metric_width, no_wrap=True)
    table.add_column(
        value_heading,
        justify=value_justify,
        width=value_width,
        ratio=value_ratio,
        no_wrap=value_no_wrap,
        overflow=value_overflow,
    )
    table.add_column(
        last_heading,
        justify=last_justify,
        width=last_width,
        ratio=last_ratio,
        no_wrap=last_no_wrap,
        overflow=last_overflow,
    )
    for metric, total, last in rows:
        table.add_row(metric, total, last)
    return table


def coverage_rows(state: PollState) -> list[dict[str, Any]]:
    coverage = state.coverage if isinstance(state.coverage, dict) else {}
    rows = coverage.get("rows")
    return rows if isinstance(rows, list) else []


def parse_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def operation_message(state: PollState) -> str:
    maintenance = state.maintenance if isinstance(state.maintenance, dict) else {}
    if maintenance.get("active"):
        return str(maintenance.get("message") or "Maintenance is running.")
    health = state.health or {}
    metrics = state.metrics or {}
    if not health:
        return "Waiting for qmd-gateway HTTP API."
    status = str(health.get("status") or "")
    if status != "running":
        return status or "Gateway is not reporting running state."
    event_rate = as_float(state.rates.get("ingest_events_per_sec"))
    if event_rate > 0:
        return f"Ingesting Massive quotes/trades at {event_rate:,.1f} events/sec."
    lag = as_float(metrics.get("last_event_lag_ms"))
    if lag > 0:
        return f"Connected; last event lag is {format_ms(lag)}."
    return "Connected; waiting for Massive events."


def market_label(state: PollState) -> str:
    payload = state.market_status or {}
    if not payload:
        return "unknown"
    market = str(payload.get("market") or "-")
    early = bool(payload.get("earlyHours"))
    after = bool(payload.get("afterHours"))
    flags = []
    if early:
        flags.append("early")
    if after:
        flags.append("after")
    return market + (f" ({', '.join(flags)})" if flags else "")


def market_detail(state: PollState) -> str:
    payload = state.market_status or {}
    if not payload:
        return state.market_status_error or "not checked yet"
    exchanges = payload.get("exchanges") if isinstance(payload.get("exchanges"), dict) else {}
    exchange_status = ", ".join(f"{key}:{value}" for key, value in exchanges.items()) or "-"
    server_time = str(payload.get("serverTime") or "-")
    return f"{market_label(state)} server={server_time} exchanges={exchange_status}"


def status_text(value: str) -> str:
    style = status_color(value)
    return f"[{style}]{value}[/{style}]"


def status_color(value: str) -> str:
    lowered = value.lower()
    if lowered in {"running", "ok", "up_to_date", "completed", "launched"}:
        return "green"
    if lowered in {"planned", "waiting", "skipped", "api_only_missing_massive_key", "awaiting_live_symbols"}:
        return "yellow"
    if lowered in {"no_symbols_available"}:
        return "red"
    if "fail" in lowered or "error" in lowered or "needs" in lowered or "blocked" in lowered:
        return "red"
    return "cyan"


def ok_label(ok: bool) -> str:
    return "[green]OK[/green]" if ok else "[red]FAIL[/red]"


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


def progress_bar(completed: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[dim]" + ("-" * width) + "[/dim] unknown"
    completed = max(0, min(completed, total))
    filled = int(round(width * completed / total))
    pct = 100.0 * completed / total
    return f"[green]{'#' * filled}[/green][dim]{'-' * (width - filled)}[/dim] {pct:5.1f}%"


def format_ms(value: Any) -> str:
    number = as_float(value)
    if number <= 0:
        return "-"
    if number >= 60_000:
        return f"{number / 60_000:,.1f}m"
    if number >= 1_000:
        return f"{number / 1_000:,.1f}s"
    return f"{number:,.0f}ms"


def clock(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def short_time(value: Any) -> str:
    event_dt = us_to_dt(value)
    if event_dt:
        return event_dt.strftime("%Y-%m-%d %H:%M:%S")
    raw = str(value or "-")
    if "T" in raw:
        return raw.replace("T", " ").replace("Z", "")[:19]
    return raw


def updated_label(value: Any) -> str:
    raw = short_time(value)
    return f"updated {raw}" if raw and raw != "-" else "-"


def us_to_dt(value: Any) -> datetime | None:
    number = as_float(value)
    if number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number / 1_000_000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nqmd terminal stopped.", file=sys.stderr)
        raise SystemExit(130)
