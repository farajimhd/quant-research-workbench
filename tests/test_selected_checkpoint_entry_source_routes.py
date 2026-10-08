"""Real selected entry publication and exit-source readers, controlled transport.

Exit witnesses are factory-built test observations. This verifies original-entry
identity/ancestry, not market certification, held-state admission or session P&L.
"""
import asyncio
import copy
import os
import subprocess
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture(scope='module')
def selected_entry():
    from src.backend import backtest_fixed_structural_lot_certification_v11 as seal
    if not seal.REVIEWED_SOURCE_AST:
        pytest.skip('Requires proposed snapshot or approved immutable source')
    patch = pytest.MonkeyPatch()
    if os.environ.get('FIXED_LOT_PROPOSED_HEAD'):
        head = os.environ['FIXED_LOT_PROPOSED_HEAD']
        original = subprocess.check_output
        patch.setattr(subprocess, 'check_output', lambda args, **kw:
            (head+'\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
            b'' if args == ['git', 'status', '--porcelain'] else original(args, **kw))

    async def publish():
        from test_fixed_structural_lot_selected_checkpoint_products import published
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.trading_runtime import arte_journal_writer as writer_api
        from src.trading_runtime.runtime import TradingRuntime
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
        from src.trading_runtime.domain import TradingMode, InstrumentContract
        from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
        from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
        from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
        from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
        actual, request, plans, config, client, flat = published(
            patch, actual_loader=True, exclusive_writer=True)
        # Memory transport must honor the real bounded ancestry SELECT.
        # Preserve all other queries and keep the production verifier intact.
        original_execute = client.execute
        def execute(sql, *, query_id=None):
            import json
            import re
            if type(sql) is not str:
                return original_execute(sql, query_id=query_id)
            match = re.fullmatch(
                r"SELECT batch_id,prior_batch_id,first_sequence,last_sequence "
                r"FROM arte\.trading_commit_v4 WHERE run_id='([^']+)' "
                r"AND first_sequence>=(\d+) AND last_sequence<=(\d+) "
                r"ORDER BY first_sequence LIMIT (\d+) FORMAT JSONEachRow", sql)
            if match:
                run_id, lower, upper, limit = match.groups()
                rows = [row for row in client.tables.get('trading_commit_v4', ())
                    if row['run_id'] == run_id
                    and int(row['first_sequence']) >= int(lower)
                    and int(row['last_sequence']) <= int(upper)]
                rows.sort(key=lambda row: int(row['first_sequence']))
                columns = ('batch_id', 'prior_batch_id', 'first_sequence', 'last_sequence')
                return '\n'.join(json.dumps({key: row[key] for key in columns})
                    for row in rows[:int(limit)])
            return original_execute(sql, query_id=query_id)
        patch.setattr(client, 'execute', execute)
        entry = request.entry.proposal
        at = request.intent.event_time
        journal = BacktestMemoryJournal(run_id=config.run_id)
        broker = SimulatedBrokerAdapter(config.account_ids, SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST, initial_time=at, fixed_bar_mode=True)
        profile = PortfolioAccountProfile('cash', entry.account_id, 'backtest', 'simulated',
            PortfolioPolicy(allow_outside_rth=True))
        portfolio = PortfolioManagementEngine((profile,), journal=journal, run_id=config.run_id,
            strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, event_clock=lambda: at)
        planner = RuntimeIbkrStrategyOrderPlanner(
            {entry.ticker: InstrumentContract(entry.ticker, 1, entry.ticker, 'STK', 'USD')},
            strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, run_id=config.run_id)
        runtime = TradingRuntime(config, broker,
            SimpleNamespace(strategy_id=config.strategy_id, revision=config.strategy_revision, automatic=True),
            journal, portfolio=portfolio, intent_planner=planner)
        actual.bind_runtime(runtime)
        patch.setattr(writer_api, 'storage_preflight', lambda *a, **kw: None)
        patch.setattr(writer_api, 'journal_permission_preflight', lambda *a, **kw: None)
        client.fixed_structural_lot_profile = actual.profile
        writer = writer_api.ArteJournalWriter(client, run_id=config.run_id,
            journal_profile='backtest_v4', coalesce_batches=False)
        publisher = BacktestTypedJournalPublisher(journal, writer, attempt_id=str(uuid4()),
            run_month=actual.operation.source.session_date.replace(day=1), expected_config=flat)
        try:
            await runtime.initialize()
            actual.operation.bind_publisher(publisher)
            runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot(
                entry.ticker, 10., 10.01, .01, at, 'arte.liquidity_100ms_v1'))
            instant = at-timedelta(microseconds=1000)
            us = int(instant.timestamp()*1000000)
            local = at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket = (local.hour*3600000+local.minute*60000+local.second*1000)//100-1
            bar = dict(ticker=entry.ticker, resolution_ms=100, bucket_index=bucket,
                event_count=1, first_event_us=us, last_event_us=us, quote_timestamp_us=us,
                quote_valid=1, bid_int=100000, ask_int=100100, bid_size=10000., ask_size=10000.,
                price_valid=1, close_int=100100, extremes_valid=1, low_int=100100, high_int=100100,
                execution_volume=100000.)
            await runtime.process_liquidity_boundary([bar], at=at)
            result = await runtime.submit_fixed_structural_lot_request(request)
            assert result[0]['decision']['status'] in ('approved', 'resized')
            await publisher._drain(target_sequence=journal.latest_sequence(config.run_id))
            contexts = tuple(client.fixed_structural_lot_contexts)
            prefix = load_verified_v4_prefix(client, config.run_id,
                first_price_source=actual.operation.source.price_authority, fixed_lot_contexts=contexts)
            assert prefix and contexts
            return actual, request, client, prefix
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
    published_entry = None
    try:
        published_entry = asyncio.run(publish())
        yield published_entry
    finally:
        if published_entry is not None:
            published_entry[2].backtest_v4_lease.release()
        patch.undo()


def _exit_graph(selected_entry, kind, reference):
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
    actual, request, client, prefix = selected_entry
    entry = request.entry.proposal
    financial = StrategyOneFinancialView(entry.assignment_id, entry.account_id, entry.ticker,
        AssignmentStatus.MANAGING, StrategyPermissions(), 10., False, False, False, 1)
    source_id = getattr(request, reference).intent_id
    number = request.revision
    day = actual.operation.source.session_date
    if kind == 'ah':
        from test_arte_confirmed_ah_failure_v4 import prepared_case
        from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent as factory
        from src.trading_runtime.arte_confirmed_ah_failure_v4 import project_confirmed_ah_failure as project
        witness = prepared_case()[0]
        five = replace(witness.five_second, reference_ask=entry.reference_ask,
            initial_stop=entry.initial_stop, completed_close_int=99400, bid=9.94, ask=9.95)
        witness = replace(witness, five_second=five)
    elif kind == 'liquidity':
        from test_arte_liquidity_fade_failure_v4 import prepared_case
        from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent as factory
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import project_liquidity_fade_failure as project
        witness = replace(prepared_case()[0], reference_ask=entry.reference_ask,
            initial_stop=entry.initial_stop, completed_close_int=99400, bid=9.94, ask=9.95)
    else:
        from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
        from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent as factory
        from src.trading_runtime.arte_followthrough_failure_v4 import project_followthrough_failure as project
        witness = FollowThroughFailure(60000, 41100, entry.reference_ask,
            entry.initial_stop, 99400, -.02, -.01, 9.94, 9.95, 1000)
    intent = factory(witness, financial, session_date=day,
        source_entry_intent_id=source_id, strategy_number=number)
    base = strategy_intent_batch(intent, run_id=prefix.run_id, run_month=day.replace(day=1),
        account_id=entry.account_id, attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id=prefix.last_batch_id, sequence=prefix.last_sequence+1,
        source_cursor='controlled-exit-source-reader', run_status='running', recorded_at=intent.event_time)
    common = dict(run_id=prefix.run_id, batch_id=base.batch_id,
        parent_record_id=base.events[0]['record_id'], strategy_number=number)
    if kind == 'followthrough':
        row = project(witness, intent, source_id, assignment_id=entry.assignment_id, **common)
    else:
        if kind == 'liquidity':
            from test_arte_liquidity_fade_failure_v4 import prepared_case
            template = prepared_case()[3]
            common.update({k: v for k, v in template.items() if k.startswith('source_')
                and k != 'source_entry_intent_id'})
            common['source_manager_checkpoint_sequence'] = prefix.last_sequence
        row = project(witness, intent, financial, session_date=day,
            source_entry_intent_id=source_id, **common)
    return row, base, witness


def _read_exit(selected_entry, kind, row, base):
    actual, request, client, prefix = selected_entry
    args = dict(verified_prefix=prefix, first_price_source=actual.operation.source.price_authority)
    if kind == 'ah':
        from src.trading_runtime.strategy_confirmed_ah_failure_source import validate_confirmed_ah_source
        return validate_confirmed_ah_source(client, row, base.intents[0], base.events[0], **args)
    if kind == 'liquidity':
        from src.trading_runtime.strategy_liquidity_fade_entry_source import validate_liquidity_fade_entry_source
        return validate_liquidity_fade_entry_source(client, row, base.intents[0], base.events[0], **args)
    from src.trading_runtime.arte_followthrough_failure_v4 import seal_followthrough_rows
    return seal_followthrough_rows(client, (row,), base.intents, base.events,
        prior_batch_id=prefix.last_batch_id, **args)


@pytest.mark.parametrize('kind', ['ah', 'liquidity', 'followthrough'])
@pytest.mark.parametrize('reference', ['original', 'intent'])
def test_actual_selected_entry_is_resolved_through_exit_reader(selected_entry, kind, reference):
    row, base, witness = _exit_graph(selected_entry, kind, reference)
    result = _read_exit(selected_entry, kind, row, base)
    if kind == 'followthrough':
        assert len(result) == 1
    else:
        assert result == witness


@pytest.mark.parametrize('kind', ['ah', 'liquidity', 'followthrough'])
def test_foreign_witness_entry_rejects(selected_entry, kind):
    row, base, _ = _exit_graph(selected_entry, kind, 'original')
    row['source_entry_intent_id'] = str(uuid4())
    with pytest.raises(ValueError, match='missing or ambiguous'):
        _read_exit(selected_entry, kind, row, base)


@pytest.mark.parametrize('kind', ['ah', 'liquidity', 'followthrough'])
def test_foreign_context_inventory_rejects_before_entry_read(selected_entry, kind):
    row, base, _ = _exit_graph(selected_entry, kind, 'original')
    client = selected_entry[2]
    contexts = client.fixed_structural_lot_contexts
    client.fixed_structural_lot_contexts = (SimpleNamespace(source=object()),)
    try:
        with pytest.raises(ValueError, match='foreign context'):
            _read_exit(selected_entry, kind, row, base)
    finally:
        client.fixed_structural_lot_contexts = contexts


@pytest.mark.parametrize('field', ['original_intent_id', 'parent_record_id', 'batch_id'])
def test_mutated_companion_link_rejects(selected_entry, field):
    from src.trading_runtime.selected_checkpoint_products import original_entry, original_link_matches
    actual, request, client, prefix = selected_entry
    stored, event, child = original_entry(client, prefix.run_id, request.original.intent_id,
        prior_batch_id=prefix.last_batch_id, exit_batch_id=str(uuid4()), verified_prefix=prefix,
        first_price_source=actual.operation.source.price_authority)
    changed = copy.deepcopy(child)
    changed[field] = str(uuid4())
    with pytest.raises(ValueError, match='differs from verified native parent'):
        original_link_matches(client, stored, changed, request.original.intent_id)


def _read_owned_oms(selected_entry, prefix):
    from src.trading_runtime.selected_checkpoint_products import source_bound_oms_lineages
    actual, request, client, _ = selected_entry
    return source_bound_oms_lineages(client, prefix,
        source=actual.operation.source,
        contexts=tuple(client.fixed_structural_lot_contexts),
        allowed_accounts=frozenset((request.entry.proposal.account_id,)))


def test_source_bound_reader_reconstructs_actual_native_entry(selected_entry):
    _, request, _, prefix = selected_entry
    lineages = _read_owned_oms(selected_entry, prefix)
    assert len(lineages) == 1
    assert lineages[0].source_intent.intent.intent_id == request.intent.intent_id
    assert lineages[0].through_sequence == prefix.last_sequence
    assert lineages[0].approved_intent.action == 'enter_long'
    assert lineages[0].orders


@pytest.mark.parametrize('corruption', ['missing', 'foreign_run', 'oversized'])
def test_source_bound_reader_rejects_invalid_prefix_before_sql(selected_entry, corruption, monkeypatch):
    prefix = selected_entry[3]
    if corruption == 'missing':
        changed = None
    elif corruption == 'foreign_run':
        changed = replace(prefix, run_id=str(uuid4()))
    else:
        changed = replace(prefix, batch_ids=(prefix.last_batch_id,) * 100_001)
    def forbidden_sql(*args, **kwargs):
        raise AssertionError('Invalid prefix must reject before transport')
    monkeypatch.setattr(selected_entry[2], 'execute', forbidden_sql)
    with pytest.raises(ValueError, match='exact issued source'):
        _read_owned_oms(selected_entry, changed)


def test_source_bound_reader_rejects_mutated_header_cursor(selected_entry):
    changed = replace(selected_entry[3], source_cursor='foreign-source-cursor')
    with pytest.raises(ValueError, match='mutated committed scope'):
        _read_owned_oms(selected_entry, changed)


def test_source_bound_reader_rejects_foreign_genesis_parent(selected_entry, monkeypatch):
    import json
    client = selected_entry[2]
    original_execute = client.execute
    def changed_ancestry(sql, **kwargs):
        result = original_execute(sql, **kwargs)
        if isinstance(sql, str) and sql.startswith(
                'SELECT batch_id,prior_batch_id,first_sequence,last_sequence '):
            rows = [json.loads(line) for line in result.splitlines() if line.strip()]
            assert rows and rows[0]['first_sequence'] == 1
            rows[0]['prior_batch_id'] = str(uuid4())
            return '\n'.join(json.dumps(row) for row in rows)
        return result
    monkeypatch.setattr(client, 'execute', changed_ancestry)
    with pytest.raises(ValueError, match='forked or discontinuous'):
        _read_owned_oms(selected_entry, selected_entry[3])
