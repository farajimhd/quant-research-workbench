from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from services.reference_gateway.audit import ReferenceAuditReport
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.policy import ReferenceWritePolicy
from services.reference_gateway.state import ReferenceSourceState, ReferenceTableState


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
    source_states: list[ReferenceSourceState] = field(default_factory=list)
    table_states: list[ReferenceTableState] = field(default_factory=list)
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
        )
        self.started = False
        self.last_update_at = 0.0

    def start(self) -> None:
        if self.started:
            return
        self.live.start(refresh=True)
        self.started = True

    def update(self) -> None:
        if self.started:
            now = time.monotonic()
            if now - self.last_update_at >= 0.5:
                self.live.update(render_reference_dashboard(self.record), refresh=True)
                self.last_update_at = now

    def stop(self) -> None:
        if self.started:
            self.live.refresh()
            self.live.stop()
            self.started = False


def render_reference_run(record: ReferenceRunRecord, *, console: Console | None = None) -> None:
    output = console or Console()
    output.print(render_reference_dashboard(record))


def render_reference_dashboard(record: ReferenceRunRecord) -> Group:
    terminal_width, terminal_height = shutil.get_terminal_size((140, 44))
    compact = terminal_height < 34
    narrow = terminal_width < 190
    roomy = terminal_width >= 190 and terminal_height >= 62
    if compact:
        return Group(
            header_panel(record),
            current_operation_panel(record),
            overview_panel(record),
            source_sync_panel(record, compact=True),
            source_coverage_panel(record, limit=8),
            reference_tables_panel(record, limit=6, detail_rows=False),
            guardrail_maintenance_panel(record, compact=True),
            audit_aggregate_panel(record, limit=3),
        )
    if narrow or not roomy:
        return Group(
            header_panel(record),
            current_operation_panel(record),
            overview_panel(record),
            source_sync_panel(record, compact=True),
            source_coverage_panel(record, limit=12),
            reference_tables_panel(record, limit=9, detail_rows=False),
            guardrail_maintenance_panel(record, compact=True),
            operations_panel(record.operations, limit=8),
            audit_aggregate_panel(record, limit=4),
        )
    return Group(
        header_panel(record),
        current_operation_panel(record),
        overview_panel(record),
        source_sync_panel(record, compact=False),
        source_coverage_panel(record, limit=20),
        reference_tables_panel(record, limit=12, detail_rows=True),
        guardrail_maintenance_panel(record, compact=False),
        operations_panel(record.operations, limit=10),
        audit_aggregate_panel(record, limit=4),
        audit_findings_panel(record, limit=6),
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


def overview_panel(record: ReferenceRunRecord) -> Panel:
    audit = record.audit
    errors, warnings = audit_failures(record)
    source = latest_operation(record.operations, "Source sync")
    issue_write = latest_operation(record.operations, "Write source-sync issues")
    block = latest_operation(record.operations, "Immediate tradability block")
    alert_write = latest_operation(record.operations, "Write reference alerts")
    preflight = dependency_operations(record.operations)
    preflight_op = preflight[-1] if preflight else None
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Area", style="cyan", no_wrap=True, width=22)
    table.add_column("Status", no_wrap=True, width=14)
    table.add_column("Rows / Sec", justify="right", no_wrap=True, width=14)
    table.add_column("Detail", overflow="fold", ratio=1)
    table.add_row(
        "Preflight",
        style_status(preflight_op.status if preflight_op else "waiting"),
        f"{preflight_op.seconds:.2f}s" if preflight_op and preflight_op.seconds is not None else "-",
        truncate(preflight_op.detail if preflight_op else "Dependency preflight has not reported yet.", 180),
    )
    table.add_row("Run", style_status(record.final_status), f"{record.wall_seconds:.2f}s" if record.wall_seconds else "-", "Gateway process state.")
    table.add_row("Audit", style_status(audit.status if audit else "not_started"), fmt(len(errors) + len(warnings)), audit.checked_at_utc if audit else "Audit has not run yet.")
    table.add_row("Source sync", style_status(source.status if source else "waiting"), fmt(source.rows if source else None), truncate(source.detail if source else "not reported", 180))
    table.add_row("Issue writes", style_status(issue_write.status if issue_write else "waiting"), fmt(issue_write.rows if issue_write else None), truncate(issue_write.detail if issue_write else "not reported", 180))
    table.add_row("Alert writes", style_status(alert_write.status if alert_write else "waiting"), fmt(alert_write.rows if alert_write else None), truncate(alert_write.detail if alert_write else "not reported", 180))
    table.add_row("Tradability blocks", style_status(block.status if block else "waiting"), fmt(block.rows if block else None), truncate(block.detail if block else "not reported", 180))
    table.add_row("Write policy", style_status("allowed" if record.write_policy.writes_allowed else "blocked"), "-", truncate(write_policy_text(record.write_policy), 180))
    ops = [preflight_op, source, issue_write, block, alert_write]
    return Panel(table, title="Runtime Overview", box=box.ROUNDED, border_style=panel_status_color(ops), padding=(0, 1))


def guardrail_maintenance_panel(record: ReferenceRunRecord, *, compact: bool = False) -> Panel:
    audit = latest_operation(record.operations, "Post-write reference audit") or latest_operation(record.operations, "Reference audit")
    resolve = latest_operation(record.operations, "Resolve issues")
    block = latest_operation(record.operations, "Immediate tradability block")
    schema = latest_operation(record.operations, "Market publication schema")
    rebuild = latest_operation(record.operations, "Rebuild tradable publications")
    gap_fill = latest_operation(record.operations, "Market publication gap fill")
    policy = latest_operation(record.operations, "Promotion write policy")
    errors, warnings = audit_failures(record)
    detail_limit = 120 if compact else 220
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Group", style="cyan", no_wrap=True, width=18)
    table.add_column("Item", style="cyan", no_wrap=True, width=24)
    table.add_column("Status", no_wrap=True, width=14)
    table.add_column("Rows", justify="right", no_wrap=True, width=10)
    table.add_column("Detail", overflow="fold", ratio=1)

    add_grouped_op(table, "Integrity", "Audit", audit, detail_limit=detail_limit)
    add_grouped_op(table, "Integrity", "Resolve", resolve, detail_limit=detail_limit)
    add_grouped_op(table, "Integrity", "Block", block, detail_limit=detail_limit)
    table.add_row("Integrity", "Error checks", style_status("failed" if errors else "ok"), fmt(len(errors)), ", ".join(check.name for check in errors[:3]) or "-")
    table.add_row("Integrity", "Warning checks", style_status("warning" if warnings else "ok"), fmt(len(warnings)), ", ".join(check.name for check in warnings[:3]) or "-")
    table.add_row("Maintenance", "Mode", style_status(record.config.maintenance_mode), "-", truncate(write_policy_text(record.write_policy), detail_limit))
    add_grouped_op(table, "Maintenance", "Policy", policy, detail_limit=detail_limit)
    add_grouped_op(table, "Maintenance", "Schema", schema, detail_limit=detail_limit)
    add_grouped_op(table, "Maintenance", "Rebuild", rebuild, detail_limit=detail_limit)
    add_grouped_op(table, "Maintenance", "Gap fill", gap_fill, detail_limit=detail_limit)
    ops = [audit, resolve, block, schema, rebuild, gap_fill, policy]
    return Panel(table, title="Integrity And Maintenance", box=box.ROUNDED, border_style=panel_status_color(ops), padding=(0, 1))


def source_sync_panel(record: ReferenceRunRecord, *, compact: bool = False) -> Panel:
    source = latest_operation(record.operations, "Source sync")
    issue_write = latest_operation(record.operations, "Write source-sync issues")
    graph = latest_operation(record.operations, "Write canonical graph")
    graph_issue = latest_operation(record.operations, "Write graph issues")
    table = compact_ops_table(item_width=28 if compact else 38)
    source_ops = [op for op in record.operations if op.name.startswith("Source: ")]
    if source_ops:
        for op in source_ops:
            add_compact_op(table, op.name.replace("Source: ", ""), op, detail_limit=120 if compact else 220)
    else:
        add_compact_op(table, "Source sync", source)
    add_compact_op(table, "Issue rows", issue_write, detail_limit=120 if compact else 220)
    add_compact_op(table, "Graph write", graph, detail_limit=120 if compact else 220)
    add_compact_op(table, "Graph issues", graph_issue, detail_limit=120 if compact else 220)
    return Panel(table, title="Source Sync", box=box.ROUNDED, border_style=panel_status_color([*source_ops, source, issue_write, graph, graph_issue]), padding=(0, 1))


def source_coverage_panel(record: ReferenceRunRecord, *, limit: int) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Source", style="cyan", no_wrap=True, width=28)
    table.add_column("Status", no_wrap=True, width=12)
    table.add_column("Coverage / Last", no_wrap=True, width=25)
    table.add_column("Rows", justify="right", no_wrap=True, width=12)
    table.add_column("Targets", overflow="fold", ratio=1)
    table.add_column("Notes", overflow="fold", ratio=1)
    states = record.source_states[:limit]
    if not states:
        table.add_row("coverage", style_status("waiting"), "-", "-", "-", "Source coverage has not been inspected yet.")
    for state in states:
        table.add_row(
            state.source,
            style_status(state.status),
            state.coverage,
            fmt(state.rows) if state.rows is not None else "-",
            truncate(state.targets, 120),
            truncate(state.note, 120),
        )
    hidden = max(0, len(record.source_states) - limit)
    if hidden:
        table.add_row("[dim]more[/dim]", "-", "-", fmt(hidden), "-", "Additional source rows hidden in compact terminal view.")
    return Panel(table, title="Source Coverage", box=box.ROUNDED, border_style=source_state_color(record.source_states), padding=(0, 1))


def reference_tables_panel(record: ReferenceRunRecord, *, limit: int, detail_rows: bool) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Group", style="cyan", no_wrap=True, width=28)
    table.add_column("Group Status", no_wrap=True, width=12)
    table.add_column("Tables", justify="right", no_wrap=True, width=8)
    if detail_rows:
        table.add_column("Table", overflow="fold", ratio=1)
        table.add_column("Table Status", no_wrap=True, width=12)
        table.add_column("Table Rows", justify="right", no_wrap=True, width=12)
        table.add_column("Latest Update", no_wrap=True, width=20)
    else:
        table.add_column("Rows", justify="right", no_wrap=True, width=13)
        table.add_column("Latest Update", no_wrap=True, width=20)
        table.add_column("Contents", overflow="fold", ratio=1)
    states = record.table_states[:limit]
    if not states:
        if detail_rows:
            table.add_row("reference tables", style_status("waiting"), "-", "-", "-", "-", "Table state has not been inspected yet.")
        else:
            table.add_row("reference tables", style_status("waiting"), "-", "-", "-", "Table state has not been inspected yet.")
    for state in states:
        details = state.details or ()
        if not detail_rows:
            table.add_row(
                state.group_id,
                style_status(state.status),
                f"{state.tables_present}/{state.tables_total}",
                fmt(state.rows),
                compact_datetime(state.latest_update),
                table_detail_summary(details),
            )
            continue
        if not details:
            table.add_row(
                state.group_id,
                style_status(state.status),
                f"{state.tables_present}/{state.tables_total}",
                "-",
                "-",
                fmt(state.rows),
                compact_datetime(state.latest_update),
            )
            continue
        for index, detail in enumerate(details):
            table.add_row(
                state.group_id if index == 0 else "",
                style_status(state.status) if index == 0 else "",
                f"{state.tables_present}/{state.tables_total}" if index == 0 else "",
                detail.table_name,
                style_status(detail.status),
                fmt(detail.rows),
                compact_datetime(detail.latest_update),
            )
    hidden = max(0, len(record.table_states) - limit)
    if hidden:
        if detail_rows:
            table.add_row("[dim]more[/dim]", "-", fmt(hidden), "-", "-", "-", "Additional table groups hidden in compact terminal view.")
        else:
            table.add_row("[dim]more[/dim]", "-", fmt(hidden), "-", "-", "Additional table groups hidden in compact terminal view.")
    return Panel(table, title="Reference Table State", box=box.ROUNDED, border_style=table_state_color(record.table_states), padding=(0, 1))


def operations_panel(operations: list[OperationRecord], *, limit: int = 10) -> Panel:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Step", style="cyan", no_wrap=True, width=30)
    table.add_column("Status", no_wrap=True, width=14)
    table.add_column("Rows", justify="right", no_wrap=True, width=12)
    table.add_column("Sec", justify="right", no_wrap=True, width=8)
    table.add_column("Detail", overflow="fold", ratio=1)
    if not operations:
        table.add_row("startup", style_status("running"), "-", "-", "No operations recorded yet.")
    hidden = max(0, len(operations) - limit)
    if hidden:
        table.add_row("[dim]earlier steps[/dim]", "-", fmt(hidden), "-", "Older operation rows are hidden to keep the terminal stable.")
    for op in operations[-limit:]:
        table.add_row(
            op.name,
            style_status(op.status),
            fmt(op.rows) if op.rows is not None else "-",
            f"{op.seconds:.2f}" if op.seconds is not None else "-",
            truncate(op.detail or "-", 220),
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
    if lowered in {"warning", "warn", "skipped", "source_not_yet_available", "deferred", "skip", "stale", "missing"}:
        return "yellow"
    if lowered in {"planned", "empty", "partial"}:
        return "cyan"
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


def compact_datetime(value: str) -> str:
    text = str(value or "").strip()
    if not text or text == "-":
        return "-"
    return text.replace("T", " ")[:19]


def table_detail_summary(details: tuple[Any, ...]) -> str:
    if not details:
        return "-"
    visible = []
    for detail in details[:8]:
        visible.append(f"{detail.table_name}:{fmt(detail.rows)}")
    hidden = len(details) - len(visible)
    if hidden > 0:
        visible.append(f"+{hidden} more")
    return "; ".join(visible)


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


def last_operation(record: ReferenceRunRecord) -> OperationRecord | None:
    return record.operations[-1] if record.operations else None


def latest_operation(operations: list[OperationRecord], name: str) -> OperationRecord | None:
    for op in reversed(operations):
        if op.name == name:
            return op
    return None


def dependency_operations(operations: list[OperationRecord]) -> list[OperationRecord]:
    return [op for op in operations if "preflight" in op.name.lower() or "dependency" in op.name.lower()]


def compact_ops_table(*, item_width: int = 16) -> Table:
    table = Table(box=box.SIMPLE, expand=True, show_edge=False)
    table.add_column("Item", style="cyan", no_wrap=True, width=item_width)
    table.add_column("Status", no_wrap=True, width=12)
    table.add_column("Rows", justify="right", no_wrap=True, width=10)
    table.add_column("Detail", overflow="fold", ratio=1)
    return table


def add_compact_op(table: Table, label: str, op: OperationRecord | None, *, detail_limit: int = 140) -> None:
    if op is None:
        table.add_row(label, style_status("waiting"), "-", "not reported")
        return
    table.add_row(
        label,
        style_status(op.status),
        fmt(op.rows) if op.rows is not None else "-",
        truncate(op.detail or "-", detail_limit),
    )


def add_grouped_op(table: Table, group: str, label: str, op: OperationRecord | None, *, detail_limit: int = 140) -> None:
    if op is None:
        table.add_row(group, label, style_status("waiting"), "-", "not reported")
        return
    table.add_row(
        group,
        label,
        style_status(op.status),
        fmt(op.rows) if op.rows is not None else "-",
        truncate(op.detail or "-", detail_limit),
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


def source_state_color(states: list[ReferenceSourceState]) -> str:
    statuses = {state.status.lower() for state in states}
    if "failed" in statuses:
        return "red"
    if statuses & {"stale", "missing", "warning"}:
        return "yellow"
    if statuses and statuses <= {"ok", "planned"}:
        return "green"
    return "cyan"


def table_state_color(states: list[ReferenceTableState]) -> str:
    statuses = {state.status.lower() for state in states}
    if "missing" in statuses:
        return "red"
    if statuses & {"warning", "stale"}:
        return "yellow"
    if statuses and statuses <= {"ok", "empty", "partial"}:
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
