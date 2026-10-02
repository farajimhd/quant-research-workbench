"""Original committed entry graph checks, separate from held-state attestation."""
from dataclasses import replace

import pytest

from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.strategy_liquidity_fade_entry_source import validate_liquidity_fade_entry_source
from test_liquidity_fade_prepared_transport import transport


def graph(monkeypatch):
    row, base = transport()
    parent, event = dict(base.intents[0]), dict(base.events[0])
    prefix = V4CommittedPrefix(row['run_id'], 64, 'prior', 'cursor', 'running', ('prior',))
    source = dict(intent_id=row['source_entry_intent_id'], ticker=parent['ticker'],
                  action='enter_long', reason='strategy_one_entry',
                  reference_price=row['reference_ask'], invalidation_price=row['initial_stop'])
    source_event = dict(account_id=event['account_id'], sequence=12,
                       event_time='2026-08-10T20:26:20+00:00')
    child = dict(strategy_number=35, assignment_id=row['assignment_id'],
                 boundary_ms=row['first_held_boundary_ms']-100)
    calls = []
    def load(client, run_id, entry_id, **kwargs):
        calls.append((client, run_id, entry_id, kwargs))
        return source, source_event, child
    monkeypatch.setattr('src.trading_runtime.arte_followthrough_failure_v4._source_entry', load)
    return row, parent, event, prefix, source, source_event, child, calls


def test_graph_uses_existing_committed_ancestry_loader(monkeypatch):
    row, parent, event, prefix, _, _, _, calls = graph(monkeypatch)
    witness = validate_liquidity_fade_entry_source('client', row, parent, event,
        verified_prefix=prefix, first_price_source='pinned')
    assert witness.boundary_ms == row['boundary_ms']
    assert calls == [('client', row['run_id'], row['source_entry_intent_id'],
                     dict(prior_batch_id='prior', exit_batch_id=row['batch_id'],
                          verified_prefix=prefix, first_price_source='pinned'))]


@pytest.mark.parametrize('target,field,value', [
    ('source', 'reference_price', 9), ('source', 'invalidation_price', 9),
    ('source', 'ticker', 'OTHER'), ('source', 'action', 'exit'),
    ('source_event', 'account_id', 'other'), ('source_event', 'sequence', 65),
    ('source_event', 'event_time', '2026-08-09T20:26:20+00:00'),
    ('source_event', 'event_time', '2026-08-10T20:27:20+00:00'),
    ('child', 'strategy_number', 34), ('child', 'strategy_number', True),
    ('child', 'assignment_id', 'other'), ('child', 'boundary_ms', 57_600_000),
    ('parent', 'account_id', 'other'), ('parent', 'execution_quote_source', 'other'),
    ('event', 'category', 'other'), ('event', 'event_time', '2026-08-10T20:26:47.500000+00:00'),
    ('row', 'record_id', '00000000-0000-0000-0000-000000000001'),
])
def test_changed_native_entry_or_exit_graph_rejects(monkeypatch, target, field, value):
    row, parent, event, prefix, source, source_event, child, _ = graph(monkeypatch)
    families = dict(row=row, parent=parent, event=event, source=source,
                    source_event=source_event, child=child)
    families[target][field] = value
    with pytest.raises(ValueError):
        validate_liquidity_fade_entry_source(None, row, parent, event, verified_prefix=prefix)


@pytest.mark.parametrize('change', [dict(status='completed'), dict(run_id='other'),
    dict(last_sequence=65), dict(batch_ids=()), dict(last_batch_id='foreign')])
def test_invalid_predecessor_rejects_before_source_read(monkeypatch, change):
    row, parent, event, prefix, _, _, _, calls = graph(monkeypatch)
    with pytest.raises(ValueError):
        validate_liquidity_fade_entry_source(None, row, parent, event,
            verified_prefix=replace(prefix, **change))
    assert not calls
