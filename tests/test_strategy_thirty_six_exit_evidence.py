"""Prepared exit evidence binds numbered ancestry and actual broker quantities.

The broker scalar/hash contracts and intent transport are real. Historical
context, entry and OMS readers are mocked here; these checks grant no release
registration or financial performance claim.
"""
from dataclasses import replace
from datetime import date

import pytest

from src.trading_runtime.arte_intent_projection import project_strategy_intent
from src.trading_runtime.arte_liquidity_fade_failure_v4 import (
    CHECKPOINT_REFERENCE_FIELDS, project_liquidity_fade_failure, restore_liquidity_fade_failure,
)
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from src.trading_runtime.strategy_liquidity_fade_entry_source import validate_liquidity_fade_entry_source
from test_arte_liquidity_fade_failure_v4 import prepared_case
from test_liquidity_fade_prepared_transport import transport
from test_liquidity_fade_entry_source import graph
from test_liquidity_fade_financial_checkpoint import case, load


def successor(row, parent):
    """Rebuild the actual factory and projection, never just relabel a row."""
    witness, financial, _, _ = prepared_case()
    intent = liquidity_fade_exit_intent(witness, financial, session_date=date(2026, 8, 10),
        source_entry_intent_id=row['source_entry_intent_id'], strategy_number=36)
    source_keys = ('source_build_id', 'source_bars_attempt_id', 'source_indicators_attempt_id',
                   'source_liquidity_attempt_id', 'source_market_plan_token', *CHECKPOINT_REFERENCE_FIELDS)
    projected = project_liquidity_fade_failure(witness, intent, financial,
        session_date=date(2026, 8, 10), source_entry_intent_id=row['source_entry_intent_id'],
        run_id=row['run_id'], batch_id=row['batch_id'], parent_record_id=row['parent_record_id'],
        strategy_number=36, **{key: row[key] for key in source_keys})
    row.clear()
    row.update(projected)
    parent.update({key: value for key, value in project_strategy_intent(intent).core.items()
                   if key != 'event_time'})
    return intent


def successor_transport():
    row, base = transport()
    parent, event = dict(base.intents[0]), dict(base.events[0])
    intent = successor(row, parent)
    event['entity_id'] = intent.intent_id
    return row, replace(base, intents=(parent,), events=(event,))


def test_complete_four_count_witness_roundtrips_and_freezes_successor_transport():
    row, base = successor_transport()
    unit = V4LiquidityFadeFailureBatch(base, row)
    assert unit.failure['strategy_number'] == 36
    assert restore_liquidity_fade_failure(row) == prepared_case()[0]
    assert tuple(row[f'trade_count_{i}'] for i in range(4)) == (57, 18, 8, 5)
    with pytest.raises(TypeError):
        unit.failure['strategy_number'] = 35


@pytest.mark.parametrize('changed', ['row_number', 'parent_reason', 'parent_uuid', 'counts'])
def test_successor_transport_rejects_relabeling_or_changed_factory(changed):
    row, base = successor_transport()
    if changed == 'row_number':
        row['strategy_number'] = 35
    elif changed == 'counts':
        row['trade_count_2'] = 101
    else:
        parent = dict(base.intents[0])
        parent['reason' if changed == 'parent_reason' else 'intent_id'] = (
            'strategy_thirty_five_liquidity_fade_failure' if changed == 'parent_reason'
            else row['source_entry_intent_id'])
        base = replace(base, intents=(parent,))
    with pytest.raises(ValueError):
        V4LiquidityFadeFailureBatch(base, row)


def numbered_graph(monkeypatch):
    data = graph(monkeypatch)
    row, parent, event, _, _, _, child, _ = data
    event['entity_id'] = successor(row, parent).intent_id
    child['strategy_number'] = 36
    return data


def test_successor_requires_same_numbered_original_entry(monkeypatch):
    row, parent, event, prefix, _, _, child, _ = numbered_graph(monkeypatch)
    assert validate_liquidity_fade_entry_source(None, row, parent, event,
        verified_prefix=prefix).boundary_ms == row['boundary_ms']
    child['strategy_number'] = 35
    with pytest.raises(ValueError, match='committed original entry'):
        validate_liquidity_fade_entry_source(None, row, parent, event, verified_prefix=prefix)


def financial_case(monkeypatch, **kwargs):
    data = case(monkeypatch, **kwargs)
    row, parent, event = data[:3]
    event['entity_id'] = successor(row, parent).intent_id
    data[6]['strategy_revision'] = 36
    for item in data[8]:
        item.state.group['strategy_revision'] = 36
    return data


def test_successor_financial_reader_checks_real_broker_bits_and_numbered_oms_scope(monkeypatch):
    data = financial_case(monkeypatch)
    assert load(data).held_quantity == 100
    assert data[9][-1][2]['strategy_number'] == 36


@pytest.mark.parametrize('quantity', [0, 37, 100.00000000000001])
def test_successor_claimed_exit_quantity_cannot_replace_native_broker_bits(monkeypatch, quantity):
    with pytest.raises(ValueError, match='native broker checkpoint'):
        load(financial_case(monkeypatch, quantity=quantity))


@pytest.mark.parametrize('changed', ['context', 'oms'])
def test_successor_rejects_parent_numbered_context_or_oms_lineage(monkeypatch, changed):
    data = financial_case(monkeypatch)
    if changed == 'context':
        data[6]['strategy_revision'] = 35
    else:
        data[8][0].state.group['strategy_revision'] = 35
    with pytest.raises(ValueError, match='pinned'):
        load(data)
