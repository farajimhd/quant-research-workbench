"""Own manager output and shared actor; installed approval is a named test seam."""
import asyncio
from dataclasses import replace
from datetime import date
from hashlib import sha256
from types import SimpleNamespace

import pytest

from test_backtest_declared_native_fixed_management import parent
from test_declared_native_management_command import exit_command, protection_command
from test_declared_native_fixed_candidate import candidate
from test_declared_native_submission import runtime
from src.trading_runtime.declared_native_execution import DeclaredNativeExecutionSpec
from src.trading_runtime.declared_native_managed_execution import DeclaredNativeManagedExecutionSpec
from src.trading_runtime.declared_native_entry_source import DeclaredNativeEntrySourcePolicy
from src.trading_runtime.declared_native_entry_request import inherited_fixed_entry_request_policy
from src.trading_runtime.declared_native_management_request import inherited_fixed_management_request_policy
from src.trading_runtime.declared_native_fixed_capabilities import _json
from src.trading_runtime.declared_native_submission import (
    DeclaredSubmissionBinding, DeclaredNativeSubmission, declared_entry_intent,
)
from src.trading_runtime import declared_native_management_submission as module
from src.backend.backtest_declared_native_fixed_assignments import DeclaredAssignmentPolicy
from src.backend.backtest_declared_native_journal import DeclaredNativeJournal
from src.trading_runtime.signals import StrategyEvaluation


@pytest.fixture
def manager(parent, monkeypatch):
    import test_backtest_declared_native_fixed_management as fixture
    loader = fixture.load_declared_entry_source_plan
    spec = candidate((parent.capabilities,))
    monkeypatch.setattr(fixture, 'load_declared_entry_source_plan',
        lambda plan, **kwargs: loader(plan, candidate=spec, **kwargs))
    return fixture.manager.__wrapped__(parent)


def transport(bundle, command):
    prep = command.context.preparation
    execution = DeclaredNativeExecutionSpec(prep.source.candidate,
        DeclaredNativeEntrySourcePolicy('declared-entry-spread-risk-quote-source@2'),
        DeclaredAssignmentPolicy(), inherited_fixed_entry_request_policy())
    spec = DeclaredNativeManagedExecutionSpec(execution, inherited_fixed_management_request_policy())
    binding = DeclaredSubmissionBinding(prep, command.context.session_date, execution.entry_request,
        'a'*64, sha256(_json(execution.payload()).encode()).hexdigest())
    origin = DeclaredNativeSubmission(binding, command.context.source, bundle[5],
        declared_entry_intent(binding, command.context.source))
    return SimpleNamespace(bundle=bundle, origin=origin, spec=spec, binding=binding,
        submission=module.declared_management_submission(binding, spec, command))


@pytest.fixture
def management(manager):
    return transport(manager, asyncio.run(exit_command(manager)))


def append_origin(journal, origin):
    return journal.append_declared_native_intent(submission=origin, intent=origin.intent,
        account_id=origin.account_id, strategy_id=origin.binding.identity.strategy_id,
        strategy_revision=origin.binding.identity.revision)


def append_management(journal, submission):
    return journal.append_declared_native_management(submission=submission,
        account_id=submission.account_id, strategy_id=submission.binding.identity.strategy_id,
        strategy_revision=submission.binding.identity.revision)


def test_actual_replayed_exit_transport_keeps_original_request_and_scope(management):
    x = management
    assert x.submission.intents[0].action == 'exit'
    assert x.submission.intents[0].quantity == x.submission.command.context.financial.position_quantity
    assert x.submission.account_id == x.origin.account_id
    assert x.submission.assignment_id == x.origin.assignment_id
    assert x.submission.binding.configuration_hash == x.origin.binding.configuration_hash
    assert x.submission.spec == x.spec


@pytest.mark.parametrize('change', ['missing', 'spread'])
def test_complete_candidate_cannot_disagree_with_actual_preparation(parent, change):
    import test_backtest_declared_native_fixed_management as fixture
    bundle = fixture.manager.__wrapped__(parent)
    command = asyncio.run(exit_command(bundle))
    prep = command.context.preparation
    selected = candidate((parent.capabilities,), spread=change == 'spread')
    execution = DeclaredNativeExecutionSpec(selected,
        DeclaredNativeEntrySourcePolicy('declared-entry-spread-risk-quote-source@2'),
        DeclaredAssignmentPolicy(), inherited_fixed_entry_request_policy())
    spec = DeclaredNativeManagedExecutionSpec(execution, inherited_fixed_management_request_policy())
    binding = DeclaredSubmissionBinding(prep, command.context.session_date, execution.entry_request,
        'a'*64, sha256(_json(execution.payload()).encode()).hexdigest())
    with pytest.raises(ValueError, match='complete prepared source candidate'):
        module.declared_management_submission(binding, spec, command)


def test_existing_source_candidate_cannot_be_rebound_to_foreign_spread(management):
    x = management
    selected = candidate((x.binding.preparation.source.parent.capabilities,), spread=True)
    execution = replace(x.spec.execution, candidate=selected)
    spec = replace(x.spec, execution=execution)
    binding = replace(x.binding, execution_spec_token=sha256(_json(execution.payload()).encode()).hexdigest())
    with pytest.raises(ValueError, match='complete prepared source candidate'):
        module.declared_management_submission(binding, spec, x.submission.command)


def test_production_gate_remains_closed_before_journal_or_cash(management):
    x = management; rt = runtime(x.origin)
    rt.last_event_time = x.submission.event_time
    with pytest.raises(RuntimeError, match='durable V4 recovery'):
        asyncio.run(rt.submit_declared_management_submission(x.submission))
    assert rt.journal.records(rt.run_id) == []


@pytest.mark.parametrize('change', ['account', 'clock', 'intent', 'quantity_alias', 'legacy', 'entry', 'journal'])
def test_exclusive_management_channel_rejects_before_installed_hook(management, monkeypatch, change):
    x = management; rt = runtime(x.origin); rt.last_event_time = x.submission.event_time
    calls = []
    monkeypatch.setattr(module, 'require_installed_management_binding', lambda _: calls.append('gate'))
    account = x.submission.account_id
    intents = x.submission.intents
    args = dict(declared_management=x.submission)
    if change == 'account': account = 'foreign'
    elif change == 'clock': rt.last_event_time = x.origin.intent.event_time
    elif change == 'intent': intents = (replace(intents[0], quantity=intents[0].quantity+1),)
    elif change == 'quantity_alias': intents = (replace(intents[0], quantity=int(intents[0].quantity)),)
    elif change == 'legacy': args['numbered_exit_assignment_id'] = x.submission.assignment_id
    elif change == 'entry': args['declared_submission'] = x.origin
    elif change == 'journal': rt.journal = object()
    with pytest.raises(ValueError):
        asyncio.run(rt._execute_intents(StrategyEvaluation(intents=intents), account, None, **args))
    assert calls == []


@pytest.mark.parametrize('change', ['intent', 'quantity_alias', 'core_token', 'binding', 'revision_alias'])
def test_transport_rejects_mutation_of_declared_request_or_prepared_scope(management, change):
    x = management
    value = x.submission
    if change == 'intent': value = replace(value, intents=(replace(value.intents[0], reason='foreign'),))
    elif change == 'quantity_alias': value = replace(value, intents=(replace(value.intents[0], quantity=int(value.intents[0].quantity)),))
    elif change == 'core_token': value = replace(value, binding=replace(value.binding, execution_spec_token='f'*64))
    elif change == 'binding': value = replace(value, binding=replace(value.binding,
        preparation=replace(value.binding.preparation)))
    identity = value.binding.identity
    with pytest.raises(ValueError):
        value.verify(run_id=value.binding.preparation.run_id, strategy_id=identity.strategy_id,
            strategy_revision=float(identity.revision) if change == 'revision_alias' else identity.revision,
            account_id=value.account_id, session_date=value.binding.session_date)


def test_original_journal_entry_is_required_and_exact_retry_survives_fence(management):
    x = management
    journal = DeclaredNativeJournal(binding=x.binding, run_id=x.origin.proposal.run_id)
    with pytest.raises(ValueError, match='original own journal entry'):
        append_management(journal, x.submission)
    assert journal.records(journal.run_id) == []
    entry = append_origin(journal, x.origin)
    journal.mark_fenced(entry.sequence)
    record, = append_management(journal, x.submission)
    assert journal.declared_management_for_record(record.record_id) == x.submission
    assert append_management(journal, x.submission) == (record,)
    journal.mark_fenced(record.sequence)
    assert journal.declared_management_for_record(record.record_id) is None
    assert append_management(journal, x.submission) == (record,)
    assert journal.pending_record_count == 0
    journal.close()
    assert not journal._declared_management_records and not journal._declared_management_intents


def test_protection_companion_buffer_failure_has_no_partial_source_index(manager):
    x = transport(manager, asyncio.run(protection_command(manager)))
    assert tuple(intent.action for intent in x.submission.intents) == ('replace_protective_stop',)
    assert x.spec.execution.candidate.base.payload()['inherited']['flags']['allows_target_escalation'] is False
    journal = DeclaredNativeJournal(binding=x.binding, run_id=x.origin.proposal.run_id, max_pending_records=1)
    entry = append_origin(journal, x.origin)
    with pytest.raises(RuntimeError, match='buffer is full'):
        append_management(journal, x.submission)
    assert journal.records(journal.run_id) == [entry]
    assert not journal._declared_management_records and not journal._declared_management_intents
    journal.mark_fenced(entry.sequence)
    records = append_management(journal, x.submission)
    assert tuple(record.payload['action'] for record in records) == ('replace_protective_stop',)
    assert records[0].sequence == entry.sequence+1
    assert append_management(journal, x.submission) == records


@pytest.mark.parametrize('kind', ['exit', 'protection'])
def test_actual_shared_management_keeps_cash_until_fill_and_applies_stop_ack(manager, monkeypatch, kind):
    from datetime import timedelta
    from test_simulated_broker_liquidity_bar import bar
    from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
    from src.trading_runtime.order_management import OrderManagementEngine
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.risk import RiskAuthority
    from src.trading_runtime.domain import TradingMode, InstrumentContract
    from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
    from src.trading_runtime import declared_native_submission as entry_module

    async def run():
        command = await (exit_command(manager) if kind == 'exit' else protection_command(manager))
        x = transport(manager,command); origin = x.origin; account = origin.account_id; p = origin.proposal
        rt = runtime(origin)
        # TEST-only installed source seams; actual Portfolio, broker and OMS below.
        monkeypatch.setattr(entry_module, 'require_installed_submission_binding', lambda b: b.__post_init__())
        monkeypatch.setattr(module, 'require_installed_management_binding', lambda s: None)
        broker = SimulatedBrokerAdapter([account], mode=TradingMode.BACKTEST,
            initial_time=origin.intent.event_time, fixed_bar_mode=True)
        await broker.initialize()
        rt.broker = broker
        at = origin.intent.event_time
        def liquidity(clock, bid, ask, *, volume=100000):
            row = bar(clock, bid=bid, ask=ask, bid_size=100000, ask_size=100000,
                low=bid, high=ask, execution_volume=volume)
            row['ticker'] = p.ticker
            if not volume:
                row.update(price_valid=0, extremes_valid=0, low_int=0, high_int=0, close_int=0,
                    bid_size=0, ask_size=0)
            return row
        await broker.on_liquidity_bar(liquidity(at, p.reference_ask-.0001, p.reference_ask), at=at)
        policy = PortfolioPolicy(policy_id='unregistered-management-actor-fixture', allow_outside_rth=True,
            maximum_position_fraction=1., maximum_ticker_fraction=1., maximum_strategy_fraction=1.,
            maximum_planned_risk_fraction=1., maximum_open_risk_fraction=1.)
        rt.portfolio = PortfolioManagementEngine((PortfolioAccountProfile('primary',account,'backtest','cash',policy),),
            journal=rt.journal, run_id=rt.run_id, strategy_id=rt.config.strategy_id,
            strategy_revision=rt.config.strategy_revision, event_clock=lambda:rt.last_event_time)
        async def refresh(**kwargs):
            rt.portfolio.synchronize_snapshot(account, summary=await broker.account_summary(account),
                ledger=await broker.account_ledger(account), positions=await broker.positions(account))
        rt._refresh_portfolio_from_broker = refresh
        rt._synchronize_portfolio_broker = refresh
        risk = RiskAuthority(); await risk.prime(broker, [account])
        instrument = InstrumentContract(p.ticker, 123, p.ticker, 'STK', 'USD')
        planner = RuntimeIbkrStrategyOrderPlanner({p.ticker:instrument},
            strategy_id=rt.config.strategy_id, strategy_revision=rt.config.strategy_revision, limit_offset_bps=0.)
        rt.order_manager = OrderManagementEngine(broker=broker,
            planner=lambda i,a,e:planner.plan(intent=i,account_id=a,event=e), risk=risk, journal=rt.journal,
            run_id=rt.run_id, strategy_id=rt.config.strategy_id, strategy_revision=rt.config.strategy_revision,
            causal_execution_clock=True)
        rt._canonical_session = None; rt._review_only = False
        try:
            entry_result = await rt.submit_declared_submission(origin)
            assert entry_result[0]['decision']['status'] in {'approved','resized'}
            fill_at = at+timedelta(milliseconds=100)
            await broker.on_liquidity_bar(liquidity(fill_at,p.reference_ask-.0001,p.reference_ask),at=fill_at)
            await rt.order_manager.reconcile()
            quantity = broker.position_quantity(account,123,p.ticker)
            assert quantity > 0
            old = x.submission.command
            financial = replace(old.context.financial, position_quantity=quantity)
            if kind == 'exit':
                command = replace(old, context=replace(old.context,financial=financial),
                    inputs=replace(old.inputs,completed=replace(old.inputs.completed,position_quantity=quantity)))
            else:
                command = replace(old, context=replace(old.context,financial=financial),
                    exit_inputs=replace(old.exit_inputs,completed=replace(old.exit_inputs.completed,position_quantity=quantity)))
            submission = module.declared_management_submission(x.binding,x.spec,command)
            rt.last_event_time = submission.event_time
            bid, ask = ((command.inputs.completed.bid,command.inputs.completed.ask) if kind=='exit'
                else (command.inputs.bid,command.inputs.ask))
            await broker.on_liquidity_bar(liquidity(rt.last_event_time,bid,ask,volume=0),at=rt.last_event_time)
            await rt.order_manager.reconcile()
            await refresh()
            await risk.prime(broker,[account])
            cash_before = (await broker.account_ledger(account)).cashbalance
            result = await rt.submit_declared_management_submission(submission)
            assert result[0]['decision']['status'] in {'approved','resized'}
            own = [r for r in rt.journal.unfenced_records() if r.entity_type=='declared_native_management_intent']
            assert len(own)==1 and rt.journal.declared_management_for_record(own[0].record_id)==submission
            assert broker.position_quantity(account,123,p.ticker)==quantity
            assert (await broker.account_ledger(account)).cashbalance==cash_before
            if kind == 'protection':
                stop = submission.intents[0].invalidation_price
                orders = await broker.live_orders()
                active = [row for row in orders if row.ticker==p.ticker and row.orderType=='STP'
                    and row.remainingQuantity>0 and row.auxPrice==stop]
                assert len(active)==1 and active[0].remainingQuantity==quantity
                return
            exit_at = rt.last_event_time+timedelta(milliseconds=100)
            await broker.on_liquidity_bar(liquidity(exit_at,bid,ask),at=exit_at)
            await rt.order_manager.reconcile()
            assert broker.position_quantity(account,123,p.ticker)==0
            assert (await broker.account_ledger(account)).cashbalance>cash_before
        finally:
            await rt.order_manager.close()
            rt.journal.close()
    asyncio.run(run())
