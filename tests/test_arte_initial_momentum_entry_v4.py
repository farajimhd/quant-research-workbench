"""Normalized18 anchor source graph, exact IEEE restoration and fail-closed seals."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
import struct
from uuid import UUID, uuid5, NAMESPACE_URL

import numpy as np
import pytest

from src.trading_runtime.arte_initial_momentum_entry_v4 import (
    INITIAL_MOMENTUM, VALUES, initial_momentum_select_columns,
    decode_initial_momentum_row, project_initial_momentum_entry,
    restore_initial_momentum, seal_initial_momentum_rows,
)
from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry
from src.trading_runtime.strategy_initial_strong_momentum import (
    InitialStrongMomentumWitness, InitialMomentumSelectionWitness,
)
from tests.test_strategy_initial_strong_momentum import witness
from tests.test_strategy_one_intent import _proposal


@pytest.fixture(autouse=True)
def register_staged_contract(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer
    monkeypatch.setitem(writer._CONTRACTS, INITIAL_MOMENTUM.name, INITIAL_MOMENTUM)


def unit():
    current, first = witness(40_100), witness(30_100)
    selection = InitialMomentumSelectionWitness(
        InitialStrongMomentumWitness(30_000, first), 'c' * 64, 'd' * 64, 'e' * 64)
    proposal = replace(_proposal(), strategy_number=18, boundary_ms=40_100,
                       episode_start_ms=30_000, momentum=current)
    parent, batch = str(UUID(int=101)), str(UUID(int=102))
    kwargs = dict(run_id='initial-momentum-run', batch_id=batch,
                  parent_record_id=parent, event_month='2026-08-01')
    rows = project_initial_momentum_entry(proposal, selection, **kwargs)
    current_rows = project_rising_momentum_entry(proposal, **kwargs)
    entry = dict(parent_record_id=parent, run_id=kwargs['run_id'], batch_id=batch,
                 event_month=kwargs['event_month'], strategy_number=18,
                 assignment_id=proposal.assignment_id, boundary_ms=40_100,
                 episode_start_ms=30_000)
    identity = (f'strategy-18:2026-08-18:{proposal.assignment_id}:'
                f'{proposal.account_id}:{proposal.ticker}:40100:30000')
    intent = dict(record_id=parent, run_id=kwargs['run_id'], batch_id=batch,
                  event_month=kwargs['event_month'], ticker=proposal.ticker,
                  account_id=proposal.account_id, action='enter_long',
                  reason='strategy_one_entry', intent_id=str(uuid5(NAMESPACE_URL, identity)))
    event = dict(record_id=parent, run_id=kwargs['run_id'], batch_id=batch,
                 event_month=kwargs['event_month'], event_time='2026-08-18T08:00:40.100000+00:00',
                 category='strategy', entity_type='strategy_intent',
                 entity_id=intent['intent_id'], account_id=proposal.account_id)
    return proposal, selection, rows, (entry,), (intent,), (event,), current_rows


def restore(rows, proposal):
    return restore_initial_momentum(rows, ticker=proposal.ticker,
        boundary_ms=proposal.boundary_ms, episode_start_ms=proposal.episode_start_ms,
        current_momentum=proposal.momentum)


def test_project_restore_and_entire_graph_seal_bind_exact_companions():
    proposal, selection, rows, entries, intents, events, current = unit()
    assert restore(rows[::-1], proposal) == selection
    sealed = seal_initial_momentum_rows(rows, entries, intents, events, current)
    assert len(sealed) == 2
    assert all(len(row['content_hash']) == 64 for row in sealed)
    assert seal_initial_momentum_rows(sealed, entries, intents, events, current) == sealed
    assert project_initial_momentum_entry(proposal, selection,
        run_id=entries[0]['run_id'], batch_id=entries[0]['batch_id'],
        parent_record_id=entries[0]['parent_record_id'], event_month='2026-08-01') == rows


def test_ieee_bit_decoder_preserves_unrounded_values_and_nulls():
    value = float(np.nextafter(0.1, np.inf))
    row = dict(current_line=round(value, 1), current_signal=None,
               prior_line=0.0, prior_signal=None)
    exact = (value, None, 0.0, None)
    row.update({name + '_bits': None if source is None else int.from_bytes(
        struct.pack('>d', source), 'big') for name, source in zip(VALUES, exact)})
    assert decode_initial_momentum_row(row)['current_line'] == value
    assert decode_initial_momentum_row(row)['current_signal'] is None
    assert initial_momentum_select_columns().count('reinterpretAsUInt64') == 4
    row['current_signal_bits'] = 0
    with pytest.raises(ValueError, match='null scalar'):
        decode_initial_momentum_row(row)


@pytest.mark.parametrize('field,value', [
    ('strategy_number', 17), ('boundary_ms', 40_200), ('episode_start_ms', 30_100),
    ('first_setup_boundary_ms', 50_100), ('market_plan_token', 'f' * 64),
    ('source_build_id', 'f' * 64), ('source_attempt_id', str(UUID(int=3))),
    ('candidate_plan_token', 'malformed'), ('selection_token', 'malformed'),
    ('current_boundary_ms', 20_000), ('prior_boundary_ms', 10_000),
])
def test_restore_rejects_foreign_stale_or_malformed_scalar_sources(field, value):
    proposal, _, rows, *_ = unit()
    altered = tuple({**row, field: value} for row in rows)
    with pytest.raises(ValueError):
        restore(altered, proposal)


def test_selection_tokens_cannot_disagree_between_observations():
    proposal, _, rows, *_ = unit()
    rows = (rows[0], {**rows[1], 'entry_plan_token': 'f' * 64})
    with pytest.raises(ValueError, match='identity'):
        restore(rows, proposal)


def test_weak_initial_and_current_evidence_reject():
    proposal, selection, rows, entries, intents, events, current = unit()
    weak_first = replace(selection, initial=replace(selection.initial,
        first_setup=witness(30_100, strong=False)))
    with pytest.raises(ValueError, match='strong first and current'):
        project_initial_momentum_entry(proposal, weak_first,
            run_id=entries[0]['run_id'], batch_id=entries[0]['batch_id'],
            parent_record_id=entries[0]['parent_record_id'], event_month='2026-08-01')
    with pytest.raises(ValueError):
        restore(rows, replace(proposal, momentum=witness(40_100, strong=False)))


@pytest.mark.parametrize('mode', ['missing', 'extra', 'duplicate', 'old', 'foreign_parent',
                                 'foreign_batch', 'foreign_event', 'weak_current', 'stale_event'])
def test_entire_graph_seal_rejects_broken_scope_and_cardinality(mode):
    proposal, _, rows, entries, intents, events, current = unit()
    if mode == 'missing':
        rows = rows[:1]
    elif mode == 'extra':
        rows = (*rows, rows[0])
    elif mode == 'duplicate':
        rows = (rows[0], rows[0])
    elif mode == 'old':
        rows = tuple({**row, 'strategy_number': 17} for row in rows)
    elif mode == 'foreign_parent':
        intents = ({**intents[0], 'intent_id': str(UUID(int=9))},)
    elif mode == 'foreign_batch':
        rows = tuple({**row, 'batch_id': str(UUID(int=9))} for row in rows)
    elif mode == 'foreign_event':
        events = ({**events[0], 'run_id': 'foreign-run'},)
    elif mode == 'weak_current':
        current = tuple({**row, 'current_line': 1.0} for row in current)
    elif mode == 'stale_event':
        events = ({**events[0], 'event_time': '2026-08-18T08:00:40.200000+00:00'},)
    with pytest.raises(ValueError):
        seal_initial_momentum_rows(rows, entries, intents, events, current)


def test_stored_scalar_hash_changes_cannot_be_resealed():
    _, _, rows, entries, intents, events, current = unit()
    sealed = seal_initial_momentum_rows(rows, entries, intents, events, current)
    altered = ({**sealed[0], 'current_line': 3.0}, sealed[1])
    with pytest.raises(ValueError, match='content seal'):
        seal_initial_momentum_rows(altered, entries, intents, events, current)


def test_old_entry_has_no_initial_companion_and_never_accepts_one():
    proposal, selection, *_ = unit()
    kwargs = dict(run_id='run', batch_id=str(UUID(int=1)),
                  parent_record_id=str(UUID(int=2)), event_month='2026-08-01')
    assert project_initial_momentum_entry(replace(proposal, strategy_number=17), None, **kwargs) == ()
    with pytest.raises(ValueError, match='Old entry'):
        project_initial_momentum_entry(replace(proposal, strategy_number=17), selection, **kwargs)


@pytest.mark.parametrize('mode', ['duplicate_event', 'duplicate_intent', 'foreign_entity',
                                 'foreign_category', 'foreign_account'])
def test_source_graph_cannot_replace_or_ambiguously_repeat_parent_authority(mode):
    _, _, rows, entries, intents, events, current = unit()
    if mode == 'duplicate_event':
        events = (*events, events[0])
    elif mode == 'duplicate_intent':
        intents = (*intents, intents[0])
    elif mode == 'foreign_entity':
        events = ({**events[0], 'entity_id': str(UUID(int=20))},)
    elif mode == 'foreign_category':
        events = ({**events[0], 'category': 'broker'},)
    elif mode == 'foreign_account':
        events = ({**events[0], 'account_id': 'foreign'},)
    with pytest.raises(ValueError):
        seal_initial_momentum_rows(rows, entries, intents, events, current)


def test_restore_rejects_untyped_current_and_noncanonical_parent():
    proposal, _, rows, *_ = unit()
    with pytest.raises(ValueError):
        restore(rows, replace(proposal, momentum=None))
    altered = tuple({**row, 'parent_record_id': str(UUID(int=0)),
        'record_id': str(uuid5(NAMESPACE_URL,
            f"{UUID(int=0)}:initial-momentum:{row['resolution_ms']}"))} for row in rows)
    with pytest.raises(ValueError, match='canonical UUID'):
        restore(altered, proposal)
