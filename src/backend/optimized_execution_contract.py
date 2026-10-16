"""Read-only fee/risk admission with separately reported native fill assumptions.

Optimization uses assumed fills. Native OMS, liquidity and portfolio admission
remain authoritative. This module chooses no configuration and publishes nothing.
"""
from dataclasses import dataclass
from math import isfinite
from collections.abc import Mapping

from src.trading_runtime.configuration_execution_binding import (
    bind_execution_configuration, restore_execution_binding,
)
from src.trading_runtime.backtest_broker_contract import project_broker_contract
from src.trading_runtime.simulated_broker import SimulationConfig


@dataclass(frozen=True)
class OptimizedExecutionCompatibility:
    mode: str
    configuration_hash: str
    execution_binding_hash: str
    broker_contract_hash: str | None
    blockers: tuple[str, ...]
    modeled_notional_cost_bps: float = 0.
    native_market_slippage_bps: float | None = None

    @property
    def compatible(self) -> bool:
        return not self.blockers

    def require_compatible(self) -> None:
        if self.blockers:
            raise ValueError("Optimized execution incompatible: " + "; ".join(self.blockers))


def verify_optimized_execution_contract(execution: Mapping, *, mode: str,
        configuration_revision: dict, execution_binding,
        broker_config: SimulationConfig | None = None,
        liquidity_plan_hash: str | None = None, execution_risk=None) -> OptimizedExecutionCompatibility:
    """Check explicit selected settings, and expose unsupported risk semantics.

    Risk must be a sealed normalized configuration contract, never a capability boolean.
    Paper/live actual commissions and fills cannot be certified by a simulation
    config. All modes require separate runtime qualification after this check.
    """
    if mode not in {"backtest", "replay", "paper", "live"}:
        raise ValueError("Unknown optimized execution mode")
    fields = {"entry_dollars", "add_dollars", "cost_bps", "fee_per_share",
              "minimum_order_fee", "risk_protection", "maximum_stop_distance",
              "maximum_position_risk"}
    if not isinstance(execution, Mapping) or set(execution) != fields:
        raise ValueError("Frozen execution requires its exact complete schema")
    for key in fields - {"risk_protection"}:
        value = execution[key]
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError("Invalid frozen execution number: " + key)
    if (type(execution["risk_protection"]) is not bool
            or min(execution["entry_dollars"], execution["add_dollars"]) <= 0
            or not 0 < execution["maximum_stop_distance"] <= .2
            or not 0 < execution["maximum_position_risk"] <= 300):
        raise ValueError("Frozen execution violates the V19 risk envelope")
    binding = restore_execution_binding(execution_binding.rows)
    bind_execution_configuration(configuration_revision, binding)
    blockers = []
    broker_hash = None
    native_slippage = None
    if mode in {"backtest", "replay"}:
        if type(broker_config) is not SimulationConfig:
            raise ValueError("Simulation mode requires an explicitly selected native broker config")
        broker = project_broker_contract("optimized-compatibility", broker_config,
                                         liquidity_plan_hash=liquidity_plan_hash)
        broker_hash = broker["content_hash"]
        native_slippage = broker_config.market_slippage_bps
        if broker_config.liquidity_resolution_ms != 100:
            blockers.append("audit100ms requires native 100ms liquidity")
        for frozen, native in (("fee_per_share", "commission_per_share"),
                               ("minimum_order_fee", "minimum_commission")):
            if execution[frozen] != getattr(broker_config, native):
                blockers.append("frozen " + frozen + " differs from selected broker " + native)
        # V19 deducts cost_bps from notional without changing its assumed fill.
        # Native market slippage changes fill prices, quantities and cash. Equal
        # numbers do not implement the same charge, and real fill assumptions
        # must not be replaced merely to make a reference comparison pass.
        if execution['cost_bps'] != 0:
            blockers.append('native broker does not implement frozen modeled notional transaction costs')
    else:
        if broker_config is not None or liquidity_plan_hash is not None:
            raise ValueError("Paper/live cannot use a simulated broker contract as qualification")
        blockers.append("paper/live actual broker costs and audit100ms adapter are not qualified")
    if execution["risk_protection"]:
        if execution_risk is None:
            blockers.append("missing explicit native fee-inclusive stop-risk contract")
        else:
            from src.trading_runtime.configuration_execution_risk import bind_execution_risk, restore_execution_risk
            risk = restore_execution_risk(execution_risk.rows)
            bind_execution_risk(configuration_revision, risk)
            if configuration_revision.get('execution_risk_hash') != risk.header['content_hash']:
                raise ValueError('Risk contract is not explicitly approved by the selected revision')
            if any(execution[key] != risk.header[key] for key in (
                    'maximum_stop_distance', 'maximum_position_risk', 'fee_per_share',
                    'minimum_order_fee', 'cost_bps')):
                blockers.append('frozen risk values differ from selected native risk contract')
    return OptimizedExecutionCompatibility(mode, binding.header["configuration_hash"],
        binding.header["content_hash"], broker_hash, tuple(blockers),
        float(execution['cost_bps']), native_slippage)
