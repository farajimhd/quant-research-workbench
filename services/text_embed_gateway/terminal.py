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
        progress_panel(metrics),
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
    table.add_row("Model", style_status(str(metrics.get("model_status") or "-")))
    table.add_row("Market", f"{str(metrics.get('market_status') or '-')} / {str(metrics.get('market_status_source') or '-')}")
    if metrics.get("market_status_error"):
        table.add_row("Market error", str(metrics.get("market_status_error") or "-"))
    if metrics.get("last_error"):
        table.add_row("Last error", str(metrics.get("last_error") or "-"))
    return Panel(table, title="Current Operation", box=box.ROUNDED, border_style=color, padding=(0, 1))


def progress_panel(metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Stage", style="cyan", no_wrap=True, width=20)
    table.add_column("News", justify="right", no_wrap=True, width=14)
    table.add_column("SEC", justify="right", no_wrap=True, width=14)
    table.add_column("Total", justify="right", no_wrap=True, width=14)
    table.add_column("Last", overflow="fold", ratio=1)
    table.add_row(
        "Source gaps",
        fmt(metrics.get("news_source_rows")),
        fmt(metrics.get("sec_source_rows")),
        fmt(metrics.get("source_rows_fetched")),
        f"last_fetch={float(metrics.get('last_fetch_seconds') or 0.0):.2f}s",
    )
    table.add_row(
        "Token gaps",
        fmt(metrics.get("news_token_rows")),
        fmt(metrics.get("sec_token_rows")),
        fmt(metrics.get("token_rows_fetched")),
        f"last_embed={float(metrics.get('last_embedding_seconds') or 0.0):.2f}s",
    )
    table.add_row(
        "Embeddings",
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
    return Panel(table, title="Live Progress", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def runtime_panel(gateway: "TextEmbedGateway", metrics: dict[str, Any]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, width=22)
    table.add_column("Value", justify="right", no_wrap=True, width=18)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row("Device", str(metrics.get("embedding_device") or "-"), f"dtype={metrics.get('embedding_torch_dtype') or '-'} pooling={metrics.get('embedding_pooling') or '-'}")
    table.add_row("Embedding dim", fmt(metrics.get("embedding_dim")), f"load={float(metrics.get('model_load_seconds') or 0.0):.2f}s")
    table.add_row("Batch sizes", fmt(gateway.config.embedding_batch_size), f"source={gateway.config.source_batch_size:,} token={gateway.config.token_batch_size:,} insert={gateway.config.embedding_insert_batch_size:,}")
    table.add_row("Lookback", f"{gateway.config.live_lookback_minutes:,}m", f"closed historical={gateway.config.historical_lookback_days:,}d limit={gateway.config.historical_batch_limit:,}")
    table.add_row("Recent status", fmt(metrics.get("recent_status_rows")), f"ttl={gateway.config.recent_status_retention_hours:.1f}h")
    table.add_row("Failures", fmt(metrics.get("failures")), str(metrics.get("last_error") or "-"))
    table.add_row("Log", "-", truncate(str(metrics.get("run_log_path") or "-"), 140))
    return Panel(table, title="Runtime", box=box.ROUNDED, border_style="green" if not metrics.get("last_error") else "yellow", padding=(0, 1))


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


def style_status(value: str) -> str:
    color = status_color(value)
    return f"[{color}]{status_label(value)}[/{color}]"


def status_color(value: str) -> str:
    lowered = value.lower()
    if lowered in {"polling", "loaded", "schema", "running"}:
        return "green"
    if lowered in {"failed", "error"}:
        return "red"
    if lowered in {"stopping", "releasing_model", "loading_model"}:
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

