"""Installed36 protection submits through shared admission before confirmation."""
import asyncio
from dataclasses import dataclass, replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.runtime import RunMode, TradingRuntime
from src.trading_runtime.strategy_one_position import ProtectionState, ProtectionTransition
from test_strategy_one_protection_intent import financial, previous


@pytest.mark.parametrize('approved', [True, False])
def test_installed36_structural_stop_waits_for_shared_portfolio_and_oms(approved):
    held = financial()
    old = previous()
    transition = ProtectionTransition(ProtectionState(31000, 9.8, old.target),
        {'price': 9.8, 'source': 'three_resistance_step_stop'}, None)

    @dataclass
    class Submitted:
        filled_quantity: float = 0.

    async def approve(intent, *, account_id, assignment_id):
        assert account_id == held.account_id and assignment_id == held.assignment_id
        decision = SimpleNamespace(reasons=('test_rejection',),
                                   payload=lambda: {'status': 'approved' if approved else 'rejected'})
        return decision, replace(intent, metadata={'assignment_id': assignment_id}) if approved else None

    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST, strategy_id='early-squeeze-strategy',
        strategy_revision=36, account_ids=(held.account_id,), anchor_date=date(2026, 8, 18))
    runtime.run_id = str(UUID(int=36001))
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id)
    runtime.intent_planner = object()
    runtime.order_manager = SimpleNamespace(submit_intent=AsyncMock(return_value=Submitted()))
    runtime.portfolio = SimpleNamespace(approve=AsyncMock(side_effect=approve),
        release_intent=lambda *_a, **_k: None, _typed_recovery=False)
    runtime.strategy = SimpleNamespace(assignments=lambda: ())
    runtime.last_event_time = None
    runtime._refresh_portfolio_from_broker = AsyncMock()
    runtime._record_intent_rejection = AsyncMock()
    runtime._fund_momentum_request = AsyncMock()
    call = runtime.submit_strategy_one_protection(old, transition, held, bid=10., ask=10.01)
    try:
        if approved:
            assert asyncio.run(call) == transition.state
            runtime.order_manager.submit_intent.assert_awaited_once()
            record, = runtime.journal.records(runtime.run_id)
            assert record.payload['strategy_revision'] == 36
            assert record.payload['reason'] == 'three_resistance_step_stop'
            assert runtime.journal.strategy_one_protection_for_record(record.record_id).metadata == {}
        else:
            with pytest.raises(RuntimeError, match='was not confirmed'):
                asyncio.run(call)
            runtime.order_manager.submit_intent.assert_not_awaited()
        runtime.portfolio.approve.assert_awaited_once()
    finally:
        runtime.journal.close()
