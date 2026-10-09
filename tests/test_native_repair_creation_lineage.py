"""Actual actor regression; producer/configuration and SQL transports are fixtures.

Uses the established partial-fill/cold-recovery setup, then publishes a real
stop amendment and reconstructs its complete cold order lineage. No financial
or production-database certification claim comes from this test.
"""
import asyncio
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from src.trading_runtime.runtime import TradingRuntime


def prepared_operation(monkeypatch):
    from test_fixed_structural_lot_configuration_routing import source_fixture
    import test_fixed_structural_lot_native as base_fixture
    from test_fixed_structural_lot_checkpoint_reader_profile import published
    from src.trading_runtime import fixed_structural_lot_release_v20 as compiler
    from src.trading_runtime.strategy_one_hundred_five_release import derive_strategy_one_hundred_five_configuration
    from src.trading_runtime.strategy_registry import numbered_strategy
    parent = source_fixture()
    monkeypatch.setattr(base_fixture, 'declarations', lambda: (parent, None, None, numbered_strategy(42)))
    original = compiler.derive_fixed_structural_lot_release
    def derive(actual_parent, **options):
        monkeypatch.setattr(compiler, 'derive_fixed_structural_lot_release', original)
        return derive_strategy_one_hundred_five_configuration(actual_parent,
            approved_code_commit=options['approved_code_commit'],
            approved_code_fingerprint=options['approved_code_fingerprint'],
            approval_reference=options['approval_reference'])
    monkeypatch.setattr(compiler, 'derive_fixed_structural_lot_release', derive)
    return published(monkeypatch, actual_loader=False, exclusive_writer=True, number=105, version=20)


def test_native_partial_repair_amendment_publication_and_cold_recovery(monkeypatch):
    quote_offset_us = 0

    async def exercise():
        from datetime import timedelta
        from time import perf_counter
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
        from src.trading_runtime.domain import TradingMode, InstrumentContract
        from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
        from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
        from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
        from src.trading_runtime import arte_journal_writer as writer_module
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from src.trading_runtime.fixed_structural_lot_snapshot import project_fixed_structural_lot_snapshot, restore_fixed_structural_lot_snapshot
        from src.backend.backtest_market_data import market_day_boundary
        actual, request, plans, config, client, flat = prepared_operation(monkeypatch)
        entry = request.entry.proposal
        at = request.intent.event_time
        journal = BacktestMemoryJournal(run_id=config.run_id)
        broker = SimulatedBrokerAdapter(config.account_ids, SimulationConfig(initial_cash=10000.0), mode=TradingMode.BACKTEST, initial_time=at, fixed_bar_mode=True)
        profile = PortfolioAccountProfile('cash', entry.account_id, 'backtest', 'simulated', PortfolioPolicy(allow_outside_rth=True))
        portfolio = PortfolioManagementEngine((profile,), journal=journal, run_id=config.run_id, strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, event_clock=lambda: at)
        planner = RuntimeIbkrStrategyOrderPlanner({entry.ticker: InstrumentContract(entry.ticker, 1, entry.ticker, 'STK', 'USD')}, strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, run_id=config.run_id)
        runtime = TradingRuntime(config, broker, SimpleNamespace(strategy_id=config.strategy_id, revision=config.strategy_revision, automatic=True), journal, portfolio=portfolio, intent_planner=planner)
        actual.bind_runtime(runtime)
        monkeypatch.setattr(writer_module, 'storage_preflight', lambda *a, **k: None)
        monkeypatch.setattr(writer_module, 'journal_permission_preflight', lambda *a, **k: None)
        client.fixed_structural_lot_profile = actual.profile
        writer = writer_module.ArteJournalWriter(client, run_id=config.run_id, journal_profile='backtest_v4', coalesce_batches=False)
        publisher = BacktestTypedJournalPublisher(journal, writer, attempt_id=str(uuid4()), run_month=actual.operation.source.session_date.replace(day=1), expected_config=flat)
        try:
            await runtime.initialize()
            actual.operation.bind_publisher(publisher)
            runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot(entry.ticker, 10.0, 10.01, 0.01, at, 'arte.liquidity_100ms_v1'))
            initial_quote = request.intent.event_time - timedelta(microseconds=1000)
            local = at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket = (local.hour * 3600000 + local.minute * 60000 + local.second * 1000 + local.microsecond // 1000) // 100 - 1
            initial_us = int(initial_quote.timestamp() * 1000000)
            initial_row = dict(ticker=entry.ticker, resolution_ms=100, bucket_index=bucket, event_count=1, first_event_us=initial_us, last_event_us=initial_us, quote_timestamp_us=initial_us, quote_valid=1, bid_int=100000, ask_int=100100, bid_size=10000.0, ask_size=10000.0, price_valid=1, close_int=100100, extremes_valid=1, low_int=100100, high_int=100100, execution_volume=100000.0)
            now_ms = entry.boundary_ms + 100
            at = market_day_boundary(actual.operation.source.session_date, now_ms)
            quote_time = request.intent.event_time + timedelta(microseconds=quote_offset_us)
            quote_us = int(quote_time.timestamp() * 1000000)
            end_us = int(at.timestamp() * 1000000)
            local = at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket = (local.hour * 3600000 + local.minute * 60000 + local.second * 1000 + local.microsecond // 1000) // 100 - 1
            row = dict(ticker=entry.ticker, resolution_ms=100, bucket_index=bucket, event_count=1, first_event_us=quote_us, last_event_us=quote_us, quote_timestamp_us=quote_us, quote_valid=1, bid_int=100000, ask_int=100100, bid_size=10000.0, ask_size=10000.0, price_valid=1, close_int=100100, extremes_valid=1, low_int=100100, high_int=100100, execution_volume=4.0, execution_price_levels=({'price_int': 100100, 'volume': 4.0},))
            row['ask_size'] = 4.0
            from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler, run_strategy_one_boundaries
            from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
            from src.backend.backtest_strategy_one_preparation import iter_strategy_one_entries
            initial_row.update(session_date=actual.operation.source.session_date, boundary_ms=entry.boundary_ms, indicator_resolution_ms=100)
            row.update(session_date=actual.operation.source.session_date, boundary_ms=now_ms, indicator_resolution_ms=100)
            cursor = next((cursor for cursor in iter_strategy_one_entries(plans.candidates.prepared) if cursor.ticker == entry.ticker and cursor.boundary_ms == entry.boundary_ms))
            candidates = (StrategyOneDecisionCandidate(initial_row, cursor),)
            scheduler = StrategyOneBoundaryScheduler(session_date=actual.operation.source.session_date, candidate_rows=iter(candidates), active_source=lambda ticker, after: iter(((now_ms, {100: row}),) if ticker == entry.ticker and after < now_ms else ()))
            reached = []

            class BoundedHeldBoundaryReached(Exception):
                pass

            async def process(work):
                reached.append(work.boundary_ms)
                await runtime.process_liquidity_boundary([resolutions[100] for _, resolutions in work.broker_rows], at=market_day_boundary(actual.operation.source.session_date, work.boundary_ms))

            async def evaluate(ticker, resolutions, candidate):
                if candidate is not None:
                    assert candidate.evidence.boundary_ms == entry.boundary_ms
                    results = await runtime.submit_fixed_structural_lot_request(request)
                    assert len(results) == 1 and results[0]['decision']['status'] in ('approved', 'resized')

            async def finish(work):
                if work.boundary_ms == now_ms:
                    raise BoundedHeldBoundaryReached()
            with pytest.raises(BoundedHeldBoundaryReached):
                await run_strategy_one_boundaries(scheduler, process_broker_boundary=process, evaluate_ticker=evaluate, financially_active_tickers=broker.financially_active_tickers, finish_boundary=finish)
            assert reached == [entry.boundary_ms, now_ms]
            group = next(iter(runtime.order_manager._groups.values()))
            assert group.filled_quantity > 0 and group.updated_at == quote_time
            from src.trading_runtime.arte_oms_projection import canonical_oms_order_metadata
            from src.trading_runtime.independent_lot_initial_stop_lineage import initial_metadata
            from src.trading_runtime.strategy_orders import canonical_runtime_metadata
            planned = next((v for v in journal.unfenced_records() if v.entity_type == 'order_group_state' and v.payload.get('event') == 'ladder_repair_planned'))
            frozen = journal.oms_group_for_record(planned.record_id)
            acknowledged = journal.oms_effective_protection_for_record(planned)
            repair = next((v for i, v in enumerate(frozen.orders) if i not in frozen.broker_order_request_indexes.values() and v.side == 'SELL'))
            check = dict(source=actual.operation.source, run_id=config.run_id, strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, sequence=planned.sequence, boundary=planned.event_time)
            metadata = canonical_runtime_metadata(repair, frozen.intent)
            assert 'confirmed_support_stop' not in metadata
            assert initial_metadata(frozen, repair, metadata, acknowledged, **check) == metadata
            inactive = {k: replace(v, payload={**v.payload, 'active': False}) for k, v in acknowledged.items()}
            assert initial_metadata(frozen, repair, metadata, inactive, **check) == metadata
            selected = next((v for v in acknowledged.values() if v.payload.get('kind') == 'stop' and frozen.plan.order_slice_ids[frozen.broker_order_request_indexes[v.payload['order_id']]] == frozen.plan.order_slice_ids[frozen.orders.index(repair)]))
            for change in ('account', 'run', 'strategy', 'ticker', 'group', 'intent', 'broker', 'sequence', 'time', 'price'):
                bad = replace(selected, account_id='foreign') if change == 'account' else replace(selected, run_id=str(uuid4())) if change == 'run' else replace(selected, sequence=planned.sequence) if change == 'sequence' else replace(selected, event_time=planned.event_time + timedelta(microseconds=1)) if change == 'time' else replace(selected, payload={**selected.payload, {'strategy': 'strategy_revision', 'ticker': 'ticker', 'group': 'order_group_id', 'intent': 'source_intent_id', 'broker': 'order_id', 'price': 'price'}[change]: 9.9 if change == 'price' else -1 if change == 'strategy' else 'foreign'})
                invalid = {k: bad if v.sequence == selected.sequence else v for k, v in acknowledged.items()}
                with pytest.raises(ValueError):
                    initial_metadata(frozen, repair, metadata, invalid, **check)
            import copy
            mutated = copy.deepcopy(frozen)
            mutated.intent.metadata['confirmed_support_stop'] = 9.9
            with pytest.raises(ValueError):
                canonical_oms_order_metadata(mutated, repair, acknowledged, source_sequence=planned.sequence, source_boundary=planned.event_time, source_run_id=config.run_id, fixed_lot_source=actual.operation.source, source_strategy_id=config.strategy_id, source_strategy_revision=config.strategy_revision)
            await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
            owner = NativeFixedStructuralLotManagement(operation=actual.operation, publisher=publisher, client=client)
            prefix, contexts = owner._prefix()
            args = owner._arguments(request, prefix, contexts)
            from src.trading_runtime.arte_oms_projection import load_committed_oms_group_state_page, load_committed_oms_admission_page, load_committed_oms_decision_page, reconstruct_strategy_one_oms_lineage
            from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
            from src.trading_runtime.arte_intent_projection import RecoveredIntent
            pending = next((v for v in load_committed_oms_group_state_page(client, prefix, limit=500, fixed_lot_contexts=contexts) if v.sequence == planned.sequence))
            cold_history = load_complete_typed_protection_history(client, prefix, fixed_lot_contexts=contexts)
            admissions = load_committed_oms_admission_page(client, prefix, (pending,))
            decisions = load_committed_oms_decision_page(client, prefix, (pending,), admissions)
            own_context = next((v for v in contexts if v.record.entity_id == request.intent.intent_id))
            cold_intent = RecoveredIntent(own_context.record.sequence, own_context.record.account_id, own_context.record.record_id, own_context.base.batch_id, request.intent, own_context.base)
            cold_orders = reconstruct_strategy_one_oms_lineage(pending, cold_intent, cold_history, admission_reservation=admissions[pending.sequence], admission_decision=decisions[pending.sequence], fixed_lot_source=actual.operation.source)
            assert cold_orders == frozen.orders
            if quote_offset_us:
                import ast
                from src.trading_runtime import fixed_structural_lot_management as current_reader
                frozen_path = os.environ.get('FIXED_LOT_FROZEN_ROSTER_SOURCE')
                frozen_source = Path(frozen_path).read_text(encoding='utf-8') if frozen_path else subprocess.check_output(['git', 'show', 'aeacd397325a4b2aabae68634fb90b35440dc6cc:src/trading_runtime/fixed_structural_lot_management.py']).decode('utf-8')
                old_function = next((node for node in ast.parse(frozen_source).body if isinstance(node, ast.FunctionDef) and node.name == 'load_fixed_structural_lot_stop_ceiling'))
                namespace = dict(vars(current_reader))
                exec(compile(ast.Module(body=[old_function], type_ignores=[]), 'frozen84_actual_roster_reader', 'exec'), namespace)
                with pytest.raises(ValueError, match='precedes its entry'):
                    namespace['load_fixed_structural_lot_stop_ceiling'](**args, entry=request.entry, group_id=group.group_id)
            owner.register_entry(request, group.group_id)
            with pytest.raises(ValueError, match='exceeds'):
                await owner.first_held((entry.account_id, entry.assignment_id, entry.ticker), boundary_ms=entry.boundary_ms)
            started = perf_counter()
            state = await owner.first_held((entry.account_id, entry.assignment_id, entry.ticker), boundary_ms=now_ms)
            elapsed = perf_counter() - started
            assert state.protection.boundary_ms == now_ms and now_ms % 100 == 0
            assert state.roster.observed_boundary_ms == now_ms
            prefix, contexts = owner._prefix()
            args = owner._arguments(request, prefix, contexts)
            from src.trading_runtime.arte_oms_projection import load_latest_committed_oms_groups
            first = load_latest_committed_oms_groups(client, prefix, allowed_accounts=frozenset((entry.account_id,)), fixed_lot_contexts=contexts)
            assert first and client.batched_queries
            assert any((query.startswith('SELECT family_name,payload FROM (') for query in client.batched_queries))
            before = len(client.batched_queries)
            second = load_latest_committed_oms_groups(client, prefix, allowed_accounts=frozenset((entry.account_id,)), fixed_lot_contexts=contexts)
            assert second == first and len(client.batched_queries) == before
            rows = project_fixed_structural_lot_snapshot(state, **args)
            restored = restore_fixed_structural_lot_snapshot(rows, entry=request.entry, **args)
            assert restored == state
            print('actual_native_first_held_seconds=' + str(round(elapsed, 6)))
            from src.trading_runtime.strategy_one_position import ResistanceBreak
            from test_strategy_one_position import level
            amend_boundary = (now_ms + 999) // 1000 * 1000
            amend_at = market_day_boundary(actual.operation.source.session_date, amend_boundary)
            amend_us = int(amend_at.timestamp() * 1000000) - 1000
            local = amend_at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket = (local.hour * 3600000 + local.minute * 60000 + local.second * 1000 + local.microsecond // 1000) // 100 - 1
            amend_row = dict(row, boundary_ms=amend_boundary, bucket_index=bucket, first_event_us=amend_us, last_event_us=amend_us, quote_timestamp_us=amend_us, execution_volume=0.0, execution_price_levels=(), ask_size=10000.0)
            await runtime.process_liquidity_boundary([amend_row], at=amend_at)
            publisher.enqueue_pending()
            await publisher.await_fence()
            from src.backend.backtest_strategy_one_financial import StrategyOneFinancialView
            held = sum((float(p.position) for p in await broker.positions(entry.account_id)))
            print('partial_repair_probe_held=' + str(held))
            from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
            financial = StrategyOneFinancialView(entry.assignment_id, entry.account_id, entry.ticker, AssignmentStatus.MANAGING, StrategyPermissions(), held, bool(group.remaining_quantity > 0), False, False, 1)
            command = owner.propose(request, financial, now_ms=amend_boundary, bid=10.0, ask=10.01, tick=actual.operation.source.tick, low_boundary_ms=None, low_int=None, low_price_valid=False, low_extremes_valid=False, breaks=tuple((ResistanceBreak(amend_boundary, level(i, 9.94 + i * 0.01)) for i in range(3))), overhead_levels=(), price_bearing_bar=True, allows_completed_30s_trailing=False)
            assert command.intents, 'Probe did not earn an actual stop command'
            try:
                await runtime.submit_fixed_structural_lot_protection(command)
            except RuntimeError:
                import json
                details=[]
                for group_id,client_id,broker_id,index,lot in command.requested_legs:
                    records=[record for record in journal.protection_records(config.run_id)
                             if record.payload.get('client_order_id')==client_id
                             and record.payload.get('order_id')==broker_id]
                    details.append(dict(client=client_id,broker=broker_id,index=index,lot=lot,
                        actual_aux_price=group.orders[index].auxPrice,
                        requested=[dict(intent_id=i.intent_id,price=i.invalidation_price,event_time=i.event_time.isoformat()) for i in command.intents],
                        history=[dict(sequence=r.sequence,event_time=r.event_time.isoformat(),action=r.payload.get('action'),phase=r.payload.get('phase'),price=r.payload.get('price'),intent_id=r.payload.get('intent_id')) for r in records]))
                print('native_stop_confirmation_diagnostic='+json.dumps(details))
                raise
            publisher.enqueue_pending()
            await publisher.await_fence()
            print('partial_repair_probe_stop_amendment_completed=True')
            from src.trading_runtime.arte_oms_projection import freeze_oms_group
            final_prefix, final_contexts = owner._prefix()
            assert final_prefix.last_sequence == journal._fenced_sequence
            final_group = max((v for v in load_committed_oms_group_state_page(client, final_prefix, limit=500, fixed_lot_contexts=final_contexts) if v.group['group_id'] == group.group_id), key=lambda v: v.sequence)
            final_history = load_complete_typed_protection_history(client, final_prefix, fixed_lot_contexts=final_contexts)
            final_admissions = load_committed_oms_admission_page(client, final_prefix, (final_group,))
            final_decisions = load_committed_oms_decision_page(client, final_prefix, (final_group,), final_admissions)
            final_context = next((v for v in final_contexts if v.record.entity_id == request.intent.intent_id))
            final_intent = RecoveredIntent(final_context.record.sequence, final_context.record.account_id, final_context.record.record_id, final_context.base.batch_id, request.intent, final_context.base)
            final_orders = reconstruct_strategy_one_oms_lineage(final_group, final_intent, final_history, admission_reservation=final_admissions[final_group.sequence], admission_decision=final_decisions[final_group.sequence], fixed_lot_source=actual.operation.source)
            assert final_orders == freeze_oms_group(group).orders
            assert any(('repair-target-' in o.cOID for o in final_orders))
            assert actual.operation.source.installed_payload['strategy']['strategy_number'] == 105
            return
        finally:
            if runtime.order_manager is not None:
                await runtime.order_manager.close()
            for task in (runtime._broker_stream_task, runtime._risk_refresh_task):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            writer.close()
            journal.close()
    asyncio.run(exercise())
