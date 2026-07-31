from __future__ import annotations

import asyncio
from typing import Any, Callable

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.rich_renderer import (
    layout_profile,
    render_operational_dashboard,
    standard_live,
    style_status,
)

from .config import IntelligenceConfig


async def run_terminal_dashboard(
    config: IntelligenceConfig,
    snapshot_metrics: Callable[[], dict[str, Any]],
    stop_event: asyncio.Event,
) -> None:
    refresh = max(0.25, config.terminal_refresh_seconds)
    with standard_live(
        render_dashboard(config, snapshot_metrics()),
        screen=config.terminal_screen_enabled,
        refresh_seconds=refresh,
    ) as live:
        while not stop_event.is_set():
            live.update(render_dashboard(config, snapshot_metrics()), refresh=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=refresh)
            except TimeoutError:
                pass


def render_dashboard(
    config: IntelligenceConfig,
    metrics: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
) -> Group:
    selected_profile = profile or layout_profile()
    error = str(metrics.get("last_error") or "")
    dependencies = [
        {
            "name": "ClickHouse canonical text",
            "status": "degraded" if error else "ok",
            "detail": error or "Bounded News and SEC canonical reads are available.",
        }
    ]
    standard = build_dashboard_snapshot(
        service_name="text_intelligence",
        config=config,
        metrics=metrics,
        dependencies=dependencies,
        sources_sinks=[
            {
                "name": "News V2 to scoped labels V5",
                "status": "running",
                "rows": metrics.get("deterministic_news_labels", 0),
                "detail": "Canonical rendered News to issuer-scoped labels.",
            },
            {
                "name": "SEC V3 to scoped labels V5",
                "status": "running",
                "rows": metrics.get("deterministic_sec_labels", 0),
                "detail": "Canonical eligible SEC documents to issuer-scoped labels.",
            },
        ],
    )
    recent_rows = list(metrics.get("deterministic_recent_work") or [])
    return render_operational_dashboard(
        standard,
        primary=workers_panel(metrics),
        compact_primary=compact_work_panel(metrics),
        secondary=counters_panel(metrics),
        recent_factory=lambda limit: recent_work_table(recent_rows, limit=limit),
        recent_count=len(recent_rows),
        profile=selected_profile,
    )


def compact_work_panel(metrics: dict[str, Any]) -> Panel:
    workers = list(metrics.get("deterministic_workers") or [])
    active = [row for row in workers if row.get("status") == "processing"]
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Work", style="cyan", no_wrap=True, width=16)
    table.add_column("State", no_wrap=True, width=12)
    table.add_column("Progress / detail", overflow="fold", ratio=1)
    table.add_row(
        "Reconciliation",
        style_status(
            "failed"
            if metrics.get("deterministic_reconcile_error_status") == "active"
            else "running"
        ),
        (
            f"notices {fmt(metrics.get('deterministic_reconcile_notices'))}; "
            f"last {seconds(metrics.get('deterministic_reconcile_seconds'))}; "
            f"runs {fmt(metrics.get('deterministic_reconcile_runs'))}"
        ),
    )
    table.add_row(
        "Deterministic queue",
        style_status("running" if active else "waiting"),
        (
            f"queued {fmt(metrics.get('deterministic_queue_size'))}; "
            f"active {len(active):,}/{len(workers):,}; "
            f"pending {fmt(metrics.get('deterministic_pending'))}"
        ),
    )
    if active:
        focus = "; ".join(
            f"W{row.get('worker')} {row.get('corpus')} {row.get('stage')} {short_id(row.get('source_id'))}"
            for row in active[:4]
        )
    else:
        focus = "Workers are waiting for canonical News or SEC notices."
    table.add_row("Current focus", style_status("running" if active else "idle"), focus)
    return Panel(
        table,
        title="Deterministic Work",
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 1),
    )


def workers_panel(metrics: dict[str, Any]) -> Panel:
    workers = list(metrics.get("deterministic_workers") or [])
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Worker", style="cyan", no_wrap=True, width=8)
    table.add_column("Corpus", no_wrap=True, width=8)
    table.add_column("Stage", no_wrap=True, width=20)
    table.add_column("Source", overflow="ellipsis", ratio=1)
    table.add_column("State", no_wrap=True, width=12)
    for row in workers:
        table.add_row(
            f"W{row.get('worker')}",
            str(row.get("corpus") or "-"),
            str(row.get("stage") or "-").replace("_", " "),
            str(row.get("source_id") or "-"),
            style_status(row.get("status") or "waiting"),
        )
    if not workers:
        table.add_row("-", "-", "startup", "Worker pool has not started.", style_status("waiting"))
    return Panel(
        table,
        title="Current Worker Focus",
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 1),
    )


def counters_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Lifecycle", style="cyan", no_wrap=True, width=24)
    table.add_column("Count", justify="right", no_wrap=True, width=12)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row(
        "Canonical notices",
        fmt(metrics.get("deterministic_queued")),
        f"reconciled {fmt(metrics.get('deterministic_reconciled'))}; pending {fmt(metrics.get('deterministic_pending'))}",
    )
    table.add_row(
        "Durable documents",
        fmt(metrics.get("deterministic_completed")),
        (
            f"already current {fmt(metrics.get('deterministic_skipped_current'))}; "
            f"failed {fmt(metrics.get('deterministic_failed'))}; "
            f"active failures {fmt(metrics.get('deterministic_active_failure_count'))}"
        ),
    )
    table.add_row(
        "Scoped label rows",
        fmt(
            int(metrics.get("deterministic_news_labels") or 0)
            + int(metrics.get("deterministic_sec_labels") or 0)
        ),
        f"News {fmt(metrics.get('deterministic_news_labels'))}; SEC {fmt(metrics.get('deterministic_sec_labels'))}",
    )
    table.add_row(
        "Optional live forwarding",
        fmt(metrics.get("deterministic_live_forwarded")),
        f"failed {fmt(metrics.get('deterministic_live_forward_failed'))}; enabled {bool(metrics.get('enable_live_ai'))}",
    )
    return Panel(
        table,
        title="Durable Lifecycle",
        box=box.ROUNDED,
        border_style="magenta",
        padding=(0, 1),
    )


def recent_work_table(rows: list[dict[str, Any]], *, limit: int) -> Panel:
    selected = rows[:limit]
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("UTC", no_wrap=True, width=19)
    table.add_column("Corpus", no_wrap=True, width=8)
    table.add_column("Source", overflow="ellipsis", ratio=1)
    table.add_column("Outcome", no_wrap=True, width=16)
    table.add_column("Detail", overflow="fold", ratio=1)
    for row in selected:
        table.add_row(
            compact_time(row.get("updated_at_utc")),
            str(row.get("corpus") or "-"),
            str(row.get("source_id") or "-"),
            style_status(row.get("status") or "-"),
            str(row.get("detail") or row.get("stage") or "-"),
        )
    return Panel(
        table,
        title=f"Recent Deterministic Work ({len(selected)})",
        box=box.ROUNDED,
        border_style="blue",
        padding=(0, 1),
    )


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return str(value or "-")


def seconds(value: Any) -> str:
    try:
        return f"{float(value or 0.0):.2f}s"
    except (TypeError, ValueError):
        return "-"


def compact_time(value: Any) -> str:
    text = str(value or "").replace("T", " ").replace("Z", "")
    return text[:19] if text else "-"


def short_id(value: Any) -> str:
    text = str(value or "-")
    return text if len(text) <= 18 else text[:15] + "..."
