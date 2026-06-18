from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rich import box
from rich.columns import Columns
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
        Columns(
            [preflight_panel(metrics), metrics_panel(gateway, metrics)],
            equal=True,
            expand=True,
        ),
        gap_panel(metrics),
        news_table(news_snapshot),
    )


def header_panel(gateway: "NewsGateway", metrics: dict[str, Any], now: str) -> Panel:
    status = str(metrics.get("last_cycle_status") or "starting")
    color = status_color(status)
    mode = "execute" if gateway.config.execute else "dry-run"
    location = "workstation" if gateway.config.is_workstation else "remote"
    poll = float(metrics.get("current_poll_seconds") or gateway.current_poll_seconds())
    grid = Table.grid(expand=True)
    grid.add_column(ratio=2)
    grid.add_column(justify="right", ratio=3)
    grid.add_row(
        f"[bold]Python News Gateway[/bold]  [{color}]{status_label(status)}[/{color}]",
        f"[dim]UTC[/dim] {now}   [dim]poll[/dim] {poll:.1f}s   [dim]mode[/dim] {mode}/{location}",
    )
    grid.add_row(
        f"[dim]bind[/dim] {gateway.config.bind}",
        f"[dim]data[/dim] {truncate(str(gateway.config.data_root_win), 96)}",
    )
    return Panel(grid, box=box.ROUNDED, border_style=color, padding=(0, 1))


def preflight_panel(metrics: dict[str, Any]) -> Panel:
    status = str(metrics.get("preflight_status") or "not_started")
    color = status_color(status)
    checks = metrics.get("preflight_checks") or []
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Check", style="cyan", no_wrap=True, width=18)
    table.add_column("Result", no_wrap=True, width=10)
    table.add_column("Sec", justify="right", no_wrap=True, width=6)
    table.add_column("Details", overflow="fold", ratio=1)
    if isinstance(checks, list) and checks:
        for check in checks:
            if not isinstance(check, dict):
                continue
            check_status = str(check.get("status") or "")
            check_color = status_color(check_status)
            table.add_row(
                labelize(str(check.get("name") or "-")),
                f"[{check_color}]{status_label(check_status)}[/{check_color}]",
                f"{float(check.get('wall_seconds') or 0.0):.2f}s",
                truncate(str(check.get("message") or "-"), 140),
            )
    else:
        table.add_row("-", "[yellow]WAIT[/yellow]", "-", "Dependency preflight has not run yet.")
    title = f"Dependencies [{color}]{status_label(status)}[/{color}]"
    checked = str(metrics.get("preflight_checked_at_utc") or "")
    if checked:
        title += f"  [dim]{compact_time(checked)}[/dim]"
    return Panel(table, title=title, box=box.ROUNDED, border_style=color, padding=(0, 1))


def metrics_panel(gateway: "NewsGateway", metrics: dict[str, Any]) -> Panel:
    status = str(metrics.get("last_cycle_status") or "starting")
    color = status_color(status)
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, ratio=2)
    table.add_column("Total", justify="right")
    table.add_column("Last Cycle", justify="right")
    table.add_row("Status", f"[{color}]{status_label(status)}[/{color}]", compact_time(str(metrics.get("last_poll_at_utc") or "")))
    table.add_row("Poll runs", fmt(metrics.get("poll_runs")), compact_time(str(metrics.get("last_poll_at_utc") or "")))
    table.add_row("Provider rows", fmt(metrics.get("provider_rows")), fmt(metrics.get("last_cycle_provider_rows")))
    table.add_row("Processed rows", fmt(metrics.get("processed_rows")), fmt(metrics.get("last_cycle_processed_rows")))
    table.add_row("Written rows", fmt(metrics.get("written_rows")), fmt(metrics.get("last_cycle_written_rows")))
    table.add_row("Skipped existing", fmt(metrics.get("skipped_existing")), fmt(metrics.get("last_cycle_skipped_existing")))
    table.add_row("Raw saved", fmt(metrics.get("raw_saved")), f"{float(metrics.get('last_cycle_wall_seconds') or 0.0):.2f}s")
    table.add_row("Failures", fmt(metrics.get("poll_failures")), truncate(str(metrics.get("last_error") or "-"), 120))
    mode = "execute" if gateway.config.execute else "dry-run"
    location = "workstation" if gateway.config.is_workstation else "remote"
    table.add_row("Mode", mode, location)
    return Panel(table, title="Runtime", box=box.ROUNDED, border_style=color, padding=(0, 1))


def gap_panel(metrics: dict[str, Any]) -> Panel:
    status = str(metrics.get("gap_status") or "not_started")
    message = str(metrics.get("gap_message") or "")
    command = str(metrics.get("manual_gap_fill_command") or "")
    script = str(metrics.get("manual_gap_fill_script_win") or "")
    manifest = str(metrics.get("manual_gap_fill_manifest_win") or "")
    color = gap_color(status)
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1)
    table.add_row("State", f"[{color}]{status_label(status)}[/{color}]")
    if message:
        table.add_row("Message", truncate(message, 180))
    if script:
        table.add_row("Script", f"[yellow]{script}[/yellow]")
    if manifest:
        table.add_row("Manifest", f"[yellow]{manifest}[/yellow]")
    if command:
        table.add_row("First command", f"[dim]{truncate(command, 220)}[/dim]")
    return Panel(table, title="Gap Handling", box=box.ROUNDED, border_style=color, padding=(0, 1))


def news_table(snapshot: dict[str, Any]) -> Table:
    rows = snapshot.get("rows") or []
    table = Table(title=f"Latest News ({len(rows)})", box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("Published UTC", no_wrap=True, width=19, style="dim")
    table.add_column("Tickers", no_wrap=True, max_width=30, style="bold magenta")
    table.add_column("Headline", overflow="fold", ratio=1)
    table.add_column("Flags", no_wrap=True, max_width=28, style="yellow")
    for row in rows:
        tickers = ", ".join(row.get("tickers") or []) or "-"
        flags = ", ".join(row.get("content_quality_flags") or []) or "-"
        table.add_row(
            compact_time(str(row.get("published_at_utc") or "")),
            tickers,
            truncate(str(row.get("title") or ""), 220),
            flags,
        )
    if not rows:
        table.add_row("-", "-", "[dim]No news in memory yet.[/dim]", "-")
    return table


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return str(value or "0")


def compact_time(value: str) -> str:
    text = value.replace("T", " ").replace("Z", "")
    return text[:19] if text else "-"


def status_color(status: str) -> str:
    text = status.strip().lower()
    if text in {"ok", "covered_by_live_lookback", "no_watermark"}:
        return "green"
    if text in {
        "starting",
        "not_started",
        "completed_with_errors",
        "auto_started",
        "workstation_auto_started_large_gap",
        "manual_required_large_gap",
    }:
        return "yellow"
    if text in {"failed"} or "failed" in text:
        return "red"
    return "cyan"


def gap_color(status: str) -> str:
    text = status.strip().lower()
    if text in {"covered_by_live_lookback", "no_watermark", "auto_completed"}:
        return "green"
    if text in {"auto_started", "workstation_auto_started_large_gap", "manual_required_large_gap", "not_started"}:
        return "yellow"
    return status_color(status)


def status_label(status: str) -> str:
    text = status.strip().replace("_", " ").upper()
    return text or "-"


def labelize(value: str) -> str:
    return value.replace("_", " ").title()


def truncate(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text or "-"
    return text[: max(0, limit - 1)].rstrip() + "..."
