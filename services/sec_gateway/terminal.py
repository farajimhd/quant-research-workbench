from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from services.sec_gateway.gateway import SecGateway


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


async def run_terminal_dashboard(gateway: "SecGateway") -> None:
    refresh = max(0.25, gateway.config.terminal_refresh_seconds)
    with Live(render_dashboard(gateway), auto_refresh=False, transient=False, screen=gateway.config.terminal_screen_enabled, vertical_overflow="crop") as live:
        while not gateway._stop_event.is_set():  # noqa: SLF001
            live.update(render_dashboard(gateway), refresh=True)
            await asyncio.sleep(refresh)


def render_dashboard(gateway: "SecGateway") -> Group:
    metrics = gateway.snapshot_metrics()
    return Group(
        header_panel(gateway, metrics),
        status_panel(metrics),
        preflight_panel(metrics),
        runtime_panel(gateway, metrics),
        gaps_panel(metrics),
        recent_table(gateway.recent_snapshot(12)),
    )


def header_panel(gateway: "SecGateway", metrics: dict[str, Any]) -> Panel:
    now = datetime.now(UTC)
    ch = gateway.config.pipeline.clickhouse
    table = Table.grid(expand=True)
    table.add_column(ratio=3)
    table.add_column(justify="right", ratio=4)
    table.add_row("[bold]Python SEC Gateway[/bold]", f"[dim]UTC[/dim] {clock(now)}  [dim]ET[/dim] {clock(now.astimezone(EASTERN))}  [dim]VAN[/dim] {clock(now.astimezone(VANCOUVER))}")
    table.add_row(
        f"[dim]bind[/dim] {gateway.config.bind}  [dim]poll[/dim] {gateway.current_poll_seconds():.1f}s",
        f"[dim]read[/dim] {ch.read_database}  [dim]write[/dim] {ch.write_database}  [dim]data[/dim] {gateway.config.pipeline.data_root_win}",
    )
    return Panel(table, box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def status_panel(metrics: dict[str, Any]) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("Phase", style_status(str(metrics.get("current_phase") or "-")))
    table.add_row("Message", str(metrics.get("current_phase_message") or "-"))
    table.add_row("Preflight", style_status(str(metrics.get("preflight_status") or "-")))
    table.add_row("Audit", style_status(str(metrics.get("audit_status") or "-")))
    table.add_row("Market", f"{str(metrics.get('market_status') or '-')} / {str(metrics.get('market_status_source') or '-')}")
    if metrics.get("audit_message"):
        table.add_row("Audit detail", str(metrics.get("audit_message") or "-"))
    if metrics.get("market_status_error"):
        table.add_row("Market error", str(metrics.get("market_status_error") or "-"))
    return Panel(table, title="Current Operation", box=box.ROUNDED, padding=(0, 1))


def preflight_panel(metrics: dict[str, Any]) -> Panel:
    checks = metrics.get("preflight_checks") or []
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Check", style="cyan", no_wrap=True, width=18)
    table.add_column("Status", no_wrap=True, width=10)
    table.add_column("Sec", justify="right", no_wrap=True, width=8)
    table.add_column("Message", overflow="fold", ratio=1)
    if not checks:
        table.add_row("preflight", style_status(str(metrics.get("preflight_status") or "not_started")), "-", "Waiting for dependency checks to report.")
    for check in checks:
        table.add_row(
            str(check.get("name") or "-"),
            style_status(str(check.get("status") or "-")),
            f"{float(check.get('wall_seconds') or 0.0):.2f}",
            str(check.get("message") or "-"),
        )
    return Panel(table, title="Dependencies", box=box.ROUNDED, padding=(0, 1), border_style="green" if metrics.get("preflight_status") == "ok" else "yellow")


def runtime_panel(gateway: "SecGateway", metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, width=20)
    table.add_column("Total", justify="right", no_wrap=True, width=14)
    table.add_column("Last / Detail", overflow="fold", ratio=1)
    table.add_row("Status", style_status(str(metrics.get("current_phase") or "-")), str(metrics.get("current_phase_message") or "-"))
    table.add_row("Poll cadence", f"{gateway.current_poll_seconds():.1f}s", "SEC current feed")
    table.add_row("Market", str(metrics.get("market_status") or "-"), str(metrics.get("market_status_source") or "-"))
    table.add_row("Last poll", "-", full_time_text(metrics.get("last_poll_at_utc")))
    table.add_row(
        "Live workers",
        f"{fmt(metrics.get('live_active_workers'))}/{fmt(metrics.get('live_workers'))}",
        f"queue {fmt(metrics.get('live_queue_size'))}/{fmt(metrics.get('live_queue_max_items'))}; {str(metrics.get('last_worker_message') or '-')}",
    )
    table.add_row("Poll runs", fmt(metrics.get("poll_runs")), "")
    table.add_row("Feed items", fmt(metrics.get("feed_items")), "")
    table.add_row("Processed", fmt(metrics.get("processed_filings")), "")
    table.add_row("Written", fmt(metrics.get("written_filings")), "")
    table.add_row("Skipped existing", fmt(metrics.get("skipped_existing")), "")
    table.add_row("Queued / completed", fmt(metrics.get("live_queued_filings")), f"completed {fmt(metrics.get('live_completed_filings'))}, worker failures {fmt(metrics.get('live_worker_failures'))}")
    table.add_row(
        "SEC caches",
        f"sub {fmt(metrics.get('submissions_cache_entries'))}/{fmt(metrics.get('submissions_cache_limit'))}",
        f"xbrl {fmt(metrics.get('xbrl_payload_cache_entries'))}/{fmt(metrics.get('xbrl_payload_cache_limit'))}; missing {fmt(metrics.get('xbrl_missing_cik_cache_entries'))}/{fmt(metrics.get('xbrl_missing_cik_cache_limit'))}",
    )
    table.add_row("XBRL facts", fmt(metrics.get("xbrl_company_fact_rows")), f"concepts {fmt(metrics.get('xbrl_concept_rows'))}, frames {fmt(metrics.get('xbrl_frame_rows'))}, observations {fmt(metrics.get('xbrl_frame_observation_rows'))}")
    table.add_row("Failures", fmt(metrics.get("poll_failures")), str(metrics.get("last_error") or "-"))
    table.add_row("Last accession", str(metrics.get("last_form_type") or "-"), str(metrics.get("last_accession") or "-"))
    return Panel(table, title="Runtime", box=box.ROUNDED, padding=(0, 1))


def gaps_panel(metrics: dict[str, Any]) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("State", style_status(str(metrics.get("gap_status") or "-")))
    table.add_row("Count", fmt(metrics.get("gap_count")))
    table.add_row("Intervals", fmt(metrics.get("coverage_interval_count")))
    table.add_row("Message", str(metrics.get("gap_message") or "-"))
    if metrics.get("manual_gap_fill_script_win"):
        table.add_row("Script", str(metrics.get("manual_gap_fill_script_win")))
    return Panel(table, title="Gap Handling", box=box.ROUNDED, padding=(0, 1))


def recent_table(snapshot: dict[str, Any]) -> Table:
    rows = snapshot.get("rows") or []
    table = Table(title=f"Latest SEC Feed Items ({len(rows)})", box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("ET", no_wrap=True, width=12)
    table.add_column("VAN", no_wrap=True, width=12)
    table.add_column("UTC", no_wrap=True, width=17)
    table.add_column("CIK", no_wrap=True, width=12)
    table.add_column("Form", no_wrap=True, width=10)
    table.add_column("Accession", no_wrap=True, width=22)
    table.add_column("Status", no_wrap=True, width=16)
    table.add_column("XBRL", justify="right", no_wrap=True, width=8)
    table.add_column("Title", overflow="fold", ratio=1)
    for row in rows:
        updated = parse_utc(row.get("updated_at_utc"))
        table.add_row(
            local_short(updated.astimezone(EASTERN)) if updated else "-",
            local_short(updated.astimezone(VANCOUVER)) if updated else "-",
            utc_short(updated) if updated else "-",
            str(row.get("cik") or "-"),
            str(row.get("form_type") or "-"),
            str(row.get("accession_number") or "-"),
            str(row.get("status") or "-"),
            fmt(row.get("xbrl_facts")),
            str(row.get("title") or ""),
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "[dim]No SEC feed items processed yet.[/dim]")
    return table


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return str(value or "-")


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        value_dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value_dt.tzinfo is None:
        return value_dt.replace(tzinfo=UTC)
    return value_dt.astimezone(UTC)


def clock(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def utc_short(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def full_time_text(value: Any) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        return "-"
    return (
        f"UTC {parsed.strftime('%Y-%m-%d %H:%M:%S')}  "
        f"ET {parsed.astimezone(EASTERN).strftime('%Y-%m-%d %H:%M:%S')}  "
        f"VAN {parsed.astimezone(VANCOUVER).strftime('%Y-%m-%d %H:%M:%S')}"
    )


def local_short(value: datetime) -> str:
    return value.strftime("%m-%d %H:%M")


def style_status(value: str) -> str:
    lowered = value.lower()
    if lowered in {"ok", "polling", "running"}:
        return f"[green]{value}[/green]"
    if lowered in {"failed", "error"}:
        return f"[red]{value}[/red]"
    if lowered in {"needs_action", "warn", "warning"}:
        return f"[yellow]{value}[/yellow]"
    return value
