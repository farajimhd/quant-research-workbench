"""Own source buffer, never a native publication authority."""
from dataclasses import replace
import pytest
from test_declared_native_submission import command, parent, RUN
from src.backend.backtest_declared_native_journal import DeclaredNativeJournal


def append(journal,command):
 return journal.append_declared_native_intent(submission=command,intent=command.intent,
  account_id=command.account_id,strategy_id=command.binding.identity.strategy_id,
  strategy_revision=command.binding.identity.revision)


def test_atomic_typed_sidecar_assignment_and_exact_retry(command):
 journal=DeclaredNativeJournal(binding=command.binding,run_id=command.binding.preparation.run_id)
 record=append(journal,command)
 assert journal.declared_submission_for_record(record.record_id) is command
 assert journal.assignment_for_intent(command.intent.intent_id)==command.assignment_id
 assert append(journal,command)==record
 assert len(journal.records(RUN))==1
 with pytest.raises(ValueError):append(journal,replace(command,intent=replace(command.intent,reference_price=1.)))
 assert len(journal.records(RUN))==1


def test_foreign_binding_and_closed_buffer_leave_no_source(command):
 with pytest.raises(ValueError):DeclaredNativeJournal(binding=command.binding,run_id='foreign')
 journal=DeclaredNativeJournal(binding=command.binding,run_id=command.binding.preparation.run_id)
 journal.close()
 with pytest.raises(RuntimeError):append(journal,command)
 assert journal.declared_submission_for_record('missing') is None


def test_source_buffer_capacity_failure_does_not_publish_sidecar(command):
 journal=DeclaredNativeJournal(binding=command.binding,run_id=RUN,max_pending_records=1)
 journal.append(run_id=RUN,category='fixture',entity_type='full',entity_id='full',payload={})
 with pytest.raises(RuntimeError,match='buffer is full'):append(journal,command)
 assert journal.assignment_for_intent(command.intent.intent_id) is None
 assert len(journal.records(RUN))==1

