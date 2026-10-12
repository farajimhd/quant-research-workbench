from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import asyncio

import pytest

from src.trading_runtime.filled_position_state import FillFact, FeeFact, FilledPositionState

START = datetime(2026, 10, 11, 14, tzinfo=timezone.utc)


def fill(execution="e1", intent="entry", action="enter_long", q="10", price="10", second=0):
    return FillFact(execution, "order-" + intent, intent, "account", "program", "assignment",
                    "listing", "ABC", action, D(q), D(price), START + timedelta(seconds=second))


def test_actual_partial_fills_weighted_basis_and_position_age():
    state = FilledPositionState()
    first = fill(q="4", price="9")
    pos = state.apply_fill(first)
    assert pos.quantity == 4 and pos.average_price == 9 and pos.position_open
    assert pos.age_seconds(START + timedelta(seconds=3, microseconds=100000)) == D("3.1")
    pos = state.apply_fill(fill(execution="e2", q="6", price="11", second=1))
    assert pos.quantity == 10 and pos.average_price == D("10.2")
    assert pos.opened_at == START
    state.apply_fill(fill(execution="e3", intent="add", action="add_long", q="2", price="12", second=2))
    pos = state.apply_fill(fill(execution="e4", intent="add", action="add_long", q="2", price="12", second=3))
    assert pos.adds == 1 and pos.quantity == 14
    assert pos.last_management_fill_at == START + timedelta(seconds=3)
    with pytest.raises(ValueError, match="future fill"):
        pos.age_seconds(START - timedelta(seconds=1))


def test_fees_determine_net_pnl_and_late_fees_correct_win_rate():
    state = FilledPositionState()
    state.apply_fill(fill())
    assert state.pending_fee_count == 1
    with pytest.raises(ValueError, match="pending execution commissions"):
        state.finalized_positions()
    state.apply_fee(FeeFact("e1", D("1"), "USD"))
    pos = state.apply_fill(fill(execution="e2", intent="exit", action="exit", price="10.15", second=1))
    assert not pos.position_open and pos.closed_positions == 1
    assert pos.net_realized_pnl == D(".50") and pos.winning_positions == 1
    pos = state.apply_fee(FeeFact("e2", D("1"), "USD"))
    assert pos.net_realized_pnl == D("-.50") and pos.winning_positions == 0
    assert state.apply_fee(FeeFact("e2", D("1"), "USD")) == pos
    assert state.pending_fee_count == 0 and state.finalized_positions()[fill().key] == pos
    metrics = state.terminal_financial_metrics()
    assert metrics == dict(net_pnl=D("-.50"), closed_positions=1, winning_positions=0,
        worst_position_pnl=D("-.50"), terminal_valid=True)
    with pytest.raises(TypeError):
        metrics["net_pnl"] = D("100")


def test_terminal_metrics_reject_open_positions_and_pending_fees():
    state = FilledPositionState()
    assert state.terminal_financial_metrics()["closed_positions"] == 0
    state.apply_fill(fill())
    with pytest.raises(ValueError, match="pending execution commissions"):
        state.terminal_financial_metrics()
    state.apply_fee(FeeFact("e1", D("1"), "USD"))
    with pytest.raises(ValueError, match="all positions closed"):
        state.terminal_financial_metrics()


def test_old_episode_late_fee_does_not_charge_new_position_episode():
    state = FilledPositionState()
    state.apply_fill(fill())
    state.apply_fill(fill(execution="e2", intent="exit", action="exit", price="11", second=1))
    state.apply_fill(fill(execution="e3", intent="new", second=2))
    pos = state.apply_fee(FeeFact("e1", D("1"), "USD"))
    assert pos.episode_net_pnl == 0 and pos.net_realized_pnl == 9
    assert pos.opened_at == START + timedelta(seconds=2)


def test_conflicting_duplicates_and_unattributed_reductions_fail_closed():
    state = FilledPositionState()
    first = fill()
    assert state.apply_fill(first) == state.apply_fill(first)
    with pytest.raises(ValueError, match="conflicting"):
        state.apply_fill(replace(first, price=D("9")))
    with pytest.raises(ValueError, match="exceeds actual"):
        state.apply_fill(fill(execution="e2", action="exit", q="11", second=1))
    with pytest.raises(ValueError, match="verified execution"):
        state.apply_fee(FeeFact("absent", D("1"), "USD"))
    assert state.positions[first.key].quantity == 10


def test_cold_prefix_replay_matches_live_projection_and_account_isolation():
    facts = [fill(), fill(execution="e2", action="reduce_long", intent="reduce", q="5", price="12", second=1),
             replace(fill(execution="e3", second=2), account_id="other")]
    fees = [FeeFact("e1", D("1"), "USD"), FeeFact("e2", D("1"), "USD")]
    a, b = FilledPositionState(), FilledPositionState()
    for fact in facts:
        a.apply_fill(fact)
    for fee in fees:
        a.apply_fee(fee)
    b.apply_fill(facts[0]); b.apply_fee(fees[0])
    b.apply_fill(facts[1]); b.apply_fee(fees[1]); b.apply_fill(facts[2])
    assert dict(a.positions) == dict(b.positions)
    assert a.positions[facts[0].key].quantity == 5
    assert a.positions[facts[2].key].quantity == 10


def test_real_broker_fill_is_used_instead_of_order_reference_price():
    from src.trading_runtime.runtime import RunMode
    from src.trading_runtime.simulated_broker import SimulationConfig, SimulatedBrokerAdapter
    from src.trading_runtime.ibkr_schema import OrderRequest
    from tests.test_simulated_broker_liquidity_bar import START as BROKER_START, bar

    async def run():
        broker = SimulatedBrokerAdapter(["TEST"], SimulationConfig(marketable_liquidity_participation=1),
            mode=RunMode.BACKTEST, initial_time=BROKER_START, fixed_bar_mode=True)
        await broker.initialize()
        await broker.place_orders("TEST", [OrderRequest(acctId="TEST", conid=1, cOID="entry",
            ticker="AAPL", orderType="LMT", side="BUY", quantity=10, price=11)])
        at = BROKER_START + timedelta(milliseconds=100)
        actual = (await broker.on_liquidity_bar(bar(at), at=at))[0]
        state = FilledPositionState()
        fact = replace(fill(), execution_id=actual.execution_id, order_id=actual.order_id,
            ticker=actual.symbol, account_id=actual.account, filled_at=actual.trade_time,
            quantity=D(str(actual.size)), price=D(str(actual.price)))
        pos = state.apply_fill(fact)
        assert pos.average_price == D(str(actual.price))
        assert pos.average_price != 11
        pos = state.apply_fee(FeeFact(actual.execution_id, D(str(actual.commission)), "USD"))
        assert pos.fees == 1 and pos.net_realized_pnl == -1
    asyncio.run(run())
