from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.backend.optimized_execution_contract import verify_optimized_execution_contract
from src.trading_runtime.configuration_execution_binding import project_execution_binding
from src.trading_runtime.execution_policies import ExecutionPolicy
from src.trading_runtime.simulated_broker import SimulationConfig


def inputs():
    binding = project_execution_binding(
        configuration_id="00000000-0000-0000-0000-000000000001",
        configuration_hash="a" * 64, policies=(ExecutionPolicy(policy_id="chosen"),),
        entry_policy_id="chosen", exit_policy_id="chosen", tick_size=.01,
        limit_offset_bps=5., published_at=datetime.now(UTC))
    revision = dict(revision_id="strategy-configuration:" + binding.header["configuration_id"],
                    selection_hash="a" * 64, payload={"oms": {"limit_offset_bps": 5.}})
    execution = dict(entry_dollars=1000., add_dollars=200., cost_bps=0.,
                     fee_per_share=.005, minimum_order_fee=1., risk_protection=True,
                     maximum_stop_distance=.2, maximum_position_risk=300.)
    return execution, dict(configuration_revision=revision, execution_binding=binding)


def test_real_native_binding_and_broker_preserve_unresolved_risk_blocker():
    execution, selected = inputs()
    report = verify_optimized_execution_contract(execution, mode="backtest", **selected,
        broker_config=SimulationConfig(), liquidity_plan_hash="b" * 64)
    assert not report.compatible
    assert len(report.blockers) == 1 and "stop-risk" in report.blockers[0]
    assert len(report.broker_contract_hash) == 64
    with pytest.raises(ValueError, match="stop-risk"):
        report.require_compatible()


def test_explicit_matching_risk_contract_qualifies_static_backtest_settings():
    from tests.test_configuration_execution_risk import contract
    from src.trading_runtime.configuration_execution_risk import bind_execution_risk
    execution, selected = inputs()
    risk = contract()
    selected['configuration_revision'] = bind_execution_risk(selected['configuration_revision'], risk)
    report = verify_optimized_execution_contract(execution, mode='backtest', **selected,
        broker_config=SimulationConfig(), liquidity_plan_hash='b' * 64, execution_risk=risk)
    assert report.compatible
    # Compatibility is static settings admission, not source/operational qualification.
    report.require_compatible()


def test_exact_selected_configuration_is_required():
    execution, selected = inputs()
    selected["configuration_revision"]["selection_hash"] = "c" * 64
    with pytest.raises(ValueError, match="selected configuration"):
        verify_optimized_execution_contract(execution, mode="live", **selected)


def test_cost_and_cadence_drift_are_all_reported():
    execution, selected = inputs()
    report = verify_optimized_execution_contract(execution, mode="replay", **selected,
        broker_config=replace(SimulationConfig(), commission_per_share=.01,
            minimum_commission=2., market_slippage_bps=1., liquidity_resolution_ms=1000),
        liquidity_plan_hash="b" * 64)
    assert len(report.blockers) == 5


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_external_modes_do_not_claim_simulated_cost_qualification(mode):
    execution, selected = inputs()
    report = verify_optimized_execution_contract(execution, mode=mode, **selected)
    assert not report.compatible and len(report.blockers) == 2
    with pytest.raises(ValueError, match="simulated broker"):
        verify_optimized_execution_contract(execution, mode=mode, **selected,
                                            broker_config=SimulationConfig())


@pytest.mark.parametrize("change", [{"risk_protection": 1}, {"maximum_stop_distance": float("nan")},
    {"maximum_position_risk": 301}, {"entry_dollars": True}, {"extra": 1}])
def test_frozen_schema_is_fail_closed(change):
    execution, selected = inputs()
    with pytest.raises(ValueError):
        verify_optimized_execution_contract({**execution, **change}, mode="live", **selected)
