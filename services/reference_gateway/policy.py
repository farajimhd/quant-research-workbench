from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from services.gateway_policy import active_collection_window, maintenance_window_message, service_collection_window
from services.reference_gateway.config import ReferenceGatewayConfig


@dataclass(frozen=True, slots=True)
class ReferenceWritePolicy:
    execute_requested: bool
    active_collection_window: bool
    writes_allowed: bool
    override_enabled: bool
    reason: str
    window_label: str


def evaluate_write_policy(config: ReferenceGatewayConfig, now_utc: datetime | None = None) -> ReferenceWritePolicy:
    now = now_utc or datetime.now(UTC)
    window = service_collection_window("REFERENCE")
    active = active_collection_window(now, service_prefix="REFERENCE")
    override_enabled = bool(config.market_hours_write_override and config.market_hours_write_reason.strip())
    if not config.execute:
        return ReferenceWritePolicy(
            execute_requested=False,
            active_collection_window=active,
            writes_allowed=False,
            override_enabled=override_enabled,
            reason="read_only_audit",
            window_label=window.label,
        )
    if not config.after_hours_writes_only:
        return ReferenceWritePolicy(
            execute_requested=True,
            active_collection_window=active,
            writes_allowed=True,
            override_enabled=override_enabled,
            reason="after_hours_policy_disabled",
            window_label=window.label,
        )
    if not active:
        return ReferenceWritePolicy(
            execute_requested=True,
            active_collection_window=False,
            writes_allowed=True,
            override_enabled=override_enabled,
            reason=maintenance_window_message("REFERENCE"),
            window_label=window.label,
        )
    if override_enabled:
        return ReferenceWritePolicy(
            execute_requested=True,
            active_collection_window=True,
            writes_allowed=True,
            override_enabled=True,
            reason="market_hours_override: " + config.market_hours_write_reason.strip(),
            window_label=window.label,
        )
    return ReferenceWritePolicy(
        execute_requested=True,
        active_collection_window=True,
        writes_allowed=False,
        override_enabled=False,
        reason="promotion_and_maintenance_writes_blocked_during_active_collection_window",
        window_label=window.label,
    )
