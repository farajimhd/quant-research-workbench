"""Shared reconciliation contracts.

Reconciliation means comparing upstream source coverage to service-owned output
coverage and deriving bounded work. It is distinct from provider polling but
uses the same backfill and coverage vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ReconciliationWorkItem:
    source: str
    target: str
    start_utc: datetime
    end_utc: datetime
    reason: str
    priority: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def days(self) -> float:
        return max(0.0, (self.end_utc - self.start_utc).total_seconds() / 86400.0)


@dataclass(frozen=True)
class ReconciliationPlan:
    service_name: str
    items: tuple[ReconciliationWorkItem, ...]
    execute_inline: bool
    generated_script_path: str = ""
    message: str = ""

    @property
    def total_gap_days(self) -> float:
        return sum(item.days for item in self.items)

    def public_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "execute_inline": self.execute_inline,
            "generated_script_path": self.generated_script_path,
            "message": self.message,
            "total_gap_days": round(self.total_gap_days, 3),
            "items": [item.__dict__ for item in self.items],
        }
