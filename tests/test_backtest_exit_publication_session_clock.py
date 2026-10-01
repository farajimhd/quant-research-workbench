"""A mixed journal prefix can start with a present-day creation event."""
from dataclasses import replace
from datetime import date, datetime, timezone
from queue import Queue
from threading import RLock
from uuid import UUID

import pytest

from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units


@pytest.mark.parametrize('family', ['profit', 'confirmation'])
@pytest.mark.parametrize('change', ['valid', 'missing_parent', 'naive_clock', 'mixed_sessions'])
def test_mixed_prefix_uses_exit_parent_clock_for_native_session(monkeypatch, family, change):
    if family == 'profit':
        from test_profit_giveback_typed_batch import unit
        from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch
        base, row = unit(strategy_number=34)
        exit_unit = V4ProfitGivebackBatch(base, row)
    else:
        from test_arte_confirmed_ah_failure_v4 import prepared_transport
        from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
        row, base = prepared_transport(monkeypatch)
        exit_unit = V4ConfirmedAhFailureBatch(base, row)
    from tests.test_arte_journal_writer import batch
    seed = batch()
    creation = writer.typed_row('trading_event_v1', {
        **{k: v for k, v in seed.events[0].items() if k != 'content_hash'},
        'run_id': base.run_id, 'batch_id': base.prior_batch_id,
        'attempt_id': base.attempt_id, 'record_id': str(UUID(int=999)),
        'sequence': base.first_sequence - 1,
        'event_time': datetime(2026, 10, 1, 12, tzinfo=timezone.utc).isoformat(),
    })
    preceding = replace(seed, run_id=base.run_id, attempt_id=base.attempt_id,
        batch_id=base.prior_batch_id, prior_batch_id=str(UUID(int=998)),
        first_sequence=base.first_sequence - 1, last_sequence=base.first_sequence - 1,
        events=(creation,))
    compound = coalesce_v4_units((preceding, exit_unit))
    key = 'profit_givebacks' if family == 'profit' else 'confirmed_ah_failures'
    if change == 'missing_parent':
        compound = replace(compound, children={**compound.children,
            key: ({**compound.children[key][0], 'parent_record_id': str(UUID(int=997))},)})
    elif change == 'naive_clock':
        events = tuple({**event, 'event_time': '2026-08-04T16:00:00'}
                       if str(event['record_id']) == str(row['parent_record_id']) else event
                       for event in compound.base.events)
        compound = replace(compound, base=replace(compound.base, events=events))
    elif change == 'mixed_sessions':
        events = tuple({**event, 'category': 'strategy', 'entity_type': 'strategy_intent'}
                       if str(event['record_id']) == str(UUID(int=999)) else event
                       for event in compound.base.events)
        compound = replace(compound, base=replace(compound.base, events=events),
            children={**compound.children, key: (*compound.children[key],
                {**compound.children[key][0], 'parent_record_id': str(UUID(int=999))})})
    journal = object.__new__(writer.ArteJournalWriter)
    journal._journal_profile, journal._run_id = 'backtest_v4', base.run_id
    journal._submission_lock, journal._queue = RLock(), Queue()
    journal._closed, journal._error = False, None
    source = object()
    calls = []
    def validate(actual, session):
        assert actual is source
        assert session == date(2026, 8, 4 if family == 'profit' else 10)
        calls.append(session)
    journal._validate_checkpoint_price_source = validate
    if change == 'valid':
        future = journal.submit_compound_v4(compound, first_price_source=source)
        assert calls and not future.done() and journal._queue.qsize() == 1
    else:
        with pytest.raises(ValueError, match={
            'missing_parent': 'linked strategy event',
            'naive_clock': 'timezone-aware event',
            'mixed_sessions': 'different native sessions',
        }[change]):
            journal.submit_compound_v4(compound, first_price_source=source)
        assert not calls and journal._queue.empty()
