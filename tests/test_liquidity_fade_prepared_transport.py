"""Prepared transport identity/immutability; no registered writer or source seal."""
from dataclasses import replace
from datetime import date

import pytest

from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from src.trading_runtime.arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE
from src.trading_runtime.arte_intent_projection import project_strategy_intent
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from test_arte_liquidity_fade_failure_v4 import prepared_case, IDENTITY


def transport():
    witness, financial, _, row = prepared_case()
    intent = liquidity_fade_exit_intent(witness, financial, session_date=date(2026, 8, 10),
                                      source_entry_intent_id=row['source_entry_intent_id'])
    parent = dict(project_strategy_intent(intent).core)
    at = parent.pop('event_time')
    parent.update(record_id=row['parent_record_id'], run_id=row['run_id'],
                  batch_id=row['batch_id'], event_month=row['event_month'], account_id=financial.account_id)
    event = dict(record_id=row['parent_record_id'], run_id=row['run_id'], batch_id=row['batch_id'],
                 sequence=65, event_time=at, account_id=financial.account_id,
                 category='strategy', entity_type='strategy_intent', entity_id=intent.intent_id)
    base = TypedJournalBatch(row['run_id'], date(2026, 8, 1), IDENTITY, row['batch_id'], IDENTITY,
                             65, 65, 'native-cursor', 'running', (event,), intents=(parent,))
    return row, base


def test_exact_factory_transport_freezes_values_with_scalar_codec_only():
    row, base = transport()
    unit = V4LiquidityFadeFailureBatch(base, row)
    row['bid'] = 1
    assert unit.failure['bid'] == 2.31
    with pytest.raises(TypeError):
        unit.failure['bid'] = 1
    from src.trading_runtime.arte_journal_writer import _CONTRACTS
    assert _CONTRACTS[LIQUIDITY_FADE_FAILURE.name] is LIQUIDITY_FADE_FAILURE
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    assert numbered_fixed_strategy(35).strategy_number == 35
    assert numbered_fixed_strategy(36).strategy_number == 36
    for number in (37, 38, 39):
        assert numbered_fixed_strategy(number).strategy_number == number
    with pytest.raises(ValueError):
        numbered_fixed_strategy(40)


@pytest.mark.parametrize('target,field,value', [
    ('row', 'record_id', '00000000-0000-0000-0000-000000000001'),
    ('row', 'run_id', 'other'), ('row', 'source_entry_intent_id', '00000000-0000-0000-0000-000000000001'),
    ('row', 'content_hash', 'a'*64), ('event', 'sequence', 66), ('event', 'entity_type', 'signal'),
    ('row', 'source_manager_checkpoint_sequence', 65),
    ('event', 'event_time', '2026-08-10T20:26:47.500000+00:00'),
    ('event', 'account_id', 'other'), ('parent', 'execution_quote_source', 'other'),
    ('parent', 'action', 'reduce'), ('parent', 'reason', 'foreign'),
])
def test_changed_parent_graph_scalar_identity_or_policy_rejects(target, field, value):
    row, base = transport()
    if target == 'row': row[field] = value
    elif target == 'event': base = replace(base, events=(dict(base.events[0], **{field: value}),))
    else: base = replace(base, intents=(dict(base.intents[0], **{field: value}),))
    with pytest.raises(ValueError):
        V4LiquidityFadeFailureBatch(base, row)


def test_transport_cannot_hide_extra_event_family_or_terminal_status():
    row, base = transport()
    for changed in (replace(base, status='completed'), replace(base, events=base.events*2),
                    replace(base, intents=base.intents*2),
                    replace(base, executions=({'record_id': IDENTITY},))):
        with pytest.raises(ValueError):
            V4LiquidityFadeFailureBatch(changed, row)
