from dataclasses import replace
from datetime import date
from uuid import UUID
import pytest
from src.trading_runtime.arte_profit_giveback_v4 import (
    PROFIT_GIVEBACK, project_profit_giveback, restore_profit_giveback,
)
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import ENTRY, financial, intent

IDENTITY = '4e453e85-1ef1-44a9-80c9-1fdc6a506328'


def project(**changes):
    args = dict(session_date=date(2026, 8, 4), source_entry_intent_id=ENTRY,
                run_id='run', batch_id=IDENTITY, parent_record_id=IDENTITY,
                source_manager_snapshot_id=IDENTITY, source_manager_checkpoint_sequence=7)
    args.update(changes)
    return project_profit_giveback(profit_giveback(sample()),
        intent(strategy_number=args.get('strategy_number', 31)), financial(), **args)


@pytest.mark.parametrize('number', [31, 32, 33, 34, 35, 36])
def test_roundtrip_preserves_causal_scalars_without_claiming_a_seal(number):
    row = project(strategy_number=number)
    assert restore_profit_giveback(row) == profit_giveback(sample())
    assert 'content_hash' not in row
    UUID(row['record_id'])
    assert set(row) == {x[0] for x in PROFIT_GIVEBACK.columns} - {'content_hash'}
    assert row['strategy_number'] == number


def test_strategy32_row_cannot_project_strategy31_factory_intent():
    with pytest.raises(ValueError, match='factory intent'):
        project_profit_giveback(profit_giveback(sample()), intent(), financial(),
            session_date=date(2026, 8, 4), source_entry_intent_id=ENTRY, run_id='run',
            batch_id=IDENTITY, parent_record_id=IDENTITY,
            source_manager_snapshot_id=IDENTITY, source_manager_checkpoint_sequence=7,
            strategy_number=32)


@pytest.mark.parametrize('sequence', [0, -1, True, 2**64])
def test_checkpoint_sequence_must_be_positive_bounded_integer(sequence):
    with pytest.raises(ValueError):
        project(source_manager_checkpoint_sequence=sequence)


def test_changed_intent_reason_or_quantity_is_rejected():
    for changed in (replace(intent(), reason='strategy_nine_followthrough_failure'), replace(intent(), quantity=1.)):
        with pytest.raises(ValueError, match='factory intent'):
            project_profit_giveback(profit_giveback(sample()), changed, financial(),
                session_date=date(2026, 8, 4), source_entry_intent_id=ENTRY, run_id='run',
                batch_id=IDENTITY, parent_record_id=IDENTITY,
                source_manager_snapshot_id=IDENTITY, source_manager_checkpoint_sequence=7)


@pytest.mark.parametrize('field,value', [('strategy_number',30), ('prior_high_int',109999),
                                       ('prior_high_through_boundary_ms',10000), ('completed_close_int',105001),
                                       ('boundary_ms',10000.0)])
def test_changed_stored_rule_facts_are_rejected(field,value):
    row=project();row[field]=value
    with pytest.raises(ValueError):restore_profit_giveback(row)
