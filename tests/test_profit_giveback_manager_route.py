"""Completed manager facts route prepared 31 exits; no database attestation."""
import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
from test_profit_arming_engine_boundary import manager_fixture, reference


@pytest.mark.parametrize('case', ['exit', 'unarmed', 'same_boundary', 'pending', 'stale', 'positive_histogram', 'parent_loss', 'unarmed_parent_loss'])
@pytest.mark.parametrize('number', [31, 32])
def test_profit_exit_requires_confirmed_prior_arm_and_current_completed_failure(case, number):
    manager, financial, state = manager_fixture(strategy_number=number)
    manager.contract = SimpleNamespace(strategy_number=number, allows_followthrough_failure_exit=True,
        liquidation_due=lambda _: False, allows_completed_30s_trailing=False,
        allows_target_escalation=False, allows_adds=False)
    manager.runtime.config.anchor_date = date(2026, 8, 4)
    manager.runtime.submit_followthrough_failure = AsyncMock()
    manager.runtime.submit_profit_giveback = AsyncMock()
    manager.runtime._strategy_one_entry_intent = Mock(return_value=SimpleNamespace(intent_id='original-entry'))
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    arm = reference(requests[0][0])
    key = state.submitted[0][0]
    if case not in ('unarmed', 'unarmed_parent_loss'):
        manager.accept_profit_arming_references(requests, (arm,), boundary_ms=state.boundary_ms)
    if case == 'same_boundary':
        manager._profit_arm_references[key] = replace(arm, candidate=replace(arm.candidate, boundary_ms=10000))
    if case == 'pending':
        financial = replace(financial, pending_exit=True)
    evidence = StrategyOneManagementEvidence(financial.ticker, 10000, 10.5, 10.51,
        True, None, None, (), ())
    if case in ('parent_loss', 'unarmed_parent_loss'):
        evidence = replace(evidence, bid=9.4, ask=9.41)
    manager.evidence.management_evidence = AsyncMock(return_value=evidence)
    at_us = int(market_day_boundary(manager.runtime.config.anchor_date, 10000).timestamp() * 1_000_000)
    rows = {100: {'price_valid': 1, 'high_int': 200000, 'quote_valid': 1,
                 'quote_timestamp_us': at_us - (1000001 if case == 'stale' else 1000)},
            5000: {'boundary_ms': 10000, 'price_valid': 1, 'close_int': 105000,
                   'macd_line': .03 if case == 'positive_histogram' else .01, 'macd_signal': .02}}
    if case in ('parent_loss', 'unarmed_parent_loss'):
        rows[5000].update(close_int=94000, macd_line=-.02, macd_signal=-.01)
    asyncio.run(manager.on_management(financial, rows, 10000))
    if case in ('parent_loss', 'unarmed_parent_loss'):
        manager.runtime.submit_followthrough_failure.assert_awaited_once()
        manager.runtime.submit_profit_giveback.assert_not_awaited()
        assert manager.runtime.submit_followthrough_failure.call_args.args[2] == 'original-entry'
        assert key not in manager._profit_arm_financials
        assert manager.profit_arming_requests(boundary_ms=10000) == ()
        return
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
    if case == 'exit':
        manager.runtime.submit_profit_giveback.assert_awaited_once()
        _, witness, entry_id, actual_reference = manager.runtime.submit_profit_giveback.call_args.args
        assert witness.prior_high_int == 110000 and witness.prior_high_through_boundary_ms == 9900
        assert manager._position_highs[key] == 200000
        assert entry_id == 'original-entry' and actual_reference is arm
    else:
        manager.runtime.submit_profit_giveback.assert_not_awaited()
        manager.runtime._strategy_one_entry_intent.assert_not_called()
