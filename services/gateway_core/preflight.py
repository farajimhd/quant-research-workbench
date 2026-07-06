"""Shared preflight result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.gateway_core.types import utc_now_text


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    seconds: float = 0.0
    detail: str = ""
    required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreflightReport:
    service_name: str
    checks: tuple[PreflightCheck, ...]
    checked_at_utc: str = field(default_factory=utc_now_text)

    @property
    def ok(self) -> bool:
        return all(check.status.lower() == "ok" or not check.required for check in self.checks)

    @property
    def status(self) -> str:
        if self.ok:
            return "ok"
        if any(check.required and check.status.lower() == "failed" for check in self.checks):
            return "failed"
        return "warning"

    def public_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "status": self.status,
            "checked_at_utc": self.checked_at_utc,
            "checks": [check.__dict__ for check in self.checks],
        }
