import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.trading_runtime.entry_cash_budget import cash_bounded_buy_price
from src.trading_runtime.execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ProtectionProfile, ProtectionSlice, StopRule,
)
from src.trading_runtime.order_management import (
    BrokerCommunicationPolicy, ExecutionQuote, execution_tactic, _apply_initial_tactic,
)
from src.trading_runtime.signals import CapitalRequest
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner, InstrumentContract
from src.trading_runtime.domain import TradingMode
from tests import test_order_management as oms_fixture
from tests.test_order_management import intent, portfolio_approved
from tests.test_simulated_broker_liquidity_bar import START, bar


def request(action='enter_long', quantity=223, cash=1000):
    return replace(intent(action=action, quantity=quantity), ticker='AAPL',
        event_time=START, reference_price=4.4751, invalidation_price=3.6883087162140806,
        capital_request=CapitalRequest('fixed_notional', cash), metadata={},
        execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=5000)))


@pytest.mark.parametrize('action,cash,quantity', [('enter_long', 1000, 223), ('add_long', 200, 40)])
def test_recorded_gnpx_entry_and_add_respect_cash_on_every_initial_step(action, cash, quantity):
    original = request(action, quantity, cash)
    tactic = execution_tactic(original, BrokerCommunicationPolicy(),
        quote=ExecutionQuote(bid=5.02, ask=5.04, tick_size=.01, observed_at=START), enforce_wall_clock_freshness=False)
    assert tactic.steps
    assert all(Decimal(str(step.price)) * quantity <= cash for step in tactic.steps)
    assert original.quantity == quantity
    assert cash_bounded_buy_price(original, 10., tick_size=.01) == (4.48 if cash == 1000 else 5.)


def test_no_quote_planner_also_preserves_cash_and_protection_quantity():
    original = request()
    plan = IbkrStrategyOrderPlanner().plan(account_id='DU1',
        instrument=InstrumentContract('ibkr:1', 1, 'AAPL', 'STK', 'USD'),
        intent=original, strategy_id='program', strategy_revision=1)
    assert Decimal(str(plan.orders[0].price)) * 223 <= 1000
    assert all(order.quantity == 223 for order in plan.orders)


def test_native_broker_waits_above_cash_ceiling_then_fills_within_budget():
    async def run():
        broker = SimulatedBrokerAdapter(['DU1'], mode=TradingMode.BACKTEST,
            initial_time=START, fixed_bar_mode=True)
        await broker.initialize()
        original = request()
        plan = IbkrStrategyOrderPlanner().plan(account_id='DU1',
            instrument=InstrumentContract('ibkr:1', 1, 'AAPL', 'STK', 'USD'),
            intent=original, strategy_id='program', strategy_revision=1)
        tactic = execution_tactic(original, BrokerCommunicationPolicy(),
            quote=ExecutionQuote(bid=4.50, ask=4.52, tick_size=.01, observed_at=START), enforce_wall_clock_freshness=False)
        await broker.place_orders('DU1', list(_apply_initial_tactic(plan.orders, tactic)))
        at = START + timedelta(milliseconds=100)
        assert not await broker.on_liquidity_bar(bar(at, bid=4.50, ask=4.52,
            low=4.50, high=4.52, execution_volume=0, execution_price_levels=()), at=at)
        at += timedelta(milliseconds=100)
        fills = await broker.on_liquidity_bar(bar(at, bid=4.46, ask=4.48,
            ask_size=1000, low=4.46, high=4.48), at=at)
        assert sum(fill.size for fill in fills) == 223
        assert sum(Decimal(str(fill.price)) * Decimal(str(fill.size)) for fill in fills) <= 1000
    asyncio.run(run())


def test_actual_oms_reprice_keeps_original_cash_ceiling(tmp_path):
    async def run():
        broker = SimulatedBrokerAdapter(['DU1'], mode=TradingMode.BACKTEST)
        manager, journal = await oms_fixture.OrderManagementPolicyTests()._manager(str(tmp_path), broker,
            policy=BrokerCommunicationPolicy(), causal_execution_clock=True)
        try:
            original = replace(request(), ticker='TEST', metadata={
                'bid': 4.45, 'ask': 4.47, 'tick_size': .01,
                'quote_observed_at': START.isoformat()})
            submitted = await manager.submit_intent(portfolio_approved(journal, original),
                account_id='DU1', event=None)
            group = manager._groups[submitted.group_id]
            # A prior partial fill must not reset the cash budget for the remainder.
            group.filled_quantity = 100
            from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
            at = START + timedelta(milliseconds=100)
            manager.on_market_snapshot(ExecutionMarketSnapshot('TEST', 4.50, 4.52, .01, at, 'qmd-history'))
            await manager._attempt_reprice(group, record_time=at)
            live = await broker.live_orders()
            assert live
            assert all(Decimal(str(order.price)) * 223 <= 1000 for order in live if order.side == 'BUY')
            assert group.intent.quantity == 223
        finally:
            await manager.close()
            journal.close()
    asyncio.run(run())


def test_other_sizing_and_sells_are_unchanged():
    original = request()
    for changed in (replace(original, capital_request=None),
                    replace(original, capital_request=CapitalRequest('fixed_quantity', 223)),
                    replace(original, action='exit')):
        assert cash_bounded_buy_price(changed, 5.04, tick_size=.01) == 5.04


@pytest.mark.parametrize('reference,stop,ask', [
    ('4.79', '3.9478444617249773', '4.98'),
    ('3.85', '3.847218030201535', '4.94'),
])
def test_recorded_gnpx_risk_gate_allows_signal_price_but_rejects_executable_ask(reference, stop, ask):
    from src.trading_runtime.configuration_execution_risk import evaluate_acquisition_risk
    from tests.test_configuration_execution_risk import contract
    values = dict(action='enter_long', quantity=Decimal(0), average_price=Decimal(0),
        stop_price=Decimal(stop), episode_paid_fees=Decimal(0),
        episode_realized_gross_pnl=Decimal(0), requested_cash=Decimal(1000))
    assert evaluate_acquisition_risk(contract(), **values, execution_price=Decimal(reference)).allowed
    actual = evaluate_acquisition_risk(contract(), **values, execution_price=Decimal(ask))
    assert actual.reasons == ('maximum_stop_distance_exceeded',)


def test_ezra_recorded_stop_waits_for_post_entry_trigger_and_next_bucket():
    """A bounded broker fixture, not a repeated historical backtest."""
    async def run():
        stop = 1.9047256296223891
        original = replace(request(quantity=515), reference_price=1.94,
            invalidation_price=stop, protection_profile=ProtectionProfile('recorded-stop', 1,
                (ProtectionSlice('one', 1., StopRule(price=stop)),)))
        broker = SimulatedBrokerAdapter(['DU1'],
            SimulationConfig(marketable_liquidity_participation=1., market_slippage_bps=5.),
            mode=TradingMode.BACKTEST, initial_time=START, fixed_bar_mode=True)
        await broker.initialize()
        plan = IbkrStrategyOrderPlanner().plan(account_id='DU1',
            instrument=InstrumentContract('ibkr:1', 1, 'AAPL', 'STK', 'USD'),
            intent=original, strategy_id='program', strategy_revision=1)
        await broker.place_orders('DU1', list(plan.orders))
        # These prices/sizes are the saved EZRA buckets ending .100 through .500.
        for index, (bid, ask, low, high, ask_size) in enumerate((
                (1.92, 1.93, 1.93, 1.93, 500),
                (1.92, 1.93, 1.92, 1.92, 500),
                (1.92, 1.93, 1.925, 1.9283, 400),
                (1.90, 1.92, 1.90, 1.92, 700),
                (1.90, 1.92, 1.90, 1.91, 600)), 1):
            at = START + timedelta(milliseconds=100 * index)
            fills = await broker.on_liquidity_bar(bar(at, bid=bid, ask=ask,
                low=low, high=high, ask_size=ask_size), at=at)
            buys = [fill for fill in fills if fill.side == 'B']
            sells = [fill for fill in fills if fill.side == 'S']
            if index <= 2:
                assert sum(fill.size for fill in buys) == (500 if index == 1 else 15)
            if index < 5:
                assert not sells
            else:
                assert len(sells) == 1
                assert sells[0].size == 100
                assert sells[0].price == 1.89905
    asyncio.run(run())


def test_runtime_certificate_rejects_unreviewed_cash_limit_dependency(tmp_path):
    from src.backend.backtest_fixed_v4_certification import certify_drawdown_measure_core_source
    name = 'src/trading_runtime/entry_cash_budget.py'
    changed = tmp_path / 'entry_cash_budget.py'
    source = (Path(__file__).parents[1] / name).read_text(encoding='utf8')
    changed.write_text(source.replace('return float(ceiling)', 'return float(ceiling) + .01'), encoding='utf8')
    with pytest.raises(ValueError, match='Drawdown core source changed:.*entry_cash_budget'):
        certify_drawdown_measure_core_source(source_overrides={name: changed})
