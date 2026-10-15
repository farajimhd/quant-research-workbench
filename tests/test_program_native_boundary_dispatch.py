"""Concrete factory and source path with isolated native receipt transports.

The financial head uses actual normalized snapshots/source seals. Transport
acknowledgements and Keeper receipt reads are in-memory, never external I/O.
"""
import asyncio
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import polars as pl
import pytest

from tests.test_columnar_history_publication import prepared_source, Store, publish, consume


@pytest.mark.parametrize('case', ['wait', 'ambiguous', 'entry', 'future_head'])
def test_native_factory_qualifies_selection_before_any_external_execution(
        prepared_source, monkeypatch, case):
    uncertain_selection_receipt = case == 'ambiguous'
    from tests.test_program_portfolio_transitions import setup
    from src.trading_runtime import program_portfolio_transitions as transitions
    from src.trading_runtime.arte_portfolio_sync import KeeperSyncAttestation
    from src.trading_runtime import strategy_registry as registry
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from tests.test_live_signal_completion_keeper import FakeKazoo
    from src.backend.program_v4_principal import ProgramV4KeeperLease, ProgramV4WriterClient
    from tests.test_live_strategy_one_v4_principal import Client
    from src.backend.program_execution_journal import ProgramExecutionJournal
    from src.backend.program_runtime_assembly import build_program_runtime
    from src.trading_runtime.runtime import RunConfig, RunMode
    from src.trading_runtime.domain import TradingMode
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    from tests.test_program_controller_runtime import definition_and_actor
    from tests.test_backtest_program_inputs import release_for
    from src.backend.backtest_program_inputs import prepare_program_block, prepare_program_scanner_projection
    from src.backend.qmd_columnar_population import CertifiedPopulationWindow
    from src.market_engine import columnar_history_product as c
    from src.market_engine.volume_rank_rules import select_volume_universe
    from tests.test_qmd_columnar_history_transport import response_for
    from tests.test_signal_source_runtime_bootstrap import source
    from tests.test_program_signal_source import executable_contextual_publisher
    from src.backend.signal_execution_context import SignalExecutionContext
    from src.backend.signal_stream_runtime_service import SignalStreamRuntime
    from src.backend.certified_program_configuration import CertifiedProgramConfiguration
    from tests.test_strategy_configuration_selection import prepare
    from src.backend.program_boundary_dispatch import dispatch_program_boundary
    from src.trading_runtime.execution_policies import (
        ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule)
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch

    storage = Store()
    publish(storage, prepared_source)
    certificate = consume(storage, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
    from datetime import datetime, timezone, timedelta
    from dataclasses import asdict
    from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient, _state
    from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
    from src.trading_runtime.portfolio import profiles_for_runtime
    from src.trading_runtime import arte_portfolio_recovery as recovery
    financial_at = datetime.combine(pin.scope.session_date, datetime.min.time(), tzinfo=timezone.utc)
    if case == 'future_head':
        financial_at += timedelta(days=1)
    clock = [financial_at]
    def empty_account_client(patch):
        client = SnapshotClient()
        patch.setattr(recovery, 'load_typed_run_context', lambda *_: {'account_ids': ('account-id',)})
        state = _state()
        state.update(control_mode='entries_paused', observed_at=financial_at,
            disabled_strategy_allocations=[], pending_operational_commands=[],
            pending_entry_requests={}, reservations=[], allocations=[], reconciliation=[])
        publish_portfolio_snapshot(client, run_id='live-run', account_id='account-id',
            state_revision=7, snapshot_at=financial_at, state=state)
        profile = profiles_for_runtime(('account-id',), mode='live')[0]
        return client, replace(profile, account_key='account-key')
    monkeypatch.setattr('tests.test_program_portfolio_transitions._client', empty_account_client)
    monkeypatch.setattr('tests.test_program_portfolio_transitions.AT', financial_at)
    portfolio, authority, prior = setup(monkeypatch)
    portfolio._event_clock = lambda: clock[0]
    from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
    campaign_client = FakeKazoo()
    original_get = campaign_client.get
    def get_with_clock(path):
        wire, stat = original_get(path)
        return wire, SimpleNamespace(version=stat.version, mtime=int(financial_at.timestamp() * 1000))
    campaign_client.get = get_with_clock
    campaign_owner = KeeperOwnershipCoordinator(campaign_client)
    for method in ('acquire_campaign_session_ownership', 'campaign_session_ownership',
                   'release_campaign_session_reservation'):
        setattr(authority.coordinator, method, getattr(campaign_owner, method))
    # First produce a real coherent normalized current image. The fixture's
    # transport records the exact source units and snapshot acknowledgement.
    asyncio.run(transitions.apply_program_portfolio_transition(portfolio, authority,
        account_id='account-id', prior_revision=7, prior_hash=prior,
        transition='set_control', arguments={'mode': 'enabled', 'reason': 'explicit enable'}))
    source_ids = tuple(unit.base.batch_id if hasattr(unit, 'base') else unit.batch_id
                       for unit in authority.writer.source_units)
    def proof(run, account, revision):
        attested = next(args for args in reversed(authority.coordinator.proofs)
                        if args[1:4] == (run, account, revision))
        return KeeperSyncAttestation(attested[1], attested[2], attested[3],
            attested[4], attested[5], 'test-owner', 1)
    authority.coordinator.load_portfolio_snapshot_receipt = proof
    head_prefix = replace(authority.writer.prefix, batch_ids=source_ids)
    prefixes = {head_prefix.run_id: head_prefix}
    monkeypatch.setattr('src.trading_runtime.arte_journal_commit_v4.load_verified_v4_prefix',
                        lambda _, run: prefixes.get(run))
    monkeypatch.setattr('src.trading_runtime.program_portfolio_journal.load_verified_v4_prefix',
                        lambda _, run: prefixes.get(run))

    registration = registry.ProgramStrategyExecutorRegistration(release.header['strategy_id'],
        release.header['strategy_number'], release.header['content_hash'], release.header['code_hash'],
        release.header['source_winner_hash'])
    monkeypatch.setattr(registry, '_PROGRAM_REGISTRY', {})
    registry.register_program_strategy_executor(registration)
    selected = prepare(strategy_id=registration.strategy_id, strategy_revision=registration.revision,
        program_release_id=release.header['release_id'], program_release_hash=release.header['content_hash'])
    session_client = FakeKazoo()
    session_client.add_listener = lambda _: None
    session = ManagedKeeperSession(session_client)
    session._on_state('CONNECTED')
    lease = ProgramV4KeeperLease.acquire(session, run_id='actual-native-replay-run', owner_id='test-worker',
        registration=registration, configuration_hash=selected.header['content_hash'])
    writer = authority.writer
    writer._journal_profile, writer._run_mode, writer._run_id = 'live_v4', 'replay', lease.run_id
    writer._client = ProgramV4WriterClient(Client(user='program_v4_runner'), lease)
    writer._live_v4_lease = lease
    writer._accepted_writes, writer._closed, writer._error = False, False, None
    monkeypatch.setattr('src.trading_runtime.arte_journal_commit_v4.load_writer_v4_snapshot_prefix',
                        lambda _, run: prefixes.get(run))
    acknowledged = []
    def submit(unit):
        future = Future()
        base = unit.base if hasattr(unit, 'base') else unit
        if type(unit) is QualifiedRuleDecisionBatch and uncertain_selection_receipt:
            future.set_exception(RuntimeError('uncertain selection receipt'))
        else:
            acknowledged.append(unit)
            from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
            before = prefixes.get(base.run_id)
            prefixes[base.run_id] = V4CommittedPrefix(base.run_id, base.last_sequence, base.batch_id,
                base.source_cursor, base.status, (*before.batch_ids, base.batch_id) if before else (base.batch_id,))
            future.set_result(base.batch_id)
        return future
    writer.submit_base_v4 = submit
    writer.submit_qualified_rule_v4 = submit
    for method in ('submit_portfolio_control_v4', 'submit_portfolio_allocation_v4',
                   'submit_reservation_reason_v4', 'submit_exact_oms_v4', 'submit_exact_order_v4'):
        setattr(writer, method, submit)
    writer.submit_protection_change_v4 = writer.submit_broker_acknowledgement_v4 = submit
    _, original = definition_and_actor()
    assignment = replace(original.assignments()[0], account_id='account-id')
    actor = AssignedProgramStrategy(release, (assignment,), {'assignment': 'listing'})
    configuration, scope, _ = source()
    scope['session_key'] = pin.scope.session_date.isoformat()
    context = SignalExecutionContext(lease.run_id, 'replay', scope['session_key'],
        scope['configuration_revision'], scope['source_revision'])
    publisher = executable_contextual_publisher(context, scope['catalogs'])
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
    broker = SimulatedBrokerAdapter(['account-id'], SimulationConfig(liquidity_resolution_ms=1000),
        initial_time=datetime.combine(pin.scope.session_date, datetime.min.time(), tzinfo=timezone.utc))
    broker.mode = TradingMode.REPLAY
    broker_calls = []
    actual_place = broker.place_orders
    async def place(*args, **kwargs):
        broker_calls.append((args, kwargs))
        assert any(type(unit) is QualifiedRuleDecisionBatch for unit in acknowledged)
        from src.trading_runtime.arte_order_exact_prices import ExactPriceOrderCommandBatch
        from src.trading_runtime.arte_oms_exact_prices import ExactPriceOmsStateBatch
        assert any(type(unit) is ExactPriceOrderCommandBatch for unit in acknowledged)
        assert any(type(unit) is ExactPriceOmsStateBatch for unit in acknowledged)
        return await actual_place(*args, **kwargs)
    broker.place_orders = place

    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        portfolio.run_id = lease.run_id
        from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner, InstrumentContract
        runtime = build_program_runtime(config=RunConfig(RunMode.REPLAY, *registration.key,
            ('account-id',), pin.scope.session_date, run_id=lease.run_id, write_progress_checkpoints=False),
            strategy=actor, journal=journal, portfolio=portfolio, portfolio_authority=authority,
            broker=broker, intent_planner=RuntimeIbkrStrategyOrderPlanner(
                {'ABC': InstrumentContract('ibkr:1', 1, 'ABC', 'STK', 'USD')},
                strategy_id=registration.strategy_id, strategy_revision=registration.revision, run_id=lease.run_id),
            attempt_id=authority.record_journal.attempt_id)
        await broker.initialize()
        signal_runtime = SignalStreamRuntime()
        await publisher.bootstrap_runtime_session_async(signal_runtime, **scope)
        runtime._program_execution_assembly.require_source_context(context,
            certified_configuration=CertifiedProgramConfiguration(selected, release,
                {'revision_id': context.configuration_revision}), signal_runtime=signal_runtime)
        def post(path, request, timeout):
            return response_for(certificate, request['first_ordinal'], request['stop_ordinal'], request['field_ids'])
        block = await prepare_program_block(release, {'listing': pin}, first_ordinal=0, stop_ordinal=200,
            evaluation_block='optimizer-block', concurrency=2,
            as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), history_post=post)
        scanner = block.market.with_columns(pl.lit('ABC').alias('ticker'),
            (pl.col('boundary_at') - pl.duration(days=1)).alias('prior_close_available_at'),
            pl.lit(10.).alias('adjusted_prior_close'), pl.lit(30).alias('volume_window_seconds'))
        population = CertifiedPopulationWindow(scope['source_revision'], scanner,
            select_volume_universe(scanner, release.universe), 1, 1, 0, 0, 0)
        projection = prepare_program_scanner_projection(block, population, release)
        candidates = block.masks.with_columns(block.market['history.swing_low.2.1__valid'],
            block.market['reference_close'])
        ordinal = candidates.filter((pl.col('entry') if case != 'wait' else ~pl.col('entry'))
            & pl.col('history.swing_low.2.1__valid') & pl.col('reference_close').is_not_null())['observed_ordinal'][0]
        at = block.row_for('listing', ordinal)[0]['boundary_at']
        clock[0] = at
        from tests.test_broker_liquidity_resolution import second_bar
        row = block.row_for('listing', ordinal)[0]
        liquidity = second_bar(at)
        price = round(row['reference_close'] * 10000)
        liquidity.update(ticker='ABC', ask_int=price, bid_int=price - 1,
            close_int=price, high_int=price + 1, low_int=price - 1)
        await runtime.process_liquidity_boundary((liquidity,), at=at)
        owner = SimpleNamespace(_runtime=runtime, _journal=journal, run_id=runtime.run_id,
            _program_strategy=actor, _program_signal_runtime=signal_runtime, _program_dispatch_failure=None)
        stage = signal_runtime.stage_resolve(configuration, projection.frame_for(at).to_dicts(),
            as_of=at, data_fields_projected=False)
        cursor = signal_runtime.typed_source_cursor()
        pending = signal_runtime.submit_staged(stage, publisher, batch_sequence=cursor[0] + 1,
            previous_commit_hash=cursor[1], configuration_revision=context.configuration_revision,
            source_revision=context.source_revision, catalogs=scope['catalogs'], allow_clock_only=True)
        await pending.wait_async()
        signal_runtime.promote_staged(pending)
        kwargs = dict(ordinal=ordinal, assignment_id='assignment',
            execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=2000)),
            protection_profile=ProtectionProfile('selected-stop', 1, (ProtectionSlice('one', 1.,
                StopRule(price=block.row_for('listing', ordinal)[0]['history.swing_low.2.1'])),)),
            outside_rth=False)
        if case == 'future_head':
            with pytest.raises(RuntimeError, match='later than the completed decision boundary'):
                await dispatch_program_boundary(owner, block, population, **kwargs)
            assert not acknowledged and journal.pending_record_count == 0
        elif uncertain_selection_receipt:
            with pytest.raises(RuntimeError, match='uncertain selection receipt'):
                await dispatch_program_boundary(owner, block, population, **kwargs)
            assert owner._program_dispatch_failure is not None
            assert not runtime._program_execution_assembly._decision_intents
        else:
            result = await dispatch_program_boundary(owner, block, population, **kwargs)
            qualified = [unit for unit in acknowledged if type(unit) is QualifiedRuleDecisionBatch]
            assert len(qualified) == 1 and qualified[0].unit.basis['basis_sequence'] == 0
            assert journal.pending_record_count == 0
            if case == 'entry':
                callbacks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()
                    and getattr(task.get_coro(), '__qualname__', '').endswith('TradingRuntime._on_order_group_state')]
                if callbacks:
                    await asyncio.wait_for(asyncio.gather(*callbacks), timeout=5)
                assert broker_calls and result['results'][0]['order_group'] is not None
                assert await broker.live_orders()
                assert not actor.position_for('assignment').position_open
                semantic = [row for unit in acknowledged for row in
                    (unit.base if hasattr(unit, 'base') else unit).intents]
                assert len(semantic) == 1
            else:
                assert result['results'] == [] and journal.fenced_sequence == 1
        if case != 'entry':
            assert not broker_calls
        await runtime.close_background_tasks()
    try:
        asyncio.run(run())
    finally:
        publisher.close()
        lease.release()

