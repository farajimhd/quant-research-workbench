"""Inactive, strict typed run-context bootstrap for a new fixed Backtest."""
from __future__ import annotations

import re
from typing import Any, Mapping

from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _RUN_CONFIG_FIELDS, _wire_row, publish_typed_run,
    publish_typed_run_context, typed_row,
)
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch


def _validate_local_context(run: Mapping[str, Any], config: Mapping[str, Any],
                            account_ids: tuple[str, ...]) -> str:
    """Apply every writer-side pure check before an irreversible Keeper create."""
    if set(run) != {name for name, _ in _CONTRACTS["trading_run_v1"].columns}:
        raise ValueError("Fixed typed run has missing or extra columns")
    run_id = run["run_id"]
    if not isinstance(run_id, str) or not run_id or run["mode"] != "backtest":
        raise ValueError("Fixed typed run identity or mode is invalid")
    interval = run["evaluation_interval_ms"]
    if type(interval) is not int or interval < 100 or interval % 100:
        raise ValueError("Fixed typed run interval is invalid")
    for field in ("configuration_hash", "code_hash"):
        if re.fullmatch(r"[0-9a-f]{64}", str(run[field])) is None:
            raise ValueError(f"Fixed typed run {field} is not a SHA-256 digest")
    wire = _wire_row("trading_run_v1", run)
    if wire["run_month"] != wire["started_at"][:7] + "-01":
        raise ValueError("Fixed typed run partition differs from UTC start")
    if set(config) != _RUN_CONFIG_FIELDS:
        raise ValueError("Fixed runtime configuration has missing or extra fields")
    if (not isinstance(config["strategy_id"], str)
            or not config["strategy_id"].strip()
            or type(config["strategy_revision"]) is not int
            or config["strategy_revision"] < 0
            or type(config["checkpoint_interval_events"]) is not int
            or config["checkpoint_interval_events"] < 1
            or any(config[key] not in (0, 1, False, True) for key in (
                "safety_supervisor_enabled", "write_progress_checkpoints"))):
        raise ValueError("Fixed runtime configuration is invalid")
    typed_row("trading_runtime_config_v1", {
        "run_id": run_id, "run_month": wire["run_month"],
        **{key: (int(value) if key in {"safety_supervisor_enabled",
                                        "write_progress_checkpoints"} else value)
           for key, value in config.items()},
    })
    if (not isinstance(account_ids, tuple) or not 1 <= len(account_ids) <= 65535
            or any(not isinstance(account, str) or not account.strip()
                   for account in account_ids)
            or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Fixed runtime account membership is invalid")
    for ordinal, account_id in enumerate(account_ids):
        typed_row("trading_run_account_v1", {
            "run_id": run_id, "run_month": wire["run_month"],
            "ordinal": ordinal, "account_id": account_id,
        })
    return run_id


def publish_fixed_run_context(
    writer_client: Any, read_client: Any, terminal_client: Any,
    dispatch: TypedInsertDispatch, *, run: Mapping[str, Any],
    config: Mapping[str, Any], account_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Create a fresh Keeper gate, publish facts, then cold-check the receipt.

    This is intentionally not called by the active controller. A failed or
    ambiguous INSERT is not retried by creating a second gate/run.
    """
    if (len({id(writer_client), id(read_client), id(terminal_client)}) != 3
            or not isinstance(dispatch, TypedInsertDispatch)
            or getattr(writer_client, "typed_insert_dispatch", None) is not dispatch
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or run.get("mode") != "backtest"):
        raise ValueError("Fixed run context requires distinct strict typed authorities")
    run_id = _validate_local_context(run, config, account_ids)
    dispatch.initialize_new_run(run_id)
    publish_typed_run(writer_client, run)
    publish_typed_run_context(
        writer_client, run_id=run_id, config=config, account_ids=account_ids)
    return verify_fixed_run_context(
        dispatch, read_client, terminal_client, run_id=run_id)


def verify_fixed_run_context(
    dispatch: TypedInsertDispatch, read_client: Any, terminal_client: Any,
    *, run_id: str,
) -> dict[str, Any]:
    """Cold-verify both clients against one persistent Keeper context receipt."""
    if (not isinstance(dispatch, TypedInsertDispatch)
            or read_client is terminal_client or not run_id):
        raise ValueError("Fixed run context lacks independent cold authorities")
    barrier = dispatch.acquire_cold_barrier(run_id)
    try:
        context = barrier.verify_run_context_receipt(read_client)
        if barrier.verify_run_context_receipt(terminal_client) != context:
            raise RuntimeError("Terminal client observes a different fixed run context")
        if context["mode"] != "backtest":
            raise RuntimeError("Fixed run context is not Backtest")
        return context
    finally:
        barrier.release()
