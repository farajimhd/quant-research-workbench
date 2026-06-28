from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
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


class ReferenceTerminalSession:
    def __init__(self, record: ReferenceRunRecord, *, console: Console | None = None, screen: bool = False) -> None:
        self.record = record
        self.console = console or Console()
        self.live = Live(
            render_reference_dashboard(record),
            console=self.console,
            auto_refresh=False,
            transient=False,
            screen=screen,
            vertical_overflow="crop",
            refresh_per_second=4,
        )
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self.live.start(refresh=True)
        self.started = True

    def update(self) -> None:
        if self.started:
            self.live.update(render_reference_dashboard(self.record), refresh=True)

    def stop(self) -> None:
        if self.started:
            self.live.stop()
            self.started = False


def render_reference_run(record: ReferenceRunRecord, *, console: Console | None = None) -> None:
    output = console or Console()
    output.print(render_reference_dashboard(record))


def render_reference_dashboard(record: ReferenceRunRecord) -> Group:
    _, terminal_height = shutil.get_terminal_size((120, 40))
    compact = terminal_height < 34
    if compact:
        return Group(
            header_panel(record),
            current_operation_panel(record),
            summary_panel(record),
            maintenance_panel(record),
            audit_aggregate_panel(record, limit=3),
            audit_findings_panel(record, limit=5),
        )
    return Group(
        header_panel(record),
        current_operation_panel(record),
        Columns([dependencies_panel(record), summary_panel(record)], equal=True, expand=True),
        Columns(
            [source_sync_panel(record), integrity_panel(record), maintenance_panel(record)],
            equal=True,
            expand=True,
        ),
        operations_panel(record.operations),
        audit_aggregate_panel(record, limit=4),
        audit_findings_panel(record, limit=10),
    )


def header_panel(record: ReferenceRunRecord) -> Panel:
    config = record.config
    now = datetime.now(UTC)
    status = record.final_status or (record.audit.status if record.audit else "running")
    color = status_color(status)
    location = "workstation" if config.is_workstation else "remote"
    execution = "execute" if config.execute else "diagnostic"
    table = Table.grid(expand=True)
    table.add_column(ratio=3)
    table.add_column(justify="right", ratio=4)
    table.add_row(
        f"[bold]Python Reference Gateway[/bold]  [{color}]{status_label(status)}[/{color}]",
        f"[dim]UTC[/dim] {clock(now)}  [dim]ET[/dim] {clock(now.astimezone(EASTERN))}  [dim]VAN[/dim] {clock(now.astimezone(VANCOUVER))}",
    )
    table.add_row(
        f"[dim]mode[/dim] {config.operator_mode}/{config.run_mode}/{execution}/{location}  [dim]bind[/dim] {config.bind}",
        f"[dim]read[/dim] {config.clickhouse_read_database}  [dim]write[/dim] {config.clickhouse_write_database}",
    )
    table.add_row(
        f"[dim]integrity[/dim] {config.integrity_mode}  [dim]maintenance[/dim] {config.maintenance_mode}",
        f"[dim]policy[/dim] {write_policy_text(record.write_policy)}",
    )
    table.add_row(
        f"[dim]data[/dim] {truncate(str(config.data_root_win), 100)}",
        f"[dim]report[/dim] {truncate(record.report_path or '-', 120)}",
    )
    return Panel(table, box=box.ROUNDED, border_style=color, padding=(0, 1))


def current_operation_panel(record: ReferenceRunRecord) -> Panel:
    current = last_operation(record)
    phase = current.name if current else "startup"
    status = current.status if current else "running"
    color = status_color(status)
    table = Table.grid(expand=True)
    table.add_column(style="cyan", no_wrap=True, width=18)
    table.add_column(ratio=1, overflow="fold")
    table.add_row("Current phase", f"[{color}]{status_label(phase)}[/{color}]")
    table.add_row("Phase status", style_status(status))
    table.add_row("Message", current.detail if current and current.detail else "Waiting for gateway work to report.")
    if current and current.rows is not None:
        table.add_row("Rows", fmt(current.rows))
    if current and current.seconds is not None:
        table.add_row("Elapsed", f"{current.seconds:.2f}s")
    return Panel(table, title="Current Operation", box=box.ROUNDED, border_style=color, padding=(0, 1))


def dependencies_panel(record: ReferenceRunRecord) -> Panel:
    checks = dependency_operations(record.operations)
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Check", style="cyan", no_wrap=True, width=22)
    table.add_column("Status", no_wrap=True, width=12)
    table.add_column("Sec", justify="right", no_wrap=True, width=8)
    table.add_column("Detail", overflow="fold", ratio=1)
    if not checks:
        table.add_row("preflight", style_status("waiting"), "-", "Dependency preflight has not reported yet.")
        status = "waiting"
    else:
        status = checks[-1].status
        for op in checks:
            table.add_row(
                short_name(op.name),
                style_status(op.status),
                f"{op.seconds:.2f}" if op.seconds is not None else "-",
                op.detail or "-",
            )
    return Panel(table, title=f"Dependencies [{status_label(status)}]", box=box.ROUNDED, border_style=status_color(status), padding=(0, 1))


def summary_panel(record: ReferenceRunRecord) -> Panel:
    audit = record.audit
    errors, warnings = audit_failures(record)
    source = latest_operation(record.operations, "Source sync")
    issue_write = latest_operation(record.operations, "Write source-sync issues")
    block = latest_operation(record.operations, "Immediate tradability block")
    alert_write = latest_operation(record.operations, "Write reference alerts")
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True, width=22)
    table.add_column("Value", justify="right", no_wrap=True, width=16)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row("Run status", style_status(record.final_status), f"{record.wall_seconds:.2f}s" if record.wall_seconds else "running")
    table.add_row("Audit status", style_status(audit.status if audit else "not_started"), audit.checked_at_utc if audit else "-")
    table.add_row("Audit failures", fmt(len(errors)), f"warnings {fmt(len(warnings))}")
    table.add_row("Source candidates", fmt(source.rows if source else None), source.detail if source else "not reported")
    table.add_row("Issue writes", fmt(issue_write.rows if issue_write else None), issue_write.detail if issue_write else "not reported")
    table.add_row("Alert writes", fmt(alert_write.rows if alert_write else None), alert_write.detail if alert_write else "not reported")
    table.add_row("Tradability blocks", fmt(block.rows if block else None), block.detail if block else "not reported")
    table.add_row("Write policy", "allowed" if record.write_policy.writes_allowed else "blocked", record.write_policy.reason)
    return Panel(table, title="Runtime Summary", box=box.ROUNDED, border_style=status_color(record.final_status), padding=(0, 1))


def source_sync_panel(record: ReferenceRunRecord) -> Panel:
    source = latest_operation(record.operations, "Source sync")
    issue_write = latest_operation(record.operations, "Write source-sync issues")
    graph = latest_operation(record.operations, "Write canonical graph")
    graph_issue = latest_operation(record.operations, "Write graph issues")
    table = compact_ops_table()
    add_compact_op(table, "Source sync", source)
    add_compact_op(table, "Issue rows", issue_write)
    add_compact_op(table, "Graph write", graph)
    add_compact_op(table, "Graph issues", graph_issue)
    return Panel(table, title="Source Sync", box=box.ROUNDED, border_style=panel_status_color([source, issue_write, graph, graph_issue]), padding=(0, 1))


def integrity_panel(record: ReferenceRunRecord) -> Panel:
    audit = latest_operation(record.operations, "Post-write reference audit") or latest_operation(record.operations, "Reference audit")
    resolve_ops = [op for op in record.operations if op.name == "Resolve issues"]
    block_ops = [op for op in record.operations if op.name == "Immediate tradability block"]
    errors, warnings = audit_failures(record)
    table = compact_ops_table()
    add_compact_op(table, "Audit", audit)
    add_compact_op(table, "Resolve", resolve_ops[-1] if resolve_ops else None)
    add_compact_op(table, "Block", block_ops[-1] if block_ops else None)
    table.add_row("Error checks", style_status("failed" if errors else "ok"), fmt(len(errors)), ", ".join(check.name for check in errors[:3]) or "-")
    table.add_row("Warning checks", style_status("warning" if warnings else "ok"), fmt(len(warnings)), ", ".join(check.name for check in warnings[:3]) or "-")
    return Panel(table, title="Integrity Guardrail", box=box.ROUNDED, border_style=status_color("failed" if errors else "warning" if warnings else "ok"), padding=(0, 1))


def maintenance_panel(record: ReferenceRunRecord) -> Panel:
    schema = latest_operation(record.operations, "Market publication schema")
    rebuild = latest_operation(record.operations, "Rebuild tradable publications")
    gap_fill = latest_operation(record.operations, "Market publication gap fill")
    policy = latest_operation(record.operations, "Promotion write policy")
    table = compact_ops_table()
    table.add_row("Mode", style_status(record.config.maintenance_mode), "-", write_policy_text(record.write_policy))
    add_compact_op(table, "Policy", policy)
    add_compact_op(table, "Schema", schema)
    add_compact_op(table, "Rebuild", rebuild)
    add_compact_op(table, "Gap fill", gap_fill)
    ops = [op for op in [policy, schema, rebuild, gap_fill] if op is not None]
    return Panel(table, title="Maintenance", box=box.ROUNDED, border_style=panel_status_color(ops), padding=(0, 1))


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


def audit_findings_panel(record: ReferenceRunRecord, *, limit: int) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Check", style="cyan", no_wrap=True, width=40)
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
        checks = sorted(audit.checks, key=audit_sort_key)
        for check in checks[:limit]:
            table.add_row(
                check.name,
                severity_label(check.severity),
                style_status(check.status),
                fmt(check.count),
                check.message,
            )
        hidden = max(0, len(checks) - limit)
        if hidden:
            table.add_row("[dim]more[/dim]", "-", "-", fmt(hidden), "Additional audit checks hidden in compact terminal view.")
    title = f"Reference Audit [{status_color(status)}]{status_label(status)}[/{status_color(status)}]"
    if record.report_path:
        title += f"  [dim]{record.report_path}[/dim]"
    return Panel(table, title=title, box=box.ROUNDED, border_style=status_color(status), padding=(0, 1))


def audit_aggregate_panel(record: ReferenceRunRecord, *, limit: int) -> Panel:
    audit = record.audit
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Group", style="cyan", no_wrap=True, width=18)
    table.add_column("Status", no_wrap=True, width=10)
    table.add_column("Checks", justify="right", no_wrap=True, width=8)
    table.add_column("Rows", justify="right", no_wrap=True, width=12)
    table.add_column("Latest / High Priority Message", overflow="fold", ratio=1)

    if audit is None:
        table.add_row("audit", style_status("not_started"), "-", "-", "Audit has not run yet.")
        return Panel(table, title="Audit Issue Summary", box=box.ROUNDED, border_style="cyan", padding=(0, 1))

    checks = list(audit.checks)
    failed_errors = [check for check in checks if check.severity == "error" and check.status != "ok"]
    failed_warnings = [check for check in checks if check.severity == "warning" and check.status != "ok"]
    ok_checks = [check for check in checks if check.status == "ok"]
    other_checks = [check for check in checks if check.status != "ok" and check.severity not in {"error", "warning"}]

    table.add_row(
        "Errors",
        style_status("failed" if failed_errors else "ok"),
        fmt(len(failed_errors)),
        fmt(audit_row_count(failed_errors)),
        audit_group_message(failed_errors, "No failed error checks."),
    )
    table.add_row(
        "Warnings",
        style_status("warning" if failed_warnings else "ok"),
        fmt(len(failed_warnings)),
        fmt(audit_row_count(failed_warnings)),
        audit_group_message(failed_warnings, "No failed warning checks."),
    )
    table.add_row(
        "Other findings",
        style_status("warning" if other_checks else "ok"),
        fmt(len(other_checks)),
        fmt(audit_row_count(other_checks)),
        audit_group_message(other_checks, "No other failed checks."),
    )
    table.add_row(
        "OK checks",
        style_status("ok"),
        fmt(len(ok_checks)),
        "-",
        f"Total checks {fmt(len(checks))}; audit status {status_label(audit.status)}.",
    )

    prioritized = sorted(failed_errors + failed_warnings + other_checks, key=audit_sort_key)
    for check in prioritized[:limit]:
        table.add_row(
            truncate(check.name, 18),
            severity_label(check.severity),
            fmt(1),
            fmt(check.count),
            f"{check.message} ({check.name})",
        )
    hidden = max(0, len(prioritized) - limit)
    if hidden:
        table.add_row("[dim]more[/dim]", "-", fmt(hidden), "-", "Additional audit warnings/errors hidden in this view.")

    return Panel(table, title="Audit Issue Summary", box=box.ROUNDED, border_style=status_color(audit.status), padding=(0, 1))


def style_status(value: str) -> str:
    color = status_color(value)
    return f"[{color}]{status_label(value)}[/{color}]"


def status_color(value: str) -> str:
    lowered = str(value or "").lower()
    if lowered in {"ok", "completed", "pass", "allowed", "done", "success", "auto"}:
        return "green"
    if lowered in {"failed", "error", "blocked"}:
        return "red"
    if lowered in {"warning", "warn", "skipped", "source_not_yet_available", "deferred", "skip"}:
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
    return text[: max(0, limit - 3)] + "..."


def short_name(value: str) -> str:
    return str(value or "-").replace("Dependency ", "")


def last_operation(record: ReferenceRunRecord) -> OperationRecord | None:
    return record.operations[-1] if record.operations else None


def latest_operation(operations: list[OperationRecord], name: str) -> OperationRecord | None:
    for op in reversed(operations):
        if op.name == name:
            return op
    return None


def dependency_operations(operations: list[OperationRecord]) -> list[OperationRecord]:
    return [op for op in operations if "preflight" in op.name.lower() or "dependency" in op.name.lower()]


def compact_ops_table() -> Table:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Item", style="cyan", no_wrap=True, width=16)
    table.add_column("Status", no_wrap=True, width=12)
    table.add_column("Rows", justify="right", no_wrap=True, width=10)
    table.add_column("Detail", overflow="fold", ratio=1)
    return table


def add_compact_op(table: Table, label: str, op: OperationRecord | None) -> None:
    if op is None:
        table.add_row(label, style_status("waiting"), "-", "not reported")
        return
    table.add_row(
        label,
        style_status(op.status),
        fmt(op.rows) if op.rows is not None else "-",
        op.detail or "-",
    )


def panel_status_color(operations: list[OperationRecord | None]) -> str:
    statuses = [str(op.status).lower() for op in operations if op is not None]
    if any(status in {"failed", "error", "blocked"} for status in statuses):
        return "red"
    if any(status in {"warning", "warn", "skipped", "deferred"} for status in statuses):
        return "yellow"
    if statuses and all(status in {"ok", "completed", "done", "pass", "success"} for status in statuses):
        return "green"
    return "cyan"


def audit_failures(record: ReferenceRunRecord) -> tuple[list[Any], list[Any]]:
    if record.audit is None:
        return [], []
    errors = [check for check in record.audit.checks if check.severity == "error" and check.status != "ok"]
    warnings = [check for check in record.audit.checks if check.severity == "warning" and check.status != "ok"]
    return errors, warnings


def audit_row_count(checks: list[Any]) -> int:
    total = 0
    for check in checks:
        try:
            total += int(getattr(check, "count", 0) or 0)
        except Exception:
            continue
    return total


def audit_group_message(checks: list[Any], empty_message: str) -> str:
    if not checks:
        return empty_message
    sorted_checks = sorted(checks, key=audit_sort_key)
    first = sorted_checks[0]
    return f"{first.message} ({first.name})"


def audit_sort_key(check: Any) -> tuple[int, int, str]:
    status_rank = 0 if getattr(check, "status", "") != "ok" else 1
    severity = getattr(check, "severity", "")
    severity_rank = 0 if severity == "error" else 1 if severity == "warning" else 2
    return (status_rank, severity_rank, str(getattr(check, "name", "")))
