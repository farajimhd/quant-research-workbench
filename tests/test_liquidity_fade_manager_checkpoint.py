"""Historical lookup uses real manager scalar projection/hash/restore contracts."""
from dataclasses import replace
from datetime import date

import pytest

from src.trading_runtime.strategy_liquidity_fade_checkpoint import load_liquidity_fade_manager_checkpoint
from src.trading_runtime.strategy_one_management_snapshot import (
    _project_manager_snapshot_scalar, project_manager_snapshot,
)
from test_arte_liquidity_fade_failure_v4 import prepared_case
from test_liquidity_fade_entry_source import graph


def checkpoint_case(monkeypatch, *, changed_state=None):
    row, parent, event, prefix, source, source_event, child, entry_calls = graph(monkeypatch)
    witness, financial, state, _ = prepared_case()
    if changed_state is not None:
        state = changed_state(state)
    child['boundary_ms'] = state.submitted[0][1].boundary_ms
    rows = _project_manager_snapshot_scalar(run_id=row['run_id'], session_date=date(2026, 8, 10),
        checkpoint_sequence=row['source_manager_checkpoint_sequence'], state=state)
    row.update(source_manager_snapshot_id=rows.snapshot['snapshot_id'],
               source_manager_snapshot_hash=rows.snapshot['content_hash'])
    cursor = dict(run_id=row['run_id'], event_sequence=64, batch_id=prefix.last_batch_id,
                  session_date='2026-08-10', boundary_ms=witness.boundary_ms)
    calls = []
    def load_cursor(client, ceiling):
        calls.append(('cursor', ceiling))
        return cursor
    def load_rows(client, **kwargs):
        calls.append(('snapshot', kwargs))
        return rows
    monkeypatch.setattr('src.trading_runtime.arte_journal_projection.load_latest_backtest_cursor', load_cursor)
    monkeypatch.setattr('src.trading_runtime.strategy_one_management_snapshot.load_unattested_manager_snapshot_rows', load_rows)
    return row, parent, event, prefix, financial, state, rows, cursor, calls, entry_calls, child


def load(case):
    row, parent, event, prefix, financial, *_ = case
    return load_liquidity_fade_manager_checkpoint(None, prefix, row, parent, event, financial)


def test_exact_historical_cursor_snapshot_and_original_entry_bind_first_held(monkeypatch):
    case = checkpoint_case(monkeypatch)
    assert load(case) == case[5]
    assert case[8] == [('cursor', replace(case[3], last_sequence=64)),
                      ('snapshot', dict(run_id='run', checkpoint_sequence=64))]
    assert len(case[9]) == 1


@pytest.mark.parametrize('field,value', [('event_sequence', 63), ('boundary_ms', 44_807_300),
    ('session_date', '2026-08-09'), ('run_id', 'foreign'), ('batch_id', 'uncommitted')])
def test_old_or_foreign_cursor_rejects_before_snapshot_read(monkeypatch, field, value):
    case = checkpoint_case(monkeypatch)
    case[7][field] = value
    with pytest.raises(ValueError, match='decision cursor'):
        load(case)
    assert len(case[8]) == 1 and not case[9]


@pytest.mark.parametrize('field,value', [('source_manager_snapshot_id', '00000000-0000-0000-0000-000000000001'),
    ('source_manager_snapshot_hash', 'f'*64)])
def test_changed_checkpoint_reference_rejects(monkeypatch, field, value):
    case = checkpoint_case(monkeypatch)
    case[0][field] = value
    with pytest.raises(ValueError, match='immutable reference'):
        load(case)
    assert not case[9]


def test_validly_hashed_different_first_held_does_not_replace_witness(monkeypatch):
    def changed(state):
        key, boundary = state.first_held_boundaries[0]
        return replace(state, first_held_boundaries=((key, boundary+100),))
    case = checkpoint_case(monkeypatch, changed_state=changed)
    with pytest.raises(ValueError, match='first-held authority'):
        load(case)
    assert not case[9]


def test_native_child_hash_tampering_rejects_even_with_correct_root_pointer(monkeypatch):
    case = checkpoint_case(monkeypatch)
    case[6].first_held_boundaries[0]['first_held_boundary_ms'] += 100
    with pytest.raises(ValueError, match='children differ'):
        load(case)


def test_manager_original_entry_boundary_must_match_committed_entry(monkeypatch):
    case = checkpoint_case(monkeypatch)
    case[10]['boundary_ms'] += 100
    with pytest.raises(ValueError, match='committed original entry'):
        load(case)


def test_full_held_exit_must_equal_independently_verified_financial_quantity(monkeypatch):
    case = list(checkpoint_case(monkeypatch))
    case[4] = replace(case[4], position_quantity=37)
    with pytest.raises(ValueError, match='quantity differs'):
        load(case)
    assert not case[9]


@pytest.mark.parametrize('change', [dict(status='completed'), dict(last_sequence=63),
    dict(batch_ids=()), dict(run_id='foreign')])
def test_unverified_or_later_checkpoint_cannot_be_adopted(monkeypatch, change):
    case = list(checkpoint_case(monkeypatch))
    case[3] = replace(case[3], **change)
    with pytest.raises(ValueError, match='preceding prefix'):
        load(case)
    assert not case[8] and not case[9]


def test_strategy35_scalar_manager_cannot_omit_first_held_or_publish_without_price_authority():
    _, _, state, _ = prepared_case()
    with pytest.raises(ValueError, match='first held'):
        _project_manager_snapshot_scalar(run_id='run', session_date=date(2026, 8, 10),
            checkpoint_sequence=64, state=replace(state, first_held_boundaries=()))
    with pytest.raises(ValueError, match='native source context'):
        project_manager_snapshot(run_id='run', session_date=date(2026, 8, 10),
            checkpoint_sequence=64, state=state)
