"""Execution bindings use the existing verified configuration publication lane."""
from .strategy_configuration_publication import ConfigurationSelectionPublisher
from src.trading_runtime.configuration_execution_binding import POLICY, SEAL, restore_execution_binding


class ConfigurationExecutionBindingPublisher(ConfigurationSelectionPublisher):
    children = (POLICY,)
    seal = SEAL
    restore = staticmethod(restore_execution_binding)


from src.trading_runtime.configuration_execution_risk import RISK, restore_execution_risk


class ConfigurationExecutionRiskPublisher(ConfigurationSelectionPublisher):
    children = ()
    seal = RISK
    restore = staticmethod(restore_execution_risk)
