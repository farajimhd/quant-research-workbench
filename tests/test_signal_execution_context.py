from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from src.backend.signal_execution_context import (
    SignalExecutionContext, seal_context, restore_context_seal,
    project_context_row, restore_context_row,
)
from src.backend.signal_stream_typed_cursor import STATE_DELTA
from src.trading_runtime.journal_contract import canonical_json


def context(run='backtest:2026-10-15:actual-1'):
    return SignalExecutionContext(run, 'backtest', '2026-10-15', 'configuration-1', 'source-1')


def row():
    value = dict(schema_version=1, session_key='2026-10-15', batch_sequence=1,
        signal_stream_id='stream', ticker='ABC', operation='upsert', matching=True,
        definition_revision='definition-1', last_emitted_at=None)
    return {**value, 'content_hash': sha256(canonical_json(value).encode()).hexdigest()}


def test_context_native_run_calendar_seal_roundtrip_and_immutable():
    selected = context()
    assert restore_context_seal(seal_context(selected)) == selected
    assert selected.source_journal_run_id == selected.run_id
    assert selected.seal_hash == seal_context(selected)['content_hash']
    with pytest.raises(FrozenInstanceError):
        selected.run_id = 'other'
    for changed in (replace(selected, run_id='backtest:2026-10-15:actual-2'),
                    replace(selected, mode='replay'), replace(selected, session_key='2026-10-16'),
                    replace(selected, configuration_revision='configuration-2'),
                    replace(selected, source_revision='source-2')):
        assert changed.context_id != selected.context_id


def test_repeated_day_rows_remain_logically_identical_but_separate_contexts():
    first, second = context(), context('backtest:2026-10-15:actual-2')
    original = row()
    a = project_context_row(first, STATE_DELTA, original)
    b = project_context_row(second, STATE_DELTA.name, original)
    assert a['content_hash'] == b['content_hash'] == original['content_hash']
    assert a['source_context_id'] != b['source_context_id']
    assert a['context_content_hash'] != b['context_content_hash']
    assert restore_context_row(first, STATE_DELTA, a) == original
    with pytest.raises(ValueError, match='integrity'):
        restore_context_row(second, STATE_DELTA, a)


@pytest.mark.parametrize('change', [dict(run_id=''), dict(mode='BACKTEST'),
    dict(session_key='20261015'), dict(configuration_revision=' '), dict(source_revision='')])
def test_context_rejects_noncanonical_or_missing_identity(change):
    with pytest.raises(ValueError):
        replace(context(), **change)


def test_envelope_rejects_modified_fields_hash_calendar_and_extra_columns():
    selected, original = context(), row()
    projected = project_context_row(selected, STATE_DELTA, original)
    for changed in ({**projected, 'ticker': 'XYZ'}, {**projected, 'extra': 'opaque'},
                    {**projected, 'context_content_hash': '0' * 64},
                    {**projected, 'source_context_id': '1' * 64}):
        with pytest.raises(ValueError):
            restore_context_row(selected, STATE_DELTA, changed)
    with pytest.raises(ValueError):
        project_context_row(replace(selected, session_key='2026-10-16'), STATE_DELTA, original)
    with pytest.raises(ValueError):
        restore_context_seal({**seal_context(selected), 'source_journal_run_id': 'other'})


def test_correctly_hashed_foreign_revision_cannot_acquire_native_context():
    from src.backend.signal_dispatch_typed_cursor import INTENT_COMMIT
    selected = context()
    value = dict(schema_version=1, session_key=selected.session_key, source_batch_sequence=1,
        source_cursor_commit_hash='a' * 64, configuration_revision_id='foreign-config',
        intent_count=0, intent_hash='b' * 64)
    original = {**value, 'content_hash': sha256(canonical_json(value).encode()).hexdigest()}
    with pytest.raises(ValueError, match='revision differs'):
        project_context_row(selected, INTENT_COMMIT, original)
    value['configuration_revision_id'] = selected.configuration_revision
    original = {**value, 'content_hash': sha256(canonical_json(value).encode()).hexdigest()}
    assert restore_context_row(selected, INTENT_COMMIT,
        project_context_row(selected, INTENT_COMMIT, original)) == original

