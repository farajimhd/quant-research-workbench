"""One normalized run identity is shared before V4 Keeper publication."""
from datetime import date, datetime, timezone
from dataclasses import replace

import pytest

from src.backend.backtest_v4_run_context import (
    fixed_v4_context_rows, historical_runtime_config,
    historical_simulated_account_ids,
    historical_strategy_one_portfolio_profiles,
    program_v4_context_rows,
)
from src.trading_runtime.runtime import RunMode, typed_run_config_payload


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
    assert runtime == typed_run_config_payload(config)
    assert set(runtime) == {"strategy_id", "strategy_revision", "anchor_date",
                            "run_plan_id", "safety_supervisor_enabled",
                            "checkpoint_interval_events", "write_progress_checkpoints"}


@pytest.mark.parametrize("interval", ["100ms", "1s"])
def test_program_context_binds_release_without_changing_its_evaluation_clock(interval):
    from tests.test_columnar_program_coordinator import inputs
    release, _, _, _ = inputs(entry=True)
    config = replace(_config(), strategy_id=release.header["strategy_id"],
        strategy_revision=release.header["strategy_number"])
    scope = dict(release=release, expected_release_hash=release.header["content_hash"],
        execution_interval=interval, configuration_hash="a" * 64,
        code_hash=release.header["code_hash"], market_plan_token="c" * 64,
        started_at=datetime(2026, 8, 18, 8, tzinfo=timezone.utc))
    run, runtime = program_v4_context_rows(config, **scope)
    assert run["evaluation_interval_ms"] == (100 if interval == "100ms" else 1000)
    assert release.header["evaluation_interval_ms"] == 1000
    assert runtime == typed_run_config_payload(config)
    for field in ("expected_release_hash", "code_hash"):
        changed = "0" * 64 if scope[field] != "0" * 64 else "1" * 64
        with pytest.raises(ValueError, match="pinned executable release"):
            program_v4_context_rows(config, **{**scope, field: changed})
    with pytest.raises(ValueError, match="pinned executable release"):
        program_v4_context_rows(replace(config, strategy_revision=config.strategy_revision + 1), **scope)
    with pytest.raises(ValueError, match="event interval"):
        program_v4_context_rows(config, **{**scope, "execution_interval": "200ms"})


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


def test_v4_portfolio_profiles_share_launch_and_recovery_identity():
    configuration = {
        "strategy": {"strategy_id": "early-squeeze-strategy"},
        "accounts": {"bindings": [
            {"account_key": "Main Alpha", "modes": ["backtest"],
             "portfolio_policy_id": "cash", "account_class": "cash",
             "strategy_allocation": 0.75},
            {"account_key": "Disabled", "modes": ["backtest"],
             "portfolio_policy_id": "cash", "enabled": False},
        ]},
        "portfolio": {"policies": [{"policy_id": "cash"}],
                      "mandates": [{"account_key": "Main Alpha",
                                    "maximum_position_fraction": 0.1}],
                      "groups": [{"group_id": "default",
                                  "account_keys": ["Main Alpha"],
                                  "maximum_gross_exposure": 100_000,
                                  "maximum_ticker_exposure": 10_000}]},
    }
    profiles, groups = historical_strategy_one_portfolio_profiles(configuration)
    assert tuple(profile.account_id for profile in profiles) == (
        "SIM-01-MAIN-ALPHA",)
    assert profiles[0].strategy_allocations == {"early-squeeze-strategy": 0.75}
    assert profiles[0].strategy_mandates["early-squeeze-strategy"][
        "maximum_position_fraction"] == 0.1
    assert groups[0].account_keys == ("Main Alpha",)
