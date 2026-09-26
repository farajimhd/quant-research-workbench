"""Pure normalized run identity shared by Strategy 1 runtime and V4 journal.

No Keeper gate, ClickHouse row, market product, or disk artifact is created
here. The exact returned scalars are validated before irreversible launch.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import re
from typing import Any, Mapping

from src.backend.backtest_fixed_run_context import _validate_local_context
from src.backend.backtest_market_data import ExecutionInterval
from src.trading_runtime.runtime import RunConfig, RunMode
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


_MAX_UINT32 = 2**32 - 1


def historical_simulated_account_ids(
    *, mode: RunMode, configuration: Mapping[str, Any],
) -> tuple[str, ...]:
    """Resolve the same ordered simulated accounts before runtime construction."""
    if not isinstance(mode, RunMode) or not isinstance(configuration, Mapping):
        raise ValueError("Historical account population lacks a pinned configuration")
    bindings = [dict(row) for row in configuration["accounts"]["bindings"]
                if bool(row.get("enabled", True))
                and mode.value in list(row.get("modes") or [])]
    keys = tuple(str(row["account_key"]) for row in bindings)
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("Historical simulated account keys are empty or repeated")
    return tuple(f"SIM-{index + 1:02d}-{_slug_account(key)}"
                 for index, key in enumerate(keys))


def _slug_account(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    return normalized[:24] or sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def historical_runtime_config(*, mode: RunMode,
                              configuration: Mapping[str, Any],
                              account_ids: tuple[str, ...],
                              anchor_date: date, run_id: str) -> RunConfig:
    """Use one identity for OMS and the normalized journal context."""
    if (not isinstance(mode, RunMode) or not isinstance(configuration, Mapping)
            or not isinstance(anchor_date, date) or not account_ids or not run_id):
        raise ValueError("Historical runtime configuration lacks a pinned run")
    strategy = dict(configuration.get("strategy") or {})
    run_plan = dict(configuration.get("run_plan") or {})
    supervisor = dict(dict(run_plan.get("safety_supervisor") or {}).get(
        "enabled_by_environment") or {})
    return RunConfig(
        mode=mode,
        strategy_id=str(strategy.get("strategy_id") or ""),
        strategy_revision=int(strategy.get("revision") or 0),
        account_ids=account_ids, anchor_date=anchor_date, run_id=run_id,
        run_plan_id=str(run_plan.get("run_plan_id")
                        or dict(configuration.get("deployment") or {}).get(
                            "deployment_id")
                        or dict(configuration.get("session_profile") or {}).get(
                            "session_profile_id") or ""),
        safety_supervisor_enabled=bool(supervisor.get(mode.value, True)),
        # The controller owns complete typed boundary checkpoints. The inner
        # runtime must not publish its processed-count-only checkpoint; its
        # inert interval still must fit the journal's UInt32 field.
        checkpoint_interval_events=(
            _MAX_UINT32 if mode == RunMode.BACKTEST else 2**63 - 1),
        write_progress_checkpoints=False,
    )


def fixed_v4_context_rows(config: RunConfig, *, execution_interval: Any,
                          configuration_hash: str,
                          code_hash: str, market_plan_token: str,
                          started_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare typed Strategy 1 run/config rows before creating a Keeper gate."""
    if (not isinstance(config, RunConfig)
            or config.mode != RunMode.BACKTEST
            or (config.strategy_id, config.strategy_revision)
            != (STRATEGY_ID, STRATEGY_NUMBER)
            or started_at.tzinfo is None
            or any(re.fullmatch(r"[0-9a-f]{64}", value or "") is None
                   for value in (configuration_hash, code_hash,
                                 market_plan_token))):
        raise ValueError("V4 run context needs pinned Strategy 1 and source hashes")
    interval = ExecutionInterval.parse(execution_interval)
    if interval.kind != "fixed" or interval.milliseconds is None:
        raise ValueError("V4 run context requires a fixed evaluation interval")
    started = started_at.astimezone(timezone.utc)
    run = {
        "run_id": config.run_id,
        "run_month": started.date().replace(day=1).isoformat(),
        "mode": config.mode.value,
        "evaluation_interval_ms": interval.milliseconds,
        "session_date": config.anchor_date.isoformat(),
        "configuration_hash": configuration_hash,
        "code_hash": code_hash,
        "market_plan_token": market_plan_token,
        "started_at": started.isoformat(),
    }
    runtime = {
        "strategy_id": config.strategy_id,
        "strategy_revision": config.strategy_revision,
        "anchor_date": config.anchor_date.isoformat(),
        "run_plan_id": config.run_plan_id,
        "safety_supervisor_enabled": config.safety_supervisor_enabled,
        "checkpoint_interval_events": config.checkpoint_interval_events,
        "write_progress_checkpoints": config.write_progress_checkpoints,
    }
    _validate_local_context(run, runtime, config.account_ids)
    return run, runtime
