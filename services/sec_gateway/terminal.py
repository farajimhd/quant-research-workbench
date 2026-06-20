from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from services.sec_gateway.gateway import SecGateway


async def run_terminal_dashboard(gateway: "SecGateway") -> None:
    refresh = max(0.25, gateway.config.terminal_refresh_seconds)
    with Live(render_dashboard(gateway), auto_refresh=False, transient=False, screen=gateway.config.terminal_screen_enabled, vertical_overflow="crop") as live:
        while not gateway._stop_event.is_set():  # noqa: SLF001
            live.update(render_dashboard(gateway), refresh=True)
            await asyncio.sleep(refresh)


def render_dashboard(gateway: "SecGateway") -> Group:
    metrics = gateway.snapshot_metrics()
    return Group(header_panel(gateway, metrics), status_panel(metrics), runtime_panel(metrics), gaps_panel(metrics), recent_table(gateway.recent_snapshot(12)))


def header_panel(gateway: "SecGateway", metrics: dict[str, Any]) -> Panel:
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    table = Table.grid(expand=True)
    table.add_column(ratio=2)
    table.add_column(justify="right", ratio=3)
    table.add_row("[bold]Python SEC Gateway[/bold]", f"[dim]UTC[/dim] {now}  [dim]poll[/dim] {gateway.current_poll_seconds():.1f}s")
    table.add_row(f"[dim]bind[/dim] {gateway.config.bind}", f"[dim]data[/dim] {gateway.config.pipeline.data_root_win}")
    return Panel(table, box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def status_panel(metrics: dict[str, Any]) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1)
    table.add_row("Phase", str(metrics.get("current_phase") or "-"))
    table.add_row("Message", truncate(str(metrics.get("current_phase_message") or "-"), 180))
    table.add_row("Preflight", str(metrics.get("preflight_status") or "-"))
    table.add_row("Audit", str(metrics.get("audit_status") or "-"))
    return Panel(table, title="Current Operation", box=box.ROUNDED, padding=(0, 1))


def runtime_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, width=20)
    table.add_column("Value", justify="right", no_wrap=True, width=18)
    table.add_column("Details", overflow="fold", ratio=1)
    table.add_row("Poll runs", fmt(metrics.get("poll_runs")), str(metrics.get("last_poll_at_utc") or "-"))
    table.add_row("Feed items", fmt(metrics.get("feed_items")), "")
    table.add_row("Processed", fmt(metrics.get("processed_filings")), "")
    table.add_row("Written", fmt(metrics.get("written_filings")), "")
    table.add_row("Skipped existing", fmt(metrics.get("skipped_existing")), "")
    table.add_row("Failures", fmt(metrics.get("poll_failures")), truncate(str(metrics.get("last_error") or "-"), 120))
    table.add_row("Last accession", str(metrics.get("last_accession") or "-"), str(metrics.get("last_form_type") or ""))
    return Panel(table, title="Runtime", box=box.ROUNDED, padding=(0, 1))


def gaps_panel(metrics: dict[str, Any]) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1)
    table.add_row("State", str(metrics.get("gap_status") or "-"))
    table.add_row("Count", fmt(metrics.get("gap_count")))
    table.add_row("Message", truncate(str(metrics.get("gap_message") or "-"), 180))
    if metrics.get("manual_gap_fill_script_win"):
        table.add_row("Script", str(metrics.get("manual_gap_fill_script_win")))
    return Panel(table, title="Gap Handling", box=box.ROUNDED, padding=(0, 1))


def recent_table(snapshot: dict[str, Any]) -> Table:
    rows = snapshot.get("rows") or []
    table = Table(title=f"Latest SEC Feed Items ({len(rows)})", box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("Updated", no_wrap=True, width=18)
    table.add_column("CIK", no_wrap=True, width=12)
    table.add_column("Form", no_wrap=True, width=10)
    table.add_column("Accession", no_wrap=True, width=22)
    table.add_column("Status", no_wrap=True, width=16)
    table.add_column("Title", overflow="fold", ratio=1)
    for row in rows:
        table.add_row(
            str(row.get("updated_at_utc") or "-")[:18],
            str(row.get("cik") or "-"),
            str(row.get("form_type") or "-"),
            str(row.get("accession_number") or "-"),
            str(row.get("status") or "-"),
            truncate(str(row.get("title") or ""), 180),
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "[dim]No SEC feed items processed yet.[/dim]")
    return table


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return str(value or "-")


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 14].rstrip() + "...<truncated>"
