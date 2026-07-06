from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


SERVICE_STATES = {
    "STARTING",
    "PREFLIGHT",
    "RUNNING",
    "IDLE",
    "WORKING",
    "CATCHING_UP",
    "DEGRADED",
    "BLOCKED",
    "STOPPING",
    "FAILED",
}

TASK_STATES = {"waiting", "running", "completed", "skipped", "deferred", "blocked", "failed"}
SEVERITIES = {"critical", "error", "warning", "info"}


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class ErrorSummary:
    active_critical_count: int = 0
    active_error_count: int = 0
    active_warning_count: int = 0
    retrying_count: int = 0
    resolved_this_run_count: int = 0
    retry_exhausted_count: int = 0
    manual_action_count: int = 0
    latest_active_errors: list[dict[str, Any]] = field(default_factory=list)
    latest_resolved_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DashboardState:
    header: dict[str, Any] = field(default_factory=dict)
    current_operation: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    runtime: dict[str, Any] = field(default_factory=dict)
    daily_summary: dict[str, Any] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    task_table_progress: list[dict[str, Any]] = field(default_factory=list)
    queues: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    sources_sinks: list[dict[str, Any]] = field(default_factory=list)
    recent_items: dict[str, Any] = field(default_factory=dict)
    error_state: dict[str, Any] = field(default_factory=dict)
    warnings_errors: dict[str, Any] = field(default_factory=dict)
    service_specific: dict[str, Any] = field(default_factory=dict)

