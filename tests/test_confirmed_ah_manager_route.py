"""Prepared manager selection using native development observations, no replay."""
import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.strategy_confirmed_ah_risk_failure import confirmed_ah_risk_failure
from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
from test_profit_arming_engine_boundary import manager_fixture
from test_strategy_confirmed_ah_risk_failure import observed_case


@pytest.mark.parametrize('case', ['exit', 'parent', 'missing_ten', 'forming_ten', 'pending', 'older_release'])
def test_confirmed_ah_manager_retains_parent_priority_and_completed_authority(case):
    manager, financial, state = manager_fixture()
    number = 33 if case == 'older_release' else 34
    manager.contract = SimpleNamespace(strategy_number=number,
        allows_followthrough_failure_exit=True, liquidation_due=lambda _: False,
        allows_completed_30s_trailing=False, allows_target_escalation=False,
        allows_adds=False)
    manager.runtime.config.anchor_date = date(2026, 8, 10)
    manager.runtime.submit_followthrough_failure = AsyncMock()
    manager.runtime.submit_profit_giveback = AsyncMock()
    manager.runtime.submit_confirmed_ah_failure = AsyncMock()
    manager.runtime._strategy_one_entry_intent = Mock(return_value=SimpleNamespace(intent_id='original-entry'))
    x = observed_case()
    five = x.five_second
    key = state.submitted[0][0]
    manager._submitted[key] = replace(manager._submitted[key], strategy_number=number,
        boundary_ms=five.first_held_boundary_ms - 100, reference_ask=2.08,
        initial_stop=1.81, initial_target=3.0)
    manager._positions[key] = replace(manager._positions[key],
        boundary_ms=five.boundary_ms - 100, stop=1.81, target=3.0)
    manager._first_held_boundaries[key] = five.first_held_boundary_ms
    financial = replace(financial, pending_exit=case == 'pending')
    bid, ask, close, line, signal = 1.96, 1.97, 19700, five.macd_line, five.macd_signal
    if case == 'parent':
        bid, ask, close, line, signal = 1.7, 1.71, 17000, -.02, -.01
    manager.evidence.management_evidence = AsyncMock(return_value=StrategyOneManagementEvidence(
        financial.ticker, five.boundary_ms, bid, ask, True, None, None, (), ()))
    at_us = int(market_day_boundary(manager.runtime.config.anchor_date, five.boundary_ms).timestamp() * 1_000_000)
    rows = {100: {'price_valid': 1, 'high_int': 19700, 'quote_valid': 1,
                  'quote_timestamp_us': at_us - 48},
            5000: {'boundary_ms': five.boundary_ms, 'price_valid': 1,
                   'close_int': close, 'macd_line': line, 'macd_signal': signal},
            10000: {'boundary_ms': x.completed_ten_second_boundary_ms,
                    'price_valid': 1, 'macd_line': x.ten_second_macd_line,
                    'macd_signal': x.ten_second_macd_signal}}
    if case == 'missing_ten':
        rows.pop(10000)
    if case == 'forming_ten':
        rows[10000]['boundary_ms'] += 10000
    asyncio.run(manager.on_management(financial, rows, five.boundary_ms))
    manager.runtime.submit_profit_giveback.assert_not_awaited()
    if case == 'parent':
        manager.runtime.submit_followthrough_failure.assert_awaited_once()
    else:
        manager.runtime.submit_followthrough_failure.assert_not_awaited()
    if case == 'exit':
        manager.runtime.submit_confirmed_ah_failure.assert_awaited_once()
        _, witness, source = manager.runtime.submit_confirmed_ah_failure.call_args.args
        assert witness == confirmed_ah_risk_failure(replace(x,
            five_second=replace(five, position_quantity=financial.position_quantity)))
        assert source == 'original-entry'
        assert key not in manager._profit_arm_financials
    else:
        manager.runtime.submit_confirmed_ah_failure.assert_not_awaited()
