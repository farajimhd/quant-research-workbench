from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

try:
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table

    from services.gateway_core.dashboard import build_dashboard_snapshot
    from services.gateway_core.rich_renderer import render_standard_snapshot, standard_live, status_color
except Exception:  # pragma: no cover - fallback is handled by caller.
    box = None
    Group = None
    Panel = None
    Table = None
    build_dashboard_snapshot = None
    render_standard_snapshot = None
    standard_live = None
    status_color = None

if TYPE_CHECKING:
    from market_ai.runtime import MarketAIService


async def run_terminal_dashboard(service: "MarketAIService") -> None:
    if standard_live is None:
        await run_plain_dashboard(service)
        return
    refresh_seconds = service.service_config.terminal_refresh_seconds
    with standard_live(
        render_dashboard(service),
        screen=service.service_config.rich_screen,
        refresh_seconds=refresh_seconds,
    ) as live:
        while not service.stop_event.is_set():
            live.update(render_dashboard(service), refresh=True)
            await asyncio.sleep(refresh_seconds)


async def run_plain_dashboard(service: "MarketAIService") -> None:
    while not service.stop_event.is_set():
        metrics = service.metrics.snapshot()
        print(
            "market-ai "
            f"source={metrics['source_status']} "
            f"events={metrics['events_received']:,} "
            f"chunks={metrics['chunks_created']:,} "
            f"encoder_samples={metrics['encoder_samples']:,} "
            f"temporal_samples={metrics['temporal_samples']:,} "
            f"errors={metrics['errors']:,}",
            flush=True,
        )
        await asyncio.sleep(service.service_config.terminal_refresh_seconds)


def render_dashboard(service: "MarketAIService") -> Any:
    metrics = service.metrics.snapshot()
    standard = (
        render_standard_snapshot(
            build_dashboard_snapshot(
                service_name="market_ai",
                config=service.service_config,
                metrics={
                    **metrics,
                    "current_phase": metrics.get("source_status"),
                    "current_phase_message": latest_message(metrics),
                    "tasks": [
                        {"name": "event ingest", "status": metrics.get("source_status"), "rows": metrics.get("events_received"), "message": service.service_config.source},
                        {"name": "chunk creation", "status": metrics.get("source_status"), "rows": metrics.get("chunks_created"), "message": "model-dependent cache construction"},
                        {"name": "prediction publish", "status": metrics.get("source_status"), "rows": metrics.get("predictions"), "message": "TBD until model is finalized"},
                    ],
                },
            )
        )
        if build_dashboard_snapshot is not None and render_standard_snapshot is not None
        else header_panel(service, metrics)
    )
    return Group(
        standard,
        throughput_panel(metrics),
        timing_panel(metrics),
        queue_panel(service, metrics),
        message_panel(metrics),
    )


def latest_message(metrics: dict[str, Any]) -> str:
    rows = metrics.get("messages") or []
    return str(rows[-1]) if rows else ""


def header_panel(service: "MarketAIService", metrics: dict[str, Any]) -> Any:
    table = Table.grid(expand=True)
    table.add_column(ratio=2)
    table.add_column(justify="right", ratio=3)
    status = str(metrics.get("source_status") or "unknown")
    color = status_color(status) if status_color is not None else "yellow"
    if metrics.get("errors"):
        color = "red"
    table.add_row(
        f"[bold]Market AI Service[/bold]  [{color}]{status}[/{color}]",
        f"[dim]source[/dim] {service.service_config.source}   [dim]events/chunk[/dim] {service.market_config.events_per_chunk}   "
        f"[dim]encoder batch[/dim] {service.market_config.encoder_batch_size:,}",
    )
    table.add_row(
        f"[dim]qmd[/dim] {service.service_config.qmd_url}",
        f"[dim]started[/dim] {metrics.get('started_at_utc')}   [dim]elapsed[/dim] {float(metrics.get('elapsed_seconds') or 0.0):,.1f}s",
    )
    return Panel(table, title="Runtime", box=box.ROUNDED, border_style=color, padding=(0, 1))


def throughput_panel(metrics: dict[str, Any]) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, width=24)
    table.add_column("Total", justify="right", no_wrap=True, width=16)
    table.add_column("Recent/sec", justify="right", no_wrap=True, width=16)
    for label, key in (
        ("Events received", "events_received"),
        ("Chunks created", "chunks_created"),
        ("Encoder samples", "encoder_samples"),
        ("Temporal samples", "temporal_samples"),
        ("Predictions", "predictions"),
    ):
        table.add_row(label, fmt(metrics.get(key)), f"{float(metrics.get(key + '_per_sec') or 0.0):,.1f}")
    table.add_row("Dropped events", fmt(metrics.get("events_dropped")), "")
    table.add_row("Warnings / errors", f"{fmt(metrics.get('warnings'))} / {fmt(metrics.get('errors'))}", "")
    return Panel(table, title="Throughput", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def timing_panel(metrics: dict[str, Any]) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Stage", style="cyan", no_wrap=True, width=28)
    table.add_column("Total sec", justify="right", no_wrap=True, width=14)
    table.add_column("Unit timing", justify="right", no_wrap=True, width=20)
    table.add_row("Event update + chunk encode", f"{float(metrics.get('event_process_seconds') or 0.0):,.3f}", f"{float(metrics.get('event_process_ms_per_event') or 0.0):,.4f} ms/event")
    table.add_row("Encoder batch prep", f"{float(metrics.get('chunk_batch_prep_seconds') or 0.0):,.3f}", f"{float(metrics.get('chunk_batch_prep_ms_per_batch') or 0.0):,.4f} ms/batch")
    table.add_row("Encoder model", f"{float(metrics.get('encoder_model_seconds') or 0.0):,.3f}", f"{float(metrics.get('encoder_model_ms_per_batch') or 0.0):,.4f} ms/batch")
    table.add_row("Temporal context prep", f"{float(metrics.get('temporal_context_seconds') or 0.0):,.3f}", f"{float(metrics.get('temporal_context_ms_per_batch') or 0.0):,.4f} ms/batch")
    table.add_row("Temporal model", f"{float(metrics.get('temporal_model_seconds') or 0.0):,.3f}", f"{float(metrics.get('temporal_model_ms_per_batch') or 0.0):,.4f} ms/batch")
    return Panel(table, title="Batch Prep And Model Timing", box=box.ROUNDED, border_style="magenta", padding=(0, 1))


def queue_panel(service: "MarketAIService", metrics: dict[str, Any]) -> Any:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Queue / State", style="cyan", no_wrap=True, width=24)
    table.add_column("Value", justify="right", no_wrap=True, width=16)
    states = service.engine.state.states()
    table.add_row("Tracked tickers", fmt(len(states)))
    table.add_row("Encoder batches", fmt(metrics.get("encoder_batches")))
    table.add_row("Temporal batches", fmt(metrics.get("temporal_batches")))
    table.add_row("Context lags", fmt(len(service.market_config.context_lags)))
    table.add_row("Embedding history", fmt(service.market_config.embedding_history))
    return Panel(table, title="Queues", box=box.ROUNDED, border_style="blue", padding=(0, 1))


def message_panel(metrics: dict[str, Any]) -> Any:
    rows = metrics.get("messages") or []
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    if rows:
        for row in rows[-8:]:
            table.add_row(str(row))
    else:
        table.add_row("[dim]No messages yet.[/dim]")
    return Panel(table, title="Messages", box=box.ROUNDED, border_style="green", padding=(0, 1))


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return str(value)
