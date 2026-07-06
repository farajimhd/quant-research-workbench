"""Shared Rich dashboard rendering primitives.

Service terminals can keep domain-specific detail panels while using the same
status vocabulary, panel/table defaults, and `Live` refresh behavior.
"""

from __future__ import annotations

import shutil
from typing import Any

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


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
    expand: bool = True,
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
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Field", style="cyan", no_wrap=True, width=key_width)
    table.add_column("Value", overflow="fold")
    for key, value in rows:
        table.add_row(str(key), str(value) if value not in {None, ""} else "-")
    return table


def metric_table(rows: list[tuple[Any, Any, Any]], *, value_heading: str = "Value", detail_heading: str = "Detail") -> Table:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
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
        _snapshot_configuration(snapshot),
        _snapshot_dependencies(snapshot, limit=4 if profile["compact"] else 8),
        _snapshot_runtime(snapshot),
        _snapshot_tasks(snapshot, limit=6 if profile["compact"] else 12),
        _snapshot_coverage(snapshot),
        _snapshot_sources(snapshot, limit=6 if profile["compact"] else 12),
        _snapshot_errors(snapshot),
    ]
    recent = snapshot.get("recent_items")
    if recent:
        parts.append(_snapshot_recent(recent, limit=4 if profile["compact"] else 8))
    return Group(*parts)


def _snapshot_header(snapshot: dict[str, Any]) -> Panel:
    header = snapshot.get("header") if isinstance(snapshot.get("header"), dict) else {}
    rows = [
        ("Service", header.get("service", "-")),
        ("Status", styled_status(header.get("status", "-"))),
        ("Bind", header.get("bind", "-")),
        ("Mode", header.get("mode", "-")),
        ("Read DB", header.get("read_database", "-")),
        ("Write DB", header.get("write_database", "-")),
        ("Updated", header.get("snapshot_utc", "-")),
        ("Market", header.get("market_status", "-")),
    ]
    return standard_panel(detail_table(rows), title="Header", status=header.get("status", "running"))


def _snapshot_current_operation(snapshot: dict[str, Any]) -> Panel:
    op = snapshot.get("current_operation") if isinstance(snapshot.get("current_operation"), dict) else {}
    rows = [
        ("Phase", op.get("phase", "-")),
        ("Status", styled_status(op.get("status", "-"))),
        ("Started", op.get("started_at", "-")),
        ("Message", op.get("message", "-")),
        ("Next", op.get("next_action", "-")),
    ]
    return standard_panel(detail_table(rows), title="Current Operation", status=op.get("status", "running"))


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
