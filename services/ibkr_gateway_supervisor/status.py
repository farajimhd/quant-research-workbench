"""Standard status snapshot adapter for the IBKR supervisor."""

from __future__ import annotations

from typing import Any

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig
from services.ibkr_gateway_supervisor.terminal import SupervisorTerminalState, overall_status


def build_ibkr_status_snapshot(config: IbkrGatewayConfig, state: SupervisorTerminalState) -> dict[str, Any]:
    metrics = {
        "current_phase": state.current_operation,
        "current_phase_message": state.last_error or state.last_tickle_error,
        "status": overall_status(state),
        "auth_status": state.auth_status,
        "gateway_status": state.gateway_status,
        "keepalive_status": state.keepalive_status,
        "account_status": state.account_status,
        "last_error": state.last_error,
        "errors": len(state.error_history),
        "poll_runs": state.tickle_count,
        "poll_failures": state.tickle_failures,
        "source_statuses": [
            {"name": "IBKR Client Portal", "status": state.gateway_status, "detail": f"pid={state.gateway_pid or '-'} listener={state.listener_pid or '-'}"},
            {"name": "IBKR auth", "status": state.auth_status, "detail": f"http={state.status_code or '-'}"},
            {"name": "IBKR keepalive", "status": state.keepalive_status, "detail": f"tickles={state.tickle_count} failures={state.tickle_failures}"},
            {"name": "IBKR account", "status": state.account_status, "detail": config.account_key},
        ],
        "tasks": [
            {"name": "gateway session", "status": state.gateway_status, "message": state.current_operation},
            {"name": "authentication", "status": state.auth_status, "rows": state.auth_failures, "message": "auth status and reauthentication checks"},
            {"name": "keepalive", "status": state.keepalive_status, "rows": state.tickle_count, "message": state.last_tickle_error},
        ],
    }
    return build_dashboard_snapshot(
        service_name="ibkr_gateway_supervisor",
        config=config,
        metrics=metrics,
        recent_items={"rows": list(state.recent_events)},
        sources_sinks=metrics["source_statuses"],
        service_specific={
            "event_log_path": state.event_log_path,
            "error_history": list(state.error_history),
            "recent_events": list(state.recent_events),
        },
    )
