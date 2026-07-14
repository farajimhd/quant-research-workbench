from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.rich_renderer import layout_profile, render_operational_dashboard, standard_live, status_color, style_status

if TYPE_CHECKING:
    from services.news_gateway.gateway import NewsGateway


MARKET_TZ = ZoneInfo("America/New_York")
VANCOUVER_TZ = ZoneInfo("America/Vancouver")


async def run_terminal_dashboard(gateway: "NewsGateway") -> None:
    refresh_seconds = max(0.25, gateway.config.terminal_refresh_seconds)
    initial_limit = terminal_recent_limit(gateway)
    initial_snapshot: dict[str, Any] = {"rows": [], "limit": initial_limit}
    with standard_live(
        render_dashboard(gateway, initial_snapshot),
        screen=gateway.config.terminal_screen_enabled,
        refresh_seconds=refresh_seconds,
    ) as live:
        while not gateway._stop_event.is_set():  # noqa: SLF001
            limit = terminal_recent_limit(gateway)
            snapshot = await gateway.state.recent_snapshot(limit)
            snapshot["limit"] = limit
            live.update(render_dashboard(gateway, snapshot), refresh=True)
            await asyncio.sleep(refresh_seconds)


def render_dashboard(gateway: "NewsGateway", news_snapshot: dict[str, Any]) -> Group:
    metrics = gateway.snapshot_metrics()
    profile = layout_profile()
    standard = build_dashboard_snapshot(
        service_name="news_gateway",
        config=gateway.config,
        metrics=metrics,
        recent_items=news_snapshot,
    )
    return render_operational_dashboard(
        standard,
        primary=news_pipeline_panel(metrics, compact=False),
        compact_primary=news_pipeline_panel(metrics, compact=True),
        secondary=news_cycle_panel(gateway, metrics),
        recent_factory=lambda limit: news_recent_panel(news_snapshot, limit=limit),
        recent_count=len(news_snapshot.get("rows") or []),
        profile=profile,
    )


def news_pipeline_panel(metrics: dict[str, Any], *, compact: bool) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Stage", style="cyan", no_wrap=True, width=14 if compact else 20)
    table.add_column("State", no_wrap=True, width=12)
    table.add_column("Progress", no_wrap=True, width=20 if compact else 30)
    table.add_column("Last trustworthy result", overflow="fold", ratio=1)
    bootstrap_total = int(metrics.get("bootstrap_probe_total") or 0)
    bootstrap_done = int(metrics.get("bootstrap_probe_completed") or 0)
    gap_total = int(metrics.get("gap_fill_total_chunks") or 0)
    gap_done = int(metrics.get("gap_fill_flushed_chunks") or 0)
    background_active = int(metrics.get("background_active_batches") or 0)
    background_queue = int(metrics.get("background_queue_size") or 0)
    table.add_row(
        "Provider poll",
        style_status(metrics.get("last_cycle_status") or metrics.get("current_phase") or "waiting"),
        f"runs {fmt(metrics.get('poll_runs'))}; rows {fmt(metrics.get('provider_rows'))}",
        f"last {compact_time(str(metrics.get('last_poll_at_utc') or ''))}; {metrics.get('current_market_session') or '-'}",
    )
    coverage_state = "running" if (bootstrap_total and bootstrap_done < bootstrap_total) or (gap_total and gap_done < gap_total) else (metrics.get("gap_status") or "idle")
    table.add_row(
        "Coverage",
        style_status(coverage_state),
        f"probes {progress_count(bootstrap_done, bootstrap_total)}; gaps {progress_count(gap_done, gap_total)}",
        truncate(str(metrics.get("gap_message") or "No active coverage job."), 180),
    )
    table.add_row(
        "Enrichment",
        style_status("running" if background_active or background_queue else "idle"),
        f"queue {background_queue}; active {background_active}; pending {fmt(metrics.get('background_pending_articles'))}",
        f"done {fmt(metrics.get('background_completed_articles'))}; failed {fmt(metrics.get('background_failed_articles'))}; {metrics.get('background_last_message') or '-'}",
    )
    table.add_row(
        "Database publish",
        style_status(metrics.get("publish_status") or "idle"),
        f"active {fmt(metrics.get('publish_active_jobs'))}; pending {fmt(metrics.get('publish_pending_rows'))}",
        f"jobs {fmt(metrics.get('publish_completed_jobs'))} done/{fmt(metrics.get('publish_failed_jobs'))} failed; last {compact_time(str(metrics.get('last_publish_at_utc') or ''))}; {metrics.get('publish_last_message') or '-'}",
    )
    return Panel(table, title="News Processing Pipeline", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def news_cycle_panel(gateway: "NewsGateway", metrics: dict[str, Any]) -> Panel:
    strategy = gateway.current_poll_strategy()
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Decision", style="cyan", no_wrap=True, width=20)
    table.add_column("Current", no_wrap=True, width=24)
    table.add_column("Evidence", overflow="fold", ratio=1)
    table.add_row("Market cadence", f"{float(metrics.get('current_poll_seconds') or strategy.poll_seconds):.1f}s", f"session {metrics.get('current_market_session') or strategy.session}; lookback {int(metrics.get('current_lookback_minutes') or strategy.lookback_minutes)}m")
    table.add_row("Last cycle", style_status(metrics.get("last_cycle_status") or "waiting"), f"provider {fmt(metrics.get('last_cycle_provider_rows'))}; unique {fmt(metrics.get('last_cycle_unique_news_rows'))}; duplicate {fmt(metrics.get('last_cycle_duplicate_news_rows'))}; written {fmt(metrics.get('last_cycle_written_rows'))}; {float(metrics.get('last_cycle_wall_seconds') or 0.0):.2f}s")
    table.add_row("Durable totals", fmt(metrics.get("written_rows")), f"unique {fmt(metrics.get('unique_news_rows'))}; skipped existing {fmt(metrics.get('skipped_existing'))}; failed rows {fmt(metrics.get('failed_rows'))}")
    table.add_row("Memory state", fmt(metrics.get("memory_recent_rows")), f"seen ids {fmt(metrics.get('memory_seen_ids'))}; ticker keys {fmt(metrics.get('memory_ticker_keys'))}; metadata TTL {float(metrics.get('memory_metadata_retention_hours') or 0.0):.1f}h")
    return Panel(table, title="Cycle And Freshness", box=box.ROUNDED, border_style="green" if not metrics.get("poll_failures") else "yellow", padding=(0, 1))


def news_recent_panel(snapshot: dict[str, Any], *, limit: int) -> Panel:
    rows = list(snapshot.get("rows") or [])[:limit]
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("UTC", no_wrap=True, width=16)
    table.add_column("Tickers", no_wrap=True, width=16)
    table.add_column("Process", no_wrap=True, width=20)
    table.add_column("Headline", overflow="fold", ratio=1)
    for row in rows:
        table.add_row(compact_time(str(row.get("published_at_utc") or ""))[:16], truncate(", ".join(row.get("tickers") or []) or "-", 16), processing_status(row), truncate(str(row.get("title") or "-"), 180))
    if not rows:
        table.add_row("-", "-", style_status("waiting"), "No news outcome recorded yet.")
    return Panel(table, title="Recent News Outcomes", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def terminal_recent_limit(gateway: "NewsGateway") -> int:
    profile = layout_profile()
    return min(
        max(1, int(gateway.config.recent_history_limit)),
        max(1, int(gateway.config.terminal_news_limit), int(profile["height"])),
    )


def header_panel(gateway: "NewsGateway", metrics: dict[str, Any], now: str) -> Panel:
    status = operational_status(metrics)
    color = status_color(status)
    mode = "execute" if gateway.config.execute else "dry-run"
    location = "workstation" if gateway.config.is_workstation else "remote"
    strategy = gateway.current_poll_strategy()
    poll = float(metrics.get("current_poll_seconds") or strategy.poll_seconds)
    lookback = int(metrics.get("current_lookback_minutes") or strategy.lookback_minutes)
    session = str(metrics.get("current_market_session") or strategy.session)
    market_status = str(metrics.get("market_status") or "-")
    status_source = str(metrics.get("market_status_source") or "-")
    lookback_text = f"   [dim]window[/dim] {lookback}m" if lookback else ""
    grid = Table.grid(expand=True)
    grid.add_column(ratio=2)
    grid.add_column(justify="right", ratio=3)
    grid.add_row(
        f"[bold]Python News Gateway[/bold]  [{color}]{status_label(status)}[/{color}]",
        f"[dim]UTC[/dim] {now}   [dim]poll[/dim] {poll:.1f}s{lookback_text}   [dim]session[/dim] {session}   [dim]mode[/dim] {mode}/{location}",
    )
    grid.add_row(
        f"[dim]bind[/dim] {gateway.config.bind}",
        f"[dim]market[/dim] {market_status}/{status_source}   [dim]data[/dim] {truncate(str(gateway.config.data_root_win), 80)}",
    )
    return Panel(grid, box=box.ROUNDED, border_style=color, padding=(0, 1))


def phase_panel(metrics: dict[str, Any]) -> Panel:
    phase = str(metrics.get("current_phase") or "starting")
    message = str(metrics.get("current_phase_message") or "")
    started = compact_time(str(metrics.get("current_phase_started_at_utc") or ""))
    color = status_color(phase)
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1)
    table.add_row("Current phase", f"[{color}]{status_label(phase)}[/{color}]")
    table.add_row("Since", started)
    table.add_row("Message", truncate(message, 220))
    return Panel(table, title="Current Operation", box=box.ROUNDED, border_style=color, padding=(0, 1))


def progress_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Background Job", style="cyan", no_wrap=True, width=24)
    table.add_column("Status", no_wrap=True, width=12)
    table.add_column("Progress", no_wrap=True, width=34)
    table.add_column("Queue", justify="right", no_wrap=True, width=10)
    table.add_column("Active", justify="right", no_wrap=True, width=10)
    table.add_column("Done", justify="right", no_wrap=True, width=12)
    table.add_column("Failed", justify="right", no_wrap=True, width=10)
    table.add_column("Detail", overflow="fold", ratio=1)

    bootstrap_total = int(metrics.get("bootstrap_probe_total") or 0)
    bootstrap_done = int(metrics.get("bootstrap_probe_completed") or 0)
    bootstrap_empty = int(metrics.get("bootstrap_probe_empty") or 0)
    bootstrap_positive = int(metrics.get("bootstrap_probe_positive") or 0)
    gap_total = int(metrics.get("gap_fill_total_chunks") or 0)
    gap_flushed = int(metrics.get("gap_fill_flushed_chunks") or 0)
    gap_submitted = int(metrics.get("gap_fill_submitted_chunks") or 0)
    gap_in_flight = int(metrics.get("gap_fill_in_flight_chunks") or 0)
    publish_status = str(metrics.get("publish_status") or "idle")
    publish_active = int(metrics.get("publish_active_jobs") or 0)
    publish_pending_rows = int(metrics.get("publish_pending_rows") or 0)
    publish_completed = int(metrics.get("publish_completed_jobs") or 0)
    publish_failed = int(metrics.get("publish_failed_jobs") or 0)
    background_queue = int(metrics.get("background_queue_size") or 0)
    background_active = int(metrics.get("background_active_batches") or 0)
    background_pending_articles = int(metrics.get("background_pending_articles") or 0)
    background_completed_articles = int(metrics.get("background_completed_articles") or 0)
    background_failed_articles = int(metrics.get("background_failed_articles") or 0)
    background_fetch_tasks = int(metrics.get("background_fetch_tasks") or 0)
    background_enriched_urls = int(metrics.get("background_enriched_urls") or 0)

    table.add_row(
        "Coverage probes",
        "running" if bootstrap_total and bootstrap_done < bootstrap_total else "idle",
        progress_text(bootstrap_done, bootstrap_total),
        "-",
        "-",
        progress_count(bootstrap_done, bootstrap_total),
        "-",
        f"empty {bootstrap_empty:,}; needs fill {bootstrap_positive:,}" if bootstrap_total else "[dim]No bootstrap probe job active.[/dim]",
    )
    table.add_row(
        "Startup gap fill",
        "running" if gap_total and gap_flushed < gap_total else "idle",
        progress_text(gap_flushed, gap_total),
        "-",
        f"{gap_in_flight:,}" if gap_total else "-",
        progress_count(gap_flushed, gap_total),
        "-",
        f"submitted {gap_submitted:,}" if gap_total else "[dim]No startup gap-fill job active.[/dim]",
    )
    table.add_row(
        "News background",
        "running" if background_queue or background_active else "idle",
        busy_text("running" if background_queue or background_active else "idle", background_queue + background_active),
        f"{background_queue:,}",
        f"{background_active:,}",
        f"{background_completed_articles:,}",
        f"{background_failed_articles:,}",
        f"pending articles {background_pending_articles:,}; url tasks {background_fetch_tasks:,}; enriched urls {background_enriched_urls:,}",
    )
    table.add_row(
        "Database publish",
        publish_status,
        busy_text(publish_status, publish_active),
        f"{publish_pending_rows:,}",
        f"{publish_active:,}",
        f"{publish_completed:,}",
        f"{publish_failed:,}",
        "normalized/ticker rows are published asynchronously",
    )
    active = (
        (bootstrap_total > 0 and bootstrap_done < bootstrap_total)
        or (gap_total > 0 and gap_flushed < gap_total)
        or background_queue > 0
        or background_active > 0
        or publish_active > 0
        or publish_status == "draining"
    )
    color = "yellow" if active else "green"
    title = "Background Progress" if active else "Background Progress [dim]idle[/dim]"
    return Panel(table, title=title, box=box.ROUNDED, border_style=color, padding=(0, 1))


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
    status = operational_status(metrics)
    color = status_color(status)
    table = Table(box=box.SIMPLE, expand=False, show_edge=False, padding=(0, 1))
    table.add_column("Metric", style="cyan", no_wrap=True, width=18)
    table.add_column("Total", justify="right", no_wrap=True, width=13)
    table.add_column("Last Cycle", justify="right", no_wrap=True, min_width=19, width=21)
    table.add_row("Status", f"[{color}]{status_label(status)}[/{color}]", compact_time(str(metrics.get("last_poll_at_utc") or "")))
    table.add_row("Last poll", status_label(str(metrics.get("last_cycle_status") or "-")), compact_time(str(metrics.get("last_poll_at_utc") or "")))
    table.add_row("Poll runs", fmt(metrics.get("poll_runs")), compact_time(str(metrics.get("last_poll_at_utc") or "")))
    table.add_row("Provider rows", fmt(metrics.get("provider_rows")), fmt(metrics.get("last_cycle_provider_rows")))
    table.add_row("Processed rows", fmt(metrics.get("processed_rows")), fmt(metrics.get("last_cycle_processed_rows")))
    table.add_row("Unique news", fmt(metrics.get("unique_news_rows")), fmt(metrics.get("last_cycle_unique_news_rows")))
    table.add_row("Duplicate rows", fmt(metrics.get("duplicate_news_rows")), fmt(metrics.get("last_cycle_duplicate_news_rows")))
    table.add_row("Written rows", fmt(metrics.get("written_rows")), fmt(metrics.get("last_cycle_written_rows")))
    table.add_row("Skipped existing", fmt(metrics.get("skipped_existing")), fmt(metrics.get("last_cycle_skipped_existing")))
    table.add_row("Raw saved", fmt(metrics.get("raw_saved")), f"{float(metrics.get('last_cycle_wall_seconds') or 0.0):.2f}s")
    table.add_row(
        "Memory state",
        fmt(metrics.get("memory_recent_rows")),
        f"seen={fmt(metrics.get('memory_seen_ids'))} tickers={fmt(metrics.get('memory_ticker_keys'))} ttl={float(metrics.get('memory_metadata_retention_hours') or 0.0):.1f}h",
    )
    table.add_row("Failures", fmt(metrics.get("poll_failures")), truncate(str(metrics.get("last_error") or "-"), 120))
    mode = "execute" if gateway.config.execute else "dry-run"
    location = "workstation" if gateway.config.is_workstation else "remote"
    table.add_row("Mode", mode, location)
    strategy = gateway.current_poll_strategy()
    poll_seconds = float(metrics.get("current_poll_seconds") or strategy.poll_seconds)
    lookback_minutes = int(metrics.get("current_lookback_minutes") or strategy.lookback_minutes)
    table.add_row("Schedule", f"{poll_seconds:.1f}s", f"{lookback_minutes:,}m window")
    table.add_row("Market", str(metrics.get("market_status") or "-"), str(metrics.get("market_status_source") or "-"))
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
    row_limit = max(1, int(snapshot.get("limit") or len(rows) or 1))
    display_rows = list(rows[:row_limit])
    table = Table(title=f"Latest News ({len(rows)})", box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("ET", no_wrap=True, width=8, style="cyan")
    table.add_column("VAN", no_wrap=True, width=8, style="cyan")
    table.add_column("UTC", no_wrap=True, width=16, style="dim")
    table.add_column("Tickers", no_wrap=True, width=18, style="bold magenta")
    table.add_column("Headline", overflow="fold", ratio=1)
    table.add_column("Flags", no_wrap=True, width=24, style="yellow")
    table.add_column("Process", no_wrap=True, width=30)
    for row in display_rows:
        published_at = str(row.get("published_at_utc") or "")
        tickers = truncate(", ".join(row.get("tickers") or []) or "-", 18)
        flags = truncate(", ".join(row.get("content_quality_flags") or []) or "-", 24)
        table.add_row(
            local_time(published_at, MARKET_TZ),
            local_time(published_at, VANCOUVER_TZ),
            compact_time(published_at)[:16] if published_at else "-",
            tickers,
            truncate(str(row.get("title") or ""), 220),
            flags,
            processing_status(row),
        )
    while len(display_rows) < row_limit:
        table.add_row("-", "-", "-", "-", "[dim]No news in memory yet.[/dim]" if not rows and not display_rows else "", "-", "-")
        display_rows.append({})
    return table


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return str(value or "0")


def compact_time(value: str) -> str:
    text = value.replace("T", " ").replace("Z", "")
    return text[:19] if text else "-"


def parse_utc_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
        return datetime.fromisoformat(text.replace(" ", "T") + "+00:00").astimezone(UTC)
    except ValueError:
        return None


def local_time(value: str, timezone: ZoneInfo) -> str:
    parsed = parse_utc_datetime(value)
    if parsed is None:
        return "-"
    return parsed.astimezone(timezone).strftime("%H:%M:%S")


def processing_status(row: dict[str, Any]) -> str:
    flags = {str(item) for item in row.get("content_quality_flags") or []}
    external_status = str(row.get("external_fetch_status") or "")
    pdf_status = str(row.get("pdf_extract_status") or "")
    has_external = int(row.get("has_external_text") or 0)
    has_pdf = int(row.get("has_pdf") or 0)
    normalizer = str(row.get("normalizer_version") or "")

    enrich = "PEND" if "background_pending" in flags or external_status == "background_pending" else "BASE"
    if "background_enrichment_failed" in flags or "failed" in external_status:
        enrich = "FAIL"
    elif has_external:
        enrich = "TXT"
    elif external_status in {"", "not_attempted"}:
        enrich = "BASE"
    elif external_status:
        enrich = compact_process_code(external_status)

    pdf = "YES" if has_pdf else "NO"
    if "failed" in pdf_status:
        pdf = "FAIL"
    elif pdf_status and pdf_status not in {"not_attempted", "no_pdf"}:
        pdf = compact_process_code(pdf_status)

    canonical = "OK" if normalizer else "MISS"
    return f"E:{enrich} PDF:{pdf} C:{canonical}"


def operational_status(metrics: dict[str, Any]) -> str:
    phase = str(metrics.get("current_phase") or "").strip().lower()
    last_status = str(metrics.get("last_cycle_status") or "").strip().lower()
    publish_status = str(metrics.get("publish_status") or "").strip().lower()
    active_publish_jobs = int(metrics.get("publish_active_jobs") or 0)
    active_background_batches = int(metrics.get("background_active_batches") or 0)
    queued_background_batches = int(metrics.get("background_queue_size") or 0)
    pending_background_articles = int(metrics.get("background_pending_articles") or 0)
    unresolved_publish_failure = publish_status == "failed" and (active_publish_jobs > 0 or int(metrics.get("publish_pending_rows") or 0) > 0)
    unresolved_background_failure = (
        phase == "live_background_process"
        and int(metrics.get("background_failed_batches") or 0) > int(metrics.get("background_completed_batches") or 0)
        and (active_background_batches > 0 or queued_background_batches > 0 or pending_background_articles > 0)
    )
    if phase == "failed" or unresolved_publish_failure or unresolved_background_failure:
        return "failed"
    if phase.startswith("shutdown"):
        return "stopping"
    if active_publish_jobs or publish_status in {"running", "draining"}:
        return "publishing"
    if queued_background_batches or active_background_batches or pending_background_articles:
        return "processing"
    if int(metrics.get("gap_fill_in_flight_chunks") or 0) or phase.startswith("gap_fill"):
        return "gap fill"
    if phase in {"preflight", "coverage_bootstrap", "gap_planning", "live_fetch", "live_process"}:
        return phase
    if int(metrics.get("poll_failures") or 0) or int(metrics.get("publish_failed_jobs") or 0) or int(metrics.get("background_failed_batches") or 0):
        return "warning"
    if phase == "polling":
        return "idle" if last_status in {"queued", "no_rows", "ok"} else (last_status or "polling")
    return last_status or phase or "starting"


def compact_process_code(value: str) -> str:
    text = value.strip().lower()
    if not text or text in {"not_attempted", "no_pdf"}:
        return "-"
    if "pending" in text:
        return "PEND"
    if "failed" in text or "error" in text:
        return "FAIL"
    if "partial" in text:
        return "PART"
    if "missing" in text:
        return "MISS"
    if "empty" in text:
        return "EMPTY"
    if "success" in text or "complete" in text or "extracted" in text:
        return "OK"
    if "artifact" in text:
        return "ART"
    return text.replace("background_", "bg_").replace("provider_verified_", "").replace("_", "-")[:6].upper()


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


def progress_count(done: int, total: int) -> str:
    if total <= 0:
        return "-"
    return f"{max(0, min(done, total)):,}/{total:,}"


def progress_text(done: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[dim]" + ("-" * width) + " -[/dim]"
    bounded_done = max(0, min(done, total))
    ratio = bounded_done / total
    filled = int(round(ratio * width))
    bar = "#" * filled + "-" * (width - filled)
    return f"[green]{bar}[/green] {ratio * 100:5.1f}%"


def busy_text(status: str, active: int, width: int = 30) -> str:
    normalized = status.strip().lower()
    if active <= 0 and normalized in {"", "idle"}:
        return "[dim]" + ("-" * width) + " idle[/dim]"
    if normalized == "failed":
        return "[red]" + ("!" * min(width, 8)).ljust(width, "-") + " failed[/red]"
    marker = "#" * min(width, max(3, active))
    return f"[yellow]{marker.ljust(width, '-')}[/yellow] {status_label(normalized)}"
