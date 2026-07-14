from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.rich_renderer import layout_profile, render_operational_dashboard
from services.gateway_core.rich_renderer import standard_live, style_status

if TYPE_CHECKING:
    from services.sec_gateway.gateway import SecGateway


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


async def run_terminal_dashboard(gateway: "SecGateway") -> None:
    refresh = max(0.25, gateway.config.terminal_refresh_seconds)
    with standard_live(render_dashboard(gateway), screen=gateway.config.terminal_screen_enabled, refresh_seconds=refresh) as live:
        while not gateway._stop_event.is_set():  # noqa: SLF001
            live.update(render_dashboard(gateway), refresh=True)
            await asyncio.sleep(refresh)


def render_dashboard(gateway: "SecGateway") -> Group:
    metrics = gateway.snapshot_metrics()
    profile = layout_profile()
    recent_snapshot = gateway.recent_snapshot(max(1, int(profile["height"])))
    standard = build_dashboard_snapshot(
        service_name="sec_gateway",
        config=gateway.config,
        metrics=metrics,
        recent_items=recent_snapshot,
    )
    return render_operational_dashboard(
        standard,
        primary=sec_pipeline_panel(gateway, metrics, compact=False),
        compact_primary=sec_pipeline_panel(gateway, metrics, compact=True),
        secondary=sec_integrity_panel(metrics),
        recent_factory=lambda limit: sec_recent_panel(recent_snapshot, limit=limit),
        recent_count=len(recent_snapshot.get("rows") or []),
        profile=profile,
    )


def sec_pipeline_panel(gateway: "SecGateway", metrics: dict[str, Any], *, compact: bool) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Stage", style="cyan", no_wrap=True, width=14 if compact else 18)
    table.add_column("State", no_wrap=True, width=12)
    table.add_column("Progress", no_wrap=True, width=20 if compact else 28)
    table.add_column("Last trustworthy result", overflow="fold", ratio=1)
    active_accessions = metrics.get("live_worker_accessions") if isinstance(metrics.get("live_worker_accessions"), dict) else {}
    active_text = ", ".join(str(value) for value in list(active_accessions.values())[:3]) or "none"
    table.add_row(
        "Feed poll",
        style_status(str(metrics.get("current_phase") or "waiting")),
        f"runs {fmt(metrics.get('poll_runs'))}; items {fmt(metrics.get('feed_items'))}",
        f"last {full_time_text(metrics.get('last_poll_at_utc'))}; cadence {gateway.current_poll_seconds():.1f}s",
    )
    table.add_row(
        "Filing workers",
        style_status("running" if metrics.get("live_active_workers") else "idle"),
        f"active {fmt(metrics.get('live_active_workers'))}/{fmt(metrics.get('live_workers'))}; queue {fmt(metrics.get('live_queue_size'))}/{fmt(metrics.get('live_queue_max_items'))}",
        f"accessions {active_text}",
    )
    table.add_row(
        "Durable outcome",
        style_status("warning" if metrics.get("live_worker_failures") else "ok"),
        f"done {fmt(metrics.get('live_completed_filings'))}; written {fmt(metrics.get('written_filings'))}",
        f"skipped {fmt(metrics.get('skipped_existing'))}; failed {fmt(metrics.get('live_worker_failures'))}; last write {compact_datetime(metrics.get('last_write_at_utc'))}",
    )
    table.add_row(
        "Latest filing",
        style_status("ok" if metrics.get("last_accession") else "waiting"),
        f"{metrics.get('last_form_type') or '-'}  {metrics.get('last_accession') or '-'}",
        f"completed {compact_datetime(metrics.get('last_success_at_utc'))}; {metrics.get('last_worker_message') or '-'}",
    )
    return Panel(table, title="SEC Filing Pipeline", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def sec_integrity_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Guardrail", style="cyan", no_wrap=True, width=20)
    table.add_column("State", no_wrap=True, width=14)
    table.add_column("Evidence / Action", overflow="fold", ratio=1)
    table.add_row("Preflight", style_status(metrics.get("preflight_status")), str(metrics.get("preflight_checked_at_utc") or "not checked"))
    table.add_row("Coverage", style_status(metrics.get("gap_status")), f"intervals {fmt(metrics.get('coverage_interval_count'))}; {metrics.get('gap_message') or '-'}")
    table.add_row("Write audit", style_status(metrics.get("audit_status")), str(metrics.get("audit_message") or "not reported"))
    table.add_row(
        "XBRL context",
        style_status("warning" if metrics.get("xbrl_context_pending_rows") or metrics.get("xbrl_context_sync_failures") else "ok"),
        f"rows {fmt(metrics.get('xbrl_context_rows'))}; pending {fmt(metrics.get('xbrl_context_pending_rows'))}; failures {fmt(metrics.get('xbrl_context_sync_failures'))}",
    )
    if float(metrics.get("sec_request_cooldown_remaining_seconds") or 0.0) > 0:
        table.add_row("Provider cooldown", style_status("degraded"), f"{float(metrics.get('sec_request_cooldown_remaining_seconds') or 0.0):.0f}s; {metrics.get('sec_request_cooldown_reason') or '-'}")
    return Panel(table, title="Coverage And Integrity", box=box.ROUNDED, border_style="green" if not metrics.get("xbrl_context_sync_failures") else "yellow", padding=(0, 1))


def sec_recent_panel(snapshot: dict[str, Any], *, limit: int) -> Panel:
    rows = snapshot.get("rows") or []
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("UTC", no_wrap=True, width=17)
    table.add_column("Form", no_wrap=True, width=9)
    table.add_column("Accession", no_wrap=True, width=22)
    table.add_column("State", no_wrap=True, width=14)
    table.add_column("Title", overflow="fold", ratio=1)
    for row in rows[:limit]:
        table.add_row(utc_short(parse_utc(row.get("updated_at_utc"))) if parse_utc(row.get("updated_at_utc")) else "-", str(row.get("form_type") or "-"), str(row.get("accession_number") or "-"), style_status(row.get("status") or "-"), str(row.get("title") or "-"))
    if not rows:
        table.add_row("-", "-", "-", style_status("waiting"), "No filing outcome recorded yet.")
    return Panel(table, title="Recent Filing Outcomes", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def compact_datetime(value: Any) -> str:
    parsed = parse_utc(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC") if parsed else "-"


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
        f"sub_ttl={ttl_text(metrics.get('submissions_cache_max_age_seconds'))}; xbrl {fmt(metrics.get('xbrl_payload_cache_entries'))}/{fmt(metrics.get('xbrl_payload_cache_limit'))} ttl={ttl_text(metrics.get('xbrl_payload_cache_max_age_seconds'))}; missing {fmt(metrics.get('xbrl_missing_cik_cache_entries'))}/{fmt(metrics.get('xbrl_missing_cik_cache_limit'))}",
    )
    table.add_row("Recent metadata", fmt(metrics.get("recent_metadata_rows")), f"ttl={float(metrics.get('recent_metadata_retention_hours') or 0.0):.1f}h")
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


def ttl_text(value: Any) -> str:
    seconds = float(value or 0.0)
    if seconds <= 0:
        return "off"
    if seconds < 3600:
        return f"{seconds / 60.0:.0f}m"
    return f"{seconds / 3600.0:.1f}h"


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
