"""New AH exit reaches native management while inherited decisions retain priority."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_followthrough_exit import validate_witness
from test_strategy_forty_two_management import prepared_manager


@pytest.mark.parametrize('number,exits', [(42, 0), (46, 1)])
def test_quarter_ah_extension_is_selected_only_by_declared_policy(number, exits):
    manager, witness, financial, rows = prepared_manager(number)
    manager.contract = numbered_fixed_strategy(number)
    key = (financial.account_id, financial.assignment_id, financial.ticker)
    source = manager._submitted[key]
    boundary = witness.completed_five_second_boundary_ms
    manager._first_held_boundaries[key] = boundary - 30_000
    price_int = int((3 * source.reference_ask + source.initial_stop) / 4 * 10_000) - 1
    assert price_int > (source.reference_ask + source.initial_stop) / 2 * 10_000
    frame = rows(boundary, completed=True, age=48)
    frame[5000].update(close_int=price_int, macd_line=.01, macd_signal=.02)
    evidence = asyncio.run(manager.evidence.management_evidence(financial.ticker, frame, boundary_ms=boundary))
    manager.evidence.management_evidence = AsyncMock(return_value=replace(
        evidence, bid=price_int / 10_000, ask=price_int / 10_000 + .01))
    asyncio.run(manager.on_management(financial, frame, boundary))
    assert manager.runtime.submit_followthrough_failure.await_count == exits
    manager.runtime.submit_profit_giveback.assert_not_awaited()
    manager.runtime.submit_confirmed_ah_failure.assert_not_awaited()
    if exits:
        _, actual, entry_id = manager.runtime.submit_followthrough_failure.await_args.args
        validate_witness(actual, strategy_number=46)
        assert entry_id == manager.runtime._strategy_one_entry_intent.return_value.intent_id
        assert actual.first_held_boundary_ms == boundary - 30_000


def test_existing_liquidity_checkpoint_retains_priority_over_extension():
    manager, witness, financial, rows = prepared_manager(46, counts=(57,18,20,10))
    manager.contract = numbered_fixed_strategy(46)
    boundary = witness.completed_five_second_boundary_ms
    frame = rows(boundary, completed=True, age=48)
    # Make the inherited first-minute condition ineligible, preserving the
    # original later negative-regime test's positive signal rejection.
    key = (financial.account_id, financial.assignment_id, financial.ticker)
    manager._first_held_boundaries[key] = boundary - 80_000
    frame[5000]['close_int'] = 23_100
    asyncio.run(manager.on_management(financial, frame, boundary))
    assert manager.liquidity_fade_requests(boundary_ms=boundary)
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
