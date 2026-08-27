from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition
from src.backend.trading_configuration_service import backtest_configuration_snapshot
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
from src.trading_runtime.runtime import RunMode

from .config import SearchConfig, TrialSpec, generate_trials
from .mutations import apply_trial, assert_hard_liquidity_contract


MAX_FAILED_TRIAL_ATTEMPTS = 8
OPTIMIZER_RESULT_SCHEMA_VERSION = 4
from .objectives import (
    TrialMetrics,
    count_causal_violations,
    decision_counts,
    liquidity_membership_violations,
    realized_drawdown,
)


DEFAULT_RUNTIME_ROOT = Path(
    r"D:\TradingML\runtimes\strategy_optimization\long_momentum_squeeze_v1"
)


async def run_search(config: SearchConfig, *, run_root: Path) -> dict[str, Any]:
    if config.initial_cash != 10_000.0:
        raise ValueError("Small-cap squeeze optimization is pinned to $10,000 test cash")
    _validate_runtime_root(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    base = backtest_configuration_snapshot(
        config.run_plan_id,
        candidate_id=config.candidate_id,
    )
    manifest_path = run_root / "manifest.json"
    manifest = _load_json(manifest_path) or {
        "schema_version": 1,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "search": _search_payload(config),
        "base_configuration": {
            "revision_id": base["revision_id"],
            "content_hash": base["content_hash"],
            "release_state": base.get("release_state"),
        },
        "trials": {},
    }
    _write_json(manifest_path, manifest)
    historical_frame_cache: dict[tuple[str, str, str, str], Any] = {}
    historical_watchlist_cache: dict[str, Any] = {}

    tuning: list[dict[str, Any]] = []
    for index, trial in enumerate(generate_trials(config), start=1):
        result = await _run_or_resume_trial(
            base,
            trial,
            fold="tuning",
            start_time=config.tuning_start,
            strategy_end_time=config.tuning_end,
            data_end_time=config.tuning_end,
            session_date=config.session_date,
            simulation_profile="baseline",
            initial_cash=config.initial_cash,
            run_root=run_root,
            historical_frame_cache=historical_frame_cache,
            historical_watchlist_cache=historical_watchlist_cache,
        )
        tuning.append(result)
        manifest["trials"][f"tuning:{trial.trial_id}:baseline"] = {
            "status": result["status"],
            "result": result["result_path"],
            "ordinal": index,
        }
        _write_json(manifest_path, manifest)

    ranked = sorted(
        (
            row
            for row in tuning
            if row["status"] == "completed" and row["metrics"]["admissible"]
        ),
        key=lambda row: tuple(row["metrics"]["objective"]),
        reverse=True,
    )
    finalists = ranked[: config.validation_candidates]
    validation: list[dict[str, Any]] = []
    for finalist in finalists:
        trial = TrialSpec(**{
            key: value
            for key, value in finalist["trial"].items()
            if key != "trial_id"
        })
        for profile in ("baseline", "stress"):
            result = await _run_or_resume_trial(
                base,
                trial,
                fold="validation",
                start_time=config.validation_start,
                strategy_end_time=config.validation_end,
                data_end_time=config.validation_end,
                session_date=config.session_date,
                simulation_profile=profile,
                initial_cash=config.initial_cash,
                run_root=run_root,
                historical_frame_cache=historical_frame_cache,
                historical_watchlist_cache=historical_watchlist_cache,
            )
            validation.append(result)
            manifest["trials"][f"validation:{trial.trial_id}:{profile}"] = {
                "status": result["status"],
                "result": result["result_path"],
            }
            _write_json(manifest_path, manifest)

    eligible = _eligible_validation_pairs(validation)
    winner_rows = max(
        eligible,
        key=lambda rows: (
            rows["baseline"]["metrics"]["net_pnl"],
            rows["stress"]["metrics"]["net_pnl"],
            -rows["baseline"]["metrics"]["maximum_realized_drawdown"],
        ),
        default=None,
    )
    summary = {
        "schema_version": 1,
        "status": "completed" if winner_rows else "no_admissible_winner",
        "completed_at": datetime.now(UTC).isoformat(),
        "search": _search_payload(config),
        "tuning_trials": len(tuning),
        "validation_runs": len(validation),
        "winner": winner_rows["baseline"] if winner_rows else None,
        "winner_stress": winner_rows["stress"] if winner_rows else None,
        "approval_state": "unapproved_test_candidate",
    }
    _write_json(run_root / "summary.json", summary)
    manifest["status"] = summary["status"]
    manifest["completed_at"] = summary["completed_at"]
    _write_json(manifest_path, manifest)
    return summary


def _eligible_validation_pairs(
    validation: list[dict[str, Any]],
) -> list[dict[str, dict[str, Any]]]:
    by_trial: dict[str, dict[str, dict[str, Any]]] = {}
    for row in validation:
        by_trial.setdefault(row["trial"]["trial_id"], {})[
            row["simulation_profile"]
        ] = row
    return [
        rows
        for rows in by_trial.values()
        if {"baseline", "stress"} <= set(rows)
        and rows["baseline"]["status"] == "completed"
        and rows["stress"]["status"] == "completed"
        and rows["baseline"]["metrics"]["admissible"]
        and rows["stress"]["metrics"]["admissible"]
    ]


async def _run_or_resume_trial(
    base: dict[str, Any],
    trial: TrialSpec,
    *,
    fold: str,
    start_time,
    strategy_end_time,
    data_end_time,
    session_date,
    simulation_profile: str,
    initial_cash: float,
    run_root: Path,
    historical_frame_cache: dict[tuple[str, str, str, str], Any],
    historical_watchlist_cache: dict[str, Any],
) -> dict[str, Any]:
    result_path = run_root / "results" / f"{fold}-{simulation_profile}-{trial.trial_id}.json"
    previous = _load_json(result_path)
    if (
        previous
        and previous.get("status") == "completed"
        and int(previous.get("schema_version") or 0)
        == OPTIMIZER_RESULT_SCHEMA_VERSION
    ):
        return previous
    engine_root = run_root / "engine_runs"
    attempted = max(
        int((previous or {}).get("attempt") or 0),
        _latest_engine_attempt(
            engine_root,
            fold=fold,
            simulation_profile=simulation_profile,
            trial_id=trial.trial_id,
        ),
    )
    if previous and previous.get("status") == "failed":
        if attempted >= MAX_FAILED_TRIAL_ATTEMPTS:
            return previous
    configuration = apply_trial(base, trial, fold_end=strategy_end_time)
    assert_hard_liquidity_contract(configuration)
    attempt = attempted + 1
    run_id = f"{fold}-{simulation_profile}-{trial.trial_id}-a{attempt}"
    controller = ReplayRunController(
        ReplayRunDefinition(
            session_date=session_date,
            start_time=start_time,
            end_time=data_end_time,
            initial_cash=initial_cash,
            configuration_revision=configuration,
            mode=RunMode.BACKTEST,
            simulation_profile=simulation_profile,
            historical_frame_cache=historical_frame_cache,
            historical_watchlist_cache=historical_watchlist_cache,
        ),
        run_id=run_id,
        runtime_root=engine_root,
    )
    started = datetime.now(UTC)
    await controller.start()
    assert controller._task is not None
    await controller._task
    try:
        result = await _collect_result(
            controller,
            trial=trial,
            fold=fold,
            simulation_profile=simulation_profile,
            initial_cash=initial_cash,
            started=started,
            result_path=result_path,
            attempt=attempt,
        )
    finally:
        if controller._journal is not None:
            controller._journal.close()
    _write_json(result_path, result)
    return result


def _latest_engine_attempt(
    engine_root: Path,
    *,
    fold: str,
    simulation_profile: str,
    trial_id: str,
) -> int:
    prefix = f"{fold}-{simulation_profile}-{trial_id}-a"
    attempts: list[int] = []
    if engine_root.is_dir():
        for path in engine_root.glob(f"{prefix}*"):
            suffix = path.name.removeprefix(prefix)
            if path.is_dir() and suffix.isdigit():
                attempts.append(int(suffix))
    return max(attempts, default=0)


async def _collect_result(
    controller: ReplayRunController,
    *,
    trial: TrialSpec,
    fold: str,
    simulation_profile: str,
    initial_cash: float,
    started: datetime,
    result_path: Path,
    attempt: int,
) -> dict[str, Any]:
    base = {
        "schema_version": OPTIMIZER_RESULT_SCHEMA_VERSION,
        "status": controller.status,
        "error": controller.error,
        "attempt": attempt,
        "run_id": controller.run_id,
        "fold": fold,
        "simulation_profile": simulation_profile,
        "trial": trial.payload(),
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "result_path": str(result_path),
        "engine_run_dir": str(controller.run_dir),
    }
    if controller.status != "completed" or controller._runtime is None or controller._journal is None:
        return base
    summaries = [
        await controller._runtime.broker.account_summary(account_id)
        for account_id in controller.account_ids
    ]
    broker = controller._runtime.broker.checkpoint_state()
    executions = [dict(row) for row in broker.get("executions") or []]
    final_positions = [
        dict(row)
        for rows in dict(broker.get("positions") or {}).values()
        for row in rows
        if abs(float(dict(row).get("quantity") or 0)) > 1e-9
    ]
    open_statuses = {status.value for status in OPEN_ORDER_STATUSES}
    final_open_orders = [
        dict(row)
        for row in broker.get("orders") or []
        if str(dict(row).get("status") or "") in open_statuses
    ]
    decisions = controller._journal.recent_records(
        controller.run_id, categories=("strategy_decision",), limit=50_000
    )
    intents = controller._journal.recent_records(
        controller.run_id, categories=("strategy",), limit=50_000
    )
    intent_records = [
        row
        for row in intents
        if str(dict(row.payload).get("action") or "")
        in {"enter_long", "enter_short", "add_long", "add_short", "exit", "take_profit"}
    ]
    source_records = controller._journal.recent_records(
        controller.run_id,
        categories=("market_discovery_signal", "watchlist_membership"),
        limit=50_000,
    )
    counts = decision_counts(decisions)
    timeline = controller._historical_watchlist_timeline()
    authority = controller._data_authority.get(
        "source_native_signal_stream:price-squeeze-5m", {}
    )
    metrics = TrialMetrics(
        net_pnl=sum(float(row.netliquidation) - initial_cash for row in summaries),
        realized_pnl=sum(float(value) for value in broker.get("realized_pnl", {}).values()),
        commissions=sum(float(row.get("commission") or 0) for row in executions),
        maximum_realized_drawdown=realized_drawdown(executions),
        execution_count=len(executions),
        entry_intent_count=sum(
            1 for row in intent_records
            if str(dict(row.payload).get("action") or "") == "enter_long"
        ),
        exit_intent_count=sum(
            1 for row in intent_records
            if str(dict(row.payload).get("action") or "") in {"exit", "take_profit"}
        ),
        reentry_count=counts.get("reason:reentry_confirmed", 0),
        profit_take_count=counts.get("reason:profit_pocket", 0),
        liquidity_violations=liquidity_membership_violations(intent_records, timeline),
        causal_violations=count_causal_violations(
            [*source_records, *decisions, *intent_records]
        ),
        squeeze_occurrence_count=int(authority.get("row_count") or 0),
        watchlist_transition_count=len(timeline),
        final_position_count=len(final_positions),
        final_absolute_position_quantity=sum(
            abs(float(row.get("quantity") or 0)) for row in final_positions
        ),
        final_open_order_count=len(final_open_orders),
    )
    return {
        **base,
        "metrics": metrics.payload(),
        "decision_counts": counts,
        "data_authority": controller.snapshot()["data_authority"],
    }


def _search_payload(config: SearchConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key, value in list(payload.items()):
        if hasattr(value, "isoformat"):
            payload[key] = value.isoformat()
    return payload


def _validate_runtime_root(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    resolved = path.resolve()
    if resolved == repo or repo in resolved.parents:
        raise ValueError("Optimization artifacts must be outside the source repository")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    for attempt in range(100):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--tuning-trials", type=int, default=24)
    parser.add_argument("--validation-candidates", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = replace(
        SearchConfig(),
        tuning_trials=args.tuning_trials,
        validation_candidates=args.validation_candidates,
    )
    summary = asyncio.run(run_search(config, run_root=args.runtime_root / args.run_id))
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
