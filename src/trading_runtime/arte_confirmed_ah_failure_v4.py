"""Prepared scalar contract only: no table installation or writer registration.

Projection/restoration do not attest market provenance, committed ancestry or
current Portfolio/OMS state. Native admission must supply those checks before
this family can become an executable Strategy 34 exit.
"""
from dataclasses import fields
from datetime import timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_journal_schema import TableContract
from .strategy_confirmed_ah_risk_failure import ConfirmedAhRiskFailure
from .strategy_confirmed_ah_failure_exit import (
    confirmed_ah_exit_intent, validate_confirmed_ah_witness,
)
from .strategy_followthrough_failure import FollowThroughFailure

CONFIRMED_AH_FAILURE = TableContract('trading_confirmed_ah_failure_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('source_entry_intent_id', 'UUID'), ('assignment_id', 'String'),
    ('boundary_ms', 'UInt32'), ('first_held_boundary_ms', 'UInt32'),
    ('reference_ask', 'Decimal(38, 18)'), ('initial_stop', 'Decimal(38, 18)'),
    ('completed_close_int', 'UInt64'), ('macd_line', 'Float64'), ('macd_signal', 'Float64'),
    ('bid', 'Decimal(38, 18)'), ('ask', 'Decimal(38, 18)'), ('quote_age_us', 'UInt64'),
    ('completed_ten_second_boundary_ms', 'UInt32'),
    ('ten_second_macd_line', 'Float64'), ('ten_second_macd_signal', 'Float64'),
    ('content_hash', 'FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, parent_record_id, record_id')
TABLES = (CONFIRMED_AH_FAILURE,)


def project_confirmed_ah_failure(
    witness, intent, financial, *, session_date, source_entry_intent_id,
    run_id, batch_id, parent_record_id,
):
    """Exact unsealed factory projection; no committed-source claim."""
    expected = confirmed_ah_exit_intent(
        witness, financial, session_date=session_date,
        source_entry_intent_id=source_entry_intent_id,
    )
    if intent != expected or type(run_id) is not str or not run_id:
        raise ValueError('AH witness differs from its exact factory intent')
    for value in (batch_id, parent_record_id, source_entry_intent_id):
        UUID(value)
    return dict(
        record_id=str(uuid5(NAMESPACE_URL, f'{run_id}:{parent_record_id}:confirmed-ah-failure')),
        parent_record_id=parent_record_id, run_id=run_id,
        event_month=intent.event_time.astimezone(timezone.utc).strftime('%Y-%m-01'),
        batch_id=batch_id, strategy_number=34,
        source_entry_intent_id=source_entry_intent_id, assignment_id=financial.assignment_id,
        **{f.name: getattr(witness.five_second, f.name) for f in fields(FollowThroughFailure)},
        completed_ten_second_boundary_ms=witness.completed_ten_second_boundary_ms,
        ten_second_macd_line=witness.ten_second_macd_line,
        ten_second_macd_signal=witness.ten_second_macd_signal,
    )


def restore_confirmed_ah_failure(row):
    """Scalar replay only; stored-row hash/prefix verification must precede it."""
    if type(row.get('strategy_number')) is not int or row['strategy_number'] != 34:
        raise ValueError('AH confirmation belongs only to prepared Strategy 34')
    integers = {'boundary_ms', 'first_held_boundary_ms', 'completed_close_int', 'quote_age_us'}
    values = {}
    for f in fields(FollowThroughFailure):
        value = row[f.name]
        if f.name in integers:
            if type(value) is not int:
                raise ValueError('AH witness lacks exact integer producer authority')
            values[f.name] = value
        else:
            if type(value) is bool:
                raise ValueError('AH witness scalar cannot be boolean')
            values[f.name] = float(value)
    witness = ConfirmedAhRiskFailure(
        FollowThroughFailure(**values), row['completed_ten_second_boundary_ms'],
        row['ten_second_macd_line'], row['ten_second_macd_signal'],
    )
    validate_confirmed_ah_witness(witness)
    return witness
