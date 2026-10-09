"""Controlled queue checks; no source admission or native persistence claim."""
from dataclasses import replace
from queue import Queue
from threading import Lock
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.trading_runtime import selected_exit_publication_policy as policy
from src.trading_runtime.arte_followthrough_failure_v4 import V4FollowThroughFailureBatch
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units
from src.trading_runtime.arte_journal_writer import ArteJournalWriter, _ProfitPublicationUnit
from tests.test_arte_followthrough_failure_v4 import fixture


def envelope(compound):
    _, _, base, row, _, event, _ = fixture()
    if not compound:
        return V4FollowThroughFailureBatch(base, row)
    batch_id = str(UUID(int=44))
    first = replace(base, batch_id=batch_id, first_sequence=1, last_sequence=1,
                    events=({**event, 'batch_id': batch_id},))
    second = replace(base, prior_batch_id=batch_id)
    return coalesce_v4_units((first, V4FollowThroughFailureBatch(second, row)))


def queue_only_writer(unit):
    # Deliberately omit startup and the persistence thread: these tests prove
    # submission-envelope behavior only, not Keeper/source/market certification.
    writer = object.__new__(ArteJournalWriter)
    writer._client = SimpleNamespace()
    writer._journal_profile = 'backtest_v4'
    writer._run_id = unit.base.run_id
    writer._queue = Queue(8)
    writer._submission_lock = Lock()
    writer._closed = False
    writer._error = None
    writer._accepted_writes = False
    return writer


@pytest.mark.parametrize('compound', (False, True))
@pytest.mark.parametrize('selected', (False, True))
def test_inherited_followthrough_retains_context_only_when_selected(monkeypatch, compound, selected):
    unit = envelope(compound)
    writer = queue_only_writer(unit)
    source = object()
    observed = []
    # Explicit installed-policy and price-validator seams; actual submission
    # methods and immutable typed envelopes remain in use.
    monkeypatch.setattr(policy, 'requires_selected_followthrough_context_for_client',
                        lambda client, value: selected)
    monkeypatch.setattr(writer, '_validate_checkpoint_price_source',
                        lambda value, day: observed.append((value, day)))
    submit = writer.submit_compound_v4 if compound else writer.submit_followthrough_exit_v4
    receipt = submit(unit, first_price_source=source if selected else None)
    queued, queued_receipt = writer._queue.get_nowait()
    assert queued_receipt is receipt and not receipt.done()
    if selected:
        assert type(queued) is _ProfitPublicationUnit
        assert queued.unit is unit and queued.first_price_source is source
        assert len(observed) == 1 and observed[0][0] is source
    else:
        assert queued is unit and observed == []


@pytest.mark.parametrize('compound', (False, True))
def test_selected_price_validation_failure_never_enqueues(monkeypatch, compound):
    unit = envelope(compound)
    writer = queue_only_writer(unit)
    monkeypatch.setattr(policy, 'requires_selected_followthrough_context_for_client',
                        lambda client, value: True)
    def reject(value, day):
        raise ValueError('controlled missing or foreign price authority')
    monkeypatch.setattr(writer, '_validate_checkpoint_price_source', reject)
    submit = writer.submit_compound_v4 if compound else writer.submit_followthrough_exit_v4
    with pytest.raises(ValueError, match='price authority'):
        submit(unit, first_price_source=None)
    assert writer._queue.empty() and not writer._accepted_writes
