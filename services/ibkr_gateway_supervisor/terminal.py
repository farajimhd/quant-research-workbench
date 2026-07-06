from __future__ import annotations

import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.rich_renderer import render_standard_snapshot, standard_live, status_color, style_status
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


@dataclass
class SupervisorTerminalState:
    started_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    gateway_status: str = "starting"
    auth_status: str = "unknown"
    keepalive_status: str = "idle"
    login_status: str = "not_started"
    account_status: str = "unknown"
    current_operation: str = "Starting supervisor"
    last_error: str = ""
    gateway_pid: int | None = None
    listener_pid: int | None = None
    status_code: int = 0
    auth_failures: int = 0
    reauth_attempts: int = 0
    login_attempts: int = 0
    tickle_count: int = 0
    tickle_failures: int = 0
    last_tickle_at_utc: datetime | None = None
    next_tickle_due_utc: datetime | None = None
    last_tickle_status_code: int = 0
    last_tickle_latency_ms: float = 0.0
    last_tickle_error: str = ""
    clickhouse_status: str = "not_started"
    clickhouse_error: str = ""
    event_log_path: str = ""
    recent_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=12))
    error_history: deque[str] = field(default_factory=lambda: deque(maxlen=6))


class SupervisorTerminal:
    def __init__(self, config: IbkrGatewayConfig, state: SupervisorTerminalState) -> None:
        self.config = config
        self.state = state
        self.live: Any | None = None

    def start(self) -> None:
        self.live = standard_live(
            render_dashboard(self.config, self.state),
            screen=self.config.terminal_screen_enabled,
            refresh_seconds=self.config.terminal_refresh_seconds,
        )
        self.live.start()

    def update(self) -> None:
        if self.live is not None:
            self.live.update(render_dashboard(self.config, self.state), refresh=True)

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None


def render_dashboard(config: IbkrGatewayConfig, state: SupervisorTerminalState) -> Group:
    _width, height = shutil.get_terminal_size((120, 40))
    compact = height < 34
    main = [
        render_standard_snapshot(ibkr_standard_snapshot(config, state)),
        tickle_panel(config, state),
        log_panel(state),
        events_panel(state, limit=4 if compact else 8),
    ]
    if not compact:
        main.insert(3, alerts_panel(state))
    return Group(*main)


def ibkr_standard_snapshot(config: IbkrGatewayConfig, state: SupervisorTerminalState) -> dict[str, Any]:
    metrics = {
        "current_phase": state.current_operation,
        "current_phase_message": state.last_error or state.last_tickle_error,
        "status": overall_status(state),
        "last_error": state.last_error,
        "errors": len(state.error_history),
        "poll_runs": state.tickle_count,
        "poll_failures": state.tickle_failures,
        "tasks": [
            {"name": "gateway session", "status": state.gateway_status, "message": state.current_operation},
            {"name": "authentication", "status": state.auth_status, "rows": state.auth_failures, "message": "auth status and reauthentication checks"},
            {"name": "keepalive", "status": state.keepalive_status, "rows": state.tickle_count, "message": state.last_tickle_error},
        ],
    }
    sources = [
        {"name": "IBKR Client Portal", "status": state.gateway_status, "detail": f"pid={state.gateway_pid or '-'} listener={state.listener_pid or '-'}"},
        {"name": "IBKR auth", "status": state.auth_status, "detail": f"http={state.status_code or '-'}"},
        {"name": "IBKR keepalive", "status": state.keepalive_status, "detail": f"tickles={state.tickle_count} failures={state.tickle_failures}"},
        {"name": "IBKR account", "status": state.account_status, "detail": config.account_key},
    ]
    return build_dashboard_snapshot(
        service_name="ibkr_gateway_supervisor",
        config=config,
        metrics=metrics,
        recent_items={"rows": list(state.recent_events)},
        sources_sinks=sources,
        service_specific={"event_log_path": state.event_log_path, "error_history": list(state.error_history)},
    )


def header_panel(config: IbkrGatewayConfig, state: SupervisorTerminalState) -> Panel:
    status = overall_status(state)
    color = status_color(status)
    now = datetime.now(UTC)
    grid = Table.grid(expand=True)
    grid.add_column(ratio=2)
    grid.add_column(justify="right", ratio=3)
    grid.add_row(
        f"[bold]IBKR Gateway Supervisor[/bold]  [{color}]{status.upper()}[/{color}]",
        f"[dim]UTC[/dim] {clock(now)}  [dim]ET[/dim] {clock(now.astimezone(EASTERN))}  [dim]VAN[/dim] {clock(now.astimezone(VANCOUVER))}",
    )
    grid.add_row(
        f"[dim]account[/dim] {config.account_key}  [dim]base[/dim] {config.base_url}",
        f"[dim]gateway[/dim] {state.gateway_status}  [dim]auth[/dim] {state.auth_status}  [dim]keepalive[/dim] {state.keepalive_status}",
    )
    return Panel(grid, box=box.ROUNDED, border_style=color, padding=(0, 1))


def operation_panel(state: SupervisorTerminalState) -> Panel:
    color = status_color(overall_status(state))
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("Operation", state.current_operation or "-")
    table.add_row("Updated", full_time(state.updated_at_utc))
    if state.last_error:
        table.add_row("Last error", f"[red]{state.last_error}[/red]")
    return Panel(table, title="Current Operation", box=box.ROUNDED, border_style=color, padding=(0, 1))


def session_panel(config: IbkrGatewayConfig, state: SupervisorTerminalState) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Item", style="cyan", no_wrap=True, width=16)
    table.add_column("Status", no_wrap=True, width=14)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row("Gateway", style_status(state.gateway_status), f"pid={state.gateway_pid or '-'} listener={state.listener_pid or '-'}")
    table.add_row("Authentication", style_status(state.auth_status), f"status_code={state.status_code or '-'}")
    table.add_row("Login", style_status(state.login_status), f"auto={config.auto_login} headless={config.login_headless}")
    table.add_row("Account", style_status(state.account_status), config.account_key)
    table.add_row("Keepalive", style_status(state.keepalive_status), f"tickle every {config.tickle_seconds:.0f}s")
    return Panel(table, title="Session", box=box.ROUNDED, border_style=status_color(overall_status(state)), padding=(0, 1))


def counters_panel(state: SupervisorTerminalState) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Counter", style="cyan", no_wrap=True, width=18)
    table.add_column("Value", justify="right", no_wrap=True, width=10)
    table.add_column("Context", overflow="fold", ratio=1)
    table.add_row("Auth failures", str(state.auth_failures), "resets after authenticated status")
    table.add_row("Reauth attempts", str(state.reauth_attempts), "ssodh/init attempts")
    table.add_row("Login attempts", str(state.login_attempts), "Playwright attempts")
    table.add_row("Tickles", str(state.tickle_count), "successful keepalive calls")
    table.add_row("Tickle failures", str(state.tickle_failures), "consecutive failed keepalive calls")
    return Panel(table, title="Counters", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def tickle_panel(config: IbkrGatewayConfig, state: SupervisorTerminalState) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Item", style="cyan", no_wrap=True, width=18)
    table.add_column("Value", overflow="fold", ratio=1)
    table.add_row("Status", style_status(state.keepalive_status))
    table.add_row("Frequency", f"{config.tickle_seconds:.0f}s")
    table.add_row("Last tickle", compact_time(state.last_tickle_at_utc))
    table.add_row("Next due", next_due_text(state.next_tickle_due_utc))
    table.add_row("HTTP", str(state.last_tickle_status_code or "-"))
    table.add_row("Latency", f"{state.last_tickle_latency_ms:.0f} ms" if state.last_tickle_latency_ms else "-")
    if state.last_tickle_error:
        table.add_row("Error", f"[red]{state.last_tickle_error[:220]}[/red]")
    return Panel(table, title="Keepalive Tickle", box=box.ROUNDED, border_style=status_color(state.keepalive_status), padding=(0, 1))


def log_panel(state: SupervisorTerminalState) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("JSONL", state.event_log_path or "-")
    table.add_row("ClickHouse", style_status(state.clickhouse_status))
    if state.clickhouse_error:
        table.add_row("CH error", f"[yellow]{state.clickhouse_error[:180]}[/yellow]")
    return Panel(table, title="Persistent Logs", box=box.ROUNDED, border_style=status_color(state.clickhouse_status), padding=(0, 1))


def alerts_panel(state: SupervisorTerminalState) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Error History", overflow="fold", ratio=1)
    if state.error_history:
        for item in list(state.error_history)[-6:]:
            table.add_row(f"[yellow]{item}[/yellow]")
    else:
        table.add_row("[dim]No errors recorded.[/dim]")
    return Panel(table, box=box.ROUNDED, border_style="yellow" if state.error_history else "green", padding=(0, 1))


def events_panel(state: SupervisorTerminalState, *, limit: int) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("UTC", no_wrap=True, width=9, style="dim")
    table.add_column("Event", no_wrap=True, width=28, style="cyan")
    table.add_column("Status", no_wrap=True, width=12)
    table.add_column("Detail", overflow="fold", ratio=1)
    rows = list(state.recent_events)[-limit:]
    if not rows:
        table.add_row("-", "-", "-", "[dim]No events yet.[/dim]")
    for row in rows:
        event = str(row.get("event") or "-")
        status = event_status(row)
        table.add_row(short_utc(row.get("ts_utc")), event, style_status(status), event_detail(row))
    return Panel(table, title="Recent Events", box=box.ROUNDED, border_style="cyan", padding=(0, 1))


def overall_status(state: SupervisorTerminalState) -> str:
    if current_connection_failed(state):
        return "failed"
    if state.auth_status == "authenticated" and state.keepalive_status in {"ok", "idle"}:
        return "ok"
    if state.login_status in {"running", "waiting"} or state.auth_status in {"unauthenticated", "unknown"}:
        return "working"
    return "warning"


def current_connection_failed(state: SupervisorTerminalState) -> bool:
    if state.auth_status == "authenticated":
        return False
    if state.last_error or state.login_status == "failed" or state.keepalive_status == "failed":
        return True
    return state.gateway_status in {"failed", "stopped"} and state.auth_status != "authenticated"


def event_status(row: dict[str, Any]) -> str:
    event = str(row.get("event") or "")
    if "failed" in event or row.get("error"):
        return "failed"
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    if result and not result.get("ok", False):
        return "warning"
    if "waiting" in event or "required" in event:
        return "warning"
    return "ok"


def event_detail(row: dict[str, Any]) -> str:
    for key in ("error", "reason", "message"):
        if row.get(key):
            return str(row[key])
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    if result:
        parts = []
        if result.get("status_code"):
            parts.append(f"status={result['status_code']}")
        if row.get("latency_ms"):
            parts.append(f"latency={float(row['latency_ms']):.0f}ms")
        if result.get("error"):
            parts.append(str(result["error"]))
        if not parts and "ok" in result:
            parts.append("ok" if result.get("ok") else "not ok")
        return " ".join(parts)
    return ""


def clock(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def full_time(value: datetime) -> str:
    return f"UTC {value.astimezone(UTC).strftime('%H:%M:%S')}  ET {value.astimezone(EASTERN).strftime('%H:%M:%S')}  VAN {value.astimezone(VANCOUVER).strftime('%H:%M:%S')}"


def compact_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    return f"UTC {value.astimezone(UTC).strftime('%H:%M:%S')} / ET {value.astimezone(EASTERN).strftime('%H:%M:%S')}"


def next_due_text(value: datetime | None) -> str:
    if value is None:
        return "-"
    seconds = max(0, int((value - datetime.now(UTC)).total_seconds()))
    return f"{compact_time(value)} ({seconds}s)"


def short_utc(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        return text.split("T", 1)[1].replace("Z", "")[:8]
    return "-"


def parse_event_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def tickle_next_due(value: Any, config: IbkrGatewayConfig) -> datetime:
    return parse_event_time(value) + timedelta(seconds=config.tickle_seconds)
