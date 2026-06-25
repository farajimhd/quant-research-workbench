from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from services.reference_gateway.audit import ReferenceAuditReport
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.policy import ReferenceWritePolicy


EASTERN = ZoneInfo("America/New_York")
VANCOUVER = ZoneInfo("America/Vancouver")


@dataclass(slots=True)
class OperationRecord:
    name: str
    status: str
    detail: str = ""
    rows: int | None = None
    seconds: float | None = None


@dataclass(slots=True)
class ReferenceRunRecord:
    config: ReferenceGatewayConfig
    write_policy: ReferenceWritePolicy
    operations: list[OperationRecord] = field(default_factory=list)
    audit: ReferenceAuditReport | None = None
    report_path: str = ""
    final_status: str = "running"
    wall_seconds: float = 0.0


def render_reference_run(record: ReferenceRunRecord, *, console: Console | None = None) -> None:
    output = console or Console()
    output.print(
        Group(
            header_panel(record),
            operations_panel(record.operations),
            audit_panel(record),
            warnings_panel(record),
        )
    )


def header_panel(record: ReferenceRunRecord) -> Panel:
    config = record.config
    now = datetime.now(UTC)
    status = record.final_status or (record.audit.status if record.audit else "running")
    color = status_color(status)
    location = "workstation" if config.is_workstation else "remote"
    mode = "execute" if config.execute else "dry-run"
    table = Table.grid(expand=True)
    table.add_column(ratio=3)
    table.add_column(justify="right", ratio=4)
    table.add_row(
        f"[bold]Python Reference Gateway[/bold]  [{color}]{status_label(status)}[/{color}]",
        f"[dim]UTC[/dim] {clock(now)}  [dim]ET[/dim] {clock(now.astimezone(EASTERN))}  [dim]VAN[/dim] {clock(now.astimezone(VANCOUVER))}",
    )
    table.add_row(
        f"[dim]mode[/dim] {mode}/{location}  [dim]bind[/dim] {config.bind}",
        f"[dim]read[/dim] {config.clickhouse_read_database}  [dim]write[/dim] {config.clickhouse_write_database}",
    )
    table.add_row(
        f"[dim]policy[/dim] {write_policy_text(record.write_policy)}",
        f"[dim]data[/dim] {truncate(str(config.data_root_win), 100)}",
    )
    return Panel(table, box=box.ROUNDED, border_style=color, padding=(0, 1))


def operations_panel(operations: list[OperationRecord]) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Step", style="cyan", no_wrap=True, width=30)
    table.add_column("Status", no_wrap=True, width=14)
    table.add_column("Rows", justify="right", no_wrap=True, width=12)
    table.add_column("Sec", justify="right", no_wrap=True, width=8)
    table.add_column("Detail", overflow="fold", ratio=1)
    if not operations:
        table.add_row("startup", style_status("running"), "-", "-", "No operations recorded yet.")
    for op in operations:
        table.add_row(
            op.name,
            style_status(op.status),
            fmt(op.rows) if op.rows is not None else "-",
            f"{op.seconds:.2f}" if op.seconds is not None else "-",
            op.detail or "-",
        )
    return Panel(table, title="Operations", box=box.ROUNDED, padding=(0, 1))


def audit_panel(record: ReferenceRunRecord) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Check", style="cyan", no_wrap=True, width=44)
    table.add_column("Severity", no_wrap=True, width=9)
    table.add_column("Status", no_wrap=True, width=10)
    table.add_column("Count", justify="right", no_wrap=True, width=12)
    table.add_column("Meaning", overflow="fold", ratio=1)
    audit = record.audit
    if audit is None:
        table.add_row("audit", "-", style_status("not_started"), "-", "Audit has not run yet.")
        status = "not_started"
    else:
        status = audit.status
        for check in audit.checks:
            table.add_row(
                check.name,
                severity_label(check.severity),
                style_status(check.status),
                fmt(check.count),
                check.message,
            )
    title = f"Reference Audit [{status_color(status)}]{status_label(status)}[/{status_color(status)}]"
    if record.report_path:
        title += f"  [dim]{record.report_path}[/dim]"
    return Panel(table, title=title, box=box.ROUNDED, border_style=status_color(status), padding=(0, 1))


def warnings_panel(record: ReferenceRunRecord) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=20)
    table.add_column(ratio=1, overflow="fold")
    audit = record.audit
    warning_checks = []
    error_checks = []
    if audit is not None:
        warning_checks = [check for check in audit.checks if check.severity == "warning" and check.status != "ok"]
        error_checks = [check for check in audit.checks if check.severity == "error" and check.status != "ok"]
    table.add_row("Final status", style_status(record.final_status))
    table.add_row("Wall time", f"{record.wall_seconds:.2f}s")
    table.add_row("Hard failures", fmt(len(error_checks)))
    table.add_row("Warnings", fmt(len(warning_checks)))
    if warning_checks:
        table.add_row(
            "Warning meaning",
            "Warnings mark rows as non-tradable or incomplete, but they do not crash the maintenance run.",
        )
    else:
        table.add_row("Warning meaning", "No warning checks failed.")
    return Panel(table, title="Summary", box=box.ROUNDED, border_style=status_color(record.final_status), padding=(0, 1))


def style_status(value: str) -> str:
    color = status_color(value)
    return f"[{color}]{status_label(value)}[/{color}]"


def status_color(value: str) -> str:
    lowered = str(value or "").lower()
    if lowered in {"ok", "completed", "pass", "allowed", "done"}:
        return "green"
    if lowered in {"failed", "error", "blocked"}:
        return "red"
    if lowered in {"warning", "warn", "skipped", "source_not_yet_available"}:
        return "yellow"
    return "cyan"


def status_label(value: str) -> str:
    return str(value or "-").replace("_", " ").upper()


def severity_label(value: str) -> str:
    lowered = str(value or "").lower()
    if lowered == "error":
        return "[red]error[/red]"
    if lowered == "warning":
        return "[yellow]warning[/yellow]"
    return f"[dim]{value}[/dim]"


def write_policy_text(policy: ReferenceWritePolicy) -> str:
    status = "allowed" if policy.writes_allowed else "blocked"
    return f"{status}; active_window={policy.active_collection_window}; {policy.reason}"


def clock(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def fmt(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return str(value or "-")


def truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
