from __future__ import annotations

from typing import Any

from services.gateway_core.dashboard import build_dashboard_snapshot


def build_health_payload(*, service_name: str, config: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    snapshot = build_dashboard_snapshot(service_name=service_name, config=config, metrics=metrics)
    status = snapshot["header"]["status"]
    return {
        "status": "ok" if status not in {"FAILED", "BLOCKED"} else "failed",
        "service_status": status,
        "config": snapshot["configuration"],
        "error_state": snapshot["error_state"],
        "metrics": metrics,
    }

