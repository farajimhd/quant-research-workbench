"""Shared audit result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditCheckResult:
    name: str
    status: str
    severity: str
    count: int = 0
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditReport:
    service_name: str
    checks: tuple[AuditCheckResult, ...]

    @property
    def status(self) -> str:
        if any(check.status.lower() == "failed" and check.severity.lower() == "error" for check in self.checks):
            return "failed"
        if any(check.status.lower() in {"failed", "warning"} for check in self.checks):
            return "warning"
        return "ok"

    def public_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "status": self.status,
            "checks": [check.__dict__ for check in self.checks],
        }
