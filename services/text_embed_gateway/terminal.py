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
    from services.text_embed_gateway.gateway import TextEmbedGateway


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


async def run_terminal_dashboard(gateway: "TextEmbedGateway") -> None:
    refresh = max(0.25, gateway.config.terminal_refresh_seconds)
    with Live(render_dashboard(gateway), auto_refresh=False, transient=False, screen=gateway.config.terminal_screen_enabled, vertical_overflow="crop") as live:
        while not gateway._stop_event.is_set():  # noqa: SLF001
            live.update(render_dashboard(gateway), refresh=True)
            await asyncio.sleep(refresh)


def render_dashboard(gateway: "TextEmbedGateway") -> Group:
    metrics = gateway.snapshot_metrics()
    return Group(
        header_panel(gateway, metrics),
        status_panel(metrics),
        work_focus_panel(metrics),
        progress_panel(metrics),
        cycle_summary_panel(metrics),
        coverage_report_panel(metrics),
        gap_summary_panel(metrics),
        timing_panel(metrics),
        runtime_panel(gateway, metrics),
        recent_table(gateway.recent_snapshot(12)),
    )


def header_panel(gateway: "TextEmbedGateway", metrics: dict[str, Any]) -> Panel:
    now = datetime.now(UTC)
    color = status_color(str(metrics.get("current_phase") or ""))
    table = Table.grid(expand=True)
    table.add_column(ratio=3)
    table.add_column(justify="right", ratio=4)
    table.add_row(
        f"[bold]Text Embedding Gateway[/bold]  [{color}]{status_label(str(metrics.get('current_phase') or '-'))}[/{color}]",
        f"[dim]UTC[/dim] {clock(now)}  [dim]ET[/dim] {clock(now.astimezone(EASTERN))}  [dim]VAN[/dim] {clock(now.astimezone(VANCOUVER))}",
    )
    table.add_row(
        f"[dim]bind[/dim] {gateway.config.bind}  [dim]model[/dim] {gateway.config.embedding_model}",
        f"[dim]target[/dim] {gateway.config.target_database}  [dim]data[/dim] {truncate(str(gateway.config.data_root_win), 90)}",
    )
    return Panel(table, box=box.ROUNDED, border_style=color, padding=(0, 1))


def status_panel(metrics: dict[str, Any]) -> Panel:
    phase = str(metrics.get("current_phase") or "-")
    color = status_color(phase)
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("Phase", f"[{color}]{status_label(phase)}[/{color}]")
    table.add_row("Message", str(metrics.get("current_phase_message") or "-"))
    if metrics.get("active_stage"):
        focus = " / ".join(
            part
            for part in (
                mode_label(str(metrics.get("active_mode") or "")),
                source_label(str(metrics.get("active_source") or "")),
                stage_label(metrics.get("active_stage")),
            )
            if part and part != "-"
        )
        table.add_row("Focus", focus or "-")
    if metrics.get("active_window_utc"):
        table.add_row("Window UTC", str(metrics.get("active_window_utc") or "-").replace("T", " ").replace("Z", ""))
    if metrics.get("next_poll_at_utc"):
        table.add_row("Next poll UTC", compact_time(str(metrics.get("next_poll_at_utc") or "")))
    if metrics.get("poll_cadence_label"):
        table.add_row("Cadence", f"{metrics.get('poll_cadence_label')} ({metrics.get('poll_cadence_reason') or '-'})")
    table.add_row("Model", style_status(str(metrics.get("model_status") or "-")))
    table.add_row("Market", f"{str(metrics.get('market_status') or '-')} / {str(metrics.get('market_status_source') or '-')}")
    if metrics.get("market_status_error"):
        table.add_row("Market error", str(metrics.get("market_status_error") or "-"))
    if metrics.get("last_error"):
        table.add_row("Last error", str(metrics.get("last_error") or "-"))
    return Panel(table, title="Current Operation", box=box.ROUNDED, border_style=color, padding=(0, 1))


def work_focus_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Item", style="cyan", no_wrap=True, width=18)
    table.add_column("Mode", no_wrap=True, width=12)
    table.add_column("Source", no_wrap=True, width=8)
    table.add_column("Stage", no_wrap=True, width=18)
    table.add_column("Rows/Seq", justify="right", no_wrap=True, width=12)
    table.add_column("Timing", no_wrap=True, width=34)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row(
        "Active focus",
        mode_label(str(metrics.get("active_mode") or "")),
        source_label(str(metrics.get("active_source") or "")),
        stage_label(metrics.get("active_stage")),
        "-",
        active_timing(metrics),
        active_detail(metrics),
    )
    table.add_row(
        "Last extraction",
        mode_label(str(metrics.get("last_embedding_mode") or "")),
        source_label(str(metrics.get("last_embedding_source") or "")),
        stage_label(metrics.get("last_embedding_stage")),
        fmt(metrics.get("last_embedding_sequences")),
        (
            f"infer={seconds(metrics.get('last_embedding_inference_seconds'))} "
            f"insert={seconds(metrics.get('last_embedding_insert_seconds'))} "
            f"seq/s={float(metrics.get('last_embedding_sequences_per_second') or 0.0):.1f}"
        ),
        f"tokens={fmt(metrics.get('last_embedding_tokens'))} updated={compact_time(str(metrics.get('last_embedding_at_utc') or ''))}",
    )
    return Panel(table, title="Work Focus", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def progress_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Stage", style="cyan", no_wrap=True, width=20)
    table.add_column("News", justify="right", no_wrap=True, width=14)
    table.add_column("SEC", justify="right", no_wrap=True, width=14)
    table.add_column("Total", justify="right", no_wrap=True, width=14)
    table.add_column("Last", overflow="fold", ratio=1)
    table.add_row(
        "Fetched source",
        fmt(metrics.get("news_source_rows")),
        fmt(metrics.get("sec_source_rows")),
        fmt(metrics.get("source_rows_fetched")),
        f"last_fetch={float(metrics.get('last_fetch_seconds') or 0.0):.2f}s",
    )
    table.add_row(
        "Fetched tokens",
        fmt(metrics.get("news_token_rows")),
        fmt(metrics.get("sec_token_rows")),
        fmt(metrics.get("token_rows_fetched")),
        f"last_embed={float(metrics.get('last_embedding_seconds') or 0.0):.2f}s",
    )
    table.add_row(
        "Embeddings written",
        "-",
        "-",
        fmt(metrics.get("embedding_rows_written")),
        f"insert={float(metrics.get('last_insert_seconds') or 0.0):.2f}s coverage={fmt(metrics.get('coverage_rows_written'))}",
    )
    table.add_row(
        "Cycles",
        fmt(metrics.get("live_cycles")),
        fmt(metrics.get("historical_cycles")),
        fmt(metrics.get("cycles")),
        f"last={float(metrics.get('last_cycle_seconds') or 0.0):.2f}s active_queries={fmt(metrics.get('active_queries'))}",
    )
    return Panel(table, title="Cumulative Rows", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def cycle_summary_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Mode", style="cyan", no_wrap=True, width=12)
    table.add_column("Cycles", justify="right", no_wrap=True, width=10)
    table.add_column("Last UTC", no_wrap=True, width=19)
    table.add_column("Window UTC", overflow="fold", ratio=1)
    table.add_column("Detected", justify="right", no_wrap=True, width=10)
    table.add_column("Done", justify="right", no_wrap=True, width=10)
    table.add_column("Remain", justify="right", no_wrap=True, width=10)
    table.add_column("Rows", justify="right", no_wrap=True, width=10)
    table.add_column("Sec", justify="right", no_wrap=True, width=8)
    for mode, label in (("live", "Live"), ("historical", "Historical")):
        table.add_row(
            label,
            fmt(metrics.get(f"{mode}_cycles")),
            compact_time(str(metrics.get(f"{mode}_last_cycle_at_utc") or "")),
            str(metrics.get(f"{mode}_last_window_utc") or "-").replace("T", " ").replace("Z", ""),
            fmt(metrics.get(f"{mode}_last_gap_detected")),
            fmt(metrics.get(f"{mode}_last_gap_completed")),
            fmt(metrics.get(f"{mode}_last_gap_remaining")),
            fmt(metrics.get(f"{mode}_last_rows_written")),
            f"{float(metrics.get(f'{mode}_last_cycle_seconds') or 0.0):.2f}",
        )
    return Panel(table, title="Cycle Summary", box=box.ROUNDED, border_style="blue", padding=(0, 1))


def coverage_report_panel(metrics: dict[str, Any]) -> Panel:
    reports = source_reports(metrics)
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Mode", style="cyan", no_wrap=True, width=10)
    table.add_column("Source", style="cyan", no_wrap=True, width=8)
    table.add_column("Available Text", justify="right", no_wrap=True, width=16)
    table.add_column("Token Chunks", justify="right", no_wrap=True, width=14)
    table.add_column("Embeddings", justify="right", no_wrap=True, width=14)
    table.add_column("Source Gap", justify="right", no_wrap=True, width=12)
    table.add_column("Embed Gap", justify="right", no_wrap=True, width=12)
    table.add_column("Processed", justify="right", no_wrap=True, width=12)
    table.add_column("Remaining", justify="right", no_wrap=True, width=12)
    table.add_column("Available Period UTC", overflow="fold", ratio=1)
    rows_added = 0
    for mode, source, report in report_rows(reports, include_empty=False):
        processed = int(report.get("source_completed") or 0) + int(report.get("embedding_completed") or 0) + int(report.get("context_completed") or 0)
        remaining = int(report.get("source_remaining") or 0) + int(report.get("embedding_remaining") or 0) + int(report.get("context_remaining") or 0) + int(report.get("context_blocked") or 0)
        table.add_row(
            mode_label(mode),
            source_label(source),
            fmt(report.get("available_source_rows")),
            fmt(report.get("available_token_rows")),
            fmt(report.get("available_embedding_rows")),
            fmt(report.get("source_detected")),
            fmt(report.get("embedding_detected")),
            fmt(processed),
            fmt(remaining),
            str(report.get("available_period") or "-"),
        )
        rows_added += 1
    if rows_added == 0:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-", "No coverage reports have completed yet.")
    return Panel(table, title="Coverage Report", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def gap_summary_panel(metrics: dict[str, Any]) -> Panel:
    reports = source_reports(metrics)
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Mode", style="cyan", no_wrap=True, width=10)
    table.add_column("Source", style="cyan", no_wrap=True, width=8)
    table.add_column("Gap", style="cyan", no_wrap=True, width=18)
    table.add_column("Detected", justify="right", no_wrap=True, width=12)
    table.add_column("Done", justify="right", no_wrap=True, width=12)
    table.add_column("Remaining", justify="right", no_wrap=True, width=12)
    table.add_column("Missing Period UTC", overflow="fold", ratio=1)
    rows_added = 0
    for mode, source, report in report_rows(reports, include_empty=False):
        if source == "sec":
            rows_added += add_gap_row(
                table,
                mode,
                source,
                "Context",
                report.get("context_detected"),
                report.get("context_completed"),
                int(report.get("context_remaining") or 0) + int(report.get("context_blocked") or 0),
                with_blocked_period(report.get("context_period"), report.get("context_blocked")),
            )
        rows_added += add_gap_row(table, mode, source, "Source", report.get("source_detected"), report.get("source_completed"), report.get("source_remaining"), str(report.get("source_period") or "-"))
        rows_added += add_gap_row(table, mode, source, "Embedding", report.get("embedding_detected"), report.get("embedding_completed"), report.get("embedding_remaining"), str(report.get("embedding_period") or "-"))
    if rows_added == 0:
        table.add_row("-", "-", "No active gaps", "-", "-", "-", "Latest completed reports have no remaining source or embedding gaps.")
    return Panel(table, title="Gap Summary by Mode", box=box.ROUNDED, border_style="magenta", padding=(0, 1))


def runtime_panel(gateway: "TextEmbedGateway", metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, width=22)
    table.add_column("Value", justify="right", no_wrap=True, width=18)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row("Device", str(metrics.get("embedding_device") or "-"), f"dtype={metrics.get('embedding_torch_dtype') or '-'} pooling={metrics.get('embedding_pooling') or '-'}")
    table.add_row("Embedding dim", fmt(metrics.get("embedding_dim")), f"load={float(metrics.get('model_load_seconds') or 0.0):.2f}s")
    table.add_row("SEC bridge", str(metrics.get("sec_bridge_status") or "-"), str(metrics.get("sec_bridge_table") or "-"))
    table.add_row("SEC context", str(metrics.get("sec_context_status") or "-"), f"{metrics.get('sec_context_table') or '-'} refreshed={fmt(metrics.get('sec_context_rows_refreshed'))}")
    table.add_row("Batch sizes", fmt(gateway.config.embedding_batch_size), f"source={gateway.config.source_batch_size:,} token={gateway.config.token_batch_size:,} insert={gateway.config.embedding_insert_batch_size:,}")
    table.add_row("Lookback", f"{gateway.config.live_lookback_minutes:,}m", f"closed historical={gateway.config.historical_lookback_days:,}d limit={gateway.config.historical_batch_limit:,}")
    table.add_row("SEC context chunks", f"{gateway.config.sec_context_refresh_chunk_hours:.1f}h", f"historical max/cycle={gateway.config.sec_context_historical_max_chunks_per_cycle:,}")
    table.add_row("Poll cadence", f"{metrics.get('poll_cadence_label') or '-'}", f"active={gateway.config.live_poll_seconds:.1f}s closed={gateway.config.closed_poll_seconds:.1f}s weekend={gateway.config.weekend_poll_seconds:.1f}s")
    table.add_row("Recent status", fmt(metrics.get("recent_status_rows")), f"ttl={gateway.config.recent_status_retention_hours:.1f}h")
    table.add_row("Failures", fmt(metrics.get("failures")), str(metrics.get("last_error") or "-"))
    table.add_row("Log", "-", truncate(str(metrics.get("run_log_path") or "-"), 140))
    return Panel(table, title="Runtime", box=box.ROUNDED, border_style="green" if not metrics.get("last_error") else "yellow", padding=(0, 1))


def timing_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Mode", style="cyan", no_wrap=True, width=12)
    table.add_column("Batches", justify="right", no_wrap=True, width=10)
    table.add_column("Seq", justify="right", no_wrap=True, width=12)
    table.add_column("Avg infer", justify="right", no_wrap=True, width=12)
    table.add_column("Last infer", justify="right", no_wrap=True, width=12)
    table.add_column("ms/seq", justify="right", no_wrap=True, width=10)
    table.add_column("Seq/s", justify="right", no_wrap=True, width=10)
    table.add_column("Tok/s", justify="right", no_wrap=True, width=12)
    table.add_column("Avg insert", justify="right", no_wrap=True, width=11)
    table.add_column("Avg batch", justify="right", no_wrap=True, width=11)
    for mode, label in (("live", "Live"), ("historical", "Historical")):
        table.add_row(
            label,
            fmt(metrics.get(f"{mode}_embedding_batches")),
            fmt(metrics.get(f"{mode}_embedding_sequences")),
            seconds(metrics.get(f"{mode}_avg_inference_seconds")),
            seconds(metrics.get(f"{mode}_last_inference_seconds")),
            f"{float(metrics.get(f'{mode}_avg_inference_ms_per_sequence') or 0.0):.1f}",
            f"{float(metrics.get(f'{mode}_avg_inference_sequences_per_second') or 0.0):.1f}",
            f"{float(metrics.get(f'{mode}_avg_inference_tokens_per_second') or 0.0):.0f}",
            seconds(metrics.get(f"{mode}_avg_insert_seconds")),
            seconds(metrics.get(f"{mode}_avg_batch_seconds")),
        )
    return Panel(table, title="Embedding Timing", box=box.ROUNDED, border_style="yellow", padding=(0, 1))


def recent_table(snapshot: dict[str, Any]) -> Table:
    rows = snapshot.get("rows") or []
    table = Table(title=f"Recent Embedding Work ({len(rows)})", box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column("UTC", no_wrap=True, width=17)
    table.add_column("Source", no_wrap=True, width=8)
    table.add_column("Mode", no_wrap=True, width=9)
    table.add_column("Stage", no_wrap=True, width=16)
    table.add_column("Rows", justify="right", no_wrap=True, width=10)
    table.add_column("Sec", justify="right", no_wrap=True, width=8)
    for row in rows:
        table.add_row(
            compact_time(str(row.get("updated_at_utc") or "")),
            str(row.get("source") or "-"),
            str(row.get("mode") or "-"),
            str(row.get("stage") or "-"),
            fmt(row.get("rows")),
            f"{float(row.get('seconds') or 0.0):.2f}",
        )
    if not rows:
        table.add_row("-", "-", "-", "-", "-", "-")
    return table


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return str(value or "-")


def seconds(value: Any) -> str:
    return f"{float(value or 0.0):.3f}s"


def source_reports(metrics: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    reports = metrics.get("source_reports")
    if isinstance(reports, dict):
        return reports
    return {"live": {}, "historical": {}}


def report_rows(reports: dict[str, dict[str, dict[str, Any]]], *, include_empty: bool = True) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for mode in ("live", "historical"):
        mode_reports = reports.get(mode) or {}
        for source in ("news", "sec"):
            report = mode_reports.get(source)
            if isinstance(report, dict):
                rows.append((mode, source, report))
            elif include_empty:
                rows.append((mode, source, {}))
    return rows


def add_gap_row(table: Table, mode: str, source: str, gap: str, detected: Any, done: Any, remaining: Any, period: str) -> int:
    values = [detected, done, remaining]
    has_count = any(int_or_zero(value) for value in values)
    has_period = bool(period and period != "-")
    if not has_count and not has_period:
        return 0
    table.add_row(mode_label(mode), source_label(source), gap, fmt(detected), fmt(done), fmt(remaining), period or "-")
    return 1


def active_timing(metrics: dict[str, Any]) -> str:
    next_poll = compact_time(str(metrics.get("next_poll_at_utc") or ""))
    if next_poll != "-":
        return f"next={next_poll} wait={float(metrics.get('next_poll_seconds') or 0.0):.0f}s"
    return f"started={compact_time(str(metrics.get('active_started_at_utc') or ''))}"


def active_detail(metrics: dict[str, Any]) -> str:
    detail = str(metrics.get("active_detail") or "-")
    window = str(metrics.get("active_window_utc") or "").replace("T", " ").replace("Z", "")
    return f"{detail}  window={window}" if window else detail


def int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def mode_label(mode: str) -> str:
    return "Live" if mode == "live" else "Historical" if mode == "historical" else str(mode or "-")


def source_label(source: str) -> str:
    return "News" if source == "news" else "SEC" if source == "sec" else str(source or "-")


def stage_label(stage: Any) -> str:
    text = str(stage or "")
    return text.replace("_", " ").title() if text else "-"


def style_status(value: str) -> str:
    color = status_color(value)
    return f"[{color}]{status_label(value)}[/{color}]"


def status_color(value: str) -> str:
    lowered = value.lower()
    if lowered in {"polling", "loaded", "schema", "running", "working"}:
        return "green"
    if lowered in {"failed", "error"}:
        return "red"
    if lowered in {"stopping", "releasing_model", "loading_model", "waiting"}:
        return "yellow"
    return "cyan"


def status_label(value: str) -> str:
    return value.replace("_", " ").upper() if value else "-"


def clock(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def compact_time(value: str) -> str:
    text = str(value or "")
    return text.replace("T", " ").replace("Z", "")[:19] if text else "-"


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "..."


def with_blocked_period(period: Any, blocked: Any) -> str:
    text = str(period or "-")
    try:
        blocked_count = int(blocked or 0)
    except Exception:
        blocked_count = 0
    if blocked_count > 0:
        return f"{text}  blocked_mapping={blocked_count:,}"
    return text
