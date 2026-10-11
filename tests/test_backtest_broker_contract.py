from decimal import Decimal
import json

import pytest

from src.trading_runtime.backtest_broker_contract import project_broker_contract, restore_broker_contract
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
