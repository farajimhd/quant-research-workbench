import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_management_owner import owner_case
from scripts.clickhouse.native_channel_runner_plan import desired_plan
from scripts.clickhouse.provision_backtest_v4_original_risk_runner import desired_plan as parent_plan
from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
from src.trading_runtime.native_channel_management_policy import selected_writer_contracts
from src.market_engine.native_causal_channel_contract import FEATURE_TABLE, COVERAGE_TABLE


def test_dedicated_plan_retains_financial_grants_without_feature_writes(installed):
    owner,_,_,_,_=owner_case(installed)
    risk=ConfirmedOriginalRiskPolicy()
    parent=parent_plan(risk)
    plan=desired_plan(owner.policy,risk)
    companions=frozenset(table.name for table in selected_writer_contracts(owner.policy))
    products=frozenset(table.removeprefix('arte.') for table in (FEATURE_TABLE,COVERAGE_TABLE))
    assert plan.principal=='backtest_v4_native_channel_runner'
    assert plan.select_arte==parent.select_arte|companions|products
    assert plan.insert_arte==parent.insert_arte|companions
    assert not products & plan.insert_arte
    assert parent.principal=='backtest_v4_original_risk_runner'
    with pytest.raises(ValueError): desired_plan(None,risk)
    with pytest.raises(ValueError): desired_plan(owner.policy,None)
