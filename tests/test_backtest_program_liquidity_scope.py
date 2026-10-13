"""Full population remains certified; broker reads retain relevant native owners."""
from dataclasses import replace
from datetime import datetime, UTC, timedelta
from decimal import Decimal
from types import MappingProxyType

import polars as pl
import pytest

from src.backend.backtest_program_liquidity_scope import program_cohort_liquidity_tickers
from src.backend.backtest_program_inputs import PreparedProgramScannerProjection
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.filled_position_state import FillFact
from tests.test_program_controller_runtime import definition_and_actor


def fixture():
    _, original = definition_and_actor()
    first = original.assignments()[0]
    assignments = tuple(replace(first, assignment_id=f'A{i}', ticker=f'T{i}') for i in range(128))
    actor = AssignedProgramStrategy(original.program_release, assignments, {f'A{i}': f'L{i}' for i in range(128)})
    # The selector only reads the OMS's canonical assignment index. Full OMS
    # processing, fill and fee geometry are covered by native cohort tests.
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager._groups_by_assignment = {}
    at = datetime(2026, 10, 12, 14, tzinfo=UTC)
    frame = pl.DataFrame(dict(boundary_at=[at, at + timedelta(seconds=1), at + timedelta(seconds=2)],
        ticker=['T0', 'T1', 'T99']))
    projection = PreparedProgramScannerProjection(actor.program_release.header['content_hash'],
        'a' * 64, frame, MappingProxyType({}))
    return actor, manager, projection, at


def test_rank_window_projects_transport_and_retains_fills_oms_and_prior_visits():
    actor, manager, projection, at = fixture()
    account = actor.assignments()[0].account_id
    actor._selection_clocks['A2'] = (at, None)
    actor.execution_state.apply_fill(FillFact('execution', 'order', 'intent', account,
        actor.strategy_id, 'A3', 'L3', 'T3', 'enter_long', Decimal(1), Decimal(10), at))
    manager._groups_by_assignment[(account, 'A4')] = ()
    assert program_cohort_liquidity_tickers(actor, manager, projection,
        first_clock=at, last_clock=at + timedelta(seconds=1)) == ('T0', 'T1', 'T2', 'T3', 'T4')
    assert len(actor.assignments()) == 128  # Complete decision/source scope stays intact.


def test_empty_rank_window_opens_no_unnecessary_broker_transport():
    actor, manager, projection, at = fixture()
    assert program_cohort_liquidity_tickers(actor, manager, projection,
        first_clock=at + timedelta(seconds=3), last_clock=at + timedelta(seconds=4)) == ()


def test_broker_order_is_retained_before_oms_observation():
    from src.trading_runtime.ibkr_schema import LiveOrder, OrderStatus
    actor, manager, projection, at = fixture()
    order = LiveOrder(actor.assignments()[0].account_id, 'order', 1, 'T17', 'BUY', 'LMT',
        'DAY', 1., 0., 1., 0., OrderStatus.SUBMITTED)
    assert program_cohort_liquidity_tickers(actor, manager, projection,
        first_clock=at, last_clock=at, broker_orders=(order,)) == ('T0', 'T17')
    with pytest.raises(ValueError, match='foreign'):
        program_cohort_liquidity_tickers(actor, manager, projection,
            first_clock=at, last_clock=at, broker_orders=(replace(order, ticker='FOREIGN'),))


@pytest.mark.parametrize('mode', ['scanner', 'oms', 'lifecycle'])
def test_foreign_owners_fail_before_liquidity_reads(mode):
    actor, manager, projection, at = fixture()
    if mode == 'scanner':
        projection = replace(projection, frame=projection.frame.with_columns(pl.lit('FOREIGN').alias('ticker')))
    elif mode == 'oms':
        manager._groups_by_assignment[('other-account', 'A1')] = ()
    else:
        actor._selection_clocks['other-assignment'] = (at, None)
    with pytest.raises(ValueError, match='foreign|unknown'):
        program_cohort_liquidity_tickers(actor, manager, projection, first_clock=at, last_clock=at)
