"""Run-wide command registration; no installed financial authority."""
from dataclasses import replace

import pytest

from test_declared_native_submission import command, parent
from src.backend.backtest_declared_native_journal import DeclaredNativeJournal
from src.trading_runtime.declared_native_submission import DeclaredNativeSubmission, declared_entry_intent


def second_command(first):
    financial = replace(first.financial, assignment_id=first.assignment_id + '-second')
    preparation = replace(first.binding.preparation, assignment_id=financial.assignment_id)
    binding = replace(first.binding, preparation=preparation)
    proposal = preparation.propose(financial.ticker, first.proposal.boundary_ms, financial).proposal
    return DeclaredNativeSubmission(binding, proposal, financial, declared_entry_intent(binding, proposal))


def append(journal, submission):
    identity = submission.binding.identity
    return journal.append_declared_native_intent(submission=submission, intent=submission.intent,
        account_id=submission.account_id, strategy_id=identity.strategy_id, strategy_revision=identity.revision)


def test_two_assignments_share_ordered_journal_and_retry_fence(command):
    second = second_command(command)
    journal = DeclaredNativeJournal(bindings=(command.binding, second.binding), run_id=command.binding.preparation.run_id)
    first_record, second_record = append(journal, command), append(journal, second)
    assert second_record.sequence == first_record.sequence + 1
    assert append(journal, command) is first_record
    assert append(journal, second) is second_record
    journal.mark_fenced(second_record.sequence)
    assert journal.declared_submission_for_record(first_record.record_id) is None
    assert journal.declared_submission_for_record(second_record.record_id) is None
    assert append(journal, second) is second_record
    journal.close()


@pytest.mark.parametrize('change', ['configuration_hash', 'execution_spec_token', 'source', 'duplicate'])
def test_foreign_or_duplicate_cohort_rejected(command, change):
    second = second_command(command).binding
    if change in ('configuration_hash', 'execution_spec_token'):
        second = replace(second, **{change: 'c' * 64})
    elif change == 'source':
        second = replace(second, preparation=replace(second.preparation, source=replace(second.preparation.source)))
    else:
        second = command.binding
    with pytest.raises(ValueError, match='cohort'):
        DeclaredNativeJournal(bindings=(command.binding, second), run_id=command.binding.preparation.run_id)


def test_registry_is_immutable_and_equal_clone_cannot_submit(command):
    journal = DeclaredNativeJournal(binding=command.binding, run_id=command.binding.preparation.run_id)
    with pytest.raises(AttributeError):
        journal.binding = replace(command.binding)
    with pytest.raises(TypeError):
        journal._binding_registry[(command.account_id, command.assignment_id)] = replace(command.binding)
    with pytest.raises(ValueError, match='source binding'):
        append(journal, replace(command, binding=replace(command.binding)))
    assert journal.records(journal.run_id) == []


@pytest.mark.parametrize('bindings', [(), [], [None]])
def test_invalid_cohort_shape_rejected(command, bindings):
    with pytest.raises(ValueError):
        DeclaredNativeJournal(bindings=bindings, run_id=command.binding.preparation.run_id)
