import asyncio
from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.backend.replay_run_service import ReplayRunDefinition
from src.backend.backtest_strategy_forty_four_controller import StrategyFortyFourController
from src.backend.backtest_strategy_forty_four_configuration import compile_configuration
from src.backend.backtest_strategy_forty_four_journal import StrategyFortyFourJournal
from src.backend.backtest_strategy_forty_four_certification import certify_projection
from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.trading_runtime.runtime import RunMode


def definition():
    envelope = compile_configuration(approved_code_commit="0"*40,
        approved_code_fingerprint="0"*64, approval_reference="test fixture")
    payload = envelope["payload"]
    return ReplayRunDefinition(mode=RunMode.BACKTEST, session_date=date(2026,9,3),
        start_time=time(4), end_time=time(9,30), initial_cash=10_000, tickers=(),
        configuration_revision=dict(payload=payload, content_hash=envelope["payload_hash"],
            revision_id="strategy-one-44:"+str(uuid4()), revision=44), execution_interval="100ms",
        market_data_plan=dict(token="1"*64, execution_interval=dict(milliseconds=100),
            strategy_forty_four_history_token="2"*64, strategy_forty_four_identity_token="3"*64,
            strategy_forty_four_structure_token="4"*64, strategy_forty_four_price_token="5"*64))


def test_controller_constructs_own_native_identity_and_portfolio():
    async def run():
        controller = StrategyFortyFourController(definition())
        controller._fixed_v4_account_ids = historical_simulated_account_ids(
            mode=RunMode.BACKTEST, configuration=controller.definition.configuration_revision["payload"])
        controller._forty_four_inputs = (SimpleNamespace(tickers=("TEST",),
            identity=SimpleNamespace(conid_for=lambda _:123)),)
        controller._journal = StrategyFortyFourJournal(run_id=controller.run_id)
        try:
            await controller._initialize_runtime()
            assert controller._runtime.config.strategy_id == "squeeze-grid-strategy"
            assert controller.account_ids == controller._fixed_v4_account_ids
            assert len(controller._strategy.assignments()) == 1
            assert controller._runtime.portfolio.states[controller.account_ids[0]].profile.policy.policy_id == "strategy-44-cash"
        finally:
            if controller._runtime is not None:
                await controller._runtime.order_manager.close()
            controller._journal.close()
    asyncio.run(run())


def test_source_certificate_is_independent_and_exhaustive():
    assert len(certify_projection()) == 64


def test_new_release_has_no_executor_repair_exemptions():
    import inspect
    from src.backend.backtest_strategy_forty_four_preflight import preflight
    source = inspect.getsource(preflight)
    assert 'compatible_executor' not in source
    assert 'current != manifest["approved_code_fingerprint"]' in source


def test_definition_rejects_missing_history_pin():
    from dataclasses import replace
    existing = definition()
    with pytest.raises(ValueError, match="certified full"):
        replace(existing, market_data_plan={**existing.market_data_plan,
            "strategy_forty_four_history_token":""})
