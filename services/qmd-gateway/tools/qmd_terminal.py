#!/usr/bin/env python
"""Operational terminal for the Rust qmd-gateway.

The monitor is a separate process. It reads local HTTP snapshots and never
participates in websocket ingest, normalization, or ClickHouse persistence.
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from rich import box
    from rich.columns import Columns
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from services.gateway_core.rich_renderer import standard_live, status_color, style_status
except Exception:  # pragma: no cover - the plain monitor remains available.
    box = None
    Columns = None
    Group = None
    Panel = None
    Table = None
    Text = None
    standard_live = None
    status_color = None
    style_status = None


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")
PRIMARY_SOURCES = ("health", "status", "metrics", "maintenance")


@dataclass
class PollIssue:
    source: str
    message: str
    first_seen_utc: datetime
    last_seen_utc: datetime
    count: int = 1


@dataclass
class Recovery:
    source: str
    message: str
    recovered_at_utc: datetime


@dataclass
class PollState:
    base_url: str
    refresh_seconds: float = 1.0
    coverage_seconds: float = 10.0
    health: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    maintenance: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    source_updated_at: dict[str, datetime] = field(default_factory=dict)
    poll_issues: dict[str, PollIssue] = field(default_factory=dict)
    recoveries: list[Recovery] = field(default_factory=list)
    rates: dict[str, float] = field(default_factory=dict)
    prior_metrics: dict[str, Any] = field(default_factory=dict)
    prior_metrics_ts: float = 0.0
    last_coverage_poll: float = 0.0
    updated_at: datetime | None = None

    def record_success(self, source: str, payload: Any, now: datetime) -> None:
        issue = self.poll_issues.pop(source, None)
        if issue is not None:
            self.recoveries.append(
                Recovery(
                    source=source,
                    message=f"Recovered after {issue.count} failed poll(s).",
                    recovered_at_utc=now,
                )
            )
            self.recoveries = self.recoveries[-12:]
        self.source_updated_at[source] = now
        if source.startswith("sample:"):
            ticker = source.split(":", 1)[1]
            self.samples[ticker] = payload if isinstance(payload, list) else []
        elif source in {"health", "status", "metrics", "maintenance", "coverage"}:
            if isinstance(payload, dict):
                setattr(self, source, payload)

    def record_failure(self, source: str, message: str, now: datetime) -> None:
        existing = self.poll_issues.get(source)
        if existing is None:
            self.poll_issues[source] = PollIssue(source, message, now, now)
        else:
            existing.message = message
            existing.last_seen_utc = now
            existing.count += 1


def main() -> int:
    args = parse_args()
    state = PollState(
        base_url=args.base_url.rstrip("/"),
        refresh_seconds=max(0.25, float(args.refresh_seconds)),
        coverage_seconds=max(2.0, float(args.coverage_seconds)),
    )
    watch = [item.strip().upper() for item in args.watch.split(",") if item.strip()]
    interactive = bool(sys.stdout.isatty()) and not args.plain

    if args.json:
        poll_once(state, watch, args.event_limit, args.timeout_seconds, details=args.details)
        print(json.dumps(machine_payload(state), sort_keys=True, default=str), flush=True)
        return 0 if state.health else 2
    if standard_live is None or not interactive:
        return run_plain(args, state, watch)
    return run_rich(args, state, watch)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the qmd-gateway operational dashboard.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8795")
    parser.add_argument("--watch", default="AAPL,NVDA,TSLA")
    parser.add_argument("--event-limit", type=int, default=6)
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    parser.add_argument("--coverage-seconds", type=float, default=10.0)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument("--details", action="store_true", help="Poll diagnostic compact-event samples.")
    parser.add_argument("--no-screen", action="store_true")
    parser.add_argument("--plain", action="store_true", help="Use human-readable non-interactive output.")
    parser.add_argument("--json", action="store_true", help="Emit one machine-readable snapshot and exit.")
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit.")
    return parser.parse_args()


def run_rich(args: argparse.Namespace, state: PollState, watch: list[str]) -> int:
    try:
        poll_once(state, watch, args.event_limit, args.timeout_seconds, details=args.details)
        if args.once:
            from rich.console import Console

            Console(no_color=bool(os.environ.get("NO_COLOR"))).print(render_dashboard(state))
            return 0 if state.health else 2
        with standard_live(
            render_dashboard(state),
            screen=not args.no_screen,
            refresh_seconds=state.refresh_seconds,
        ) as live:
            while True:
                poll_once(state, watch, args.event_limit, args.timeout_seconds, details=args.details)
                live.update(render_dashboard(state), refresh=True)
                time.sleep(state.refresh_seconds)
    except KeyboardInterrupt:
        return 130


def run_plain(args: argparse.Namespace, state: PollState, watch: list[str]) -> int:
    last_line = ""
    last_print = 0.0
    try:
        while True:
            poll_once(state, watch, args.event_limit, args.timeout_seconds, details=args.details)
            line = plain_summary(state)
            now = time.monotonic()
            if line != last_line or now - last_print >= 60:
                print(line, flush=True)
                last_line = line
                last_print = now
            if args.once:
                return 0 if state.health else 2
            time.sleep(state.refresh_seconds)
    except KeyboardInterrupt:
        return 130


def poll_once(
    state: PollState,
    watch: list[str],
    event_limit: int,
    timeout: float,
    *,
    details: bool,
) -> None:
    started = time.monotonic()
    now = datetime.now(UTC)
    requests: dict[str, str] = {
        "health": "/health",
        "status": "/snapshot/status",
        "metrics": "/metrics",
        "maintenance": "/snapshot/maintenance",
    }
    if started - state.last_coverage_poll >= state.coverage_seconds:
        requests["coverage"] = "/snapshot/coverage?limit=40"
        state.last_coverage_poll = started
    if details:
        for ticker in watch:
            requests[f"sample:{ticker}"] = (
                f"/snapshot/compact-events/{ticker}?limit={max(1, event_limit)}"
            )

    results: dict[str, Any] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(requests))) as pool:
        futures = {
            pool.submit(fetch_json, state.base_url + path, timeout): source
            for source, path in requests.items()
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                results[source] = future.result()
            except Exception as exc:  # noqa: BLE001 - the error is normalized for the monitor.
                failures[source] = str(exc)

    for source, payload in results.items():
        state.record_success(source, payload, now)
    for source, message in failures.items():
        state.record_failure(source, message, now)
    if "metrics" in results and isinstance(results["metrics"], dict):
        update_rates(state, results["metrics"], started)
    state.updated_at = now


def fetch_json(url: str, timeout: float) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(str(exc)) from exc


def update_rates(state: PollState, metrics: dict[str, Any], now: float) -> None:
    elapsed = now - state.prior_metrics_ts if state.prior_metrics_ts else 0.0
    keys = (
        "ingest_events",
        "ingest_trades",
        "ingest_quotes",
        "compact_events_emitted",
        "compact_events_persisted",
        "compact_event_rejected",
        "intraday_bar_rows_emitted",
        "intraday_bar_rows_persisted",
        "scanner_candidates_emitted",
        "live_market_state_events_persisted",
    )
    if elapsed > 0:
        state.rates = {
            f"{key}_per_sec": max(
                0.0,
                (as_float(metrics.get(key)) - as_float(state.prior_metrics.get(key))) / elapsed,
            )
            for key in keys
        }
    state.prior_metrics = dict(metrics)
    state.prior_metrics_ts = now


def render_dashboard(state: PollState) -> Any:
    width, height = shutil.get_terminal_size((120, 40))
    compact = height < 42 or width < 100
    narrow = width < 120
    if compact:
        return Group(
            render_header(state, compact=True),
            render_attention(state, compact=True),
            render_operational_core(state, narrow=True),
        )

    lower = render_maintenance(state, narrow=narrow) if maintenance_active(state) else render_products(state, narrow=narrow)
    coverage_narrow = width < 160
    parts: list[Any] = [
        render_header(state, compact=False),
        render_attention(state, compact=False),
        render_pipeline(state, narrow=narrow),
        Columns(
            [
                render_recent_coverage(state, narrow=coverage_narrow),
                render_historical_sync(state, narrow=coverage_narrow),
            ],
            equal=True,
            expand=True,
        ),
        lower,
    ]
    if maintenance_active(state) and height >= 48:
        parts.append(render_products(state, narrow=narrow))
    return Group(*parts)


def render_header(state: PollState, *, compact: bool) -> Any:
    status = overall_status(state)
    color = color_for(status)
    header = state.status.get("header") if isinstance(state.status.get("header"), dict) else {}
    calendar = state.health.get("market_calendar") if isinstance(state.health.get("market_calendar"), dict) else {}
    config = state.health.get("config") if isinstance(state.health.get("config"), dict) else {}
    now = state.updated_at or datetime.now(UTC)
    age = source_age(state, "status")
    host = str(header.get("host_role") or state.health.get("host_role") or "-")
    market = "COLLECTING" if calendar.get("active_collection_window") else "CLOSED"
    if calendar.get("stale"):
        market += " / CALENDAR STALE"
    title = Text.assemble(
        ("QMD Gateway  ", "bold"),
        (status.replace("_", " ").upper(), f"bold {color}"),
        (f"   {market}", "bold cyan"),
    )
    right = f"ET {clock(now.astimezone(EASTERN))}  UTC {clock(now)}"
    if not compact:
        right += f"  VAN {clock(now.astimezone(VANCOUVER))}"
    grid = Table.grid(expand=True)
    grid.add_column(ratio=3, overflow="fold")
    grid.add_column(ratio=2, justify="right", overflow="fold")
    grid.add_row(title, right)
    mode = "live"
    grid.add_row(
        f"host {host}  mode {mode}  session {state.health.get('session_phase') or '-'}",
        f"snapshot {format_age(age)}  poll {state.refresh_seconds:.1f}s",
    )
    if not compact:
        grid.add_row(
            f"read {config.get('historical_clickhouse_database') or '-'} (RO)  "
            f"write {config.get('clickhouse_database') or '-'}.{config.get('compact_event_table') or 'events'}",
            f"calendar {calendar.get('source') or '-'}  {calendar.get('reason') or ''}",
        )
    return Panel(grid, box=box.ROUNDED, border_style=color, padding=(0, 1))


def render_attention(state: PollState, *, compact: bool) -> Any:
    rows = attention_rows(state)
    limit = 2 if compact else 4
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("State", width=10, no_wrap=True)
    table.add_column("Area", width=18 if not compact else 14, no_wrap=True, overflow="ellipsis")
    table.add_column("Message / Action", overflow="ellipsis" if compact else "fold")
    if not rows:
        table.add_row(status_text("ok"), "All required paths", "No active action. Last trustworthy snapshots are current.")
        color = "green"
    else:
        for row in rows[:limit]:
            message = str(row.get("message") or "-")
            action = str(row.get("action") or "").strip()
            detail = f"{message}  Action: {action}" if action else message
            table.add_row(
                status_text(str(row.get("severity") or row.get("status") or "warning")),
                truncate(row.get("area") or row.get("source") or "monitor", 18 if compact else 32),
                truncate(detail, 90 if compact else 280),
            )
        hidden = max(0, len(rows) - limit)
        if hidden:
            table.add_row("", "more", f"{hidden} additional active item(s); see the gateway stderr log or status API.")
        color = "red" if any(str(row.get("severity")) == "critical" for row in rows) else "yellow"
    return Panel(table, title="Attention / Required Action", box=box.ROUNDED, border_style=color, padding=(0, 1))


def render_pipeline(state: PollState, *, narrow: bool) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Stage", width=19, no_wrap=True)
    table.add_column("State", width=12, no_wrap=True)
    table.add_column("Rate", width=18, no_wrap=True)
    table.add_column("Freshness", width=14, no_wrap=True)
    if not narrow:
        table.add_column("Pending", justify="right", width=10, no_wrap=True)
    table.add_column("Integrity / Detail", overflow="fold")
    for row in pipeline_rows(state):
        values = [
            row["stage"],
            status_text(row["state"]),
            row["rate"],
            row["freshness"],
        ]
        if not narrow:
            values.append(row["pending"])
        values.append(row["detail"])
        table.add_row(*values)
    return Panel(table, title="Live Event Pipeline", box=box.ROUNDED, border_style=color_for(overall_status(state)), padding=(0, 1))


def render_operational_core(state: PollState, *, narrow: bool) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Path", width=18, no_wrap=True)
    table.add_column("State", width=11, no_wrap=True)
    table.add_column("Now", overflow="ellipsis")
    for row in pipeline_rows(state):
        table.add_row(row["stage"], status_text(row["state"]), f"{row['rate']}  {row['freshness']}  pending {row['pending']}")
    recent = recent_coverage_summary(state)
    historical = historical_summary(state)
    table.add_row("Recent coverage", status_text(recent[0]), truncate(recent[1], 80))
    table.add_row("Historical sync", status_text(historical[0]), truncate(historical[1], 80))
    if maintenance_active(state):
        maintenance = state.maintenance
        table.add_row(
            "Active repair",
            status_text(str(maintenance.get("status") or "running")),
            truncate(
                f"{maintenance.get('phase') or '-'}: {maintenance.get('message') or '-'}; "
                f"jobs {format_int(maintenance.get('completed_jobs'))}/{format_int(maintenance.get('total_jobs'))}",
                80,
            ),
        )
    return Panel(table, title="Operational Core", box=box.ROUNDED, border_style=color_for(overall_status(state)), padding=(0, 1))


def render_recent_coverage(state: PollState, *, narrow: bool) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Session", width=10, no_wrap=True)
    table.add_column("Events", width=13, no_wrap=True)
    table.add_column("Bars", width=13, no_wrap=True)
    if not narrow:
        table.add_column("Repair / Retention", overflow="fold")
    sessions = required_sessions(state)
    if not sessions:
        table.add_row("-", "waiting", "waiting", *( ["No session contract reported."] if not narrow else [] ))
    for session in sessions[-4:]:
        event_state, bar_state, detail = live_session_state(state, session)
        values = [session, status_text(event_state), status_text(bar_state)]
        if not narrow:
            values.append(detail)
        table.add_row(*values)
    return Panel(table, title="Recent Live Coverage — q_live.events", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def render_historical_sync(state: PollState, *, narrow: bool) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Session", width=10, no_wrap=True)
    table.add_column("Quotes", width=14, no_wrap=True)
    table.add_column("Trades", width=14, no_wrap=True)
    if not narrow:
        table.add_column("Historical / Action", overflow="fold")
    groups = flatfile_groups(state)
    if not groups:
        table.add_row("-", "waiting", "waiting", *( ["No flatfile coverage snapshot yet."] if not narrow else [] ))
    for session, sources in list(groups.items())[-4:]:
        quote = sources.get("quote", {})
        trade = sources.get("trade", {})
        detail = historical_action(quote, trade)
        values = [session, status_text(source_status(quote)), status_text(source_status(trade))]
        if not narrow:
            values.append(detail)
        table.add_row(*values)
    return Panel(table, title="Historical Sync — market_sip_compact (RO)", box=box.ROUNDED, border_style="magenta", padding=(0, 1))


def render_products(state: PollState, *, narrow: bool) -> Any:
    products = state.status.get("downstream_products")
    products = products if isinstance(products, list) else []
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Product", width=24, no_wrap=True)
    table.add_column("State", width=12, no_wrap=True)
    table.add_column("Rows", justify="right", width=12, no_wrap=True)
    table.add_column("Meaning", overflow="fold")
    for item in products:
        if not isinstance(item, dict):
            continue
        enabled = bool(item.get("enabled", True))
        status = str(item.get("state") or ("healthy" if enabled else "disabled"))
        detail = str(item.get("detail") or "-")
        table.add_row(
            str(item.get("product") or "-"),
            status_text(status),
            format_int(item.get("rows")) if item.get("rows") is not None else "-",
            truncate(detail, 120 if narrow else 240),
        )
    if not products:
        table.add_row("Downstream products", status_text("waiting"), "-", "No product status reported.")
    return Panel(table, title="Downstream Products", box=box.ROUNDED, border_style="blue", padding=(0, 1))


def render_maintenance(state: PollState, *, narrow: bool) -> Any:
    value = state.maintenance if isinstance(state.maintenance, dict) else {}
    table = Table(box=box.SIMPLE, expand=True, show_edge=False, padding=(0, 1))
    table.add_column("Stage", width=18, no_wrap=True)
    table.add_column("Progress", width=24 if not narrow else 16, no_wrap=True)
    table.add_column("Current durable unit", overflow="fold")
    total_jobs = int(as_float(value.get("total_jobs")))
    completed_jobs = int(as_float(value.get("completed_jobs")))
    total_symbols = int(as_float(value.get("total_symbols")))
    completed_symbols = int(as_float(value.get("completed_symbols")))
    table.add_row("Mode / phase", f"{value.get('mode') or '-'} / {value.get('phase') or '-'}", str(value.get("message") or "-"))
    table.add_row("Intervals", count_progress(completed_jobs, total_jobs), interval_detail(value))
    table.add_row("Symbols", count_progress(completed_symbols, total_symbols), ", ".join(value.get("active_symbols") or []) or "-")
    table.add_row("Durability", f"rows {format_int(value.get('rows_written'))}", f"errors {format_int(value.get('errors'))}  page limits {format_int(value.get('page_limited_symbols'))}")
    return Panel(table, title="Active Coverage Repair", box=box.ROUNDED, border_style=color_for(str(value.get("status") or "running")), padding=(0, 1))


def pipeline_rows(state: PollState) -> list[dict[str, str]]:
    metrics = state.metrics or {}
    lanes = operational_lanes(state)
    feed = lanes.get("massive_feed", {})
    compact = lanes.get("compact_events", {})
    bars = lanes.get("intraday_bars", {})
    event_age = as_float(metrics.get("last_event_lag_ms")) / 1000 if metrics.get("last_event_lag_ms") is not None else None
    return [
        {
            "stage": "Massive feed",
            "state": str(feed.get("state") or "waiting"),
            "rate": f"Q {format_rate(state.rates.get('ingest_quotes_per_sec'))}  T {format_rate(state.rates.get('ingest_trades_per_sec'))}",
            "freshness": format_age(event_age),
            "pending": "-",
            "detail": f"reconnect failures {format_int(metrics.get('massive_connect_failures'))}; disconnects {format_int(metrics.get('massive_disconnects'))}",
        },
        {
            "stage": "Normalize / encode",
            "state": "warning" if as_float(state.rates.get("compact_event_rejected_per_sec")) > 0 else ("healthy" if feed.get("state") == "healthy" else "waiting"),
            "rate": format_rate(state.rates.get("compact_events_emitted_per_sec")),
            "freshness": format_age(event_age),
            "pending": format_int(metrics.get("compact_events_reorder_pending")),
            "detail": f"rejected {format_int(metrics.get('compact_event_rejected'))}; late {format_int(metrics.get('compact_event_reorder_late_arrivals'))}; forced {format_int(metrics.get('compact_event_reorder_forced_flushes'))}",
        },
        {
            "stage": "q_live.events",
            "state": str(compact.get("state") or "disabled"),
            "rate": format_rate(state.rates.get("compact_events_persisted_per_sec")),
            "freshness": lane_freshness(compact),
            "pending": format_int(compact.get("pending_rows")),
            "detail": f"persisted {format_int(metrics.get('compact_events_persisted'))}; failures {format_int(compact.get('failures'))}",
        },
        {
            "stage": "Intraday bars",
            "state": str(bars.get("state") or "waiting"),
            "rate": format_rate(state.rates.get("intraday_bar_rows_persisted_per_sec")),
            "freshness": lane_freshness(bars),
            "pending": format_int(bars.get("pending_rows")),
            "detail": f"persisted {format_int(metrics.get('intraday_bar_rows_persisted'))}; emitted {format_int(metrics.get('intraday_bar_rows_emitted'))}; late repairs {format_int(metrics.get('intraday_bar_repairs_completed'))}/{format_int(metrics.get('intraday_bar_repairs_requested'))}; failures {format_int(bars.get('failures'))}",
        },
    ]


def attention_rows(state: PollState) -> list[dict[str, Any]]:
    rows = state.status.get("attention")
    result = [dict(item) for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    for row in coverage_rows(state):
        status = str(row.get("status") or "")
        if "manual_action_required" in status or "needs_manual_rebuild" in status or "retention_blocked" in status:
            command = str(row.get("command") or "").strip()
            result.append(
                {
                    "severity": "critical" if "rebuild" in status else "warning",
                    "area": "Historical handoff" if command else "Recent coverage",
                    "message": status.replace("_", " "),
                    "action": f"Run on workstation: {command}" if command else coverage_detail(row),
                }
            )
    coverage_error = state.coverage.get("error") if isinstance(state.coverage, dict) else ""
    if coverage_error:
        result.append({"severity": "warning", "area": "Coverage snapshot", "message": coverage_error})
    recent_state, recent_detail = recent_coverage_summary(state)
    if recent_state == "warning":
        result.append(
            {
                "severity": "warning",
                "area": "Recent live coverage",
                "message": recent_detail,
                "action": "Allow the scheduled recent-window repair to complete; inspect maintenance if the gap persists.",
            }
        )
    for issue in sorted(state.poll_issues.values(), key=lambda item: item.last_seen_utc, reverse=True):
        result.append(
            {
                "severity": "critical" if issue.source in PRIMARY_SOURCES else "warning",
                "area": f"Monitor {issue.source}",
                "message": f"{issue.message} ({issue.count} consecutive poll failure(s)); last good {format_age(source_age(state, issue.source))} ago.",
            }
        )
    return dedupe_attention(result)


def dedupe_attention(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("area") or row.get("source")), str(row.get("message")))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def operational_lanes(state: PollState) -> dict[str, dict[str, Any]]:
    operational = None
    specific = state.status.get("service_specific") if isinstance(state.status.get("service_specific"), dict) else {}
    if isinstance(specific.get("operational"), dict):
        operational = specific.get("operational")
    elif isinstance(state.health.get("operational"), dict):
        operational = state.health.get("operational")
    lanes = operational.get("lanes") if isinstance(operational, dict) else []
    return {
        str(item.get("key")): item
        for item in lanes
        if isinstance(item, dict) and item.get("key")
    }


def required_sessions(state: PollState) -> list[str]:
    specific = state.status.get("service_specific") if isinstance(state.status.get("service_specific"), dict) else {}
    values = specific.get("recent_sessions")
    return [str(value) for value in values] if isinstance(values, list) else []


def live_session_state(state: PollState, session: str) -> tuple[str, str, str]:
    rows = [row for row in coverage_rows(state) if row.get("table_group") == "live_coverage" and interval_contains_session(row, session)]
    current = bool(required_sessions(state) and session == required_sessions(state)[-1])
    collecting = current and bool((state.health.get("market_calendar") or {}).get("active_collection_window"))
    event_statuses = [str(row.get("status") or "") for row in rows if "compact" in str(row.get("action") or "")]
    bar_statuses = [str(row.get("status") or "") for row in rows if "bar" in str(row.get("action") or "")]
    repair = [str(row.get("status") or "") for row in rows if "repair" in str(row.get("status") or "")]
    event_state = "collecting" if collecting else ("covered" if any("persisted" in value or "completed" in value for value in event_statuses + repair) else "no evidence")
    bar_state = "collecting" if collecting else ("covered" if any("persisted" in value or "completed" in value for value in bar_statuses + repair) else "no evidence")
    detail = ", ".join(sorted(set(repair))) or ("live collection active" if collecting else "awaiting confirmed event/bar overlap")
    return event_state, bar_state, detail


def recent_coverage_summary(state: PollState) -> tuple[str, str]:
    sessions = required_sessions(state)
    if not sessions:
        return "waiting", "No required-session contract reported."
    states = [(session, *live_session_state(state, session)) for session in sessions[-4:]]
    missing = [session for session, events, bars, _ in states[:-1] if events == "no evidence" or bars == "no evidence"]
    if missing:
        return "warning", f"No confirmed event/bar overlap for {', '.join(missing)}."
    return "healthy", f"{len(states)} required session(s) represented; current session may still be collecting."


def flatfile_groups(state: PollState) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for row in coverage_rows(state):
        if row.get("table_group") != "flatfile_coverage":
            continue
        session = str(row.get("start_ts_utc") or "")[:10]
        source = str(row.get("action") or "")
        if session and source:
            groups.setdefault(session, {})[source] = row
    return dict(sorted(groups.items()))


def source_status(row: dict[str, Any]) -> str:
    if not row:
        return "waiting"
    status = str(row.get("status") or "")
    if status == "remote_ready/confirmed":
        return "confirmed"
    if "manual_action_required" in status:
        return "action required"
    if "missing" in status or "failed" in status:
        return "warning"
    if status in {"remote_ready/not_confirmed", "remote_changed", "launched", "launch_in_progress"}:
        return "waiting"
    return status or "waiting"


def historical_action(quote: dict[str, Any], trade: dict[str, Any]) -> str:
    rows = [row for row in (quote, trade) if row]
    command = next((str(row.get("command") or "").strip() for row in rows if row.get("command")), "")
    statuses = ", ".join(sorted({str(row.get("status") or "waiting") for row in rows}))
    host = next((str(row.get("host_role") or "") for row in rows if row.get("host_role")), "-")
    if command and any("manual_action_required" in str(row.get("status")) for row in rows):
        return f"{host}: run {command}"
    return f"{host}: {statuses or 'waiting'}"


def historical_summary(state: PollState) -> tuple[str, str]:
    groups = flatfile_groups(state)
    if not groups:
        return "waiting", "No remote/historical coverage snapshot yet."
    actions = []
    unconfirmed = []
    for session, sources in groups.items():
        statuses = [str(row.get("status") or "") for row in sources.values()]
        if any("manual_action_required" in status for status in statuses):
            actions.append(session)
        if not statuses or not all(status.endswith("/confirmed") for status in statuses):
            unconfirmed.append(session)
    if actions:
        return "action required", f"Workstation update required for {', '.join(actions)}."
    if unconfirmed:
        return "waiting", f"Awaiting confirmation for {', '.join(unconfirmed)}."
    return "healthy", f"Latest {len(groups)} indexed session(s) confirmed."


def coverage_rows(state: PollState) -> list[dict[str, Any]]:
    rows = state.coverage.get("rows") if isinstance(state.coverage, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def interval_contains_session(row: dict[str, Any], session: str) -> bool:
    start = str(row.get("start_ts_utc") or "")[:10]
    end = str(row.get("end_ts_utc") or "")[:10]
    return bool(start and start <= session and (not end or session <= end))


def coverage_detail(row: dict[str, Any]) -> str:
    raw = row.get("summary_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return str(parsed.get("message") or parsed.get("error") or parsed)
        except json.JSONDecodeError:
            pass
    return str(row.get("status") or "Inspect coverage state.")


def maintenance_active(state: PollState) -> bool:
    return bool((state.maintenance or {}).get("active"))


def overall_status(state: PollState) -> str:
    if any(issue.source in PRIMARY_SOURCES for issue in state.poll_issues.values()):
        return "stale" if state.health or state.status else "failed"
    header = state.status.get("header") if isinstance(state.status.get("header"), dict) else {}
    return str(header.get("status") or state.health.get("status") or "starting").lower()


def source_age(state: PollState, source: str) -> float | None:
    updated = state.source_updated_at.get(source)
    if updated is None:
        return None
    return max(0.0, (datetime.now(UTC) - updated).total_seconds())


def lane_freshness(lane: dict[str, Any]) -> str:
    value = parse_datetime(lane.get("last_success_utc"))
    if value is None:
        return "no commit yet"
    return format_age(max(0.0, (datetime.now(UTC) - value).total_seconds()))


def interval_detail(value: dict[str, Any]) -> str:
    symbol = str(value.get("current_symbol") or "-")
    start = short_datetime(value.get("current_interval_start_utc"))
    end = short_datetime(value.get("current_interval_end_utc"))
    reason = str(value.get("current_interval_reason") or "-")
    return f"{symbol} {start} to {end} ({reason})"


def plain_summary(state: PollState) -> str:
    metrics = state.metrics or {}
    feed = operational_lanes(state).get("massive_feed", {})
    compact = operational_lanes(state).get("compact_events", {})
    coverage = recent_coverage_summary(state)
    historical = historical_summary(state)
    return (
        f"{datetime.now(UTC).isoformat(timespec='seconds')} "
        f"status={overall_status(state)} feed={feed.get('state', 'waiting')} "
        f"quotes={format_rate(state.rates.get('ingest_quotes_per_sec'))} "
        f"trades={format_rate(state.rates.get('ingest_trades_per_sec'))} "
        f"event_age={format_age(as_float(metrics.get('last_event_lag_ms')) / 1000 if metrics.get('last_event_lag_ms') is not None else None)} "
        f"q_live={compact.get('state', 'disabled')} pending={format_int(compact.get('pending_rows'))} "
        f"coverage={coverage[0]} historical={historical[0]} actions={len(attention_rows(state))}"
    )


def machine_payload(state: PollState) -> dict[str, Any]:
    return {
        "updated_at_utc": state.updated_at,
        "overall_status": overall_status(state),
        "health": state.health,
        "status": state.status,
        "metrics": state.metrics,
        "maintenance": state.maintenance,
        "coverage": state.coverage,
        "rates": state.rates,
        "poll_issues": {key: asdict(value) for key, value in state.poll_issues.items()},
        "recoveries": [asdict(value) for value in state.recoveries],
    }


def status_text(value: str) -> str:
    return style_status(value) if style_status is not None else value.upper()


def color_for(value: str) -> str:
    if status_color is not None:
        return status_color(value)
    value = value.lower()
    if value in {"failed", "degraded", "critical"}:
        return "red"
    if value in {"warning", "stale", "action_required", "action required", "waiting"}:
        return "yellow"
    return "green"


def format_rate(value: Any) -> str:
    return f"{as_float(value):,.1f}/s"


def format_int(value: Any) -> str:
    return f"{int(as_float(value)):,}"


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def count_progress(completed: int, total: int) -> str:
    if total <= 0:
        return f"{completed:,} completed; total not established"
    return f"{completed:,}/{total:,} ({100 * min(completed, total) / total:.1f}%)"


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def truncate(value: Any, limit: int) -> str:
    text = str(value or "-").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def clock(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def short_datetime(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.astimezone(EASTERN).strftime("%m-%d %H:%M ET") if parsed else "-"


if __name__ == "__main__":
    raise SystemExit(main())
