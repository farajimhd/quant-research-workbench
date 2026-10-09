"""Real in-memory journal with an explicit already-committed entry-prefix seam."""
from dataclasses import replace
from datetime import date

import pytest

from test_arte_structural_rejection_exit_v1 import prepared, PARENT
from test_profit_armed_structural_rejection_publication import BATCH
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.arte_structural_rejection_exit_v1 import (
    require_buffered_structural_rejection_exit, project_buffered_structural_rejection_exit)


def journal_fixture(monkeypatch):
    _,confirmation,intent,context=prepared(monkeypatch)
    journal=BacktestMemoryJournal(run_id=confirmation.run_id,initial_sequence=9)
    # The real native original-entry prefix must be verified by the eventual
    # cold verifier. This seam tests journal buffering, not source admission.
    journal._entry_assignments[confirmation.request.source_entry_intent_id]='A1'
    return journal,confirmation,intent,context


def test_exact_journal_retry_is_idempotent_and_uses_declared_config(monkeypatch):
    journal,confirmation,intent,context=journal_fixture(monkeypatch)
    record=journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    assert journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent) is record
    assert journal.latest_sequence(journal.run_id)==10
    assert record.payload['strategy_id']==context.profile.owner.manager.runtime.config.strategy_id
    assert record.payload['strategy_revision']==context.profile.owner.manager.runtime.config.strategy_revision
    frozen=journal.structural_rejection_exit_for_record(record.record_id)
    assert frozen.intent is not intent and frozen.intent==intent
    row=project_buffered_structural_rejection_exit(frozen,parent_record_id=record.record_id,batch_id=BATCH)
    assert row['parent_record_id']==record.record_id and row['source_manager_checkpoint_sequence']==9
    assert context.profile.owner.manager.runtime.calls==['entry']


def test_frozen_journal_can_project_after_request_retirement_without_live_authority(monkeypatch):
    journal,confirmation,intent,context=journal_fixture(monkeypatch)
    record=journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    frozen=journal.structural_rejection_exit_for_record(record.record_id)
    before=project_buffered_structural_rejection_exit(frozen,parent_record_id=PARENT,batch_id=BATCH)
    context.profile.owner.complete_requests((confirmation.request,),boundary_ms=25000)
    object.__setattr__(intent,'quantity',999.)
    object.__setattr__(confirmation.request.financial,'position_quantity',999.)
    assert journal.structural_rejection_exit_for_record(record.record_id) is frozen
    assert project_buffered_structural_rejection_exit(frozen,parent_record_id=PARENT,batch_id=BATCH)==before
    with pytest.raises(ValueError): journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)


def test_fence_releases_pending_companion_and_close_releases_retry_cache(monkeypatch):
    journal,confirmation,intent,_=journal_fixture(monkeypatch)
    record=journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    frozen=journal.structural_rejection_exit_for_record(record.record_id)
    journal.mark_fenced(record.sequence)
    assert journal.structural_rejection_exit_for_record(record.record_id) is None
    assert require_buffered_structural_rejection_exit(frozen) is frozen
    assert journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent) is record
    journal.close()
    assert not journal._structural_rejection_exits and not journal._structural_rejection_intents
    with pytest.raises(RuntimeError,match='closed'):
        journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)


@pytest.mark.parametrize('kind',('run','assignment','missing-entry','checkpoint','intent','capacity'))
def test_invalid_admission_does_not_append_any_event(monkeypatch,kind):
    journal,confirmation,intent,_=journal_fixture(monkeypatch)
    if kind=='run': journal.run_id='foreign'
    elif kind=='assignment': journal._entry_assignments[confirmation.request.source_entry_intent_id]='other'
    elif kind=='missing-entry': journal._entry_assignments.clear()
    elif kind=='checkpoint': journal._next_sequence=8
    elif kind=='intent': intent=replace(intent,quantity=100.)
    else: journal.max_pending_records=0
    before=journal._next_sequence
    with pytest.raises(ValueError): journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    assert journal._next_sequence==before and journal._records==[]


@pytest.mark.parametrize('kind',('copy','field','nested-intent'))
def test_buffered_content_has_fresh_guards_on_async_use(monkeypatch,kind):
    journal,confirmation,intent,_=journal_fixture(monkeypatch)
    record=journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    frozen=journal.structural_rejection_exit_for_record(record.record_id)
    if kind=='copy': frozen=replace(frozen)
    elif kind=='field': object.__setattr__(frozen,'strategy_revision',999)
    else: frozen.intent.metadata['foreign']=True
    with pytest.raises(ValueError): require_buffered_structural_rejection_exit(frozen)


@pytest.mark.parametrize('own_source',(True,False))
def test_unimplemented_native_prefix_cannot_publish_as_ordinary_exit(monkeypatch,own_source):
    journal,confirmation,intent,_=journal_fixture(monkeypatch)
    if own_source: journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    else: journal.append(run_id=journal.run_id,category='strategy',entity_type='strategy_intent',
        entity_id=intent.intent_id,account_id='DU1',event_time=intent.event_time,payload=intent.payload())
    with pytest.raises(RuntimeError,match='cold-prefix admission|own frozen evidence'):
        project_pending_backtest_v4_prefix(journal,attempt_id=BATCH,run_month=date(2026,8,1),
            prior_sequence=9,prior_batch_id=PARENT,through_sequence=10,expected_config={'mode':'backtest'})
