from __future__ import annotations

from typing import Any


TERMINAL_STATES = {"cancelled", "completed", "failed", "stopped"}


MODE_ADAPTERS = {
    "live": {
        "clock": "wall_exchange_clock",
        "observation_source": "qmd_live",
        "latency": "measured_source_and_processing_latency",
        "execution": "ibkr_cpapi",
        "fills": "canonical_broker_events",
    },
    "paper": {
        "clock": "wall_exchange_clock",
        "observation_source": "qmd_live",
        "latency": "measured_source_and_processing_latency",
        "execution": "ibkr_cpapi_paper",
        "fills": "canonical_broker_events",
    },
    "replay": {
        "clock": "replay_clock",
        "observation_source": "qmd_history",
        "latency": "configured_replay_latency",
        "execution": "simulated_broker",
        "fills": "simulated_execution_events",
    },
    "backtest": {
        "clock": "simulation_clock",
        "observation_source": "qmd_history",
        "latency": "configured_simulation_latency",
        "execution": "simulated_broker",
        "fills": "simulated_execution_events",
    },
    "backtest_debug": {
        "clock": "fixture_clock",
        "observation_source": "content_hashed_fixture",
        "latency": "configured_simulation_latency",
        "execution": "simulated_broker",
        "fills": "simulated_execution_events",
    },
    "offline": {
        "clock": "job_clock",
        "observation_source": "pinned_persisted_source",
        "latency": "not_applicable",
        "execution": "none",
        "fills": "none",
    },
}


def lifecycle_projection(
    *,
    resource_type: str,
    resource_id: str,
    status: str,
    authority: str,
    progress: float | None = None,
    completed_units: int | None = None,
    total_units: int | None = None,
    unit: str = "items",
    checkpoint: dict[str, Any] | None = None,
    error: str = "",
    created_at: Any = None,
    updated_at: Any = None,
    started_at: Any = None,
    finished_at: Any = None,
    supported_commands: tuple[str, ...] = (),
    mode: str = "offline",
    adapter_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    state = canonical_lifecycle_state(status)
    fraction = None if progress is None else min(1.0, max(0.0, float(progress)))
    checkpoint_payload = dict(checkpoint or {})
    commands = [
        {
            "command": command,
            "enabled": lifecycle_command_enabled(command, state, checkpoint_payload),
        }
        for command in supported_commands
    ]
    return {
        "schema_version": 2,
        "resource_type": str(resource_type),
        "resource_id": str(resource_id),
        "state": state,
        "source_status": str(status or "unknown").strip().lower() or "unknown",
        "terminal": state in TERMINAL_STATES,
        "progress": {
            "fraction": fraction,
            "completed_units": completed_units,
            "total_units": total_units,
            "unit": unit,
        },
        "checkpoint": checkpoint_payload,
        "commands": commands,
        "failure": (
            {
                "code": f"{resource_type.upper()}_FAILED",
                "message": str(error),
                "retryable": bool(
                    checkpoint_payload.get("resume_supported")
                    or checkpoint_payload.get("retry_stateful_supported")
                ),
            }
            if error
            else None
        ),
        "timestamps": {
            "created_at": created_at,
            "started_at": started_at,
            "updated_at": updated_at,
            "finished_at": finished_at,
        },
        "authority": str(authority),
        "mode": str(mode),
        "adapters": mode_adapter_contract(mode, adapter_overrides),
    }


def mode_adapter_contract(
    mode: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    normalized = str(mode or "offline").strip().lower()
    if normalized not in MODE_ADAPTERS:
        raise ValueError(f"Unsupported lifecycle mode: {mode}")
    adapters = {**MODE_ADAPTERS[normalized], **dict(overrides or {})}
    required = {"clock", "observation_source", "latency", "execution", "fills"}
    missing = sorted(key for key in required if not str(adapters.get(key) or "").strip())
    if missing:
        raise ValueError(
            "Lifecycle adapter contract omitted: " + ", ".join(missing)
        )
    return adapters


def canonical_lifecycle_state(status: str) -> str:
    normalized = str(status or "unknown").strip().lower()
    return {
        "canceling": "cancelling",
        "canceled": "cancelled",
        "complete": "completed",
        "error": "failed",
        "fast_forwarding": "running",
        "pausing": "pausing",
        "play": "running",
        "ready": "ready",
        "warming": "preparing",
    }.get(normalized, normalized or "unknown")


def lifecycle_command_enabled(
    command: str, state: str, checkpoint: dict[str, Any]
) -> bool:
    if command == "pause":
        return state in {"queued", "ready", "running"}
    if command in {"cancel", "stop"}:
        return state not in TERMINAL_STATES
    if command in {"play", "resume"}:
        return state == "paused" or (
            state in {"cancelled", "failed", "stopped"}
            and bool(checkpoint.get("resume_supported"))
        )
    if command == "retry_stateful":
        return state in {"cancelled", "failed"}
    return False
