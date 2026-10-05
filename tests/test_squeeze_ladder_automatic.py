"""Real native financial consumer checks; these do not certify publication."""
import asyncio
from dataclasses import replace, asdict
from datetime import date, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_ladder_entry_authority import NativeLadderMarketContext
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderPolicy, submit_automatic_ladder
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyAssignment
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_backtest_squeeze_ladder_entry import prepared, decide
from tests.test_backtest_squeeze_ladder_setup import plans
from tests.test_backtest_strategy_one_loader import DAY, TICKER
from tests.test_backtest_squeeze_ladder_loader import fixture
from tests.test_trading_runtime import quote

RUN = '00000000-0000-4000-8000-000000000055'


def source():
    observed, setup, v7 = prepared()
    _, market, _, pivots = plans()
    policy = AutomaticLadderPolicy()
    _, _, gate_policy, _, _ = fixture()
    payload = {'strategy': {'strategy_id': 'prepared-native-ladder', 'strategy_number': 1,
               'numbered_release': {'automatic_entry_policy': policy.payload()}}}
    market_policy = {'gate': asdict(gate_policy), 'tick_int':100,
        'stop_buffer_ticks':1, 'break_buffer_ticks':1, 'source_through_boundary_ms':70000}
    payload['strategy']['numbered_release']['automatic_market_policy'] = market_policy
    configuration = CertifiedStrategyOneConfiguration('attempt',
        sha256(canonical_json(payload).encode()).hexdigest(), 'nodes', 'candidate', 'candidatehash',
        'config-token', payload)
    context = NativeLadderMarketContext(RUN, date.fromisoformat(DAY), configuration,
        observed, market, v7, pivots, 100, 1, 1, gate_policy, 70000)
    at = market_day_boundary(DAY, 65100)
    assignment = StrategyAssignment(f'strategy-1:DU1:{TICKER}', 'prepared-native-ladder', 1, 'DU1',
        TICKER, 123, AssignmentStatus.WATCHING, policy.permissions, {},
        created_at=at, updated_at=at)
    return context, policy, assignment, decide(observed, setup, v7)


async def runtime_fixture():
    context, policy, assignment, decision = source()
    journal = BacktestMemoryJournal(run_id=RUN)
    broker = SimulatedBrokerAdapter(['DU1'], SimulationConfig(initial_cash=10000,
        commission_per_share=0, minimum_commission=0, liquidity_participation=1),
        mode=RunMode.BACKTEST)
    portfolio = PortfolioManagementEngine([PortfolioAccountProfile('cash', 'DU1', 'backtest',
        'cash', PortfolioPolicy(allow_outside_rth=True, maximum_position_fraction=1., maximum_ticker_fraction=1.,
            maximum_gross_exposure=10000., maximum_net_long_exposure=10000.,
            maximum_order_notional=10000., maximum_snapshot_age_ms=1000000000000),
        strategy_allocations={'prepared-native-ladder': 1.})], journal=journal, run_id=RUN,
        strategy_id='prepared-native-ladder', strategy_revision=1)
    class Planner:
        def plan(self, *, intent, account_id, event):
            return IbkrStrategyOrderPlanner().plan(account_id=account_id,
                instrument=InstrumentContract(TICKER, 123, TICKER, 'STK', 'USD'),
                intent=intent, strategy_id='prepared-native-ladder', strategy_revision=1)
    strategy = SimpleNamespace(strategy_id='prepared-native-ladder', revision=1, automatic=True,
        assignments=lambda: (assignment,))
    runtime = TradingRuntime(RunConfig(RunMode.BACKTEST, 'prepared-native-ladder', 1,
        ('DU1',), context.session_date, run_id=RUN, safety_supervisor_enabled=False),
        broker, strategy, journal, intent_planner=Planner(), portfolio=portfolio)
    await runtime.initialize()
    event = replace(quote(bid=10.22, ask=10.23, ask_size=1000), ticker=TICKER,
        raw={'conid':123}, ts=market_day_boundary(DAY, 65100),
        ingest_ts=market_day_boundary(DAY, 65100))
    await runtime.process_event(event, evaluate_strategy=False)
    return runtime, context, policy, assignment, decision, event


def test_real_portfolio_oms_cash_and_independent_oca_targets():
    async def exercise():
        runtime, context, policy, assignment, decision, event = await runtime_fixture()
        try:
            result = await submit_automatic_ladder(runtime, decision, assignment=assignment,
                market_context=context, policy=policy)
            assert result[0]['order_group'] is not None, canonical_json(result)
            group = next(iter(runtime.order_manager._groups.values()))
            assert len(group.plan.orders) == 9
            assert len(group.plan.broker_batches) == 3
            assert all(len(batch) == 3 for batch in group.plan.broker_batches)
            assert len({order.parentId for order in group.plan.orders if order.side == 'SELL'}) == 3
            assert {order.auxPrice for order in group.plan.orders if order.orderType == 'STP'} == {9.79}
            assert sorted(order.price for order in group.plan.orders if order.side == 'SELL'
                          and order.orderType == 'LMT') == [10.99, 11.99, 12.99]
            await runtime.process_event(replace(event, ts=event.ts+timedelta(milliseconds=100),
                ingest_ts=event.ts+timedelta(milliseconds=100), sequence=2), evaluate_strategy=False)
            positions = await runtime.broker.positions('DU1')
            held = sum(float(row.position) for row in positions)
            assert held > 0
            cash = float((await runtime.broker.account_summary('DU1')).totalcashvalue)
            assert cash == pytest.approx(10000-held*10.23)
            # Only the first independent lot's target fills; its paired stop
            # cancels while the other two lots remain protected.
            await runtime.process_event(replace(event, bid_price=11., ask_price=11.01,
                bid_size=1000, ts=event.ts+timedelta(milliseconds=200),
                ingest_ts=event.ts+timedelta(milliseconds=200), sequence=3), evaluate_strategy=False)
            remaining = sum(float(row.position) for row in await runtime.broker.positions('DU1'))
            assert 0 < remaining < held
            assert float((await runtime.broker.account_summary('DU1')).totalcashvalue) > cash
            repeat = await submit_automatic_ladder(runtime, replace(decision, boundary_ms=65300), assignment=assignment,
                market_context=context, policy=policy)
            assert repeat[0]['decision']['status'] == 'accepted_batch_consumed_session'
            parents = [record for record in runtime.journal.records(RUN)
                       if record.entity_type == 'strategy_intent']
            assert len(parents) == 1
            assert runtime.journal.automatic_entry_for_record(parents[0].record_id).intent.quantity == 0
        finally:
            await runtime.order_manager.close()
            runtime.journal.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('mutation', ['stop', 'permissions', 'config', 'control'])
def test_invalid_source_or_immutable_permissions_do_not_reserve_or_order(mutation):
    async def exercise():
        runtime, context, policy, assignment, decision, event = await runtime_fixture()
        try:
            if mutation == 'stop':
                decision = replace(decision, setup=replace(decision.setup,
                    stop=replace(decision.setup.stop, stop_int=97800)))
            elif mutation == 'permissions':
                assignment = replace(assignment, permissions=replace(policy.permissions, reenter=True))
            elif mutation == 'config':
                context.configuration.payload['strategy']['numbered_release']['automatic_entry_policy'] = {}
            else:
                runtime.journal.append(run_id=RUN, category='strategy',
                    entity_type='strategy_assignment_command', entity_id='command', account_id='DU1',
                    event_time=event.ts, payload={'command':'resume'})
            with pytest.raises(ValueError):
                await submit_automatic_ladder(runtime, decision, assignment=assignment,
                    market_context=context, policy=policy)
            assert await runtime.broker.live_orders() == []
            assert runtime.portfolio.reservations == {}
        finally:
            await runtime.order_manager.close()
            runtime.journal.close()
    asyncio.run(exercise())


def test_scalar_transport_binds_sealed_parent_and_rejects_target_mutation():
    from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.automatic_ladder_transport import V4AutomaticLadderBatch
    from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderRequest
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.backend.backtest_squeeze_ladder_evidence import LadderEvidenceRows
    context, policy, assignment, decision = source()
    financial = StrategyOneFinancialView(assignment.assignment_id, 'DU1', TICKER,
        AssignmentStatus.WATCHING, policy.permissions, 0., False, False, False, 0)
    admission = admit_ladder_proposal(decision, financial, session_date=context.session_date, groups=())
    request = AutomaticLadderRequest(admission, context, assignment.assignment_id, policy)
    previous = '00000000-0000-4000-8000-000000000054'
    batch = strategy_intent_batch(request.intent, run_id=RUN,
        run_month=context.session_date.replace(day=1), account_id='DU1', attempt_id=RUN,
        batch_id='00000000-0000-4000-8000-000000000056', prior_batch_id=previous,
        sequence=2, source_cursor='boundary', run_status='running', recorded_at=request.intent.event_time,
        record_id='00000000-0000-4000-8000-000000000057')
    unit = V4AutomaticLadderBatch.from_request(batch, request)
    prefix = V4CommittedPrefix(RUN, 1, previous,
        'boundary', 'running', (previous,))
    families = unit.prepare_families(verified_prior_prefix=prefix, native_financial=financial)
    assert [len(rows) for _, rows in families] == [1, 3]
    altered = LadderEvidenceRows(dict(unit.evidence.setup),
        ({**unit.evidence.targets[0], 'price_bits':0}, *unit.evidence.targets[1:]))
    with pytest.raises(ValueError):
        replace(unit, evidence=altered)
    with pytest.raises(ValueError):
        replace(unit, base=replace(batch, intents=({**batch.intents[0], 'account_id':'OTHER'},)))


def test_native_projection_keeps_ladder_source_unit_isolated_and_never_drops_companion():
    from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderRequest
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.backend.backtest_typed_publisher import _coalesce_v4_units
    from src.trading_runtime.automatic_ladder_transport import V4AutomaticLadderBatch
    context, policy, assignment, decision = source()
    financial = StrategyOneFinancialView(assignment.assignment_id, 'DU1', TICKER,
        AssignmentStatus.WATCHING, policy.permissions, 0., False, False, False, 0)
    admission = admit_ladder_proposal(decision, financial, session_date=context.session_date, groups=())
    request = AutomaticLadderRequest(admission, context, assignment.assignment_id, policy)
    journal = BacktestMemoryJournal(run_id=RUN)
    record = journal.append_automatic_entry(request=request, account_id='DU1',
        strategy_id='prepared-native-ladder', strategy_revision=1)
    units = project_pending_backtest_v4_prefix(journal, attempt_id=RUN,
        run_month=context.session_date.replace(day=1), prior_sequence=0, through_sequence=1,
        expected_config={'strategy_id':'prepared-native-ladder', 'strategy_revision':1})
    assert len(units) == 1 and type(units[0]) is V4AutomaticLadderBatch
    assert _coalesce_v4_units(units) == units
    journal._automatic_entries.pop(record.record_id)
    with pytest.raises(RuntimeError, match='lacks its normalized source companion'):
        project_pending_backtest_v4_prefix(journal, attempt_id=RUN,
            run_month=context.session_date.replace(day=1), prior_sequence=0, through_sequence=1,
            expected_config={'strategy_id':'prepared-native-ladder', 'strategy_revision':1})
    journal.close()


def test_cold_readback_requires_bound_source_and_writer_rejects_unbound_reader():
    from src.trading_runtime.automatic_ladder_transport import verify_cold_automatic_ladder_families
    from src.trading_runtime.arte_squeeze_ladder_schema import SETUP, TARGET
    from src.trading_runtime.arte_journal_writer import ArteJournalWriter
    with pytest.raises(ValueError, match='independently bound source scope'):
        verify_cold_automatic_ladder_families(object(), related_rows={
            SETUP.name: ({'ticker':TICKER},), TARGET.name: ({}, {}, {})},
            run_id=RUN, batch_id=RUN, prior_batch_id=RUN, verified_prior_prefix=None, sources=())
    writer = object.__new__(ArteJournalWriter)
    writer._journal_profile = 'backtest_v4'
    with pytest.raises(ValueError, match='dedicated native source reader'):
        writer.submit_automatic_ladder_v4(object())
    from src.trading_runtime.arte_journal_commit_v4 import _publish_sealed_batch_v4
    with pytest.raises(ValueError, match='cannot bypass'):
        _publish_sealed_batch_v4(SimpleNamespace(typed_insert_dispatch=object()),
            object(), (), ((SETUP.name, ({},)),))
