"""Pure dedicated native-channel grant plan; no credentials or apply authority."""
from dataclasses import replace

from src.trading_runtime.native_channel_management_policy import selected_writer_contracts
from src.trading_runtime.native_channel_writer_profile import channel_runner_identity
from src.market_engine.native_causal_channel_contract import FEATURE_TABLE, COVERAGE_TABLE


def desired_plan(channel_policy, risk_policy):
    """Retain inherited financial grants and add exact selected companions.

    Feature products are SELECT-only. This function does not install tables,
    provision credentials, change grants, or prove physical SSD placement.
    """
    _, principal = channel_runner_identity(channel_policy, risk_policy)
    if getattr(risk_policy, 'entry_cost_capability', False):
        from scripts.clickhouse.provision_backtest_v4_original_risk_entry_cost_runner import desired_plan as parent_plan
    else:
        from scripts.clickhouse.provision_backtest_v4_original_risk_runner import desired_plan as parent_plan
    parent = parent_plan(risk_policy)
    companions = frozenset(table.name for table in selected_writer_contracts(channel_policy))
    products = frozenset(table.removeprefix('arte.') for table in (FEATURE_TABLE, COVERAGE_TABLE))
    return replace(parent, principal=principal,
        select_arte=parent.select_arte | companions | products,
        insert_arte=parent.insert_arte | companions)
