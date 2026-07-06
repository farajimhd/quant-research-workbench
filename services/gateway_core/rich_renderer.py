"""Shared Rich dashboard rendering primitives.

Service terminals can keep domain-specific detail panels while using the same
status vocabulary, panel/table defaults, and `Live` refresh behavior.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


STATUS_STYLES = {
    "starting": "blue",
    "preflight": "blue",
    "working": "blue",
    "catching_up": "blue",
    "catching up": "blue",
    "ok": "green",
    "healthy": "green",
    "ready": "green",
    "running": "cyan",
    "polling": "green",
    "loaded": "green",
    "schema": "green",
    "no rows": "green",
    "covered by live lookback": "green",
    "no watermark": "green",
    "up to date": "green",
    "launched": "green",
    "allowed": "green",
    "done": "green",
    "success": "green",
    "pass": "green",
    "auto": "green",
    "draining": "green",
    "finished": "green",
    "waiting": "blue",
    "queued": "yellow",
    "publishing": "yellow",
    "processing": "yellow",
    "stopping": "yellow",
    "loading model": "yellow",
    "releasing model": "yellow",
    "source not yet available": "yellow",
    "manual required large gap": "yellow",
    "workstation auto started large gap": "yellow",
    "awaiting live symbols": "yellow",
    "api only missing massive key": "yellow",
    "warning": "yellow",
    "warn": "yellow",
    "needs action": "yellow",
    "unauthenticated": "yellow",
    "unknown": "yellow",
    "degraded": "yellow",
    "failed": "red",
    "error": "red",
    "blocked": "red",
    "no symbols available": "red",
    "completed": "green",
    "skipped": "yellow",
    "deferred": "yellow",
    "stale": "yellow",
    "missing": "yellow",
    "planned": "cyan",
    "partial": "cyan",
    "empty": "cyan",
    "idle": "green",
    "disabled": "bright_black",
    "not_started": "bright_black",
    "n/a": "bright_black",
}


def status_style(status: Any) -> str:
    text = normalize_status(status)
    if "failed" in text or "error" in text:
        return "red"
    if "blocked" in text:
        return "red"
    if "warning" in text or "warn" in text or "stale" in text or "missing" in text:
        return "yellow"
    return STATUS_STYLES.get(text, "cyan")


def status_color(status: Any) -> str:
    return status_style(status)


def styled_status(status: Any) -> str:
    text = str(status or "-")
    style = status_style(text)
    return f"[{style}]{status_label(text)}[/{style}]"


def style_status(status: Any) -> str:
    return styled_status(status)


def status_label(status: Any) -> str:
    text = str(status or "-").strip()
    return text.replace("_", " ").upper() if text else "-"


def normalize_status(status: Any) -> str:
    return str(status or "").strip().lower().replace("_", " ")


def standard_live(
    renderable: Any,
    *,
    console: Any | None = None,
    screen: bool = False,
    refresh_seconds: float = 1.0,
) -> Live:
    refresh_per_second = max(1, int(1 / max(0.1, refresh_seconds)))
    return Live(
        renderable,
        console=console,
        auto_refresh=False,
        transient=False,
        screen=screen,
        vertical_overflow="crop",
        refresh_per_second=refresh_per_second,
    )


def layout_profile(default_width: int = 140, default_height: int = 44) -> dict[str, Any]:
    width, height = shutil.get_terminal_size((default_width, default_height))
    return {
        "width": width,
        "height": height,
        "compact": height < 34,
        "narrow": width < 190,
        "roomy": width >= 190 and height >= 62,
    }


def standard_panel(
    renderable: Any,
    *,
    title: str = "",
    status: Any = "",
    border_style: str | None = None,
    padding: tuple[int, int] = (0, 1),
) -> Panel:
    return Panel(
        renderable,
        title=title or None,
        box=box.ROUNDED,
        border_style=border_style or status_style(status),
        padding=padding,
    )


def standard_table(
    *columns: str,
    expand: bool = False,
    show_edge: bool = False,
    simple: bool = True,
    header_style: str = "bold cyan",
) -> Table:
    table = Table(
        box=box.SIMPLE if simple else box.ROUNDED,
        expand=expand,
        show_edge=show_edge,
        header_style=header_style,
    )
    for column in columns:
        table.add_column(column)
    return table


def detail_table(rows: list[tuple[Any, Any]], *, key_width: int = 18) -> Table:
    table = Table(box=box.SIMPLE, expand=False, show_edge=False)
    table.add_column("Field", style="cyan", no_wrap=True, width=key_width)
    table.add_column("Value", overflow="fold")
    for key, value in rows:
        table.add_row(str(key), str(value) if value not in {None, ""} else "-")
    return table


def metric_table(rows: list[tuple[Any, Any, Any]], *, value_heading: str = "Value", detail_heading: str = "Detail") -> Table:
    table = Table(box=box.SIMPLE, expand=False, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column(value_heading, justify="right", no_wrap=True)
    table.add_column(detail_heading, overflow="fold")
    for metric, value, detail in rows:
        table.add_row(str(metric), str(value) if value not in {None, ""} else "-", str(detail) if detail not in {None, ""} else "-")
    return table


def progress_count(done: Any, total: Any) -> str:
    try:
        done_int = int(done or 0)
        total_int = int(total or 0)
    except Exception:
        return "-"
    if total_int <= 0:
        return "-"
    return f"{max(0, min(done_int, total_int)):,}/{total_int:,}"


def truncate(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "-"
    return text[: max(0, limit - 3)].rstrip() + "..."


def render_standard_snapshot(snapshot: dict[str, Any]) -> Group:
    profile = layout_profile()
    parts: list[Any] = [
        _snapshot_header(snapshot),
        _snapshot_current_operation(snapshot),
        _snapshot_overview(snapshot, profile=profile),
    ]
    recent = snapshot.get("recent_items")
    if recent and profile["compact"]:
        parts.append(_snapshot_recent(recent, limit=4 if profile["compact"] else 8))
    return Group(*parts)


def _snapshot_header(snapshot: dict[str, Any]) -> Panel:
    header = snapshot.get("header") if isinstance(snapshot.get("header"), dict) else {}
    service = str(header.get("service") or "-").replace("_", " ").title()
    status = header.get("status", "-")
    color = status_style(status)
    now = parse_utc_datetime(header.get("snapshot_utc"))
    market = compact_market_label(header)
    left_line_1 = f"[bold]{service}[/bold]  [{color}]{status_label(status)}[/{color}]"
    left_line_2 = "  ".join(
        part
        for part in (
            label_value("bind", header.get("bind")),
            label_value("mode", header.get("mode")),
            label_value("run", header.get("run_mode")),
            label_value("exec", header.get("execute")),
        )
        if part
    )
    left_line_3 = "  ".join(
        part
        for part in (
            label_value("read", header.get("read_database")),
            label_value("write", header.get("write_database")),
            label_value("market", market),
        )
        if part
    )
    right_line_1 = time_triplet(now)
    right_line_2 = label_value("data", truncate(header.get("data_root"), 96)) or ""
    grid = Table.grid(expand=True)
    grid.add_column(ratio=3, overflow="fold")
    grid.add_column(ratio=2, justify="right", overflow="fold")
    grid.add_row(left_line_1, right_line_1)
    grid.add_row(left_line_2 or "-", right_line_2)
    if left_line_3:
        grid.add_row(left_line_3, "")
    return Panel(grid, box=box.ROUNDED, border_style=color, padding=(0, 1))


def _snapshot_current_operation(snapshot: dict[str, Any]) -> Panel:
    op = snapshot.get("current_operation") if isinstance(snapshot.get("current_operation"), dict) else {}
    status = op.get("status", "running")
    color = status_style(status)
    grid = Table.grid(expand=True)
    grid.add_column(style="cyan", no_wrap=True, width=14)
    grid.add_column(ratio=1, overflow="fold")
    grid.add_column(style="cyan", no_wrap=True, width=10)
    grid.add_column(ratio=1, overflow="fold")
    grid.add_row("Phase", status_label(op.get("phase", "-")), "Status", styled_status(status))
    if op.get("started_at") or op.get("next_action"):
        grid.add_row("Started", str(op.get("started_at") or "-"), "Next", str(op.get("next_action") or "-"))
    grid.add_row("Message", truncate(op.get("message") or "No active operation message.", 260), "", "")
    return Panel(grid, title="Current Operation", box=box.ROUNDED, border_style=color, padding=(0, 1))


def _snapshot_overview(snapshot: dict[str, Any], *, profile: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Area", style="cyan", no_wrap=True, width=18)
    table.add_column("Status", no_wrap=True, width=14)
    table.add_column("Rows / Sec", justify="right", no_wrap=True, width=14)
    table.add_column("Detail", overflow="fold", ratio=1)
    config = snapshot.get("configuration") if isinstance(snapshot.get("configuration"), dict) else {}
    deps = snapshot.get("dependencies") if isinstance(snapshot.get("dependencies"), list) else []
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    tasks = snapshot.get("tasks") if isinstance(snapshot.get("tasks"), list) else []
    coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), dict) else {}
    sources = snapshot.get("sources_sinks") if isinstance(snapshot.get("sources_sinks"), list) else []
    error = snapshot.get("error_state") if isinstance(snapshot.get("error_state"), dict) else {}
    table.add_row("Configuration", styled_status("ok"), "-", compact_pairs(config, ("bind", "execute", "read_database", "write_database", "source_database", "target_database"), 180))
    table.add_row("Dependencies", aggregate_status(deps), str(len(deps)) if deps else "-", aggregate_detail(deps, empty="No dependency state reported."))
    table.add_row("Runtime", styled_status("running"), runtime_primary_value(runtime), compact_pairs(runtime, tuple(runtime.keys()), 220))
    if tasks:
        table.add_row("Tasks", aggregate_status(tasks), str(len(tasks)), aggregate_detail(tasks, name_key="name", detail_key="message", empty="No task state reported."))
    elif not profile["compact"]:
        table.add_row("Tasks", styled_status("waiting"), "-", "No task state reported.")
    coverage_status = coverage.get("status") or "ok"
    coverage_detail = compact_pairs(coverage, ("status", "message", "coverage_interval_count", "active_window_utc"), 240)
    table.add_row("Coverage", styled_status(coverage_status), str(coverage.get("coverage_interval_count") or "-"), coverage_detail)
    if sources:
        table.add_row("Sources", aggregate_status(sources), str(len(sources)), aggregate_detail(sources, empty="No source/sink state reported."))
    elif not profile["compact"]:
        table.add_row("Sources", styled_status("waiting"), "-", "No source/sink state reported.")
    error_status = error.get("status") or "ok"
    error_detail = compact_pairs(error, ("message", "last_error", "severity", "retryable"), 240)
    table.add_row("Errors", styled_status(error_status), str(error.get("error_count") or error.get("failures") or "-"), error_detail)
    return Panel(table, title="Service Overview", box=box.ROUNDED, border_style=status_style(error_status), padding=(0, 1))


def _snapshot_configuration(snapshot: dict[str, Any]) -> Panel:
    config = snapshot.get("configuration") if isinstance(snapshot.get("configuration"), dict) else {}
    return standard_panel(detail_table(list(config.items())), title="Configuration And Mode", status="ok")


def _snapshot_dependencies(snapshot: dict[str, Any], *, limit: int) -> Panel:
    rows = snapshot.get("dependencies") if isinstance(snapshot.get("dependencies"), list) else []
    table = standard_table("Dependency", "Status", "Latency", "Detail")
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("name") or item.get("check") or "-"),
            styled_status(item.get("status", "-")),
            str(item.get("seconds") or item.get("latency") or "-"),
            truncate(item.get("detail") or item.get("message") or "-", 180),
        )
    if not rows:
        table.add_row("dependencies", styled_status("waiting"), "-", "No dependency state reported.")
    hidden = max(0, len(rows) - limit)
    if hidden:
        table.add_row("more", "-", "-", f"{hidden:,} hidden")
    return standard_panel(table, title="Dependencies", status="ok")


def _snapshot_runtime(snapshot: dict[str, Any]) -> Panel:
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    rows = [(key, value, "") for key, value in list(runtime.items())[:12]]
    return standard_panel(metric_table(rows), title="Runtime Summary", status="running")


def _snapshot_tasks(snapshot: dict[str, Any], *, limit: int) -> Panel:
    tasks = snapshot.get("tasks") if isinstance(snapshot.get("tasks"), list) else []
    table = standard_table("Task", "Status", "Rows", "Detail")
    for item in tasks[:limit]:
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("task") or item.get("name") or "-"),
            styled_status(item.get("status", "-")),
            str(item.get("rows") if item.get("rows") is not None else "-"),
            truncate(item.get("detail") or item.get("message") or "-", 180),
        )
    if not tasks:
        table.add_row("task ledger", styled_status("waiting"), "-", "No task state reported.")
    return standard_panel(table, title="Work Plan / Task Ledger", status="running")


def _snapshot_coverage(snapshot: dict[str, Any]) -> Panel:
    coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), dict) else {}
    rows = [(key, value) for key, value in coverage.items()]
    return standard_panel(detail_table(rows), title="Coverage / Reconciliation", status=coverage.get("status", "ok"))


def _snapshot_sources(snapshot: dict[str, Any], *, limit: int) -> Panel:
    rows = snapshot.get("sources_sinks") if isinstance(snapshot.get("sources_sinks"), list) else []
    table = standard_table("Source/Sink", "Status", "Rows", "Detail")
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("name") or item.get("source") or "-"),
            styled_status(item.get("status", "-")),
            str(item.get("rows") if item.get("rows") is not None else "-"),
            truncate(item.get("detail") or item.get("targets") or "-", 180),
        )
    if not rows:
        table.add_row("sources", styled_status("waiting"), "-", "No source/sink state reported.")
    return standard_panel(table, title="Sources And Sinks", status="ok")


def _snapshot_errors(snapshot: dict[str, Any]) -> Panel:
    error = snapshot.get("error_state") if isinstance(snapshot.get("error_state"), dict) else {}
    rows = [(key, value) for key, value in error.items() if key in {"status", "active", "severity", "message", "retryable", "last_error"}]
    return standard_panel(detail_table(rows), title="Warnings And Errors", status=error.get("status", "ok"))


def _snapshot_recent(recent: dict[str, Any], *, limit: int) -> Panel:
    rows = recent.get("rows") if isinstance(recent.get("rows"), list) else []
    table = standard_table("Time", "Item", "Status", "Detail", simple=False, show_edge=True)
    for row in rows[:limit]:
        if isinstance(row, dict):
            table.add_row(
                str(row.get("published_at_utc") or row.get("ts_utc") or row.get("updated_at_utc") or "-"),
                truncate(row.get("ticker") or row.get("accession") or row.get("title") or row.get("source_id") or "-", 40),
                styled_status(row.get("status") or row.get("process") or "-"),
                truncate(row.get("headline") or row.get("title") or row.get("message") or "-", 160),
            )
    if not rows:
        table.add_row("-", "-", "-", "No recent items reported.")
    return standard_panel(table, title="Recent Domain Items", status="ok")


def parse_utc_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def time_triplet(value: datetime) -> str:
    date = value.astimezone(UTC).strftime("%Y-%m-%d")
    return (
        f"[dim]{date}[/dim]  "
        f"[dim]UTC[/dim] {value.astimezone(UTC).strftime('%H:%M:%S')}  "
        f"[dim]ET[/dim] {value.astimezone(EASTERN).strftime('%H:%M:%S')}  "
        f"[dim]VAN[/dim] {value.astimezone(VANCOUVER).strftime('%H:%M:%S')}"
    )


def label_value(label: str, value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    return f"[dim]{label}[/dim] {text}"


def compact_market_label(header: dict[str, Any]) -> str:
    status = str(header.get("market_status") or "").strip()
    source = str(header.get("market_status_source") or "").strip()
    if status and source:
        return f"{status}/{source}"
    return status or source


def compact_pairs(payload: dict[str, Any], keys: tuple[Any, ...], limit: int) -> str:
    parts: list[str] = []
    for key in keys:
        text_key = str(key)
        if text_key not in payload:
            continue
        value = payload.get(text_key)
        if value is None or value == "":
            continue
        parts.append(f"{text_key}={value}")
    return truncate("; ".join(parts) if parts else "-", limit)


def runtime_primary_value(runtime: dict[str, Any]) -> str:
    for key in ("poll_runs", "cycles", "provider_rows", "feed_items", "source_rows_fetched", "embedding_rows_written"):
        if key in runtime:
            return str(runtime.get(key) if runtime.get(key) is not None else "-")
    return "-"


def aggregate_status(rows: list[Any]) -> str:
    statuses = [normalize_status(row.get("status") if isinstance(row, dict) else "") for row in rows]
    if any("failed" in status or "error" in status for status in statuses):
        return styled_status("failed")
    if any(status in {"warning", "warn", "stale", "missing", "degraded"} for status in statuses):
        return styled_status("warning")
    if any(status in {"running", "working", "queued", "processing"} for status in statuses):
        return styled_status("running")
    if rows:
        return styled_status("ok")
    return styled_status("waiting")


def aggregate_detail(
    rows: list[Any],
    *,
    name_key: str = "name",
    detail_key: str = "detail",
    empty: str,
    limit: int = 260,
) -> str:
    if not rows:
        return empty
    parts: list[str] = []
    for row in rows[:4]:
        if not isinstance(row, dict):
            continue
        name = row.get(name_key) or row.get("check") or row.get("source") or row.get("task") or "-"
        status = row.get("status") or "-"
        detail = row.get(detail_key) or row.get("message") or row.get("targets") or ""
        text = f"{name}:{status}"
        if detail:
            text += f" ({detail})"
        parts.append(str(text))
    hidden = max(0, len(rows) - 4)
    if hidden:
        parts.append(f"+{hidden} more")
    return truncate("; ".join(parts), limit)
