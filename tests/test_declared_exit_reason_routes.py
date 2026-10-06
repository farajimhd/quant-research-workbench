"""Actual exit graph/reader regressions; external reads only are mocked.

Each 42/64 exit is projected by its own factory, never relabeled. Liquidity
parent detection stops at a deliberately unavailable native context read; it
does not claim full producer/checkpoint publication certification.
"""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_intent_projection import project_strategy_intent


@pytest.mark.parametrize('reason,rule', [
    ('strategy_064_confirmed_ah_failure', 'strategy.confirmed-ah-risk-failure.v1'),
    ('strategy_٦٤_confirmed_ah_failure', 'strategy.confirmed-ah-risk-failure.v1'),
    ('strategy_64_confirmed_ah_failure ', 'strategy.confirmed-ah-risk-failure.v1'),
    ('strategy_64_foreign_exit', 'strategy.confirmed-ah-risk-failure.v1'),
    ('strategy_63_confirmed_ah_failure', 'strategy.confirmed-ah-risk-failure.v1'),
    ('strategy_64_confirmed_ah_failure', 'strategy-four-no-add-v1'),
    ('strategy_64_confirmed_ah_failure', 'strategy-thirty-five-completed-liquidity-fade-v1'),
])
def test_generic_declared_reason_rejects_malformed_foreign_and_wrong_semantic(reason, rule):
    from src.trading_runtime.numbered_fixed_strategy import declared_fixed_exit_reason
    assert declared_fixed_exit_reason(reason, rule) is False


def graph(kind, number):
    if kind == 'ah':
        from test_arte_confirmed_ah_failure_v4 import prepared_case
        from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent as factory
        from src.trading_runtime.arte_confirmed_ah_failure_v4 import project_confirmed_ah_failure as project
        witness, held, *_ = prepared_case()
        extra = {}
    else:
        from test_arte_liquidity_fade_failure_v4 import prepared_case
        from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent as factory
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import project_liquidity_fade_failure as project
        witness, held, *_ = prepared_case()
        extra = dict(source_build_id='a'*64, source_market_plan_token='b'*64,
            source_bars_attempt_id=str(UUID(int=11)), source_indicators_attempt_id=str(UUID(int=12)),
            source_liquidity_attempt_id=str(UUID(int=13)), source_manager_snapshot_id=str(UUID(int=14)),
            source_manager_checkpoint_sequence=7, source_manager_snapshot_hash='c'*64,
            source_broker_snapshot_id=str(UUID(int=15)), source_broker_snapshot_hash='d'*64)
    run = f'reason-route-{kind}-{number}'
    batch, parent_id, entry_id, preceding = (str(UUID(int=number*100+i)) for i in (1, 2, 3, 4))
    args = dict(session_date=date(2026, 8, 10), source_entry_intent_id=entry_id, strategy_number=number)
    intent = factory(witness, held, **args)
    row = project(witness, intent, held, **args, run_id=run, batch_id=batch,
        parent_record_id=parent_id, **extra)
    parent = dict(project_strategy_intent(intent).core)
    at = parent.pop('event_time')
    parent.update(record_id=parent_id, run_id=run, batch_id=batch,
        event_month=row['event_month'], account_id=held.account_id)
    event = dict(record_id=parent_id, run_id=run, batch_id=batch, event_time=at,
        sequence=65, account_id=held.account_id, entity_id=intent.intent_id)
    prefix = V4CommittedPrefix(run, 64, preceding, 'cursor', 'running', (preceding,))
    return witness, held, intent, row, parent, event, prefix


@pytest.mark.parametrize('number', [42, 64])
@pytest.mark.parametrize('change', [None, 'missing', 'duplicate', 'foreign_reason'])
def test_actual_ah_sealer_detects_declared_parent_and_missing_children(monkeypatch, number, change):
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import seal_confirmed_ah_rows
    witness, held, intent, row, parent, event, prefix = graph('ah', number)
    calls = []
    def source_read(client, run, entry, **context):
        # Explicit synthetic external committed-entry read, with original prices;
        # consumer source/identity/factory checks below remain real.
        calls.append(entry)
        assert run == row['run_id'] and entry == row['source_entry_intent_id']
        assert context['verified_prefix'] == prefix
        w = witness.five_second
        return (dict(intent_id=entry, ticker=held.ticker, action='enter_long',
            reason='strategy_one_entry', reference_price=w.reference_ask,
            invalidation_price=w.initial_stop),
            dict(account_id=held.account_id, sequence=4),
            dict(strategy_number=number, assignment_id=held.assignment_id,
                boundary_ms=w.first_held_boundary_ms-100))
    monkeypatch.setattr('src.trading_runtime.arte_followthrough_failure_v4._source_entry', source_read)
    rows = (row,)
    if change == 'missing': rows = ()
    elif change == 'duplicate': rows = (row, row)
    elif change == 'foreign_reason': parent = {**parent, 'reason': 'foreign_exit'}
    if change:
        with pytest.raises(ValueError):
            seal_confirmed_ah_rows(None, rows, (parent,), (event,), verified_prefix=prefix)
        assert calls == []
    else:
        result = seal_confirmed_ah_rows(None, rows, (parent,), (event,), verified_prefix=prefix)
        assert len(result) == 1 and result[0]['strategy_number'] == number
        assert result[0]['content_hash'] and calls == [row['source_entry_intent_id']]


class ExternalReadUnavailable(RuntimeError):
    pass


@pytest.mark.parametrize('number', [42, 64])
@pytest.mark.parametrize('change', [None, 'missing', 'duplicate', 'foreign_reason'])
def test_liquidity_native_parent_detection_preserves_mandatory_external_authority(monkeypatch, number, change):
    from src.trading_runtime.strategy_liquidity_fade_publication import prepare_liquidity_fade_publication_rows
    _, held, _, row, parent, event, prefix = graph('liquidity', number)
    calls = []
    def context_read(client, run):
        calls.append(run)
        raise ExternalReadUnavailable('Native context unavailable; no source check bypass')
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer.load_typed_run_context', context_read)
    rows = (row,)
    if change == 'missing': rows = ()
    elif change == 'duplicate': rows = (row, row)
    elif change == 'foreign_reason': parent = {**parent, 'reason': 'foreign_exit'}
    kwargs = dict(verified_prefix=prefix, market_plan=object(),
        financial_views={row['parent_record_id']: held})
    if change:
        with pytest.raises(ValueError):
            prepare_liquidity_fade_publication_rows(None, rows, (parent,), (event,), **kwargs)
        assert calls == []
    else:
        with pytest.raises(ExternalReadUnavailable):
            prepare_liquidity_fade_publication_rows(None, rows, (parent,), (event,), **kwargs)
        assert calls == [row['run_id']]


@pytest.mark.parametrize('number', [42, 64])
def test_native_liquidity_wrapper_cannot_silently_drop_missing_declared_child(number):
    from src.trading_runtime.strategy_liquidity_fade_publication import prepare_native_liquidity_fade_rows
    _, _, _, _, parent, event, prefix = graph('liquidity', number)
    with pytest.raises(ValueError):
        prepare_native_liquidity_fade_rows(None, (), (parent,), (event,),
            verified_prefix=prefix, first_price_source=None)


@pytest.mark.parametrize('number', [42, 64])
@pytest.mark.parametrize('kind', ['ah', 'liquidity'])
@pytest.mark.parametrize('change', [None, 'missing', 'tampered'])
def test_actual_oms_cold_second_inventory_loads_exact_native_exit_family(monkeypatch, number, kind, change):
    from test_profit_giveback_oms_recovery import prepared
    from src.trading_runtime import arte_oms_projection as oms, arte_intent_projection as intents
    from src.trading_runtime.arte_journal_writer import typed_row
    if kind == 'ah':
        from src.trading_runtime.arte_confirmed_ah_failure_v4 import CONFIRMED_AH_FAILURE as schema
    else:
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE as schema
    group, source, history, reservation, decision, _ = prepared(number)
    _, held, intent, row, _, _, _ = graph(kind, number)
    # Freshly projected own-number exit joins the independently prepared own-
    # number OMS authority; no other-number parent is adopted.
    row = {**row, 'run_id': history.run_id, 'batch_id': source.batch_id,
        'parent_record_id': source.record_id}
    from uuid import NAMESPACE_URL, uuid5
    suffix = 'confirmed-ah-failure' if kind == 'ah' else 'liquidity-fade-failure'
    row['record_id'] = str(uuid5(NAMESPACE_URL, f'{history.run_id}:{source.record_id}:{suffix}'))
    source = replace(source, intent=intent)
    group = replace(group, group={**group.group, 'strategy_intent_id': intent.intent_id},
        orders=(replace(group.orders[0], ticker=held.ticker, quantity=held.position_quantity,
            price=intent.reference_price),))
    reservation = {**reservation, 'intent_id': intent.intent_id, 'quantity': held.position_quantity}
    decision = {**decision, 'requested_quantity': held.position_quantity}
    prefix = V4CommittedPrefix(history.run_id, 12, group.group['batch_id'], 'cursor',
        'running', history.committed_batch_ids)
    monkeypatch.setattr(oms, 'load_latest_committed_oms_groups', lambda *a, **k: (group,))
    monkeypatch.setattr(intents, 'load_committed_strategy_intent_page', lambda *a, **k: (source,))
    monkeypatch.setattr(oms, 'load_committed_oms_admission_page', lambda *a, **k: {12: reservation})
    monkeypatch.setattr(oms, 'load_committed_oms_decision_page', lambda *a, **k: {12: decision})
    stored = typed_row(schema.name, row)
    if change == 'tampered': stored['bid'] += .01
    calls = []
    def native_rows(client, query):
        calls.append(query)
        assert schema.name in query and 'LIMIT 2 FORMAT JSONEachRow' in query
        return [] if change == 'missing' else [stored]
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer._rows', native_rows)
    if change:
        with pytest.raises(RuntimeError):
            oms.load_recovered_strategy_one_oms_lineage(None, prefix,
                allowed_accounts=frozenset({held.account_id}), protection_history=history,
                strategy_number=number)
    else:
        result = oms.load_recovered_strategy_one_oms_lineage(None, prefix,
            allowed_accounts=frozenset({held.account_id}), protection_history=history,
            strategy_number=number)
        assert len(result) == 1 and replace(result[0].approved_intent, metadata={}) == intent
    assert len(calls) == 1
