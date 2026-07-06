"""Shared lifecycle task ledger helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from services.gateway_core.types import TASK_STATES, utc_now_text


STANDARD_LIFECYCLE_TASKS: tuple[str, ...] = (
    "preflight",
    "bootstrap",
    "coverage_check",
    "gap_fill",
    "source_sync",
    "audit",
    "publish",
    "maintenance",
    "shutdown",
)


@dataclass
class TaskLedgerRow:
    name: str
    status: str = "waiting"
    rows: int | None = None
    message: str = ""
    started_at_utc: str = ""
    completed_at_utc: str = ""
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    _started_monotonic: float = field(default=0.0, repr=False)

    def start(self, message: str = "") -> None:
        self.status = "running"
        self.message = message or self.message
        self.started_at_utc = utc_now_text()
        self.completed_at_utc = ""
        self._started_monotonic = monotonic()

    def finish(self, status: str = "completed", message: str = "", rows: int | None = None) -> None:
        if status.lower() not in TASK_STATES:
            status = "completed"
        self.status = status.lower()
        self.message = message or self.message
        if rows is not None:
            self.rows = rows
        self.completed_at_utc = utc_now_text()
        if self._started_monotonic:
            self.elapsed_seconds = max(0.0, monotonic() - self._started_monotonic)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "rows": self.rows,
            "message": self.message,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "metadata": self.metadata,
        }


def new_task_ledger(tasks: tuple[str, ...] = STANDARD_LIFECYCLE_TASKS) -> dict[str, TaskLedgerRow]:
    return {name: TaskLedgerRow(name=name) for name in tasks}


def task_rows_for_dashboard(ledger: dict[str, TaskLedgerRow]) -> list[dict[str, Any]]:
    return [row.public_dict() for row in ledger.values()]
