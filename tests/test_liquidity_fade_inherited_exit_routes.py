"""Prepared Strategy35 exits keep Portfolio/OMS ownership and exact sources."""
import asyncio
from dataclasses import replace

import pytest

from test_arte_confirmed_ah_failure_v4 import prepared_case as ah_case
from test_arte_liquidity_fade_failure_v4 import prepared_case as liquidity_case
from test_profit_giveback_runtime_route import runtime_fixture
from test_profit_giveback_oms_recovery import prepared as profit_oms
from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent
from src.trading_runtime.arte_confirmed_ah_failure_v4 import project_confirmed_ah_failure, restore_confirmed_ah_failure
from src.trading_runtime.strategy_engine import StrategyEvaluation
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from src.trading_runtime.arte_oms_projection import _approved_strategy_one_oms_intent, reconstruct_strategy_one_oms_lineage
from src.trading_runtime.strategy_orders import canonical_runtime_order_raw


def test_inherited_ah_factory_preserves_policy_and_uses_separate_identity():
    witness, financial, args, old, old_row = ah_case()
    new = confirmed_ah_exit_intent(witness, financial, **args, strategy_number=35)
    assert new.reason == 'strategy_thirty_five_confirmed_ah_failure'
    assert new.intent_id != old.intent_id
    assert replace(new, intent_id=old.intent_id, reason=old.reason) == old
    row = project_confirmed_ah_failure(witness, new, financial, **args, strategy_number=35,
        run_id=old_row['run_id'], batch_id=old_row['batch_id'], parent_record_id=old_row['parent_record_id'])
    assert restore_confirmed_ah_failure(row) == witness
    assert dict(row, strategy_number=34) == old_row


@pytest.mark.parametrize('generic', [False, True])
def test_ah35_runtime_retains_witness_and_requires_normalized_dispatch(generic):
    runtime, _, _ = runtime_fixture(strategy_number=35)
    witness, financial, args, _, _ = ah_case()
    intent = confirmed_ah_exit_intent(witness, financial, **args, strategy_number=35)
    runtime.config.anchor_date = args['session_date']
    runtime.config.account_ids = (financial.account_id,)
    decision, _ = runtime.portfolio.approve.return_value
    runtime.portfolio.approve.return_value = decision, replace(intent, metadata={'assignment_id': financial.assignment_id})
    try:
        if generic:
            with pytest.raises(ValueError, match='normalized witness'):
                asyncio.run(runtime._execute_intents(StrategyEvaluation(intents=(intent,)), financial.account_id, None))
            runtime.portfolio.approve.assert_not_awaited()
            runtime.order_manager.submit_intent.assert_not_awaited()
            assert runtime.journal.pending_record_count == 0
        else:
            asyncio.run(runtime.submit_confirmed_ah_failure(financial, witness, args['source_entry_intent_id']))
            runtime.portfolio.approve.assert_awaited_once_with(intent,
                account_id=financial.account_id, assignment_id=financial.assignment_id)
            runtime.order_manager.submit_intent.assert_awaited_once()
            record = runtime.journal.unfenced_records()[0]
            assert record.payload['strategy_revision'] == 35
            assert runtime.journal.confirmed_ah_exit_for_record(record.record_id)[1] == witness
    finally:
        runtime.journal.close()


def test_ah35_projection_requires_original_entry_and_preserves_numbered_scalar():
    from datetime import date, timedelta
    from uuid import uuid4
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
    witness, financial, args, _, row = ah_case()
    intent = confirmed_ah_exit_intent(witness, financial, **args, strategy_number=35)
    journal = BacktestMemoryJournal(run_id=row['run_id'], initial_sequence=1)
    try:
        journal.append_confirmed_ah_exit(intent=intent, witness=witness, financial=financial,
            **args, strategy_id='early-squeeze-strategy', strategy_revision=35)
        entry = replace(intent, intent_id=args['source_entry_intent_id'], action='enter_long',
            reason='strategy_one_entry', reference_price=2.08, invalidation_price=1.81,
            event_time=intent.event_time-timedelta(seconds=53))
        entry_batch = strategy_intent_batch(entry, run_id=row['run_id'], run_month=date(2026,8,1),
            account_id=financial.account_id, attempt_id=str(uuid4()), batch_id=str(uuid4()),
            prior_batch_id=str(uuid4()), sequence=1, source_cursor='entry-fixture',
            run_status='running', recorded_at=entry.event_time)
        values = dict(attempt_id=entry_batch.attempt_id, run_month=date(2026,8,1),
            prior_sequence=1, prior_batch_id=entry_batch.batch_id, through_sequence=2,
            expected_config={'strategy_id':'early-squeeze-strategy','strategy_revision':35})
        with pytest.raises(RuntimeError, match='original typed entry source'):
            project_pending_backtest_v4_prefix(journal, **values)
        unit, = project_pending_backtest_v4_prefix(journal, **values,
            published_sources={entry.intent_id:(entry_batch,entry)})
        assert type(unit) is V4ConfirmedAhFailureBatch and unit.confirmation['strategy_number'] == 35
        assert restore_confirmed_ah_failure(unit.confirmation) == witness
    finally:
        journal.close()


def test_ah35_original_entry_graph_keeps_independent_preceding_source(monkeypatch):
    from datetime import date
    from test_arte_confirmed_ah_failure_v4 import prepared_source_graph
    from src.trading_runtime.arte_intent_projection import project_strategy_intent
    from src.trading_runtime.strategy_confirmed_ah_failure_source import validate_confirmed_ah_source
    witness, row, parent, event, prefix, _, _, child = prepared_source_graph(monkeypatch)
    _, financial, _, _, _ = ah_case()
    intent = confirmed_ah_exit_intent(witness, financial, session_date=date(2026,8,10),
        source_entry_intent_id=row['source_entry_intent_id'], strategy_number=35)
    row = dict(row, strategy_number=35)
    child['strategy_number'] = 35
    parent.update({key:value for key,value in project_strategy_intent(intent).core.items() if key != 'event_time'})
    event['entity_id'] = intent.intent_id
    assert validate_confirmed_ah_source(None, row, parent, event, verified_prefix=prefix) == witness
    child['strategy_number'] = 34
    with pytest.raises(ValueError, match='original entry or exit graph'):
        validate_confirmed_ah_source(None, row, parent, event, verified_prefix=prefix)


def oms_case(family):
    from datetime import date
    group, source, history, reservation, decision, _ = profit_oms(35)
    if family == 'ah':
        witness, financial, args, _, row = ah_case()
        intent = confirmed_ah_exit_intent(witness, financial, **args, strategy_number=35)
        row = project_confirmed_ah_failure(witness, intent, financial, **args, strategy_number=35,
            run_id=history.run_id, batch_id=source.batch_id, parent_record_id=source.record_id)
    else:
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import project_liquidity_fade_failure, CHECKPOINT_REFERENCE_FIELDS
        witness, financial, _, row = liquidity_case()
        intent = liquidity_fade_exit_intent(witness, financial, session_date=date(2026,8,10),
            source_entry_intent_id=row['source_entry_intent_id'])
        refs = {key: row[key] for key in ('source_build_id', 'source_market_plan_token',
            'source_bars_attempt_id', 'source_indicators_attempt_id', 'source_liquidity_attempt_id',
            *CHECKPOINT_REFERENCE_FIELDS)}
        row = project_liquidity_fade_failure(witness, intent, financial, session_date=date(2026,8,10),
            source_entry_intent_id=row['source_entry_intent_id'], run_id=history.run_id,
            batch_id=source.batch_id, parent_record_id=source.record_id, **refs)
    source = replace(source, intent=intent)
    order = replace(group.orders[0], ticker=financial.ticker, quantity=financial.position_quantity,
        price=intent.reference_price)
    group = replace(group, group=dict(group.group, strategy_intent_id=intent.intent_id), orders=(order,))
    reservation = dict(reservation, intent_id=intent.intent_id, quantity=financial.position_quantity,
        assignment_id=financial.assignment_id)
    decision = dict(decision, requested_quantity=financial.position_quantity)
    return group, source, history, reservation, decision, row


@pytest.mark.parametrize('family', ['ah', 'liquidity'])
def test_complete35_exit_reconstructs_exact_approved_order(family):
    group, source, history, reservation, decision, row = oms_case(family)
    kwargs = {'confirmed_ah_row' if family == 'ah' else 'liquidity_fade_row': row}
    approved, _ = _approved_strategy_one_oms_intent(group, source, history, reservation, decision, **kwargs)
    assert approved.metadata['assignment_id'] == reservation['assignment_id']
    orders = reconstruct_strategy_one_oms_lineage(group, source, history,
        admission_reservation=reservation, admission_decision=decision, **kwargs)
    assert orders[0].raw == canonical_runtime_order_raw(group.orders[0], approved,
        run_id=history.run_id, strategy_id='early-squeeze-strategy', strategy_revision=35)


@pytest.mark.parametrize('family', ['ah', 'liquidity'])
@pytest.mark.parametrize('change', ['missing', 'no_admission', 'quantity', 'parent', 'revision', 'assignment'])
def test_incomplete35_exit_lineage_rejects(family, change):
    group, source, history, reservation, decision, row = oms_case(family)
    if change == 'missing': row = None
    elif change == 'no_admission': reservation = decision = None
    elif change == 'quantity': reservation = dict(reservation, quantity=1)
    elif change == 'parent': row = dict(row, parent_record_id='foreign')
    elif change == 'revision': group = replace(group, group=dict(group.group, strategy_revision=33))
    else: row = dict(row, assignment_id='foreign')
    kwargs = {'confirmed_ah_row' if family == 'ah' else 'liquidity_fade_row': row}
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision, **kwargs)


def test_cold35_oms_stays_closed_until_complete_release_is_installed(monkeypatch):
    from unittest.mock import Mock
    from src.trading_runtime import arte_oms_projection as oms
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    group, source, history, _, _, _ = oms_case('liquidity')
    prefix = V4CommittedPrefix(history.run_id, 12, group.group['batch_id'],
        'cursor', 'running', history.committed_batch_ids)
    reader = Mock()
    monkeypatch.setattr(oms, 'load_latest_committed_oms_groups', reader)
    with pytest.raises(ValueError, match='No installed'):
        oms.load_recovered_strategy_one_oms_lineage(object(), prefix,
            allowed_accounts=frozenset({source.account_id}), protection_history=history,
            strategy_number=35)
    reader.assert_not_called()
