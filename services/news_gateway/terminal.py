from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from services.news_gateway.gateway import NewsGateway


async def run_terminal_dashboard(gateway: "NewsGateway") -> None:
    refresh_seconds = max(0.25, gateway.config.terminal_refresh_seconds)
    refresh_per_second = max(1.0, min(4.0, 1.0 / refresh_seconds))
    with Live(render_dashboard(gateway, {}), refresh_per_second=refresh_per_second, transient=False) as live:
        while not gateway._stop_event.is_set():  # noqa: SLF001
            snapshot = await gateway.state.recent_snapshot(gateway.config.terminal_news_limit)
            live.update(render_dashboard(gateway, snapshot))
            await asyncio.sleep(refresh_seconds)


def render_dashboard(gateway: "NewsGateway", news_snapshot: dict[str, Any]) -> Group:
    metrics = gateway.snapshot_metrics()
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return Group(
        header_panel(gateway, metrics, now),
        metrics_table(gateway, metrics),
        gap_panel(metrics),
        news_table(news_snapshot),
    )


def header_panel(gateway: "NewsGateway", metrics: dict[str, Any], now: str) -> Panel:
    status = str(metrics.get("last_cycle_status") or "starting")
    color = "green" if status in {"ok", "starting", ""} else "yellow" if status == "completed_with_errors" else "red"
    text = Text()
    text.append("Python News Gateway", style="bold")
    text.append("  ")
    text.append(status, style=f"bold {color}")
    text.append(f"  now={now}")
    text.append(f"  poll={metrics.get('current_poll_seconds') or gateway.current_poll_seconds():.1f}s")
    text.append(f"  data={gateway.config.data_root_win}")
    return Panel(text, box=box.SIMPLE, padding=(0, 1))


def metrics_table(gateway: "NewsGateway", metrics: dict[str, Any]) -> Table:
    table = Table(title="Live Summary", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Total", justify="right")
    table.add_column("Last Cycle", justify="right")
    table.add_row("Poll runs", fmt(metrics.get("poll_runs")), str(metrics.get("last_poll_at_utc") or "-"))
    table.add_row("Provider rows", fmt(metrics.get("provider_rows")), fmt(metrics.get("last_cycle_provider_rows")))
    table.add_row("Processed rows", fmt(metrics.get("processed_rows")), fmt(metrics.get("last_cycle_processed_rows")))
    table.add_row("Written rows", fmt(metrics.get("written_rows")), fmt(metrics.get("last_cycle_written_rows")))
    table.add_row("Skipped existing", fmt(metrics.get("skipped_existing")), fmt(metrics.get("last_cycle_skipped_existing")))
    table.add_row("Raw saved", fmt(metrics.get("raw_saved")), f"{float(metrics.get('last_cycle_wall_seconds') or 0.0):.2f}s")
    table.add_row("Failures", fmt(metrics.get("poll_failures")), str(metrics.get("last_error") or "-"))
    table.add_row("Mode", "execute" if gateway.config.execute else "dry-run", "workstation" if gateway.config.is_workstation else "remote")
    return table


def gap_panel(metrics: dict[str, Any]) -> Panel:
    status = str(metrics.get("gap_status") or "not_started")
    message = str(metrics.get("gap_message") or "")
    command = str(metrics.get("manual_gap_fill_command") or "")
    text = Text()
    text.append(status, style="bold cyan")
    if message:
        text.append(f"\n{message}")
    if command:
        text.append("\nmanual command:\n", style="bold yellow")
        text.append(command, style="yellow")
    return Panel(text, title="Gap Handling", box=box.SIMPLE, padding=(0, 1))


def news_table(snapshot: dict[str, Any]) -> Table:
    rows = snapshot.get("rows") or []
    table = Table(title=f"Latest News ({len(rows)})", box=box.SIMPLE, expand=True)
    table.add_column("Time", no_wrap=True, width=19)
    table.add_column("Tickers", no_wrap=True, max_width=28)
    table.add_column("Title", overflow="fold")
    table.add_column("Flags", no_wrap=True, max_width=24)
    for row in rows:
        tickers = ", ".join(row.get("tickers") or []) or "-"
        flags = ", ".join(row.get("content_quality_flags") or []) or "-"
        table.add_row(
            compact_time(str(row.get("published_at_utc") or "")),
            tickers,
            str(row.get("title") or "")[:180],
            flags,
        )
    if not rows:
        table.add_row("-", "-", "No news in memory yet.", "-")
    return table


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return str(value or "0")


def compact_time(value: str) -> str:
    text = value.replace("T", " ").replace("Z", "")
    return text[:19] if text else "-"
