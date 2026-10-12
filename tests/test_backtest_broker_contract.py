from decimal import Decimal
import json
from dataclasses import replace

import pytest

from src.trading_runtime.backtest_broker_contract import project_broker_contract, restore_broker_contract
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, read_broker_contract
from src.trading_runtime.simulated_broker import SimulationConfig


@pytest.mark.parametrize("resolution", [100, 1000])
def test_selected_actual_broker_settings_roundtrip_without_profile_defaults(resolution):
    config = SimulationConfig(initial_cash=12000, liquidity_resolution_ms=resolution,
        commission_per_share=.005, minimum_commission=1, market_slippage_bps=5,
        new_order_activation_delay_ms=.125, liquidity_participation=.25,
        marketable_liquidity_participation=1)
    row = project_broker_contract("run", config, liquidity_plan_hash="a" * 64)
    assert row["commission_per_share"] == Decimal(".005")
    assert row["activation_delay_us"] == 125
    wire = json.loads(json.dumps(row, default=str))
    actual = restore_broker_contract(wire, run_id="run", content_hash=row["content_hash"],
                                    liquidity_plan_hash="a" * 64, initial_cash=12000)
    assert actual == config
    wire["minimum_commission"] = "0"
    with pytest.raises(ValueError, match="pinned run"):
        restore_broker_contract(wire, run_id="run", content_hash=row["content_hash"],
                                liquidity_plan_hash="a" * 64, initial_cash=12000)


def test_nonpersistable_or_nonfinite_settings_fail_before_publication():
    for config in (SimulationConfig(commission_per_share=float("nan")),
                   SimulationConfig(market_slippage_bps=float("inf")),
                   SimulationConfig(new_order_activation_delay_ms=.0001),
                   SimulationConfig(allow_short=True)):
        with pytest.raises(ValueError):
            project_broker_contract("run", config, liquidity_plan_hash="a" * 64)


@pytest.mark.parametrize("resolution", [100, 1000])
def test_immutable_contract_binds_actual_selected_broker_and_cash(resolution):
    config = SimulationConfig(initial_cash=45000, liquidity_resolution_ms=resolution,
        commission_per_share=.005, minimum_commission=1, market_slippage_bps=5,
        marketable_liquidity_participation=1)
    row = project_broker_contract("run", config, liquidity_plan_hash="a" * 64)
    contract = BacktestBrokerContract(row, config.initial_cash)
    assert contract.require_config(config, run_id="run", content_hash=row["content_hash"],
        liquidity_plan_hash="a" * 64) == config
    assert BacktestBrokerContract(contract.wire_row(), config.initial_cash).row == contract.row
    with pytest.raises(TypeError):
        contract.row["minimum_commission"] = 0
    row["minimum_commission"] = 0
    assert contract.row["minimum_commission"] == 1
    for changes in ({"initial_cash": 46000}, {"minimum_commission": 0},
                    {"market_slippage_bps": 10}, {"liquidity_resolution_ms": 1000 if resolution == 100 else 100}):
        with pytest.raises(ValueError, match="selected native"):
            contract.require_config(replace(config, **changes), run_id="run",
                content_hash=contract.row["content_hash"], liquidity_plan_hash="a" * 64)


@pytest.mark.parametrize("case", ["valid", "missing", "duplicate", "changed", "oversize", "writable", "storage", "array", "duplicate_field"])
def test_broker_readback_is_bounded_and_checks_actual_storage(monkeypatch, case):
    from src.trading_runtime import arte_journal_schema as schema
    from src.trading_runtime.columnar_strategy_schema import BROKER
    config = SimulationConfig(initial_cash=45000, market_slippage_bps=5,
        marketable_liquidity_participation=1)
    row = project_broker_contract("run", config, liquidity_plan_hash="a" * 64)
    response = json.dumps(row, default=str)
    if case == "missing":
        response = ""
    elif case == "duplicate":
        response += "\n" + response
    elif case == "changed":
        response = json.dumps({**row, "minimum_commission": "0"}, default=str)
    elif case == "oversize":
        response = " " * 16385
    elif case == "array":
        response = json.dumps(list(row.items()), default=str)
    elif case == "duplicate_field":
        response = response[:-1] + ',"run_id":"run"}'
    calls = []
    def storage(client, *, tables):
        assert tables == (BROKER,)
        calls.append("storage")
        if case == "storage":
            raise ValueError("misplaced parts")
    monkeypatch.setattr(schema, "storage_preflight", storage)
    class Client:
        def execute(self, sql):
            assert sql.startswith("SELECT ")
            calls.append(sql)
            if "getSetting" in sql:
                return "0" if case == "writable" else "1"
            assert "LIMIT 2 FORMAT JSONEachRow" in sql
            return response
    scope = dict(run_id="run", content_hash=row["content_hash"],
        liquidity_plan_hash="a" * 64, initial_cash=config.initial_cash)
    if case == "valid":
        actual = read_broker_contract(Client(), **scope)
        assert actual.row == row
        assert actual.config == config
    else:
        with pytest.raises((ValueError, RuntimeError)):
            read_broker_contract(Client(), **scope)
    if case in ("storage", "writable"):
        assert not any("FROM arte." in sql for sql in calls)
