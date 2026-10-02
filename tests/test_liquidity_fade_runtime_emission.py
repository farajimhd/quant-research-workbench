"""Real buffer/projector/runtime dispatch; constructed runtime and mocked OMS."""
import asyncio
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_liquidity_fade_failure_v4 import CHECKPOINT_REFERENCE_FIELDS, restore_liquidity_fade_failure
from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.strategy_engine import StrategyEvaluation
from test_arte_liquidity_fade_failure_v4 import prepared_case
from test_profit_giveback_runtime_route import runtime_fixture


def values():
    witness, financial, _, row = prepared_case()
    day = date(2026, 8, 10)
    source = {key: row[key] for key in ('source_build_id', 'source_market_plan_token',
        'source_bars_attempt_id', 'source_indicators_attempt_id', 'source_liquidity_attempt_id', *CHECKPOINT_REFERENCE_FIELDS)}
    intent = liquidity_fade_exit_intent(witness, financial, session_date=day,
                                      source_entry_intent_id=row['source_entry_intent_id'])
    return dict(intent=intent, witness=witness, financial=financial,
        source_entry_intent_id=row['source_entry_intent_id'], session_date=day, observation_source=source,
        strategy_id='early-squeeze-strategy', strategy_revision=35)


def test_memory_source_is_immutable_retry_is_idempotent_and_fence_releases_sidecar():
    data = values()
    journal = BacktestMemoryJournal(run_id='run', initial_sequence=64)
    try:
        record = journal.append_liquidity_fade_exit(**data)
        assert record.sequence == 65
        assert journal.append_liquidity_fade_exit(**data) is record
        source = journal.liquidity_fade_exit_for_record(record.record_id)
        data['observation_source']['source_broker_snapshot_hash'] = 'f'*64
        assert source[5]['source_broker_snapshot_hash'] == 'd'*64
        with pytest.raises(TypeError): source[5]['source_broker_snapshot_hash'] = 'f'*64
        with pytest.raises(ValueError, match='retry changed'):
            journal.append_liquidity_fade_exit(**data)
        journal.mark_fenced(record.sequence)
        assert journal.liquidity_fade_exit_for_record(record.record_id) is None
        assert journal.pending_record_count == 0
    finally:
        journal.close()


@pytest.mark.parametrize('change', ['revision', 'missing_ref', 'bad_hash', 'future_checkpoint', 'pending'])
def test_invalid_buffer_source_cannot_append(change):
    data = values()
    if change == 'revision': data['strategy_revision'] = 34
    elif change == 'missing_ref': del data['observation_source']['source_broker_snapshot_id']
    elif change == 'bad_hash': data['observation_source']['source_manager_snapshot_hash'] = 'bad'
    elif change == 'future_checkpoint': data['observation_source']['source_manager_checkpoint_sequence'] = 65
    else: data['financial'] = replace(data['financial'], pending_exit=True)
    journal = BacktestMemoryJournal(run_id='run', initial_sequence=64)
    try:
        with pytest.raises(ValueError): journal.append_liquidity_fade_exit(**data)
        assert journal.pending_record_count == 0
    finally:
        journal.close()


def test_projection_keeps_all_refs_and_requires_earlier_original_entry():
    data = values()
    journal = BacktestMemoryJournal(run_id='run', initial_sequence=64)
    try:
        journal.append_liquidity_fade_exit(**data)
        entry = replace(data['intent'], intent_id=data['source_entry_intent_id'], action='enter_long',
            reason='strategy_one_entry', reference_price=data['witness'].reference_ask,
            invalidation_price=data['witness'].initial_stop, event_time=data['intent'].event_time-timedelta(seconds=30))
        base = strategy_intent_batch(entry, run_id='run', run_month=date(2026,8,1),
            account_id=data['financial'].account_id, attempt_id=str(uuid4()), batch_id=str(uuid4()),
            prior_batch_id=str(uuid4()), sequence=12, source_cursor='entry-fixture', run_status='running', recorded_at=entry.event_time)
        context = dict(attempt_id=base.attempt_id, run_month=date(2026,8,1), prior_sequence=64,
            prior_batch_id=str(uuid4()), through_sequence=65,
            expected_config={'strategy_id':'early-squeeze-strategy','strategy_revision':35})
        with pytest.raises(RuntimeError, match='original typed entry source'):
            project_pending_backtest_v4_prefix(journal, **context)
        units = project_pending_backtest_v4_prefix(journal, **context, published_sources={entry.intent_id:(base,entry)})
        assert len(units) == 1 and type(units[0]) is V4LiquidityFadeFailureBatch
        assert restore_liquidity_fade_failure(units[0].failure) == data['witness']
        assert all(units[0].failure[key] == value for key,value in data['observation_source'].items())
        wrong = replace(base, first_sequence=64, last_sequence=64)
        with pytest.raises(RuntimeError, match='original typed entry source'):
            project_pending_backtest_v4_prefix(journal, **context, published_sources={entry.intent_id:(wrong,entry)})
    finally:
        journal.close()


def runtime_case():
    data = values()
    runtime, _, _ = runtime_fixture()
    runtime.journal.close()
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id, initial_sequence=64)
    runtime.config.strategy_revision, runtime.config.anchor_date = 35, data['session_date']
    runtime.config.account_ids = (data['financial'].account_id,)
    decision, _ = runtime.portfolio.approve.return_value
    runtime.portfolio.approve.return_value = decision, replace(data['intent'], metadata={'assignment_id':data['financial'].assignment_id})
    return runtime, data


def submit(runtime, data):
    return runtime.submit_liquidity_fade_failure(data['financial'], data['witness'],
        data['source_entry_intent_id'], data['observation_source'])


def test_runtime_preserves_shared_assignment_portfolio_and_oms_authority():
    runtime, data = runtime_case()
    try:
        result = asyncio.run(submit(runtime, data))
        assert result[0]['decision']['status'] == 'approved'
        runtime.portfolio.approve.assert_awaited_once_with(data['intent'],
            account_id=data['financial'].account_id, assignment_id=data['financial'].assignment_id)
        runtime.order_manager.submit_intent.assert_awaited_once_with(runtime.portfolio.approve.return_value[1],
            account_id=data['financial'].account_id, event=None)
        record = runtime.journal.unfenced_records()[0]
        assert runtime.journal.liquidity_fade_exit_for_record(record.record_id)[5] == data['observation_source']
    finally:
        runtime.journal.close()


@pytest.mark.parametrize('change', ['mode', 'revision', 'strategy', 'pending', 'missing_ref', 'future_checkpoint'])
def test_invalid_runtime_source_never_reaches_portfolio_or_oms(change):
    runtime, data = runtime_case()
    if change == 'mode': runtime.config.mode = RunMode.REPLAY
    elif change == 'revision': runtime.config.strategy_revision = 34
    elif change == 'strategy': runtime.config.strategy_id = 'foreign'
    elif change == 'pending': data['financial'] = replace(data['financial'], pending_exit=True)
    elif change == 'missing_ref': del data['observation_source']['source_manager_snapshot_id']
    else: data['observation_source']['source_manager_checkpoint_sequence'] = 65
    try:
        with pytest.raises(ValueError): asyncio.run(submit(runtime, data))
        assert runtime.journal.pending_record_count == 0
        runtime.portfolio.approve.assert_not_awaited()
        runtime.order_manager.submit_intent.assert_not_awaited()
    finally:
        runtime.journal.close()


def test_generic_liquidity_reason_cannot_bypass_normalized_witness():
    runtime, data = runtime_case()
    try:
        with pytest.raises(ValueError, match='normalized witness'):
            asyncio.run(runtime._execute_intents(StrategyEvaluation(intents=(data['intent'],)),
                data['financial'].account_id, None))
        runtime.portfolio.approve.assert_not_awaited()
    finally:
        runtime.journal.close()


def test_async_publisher_retains_exit_source_only_after_writer_receipt():
    from concurrent.futures import Future
    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
    from tests.test_backtest_typed_publisher import FakeWriter
    async def exercise():
        data = values()
        journal = BacktestMemoryJournal(run_id='run', initial_sequence=64)
        record = journal.append_liquidity_fade_exit(**data)
        entry = replace(data['intent'], intent_id=data['source_entry_intent_id'], action='enter_long',
            reason='strategy_one_entry', reference_price=data['witness'].reference_ask,
            invalidation_price=data['witness'].initial_stop, event_time=data['intent'].event_time-timedelta(seconds=30))
        entry_batch = strategy_intent_batch(entry, run_id='run', run_month=date(2026,8,1),
            account_id=data['financial'].account_id, attempt_id=str(uuid4()), batch_id=str(uuid4()),
            prior_batch_id=str(uuid4()), sequence=12, source_cursor='entry-fixture', run_status='running', recorded_at=entry.event_time)
        submitted = asyncio.Event()
        class Writer(FakeWriter):
            run_id, journal_profile = 'run', 'backtest_v4'
            def submit_liquidity_fade_exit_v4(self, unit, **context):
                self.unit, self.context, self.receipt = unit, context, Future()
                submitted.set()
                return self.receipt
        writer = Writer()
        publisher = BacktestTypedJournalPublisher(journal, writer, attempt_id=entry_batch.attempt_id,
            run_month=date(2026,8,1), initial_sequence=64, prior_batch_id=str(uuid4()),
            expected_config={'strategy_id':'early-squeeze-strategy','strategy_revision':35})
        publisher._committed_strategy_intents[entry.intent_id] = entry_batch, entry
        authority = None  # transport-only fake writer; native authority rejection has separate real-writer tests
        publisher._first_price_source = authority
        task = publisher.enqueue_pending()
        try:
            try:
                await asyncio.wait_for(submitted.wait(), timeout=3)
            except TimeoutError:
                if task.done():
                    await task
                raise
            assert data['intent'].intent_id not in publisher._committed_strategy_intents
            assert journal.liquidity_fade_exit_for_record(record.record_id) is not None
            assert writer.context == {'first_price_source':authority}
            writer.receipt.set_result(writer.unit.base.batch_id)
            receipt = await asyncio.wait_for(task, timeout=3)
            assert receipt.last_sequence == 65
            assert publisher._committed_strategy_intents[data['intent'].intent_id][1] == data['intent']
            assert journal.liquidity_fade_exit_for_record(record.record_id) is None
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            journal.close()
    asyncio.run(exercise())


def test_liquidity_exit_starts_after_separately_committed_predecessor_group():
    from test_liquidity_fade_native_publication import native_unit
    from src.backend.backtest_typed_publisher import _coalesce_v4_units
    unit = native_unit()
    event = dict(unit.base.events[0], sequence=64, record_id=str(uuid4()),
        batch_id=unit.base.prior_batch_id, category='run_state', entity_type='lifecycle')
    predecessor = replace(unit.base, batch_id=unit.base.prior_batch_id, prior_batch_id=str(uuid4()),
        first_sequence=64, last_sequence=64, events=(event,), intents=())
    groups = _coalesce_v4_units((predecessor, unit))
    assert groups == (predecessor, unit)
