"""One normalized run identity is shared before V4 Keeper publication."""
from datetime import date, datetime, timezone

import pytest

from src.backend.backtest_v4_run_context import (
    fixed_v4_context_rows, historical_runtime_config,
    historical_simulated_account_ids,
)
from src.trading_runtime.runtime import RunMode


def _config(*, mode=RunMode.BACKTEST):
    return historical_runtime_config(
        mode=mode, account_ids=("DU1",),
        anchor_date=date(2026, 8, 18), run_id="run-1",
        configuration={
            "strategy": {"strategy_id": "early-squeeze-strategy",
                         "revision": 1},
            "run_plan": {
                "run_plan_id": "strategy-one-plan",
                "safety_supervisor": {
                    "enabled_by_environment": {"backtest": True}}},
        })


def test_v4_context_uses_same_runtime_scalars_and_uint32_checkpoint_field():
    config = _config()
    run, runtime = fixed_v4_context_rows(
        config, execution_interval="100ms", configuration_hash="a" * 64,
        code_hash="b" * 64, market_plan_token="c" * 64,
        started_at=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    assert run["run_month"] == "2026-08-01"
    assert run["session_date"] == "2026-08-18"
    assert run["evaluation_interval_ms"] == 100
    assert runtime["strategy_id"] == config.strategy_id
    assert runtime["strategy_revision"] == config.strategy_revision
    assert runtime["run_plan_id"] == config.run_plan_id
    assert runtime["checkpoint_interval_events"] == 2**32 - 1
    assert runtime["write_progress_checkpoints"] is False


def test_v4_context_pins_selected_fixed_interval_and_rejects_events():
    config = _config()
    fields = dict(configuration_hash="a" * 64, code_hash="b" * 64,
                  market_plan_token="c" * 64,
                  started_at=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    run, _ = fixed_v4_context_rows(config, execution_interval="200ms", **fields)
    assert run["evaluation_interval_ms"] == 200
    with pytest.raises(ValueError, match="fixed evaluation interval"):
        fixed_v4_context_rows(config, execution_interval="events", **fields)


def test_v4_context_rejects_wrong_strategy_or_unpinned_hash():
    with pytest.raises(ValueError, match="Strategy 1"):
        fixed_v4_context_rows(
            _config(mode=RunMode.REPLAY), execution_interval="100ms",
            configuration_hash="a" * 64,
            code_hash="b" * 64, market_plan_token="c" * 64,
            started_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="source hashes"):
        fixed_v4_context_rows(
            _config(), execution_interval="100ms",
            configuration_hash="invalid",
            code_hash="b" * 64, market_plan_token="c" * 64,
            started_at=datetime.now(timezone.utc))


def test_v4_resolves_published_accounts_before_runtime_initialization():
    configuration = {"accounts": {"bindings": [
        {"account_key": "Main Alpha", "modes": ["backtest"]},
        {"account_key": "Disabled", "modes": ["backtest"], "enabled": False},
        {"account_key": "Second/Beta", "modes": ["backtest"]},
        {"account_key": "Replay", "modes": ["replay"]},
    ]}}
    assert historical_simulated_account_ids(
        mode=RunMode.BACKTEST, configuration=configuration) == (
            "SIM-01-MAIN-ALPHA", "SIM-02-SECOND-BETA")
    with pytest.raises(ValueError, match="repeated"):
        historical_simulated_account_ids(
            mode=RunMode.BACKTEST, configuration={"accounts": {"bindings": [
                {"account_key": "A", "modes": ["backtest"]},
                {"account_key": "A", "modes": ["backtest"]},
            ]}})
