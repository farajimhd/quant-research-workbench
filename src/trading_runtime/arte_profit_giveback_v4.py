"""Prepared normalized profit-protection contract; no installation or writer.

Projection alone does not prove source checkpoint ancestry. Journal admission
must verify the referenced manager snapshot and original committed entry before
this family can be registered for execution.
"""
from dataclasses import dataclass, fields
from datetime import date, timezone
from uuid import NAMESPACE_URL, UUID, uuid5
from types import MappingProxyType

from .arte_journal_schema import TableContract
from .strategy_profit_giveback import ProfitGivebackWitness
from .strategy_profit_giveback_exit import profit_giveback_exit_intent, validate_profit_giveback_witness

PROFIT_GIVEBACK = TableContract('trading_profit_giveback_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('source_entry_intent_id', 'UUID'), ('assignment_id', 'String'),
    ('source_manager_snapshot_id', 'UUID'), ('source_manager_checkpoint_sequence', 'UInt64'),
    ('boundary_ms', 'UInt32'), ('first_held_boundary_ms', 'UInt32'),
    ('reference_ask', 'Decimal(38, 18)'), ('initial_stop', 'Decimal(38, 18)'),
    ('completed_close_int', 'UInt64'), ('macd_line', 'Float64'), ('macd_signal', 'Float64'),
    ('bid', 'Decimal(38, 18)'), ('ask', 'Decimal(38, 18)'), ('quote_age_us', 'UInt64'),
    ('prior_high_int', 'UInt64'), ('prior_high_through_boundary_ms', 'UInt32'),
    ('content_hash', 'FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, parent_record_id, record_id')
TABLES = (PROFIT_GIVEBACK,)


@dataclass(frozen=True, slots=True)
class V4ProfitGivebackBatch:
    """Prepared one-intent transport unit; not admitted by the writer yet."""
    base: object
    profit: object

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch
        from .strategy_profit_giveback_exit import REASON
        if (type(self.base) is not TypedJournalBatch or self.base.status!='running'
                or len(self.base.events)!=1 or len(self.base.intents)!=1):
            raise ValueError('Profit witness needs one exact running typed intent')
        row=dict(self.profit)
        if set(row)!={name for name,_ in PROFIT_GIVEBACK.columns}-{'content_hash'}:
            raise ValueError('Profit unit requires exact unsealed scalar witness shape')
        witness=restore_profit_giveback(row)
        event,intent=self.base.events[0],self.base.intents[0]
        if (row['run_id']!=self.base.run_id or row['batch_id']!=self.base.batch_id
                or row['parent_record_id']!=event['record_id']
                or intent['record_id']!=event['record_id']
                or intent['intent_id']!=event['entity_id']
                or intent['action']!='exit' or intent['reason']!=REASON
                or float(intent['reference_price'])!=witness.bid
                or type(row['source_manager_checkpoint_sequence']) is not int
                or not 0<row['source_manager_checkpoint_sequence']<self.base.first_sequence):
            raise ValueError('Profit unit differs from its intent or prior checkpoint')
        for identity in ('source_entry_intent_id','source_manager_snapshot_id','record_id'):
            UUID(str(row[identity]))
        object.__setattr__(self,'profit',MappingProxyType(row))


def project_profit_giveback(
    witness, intent, financial, *, session_date: date, source_entry_intent_id: str,
    run_id: str, batch_id: str, parent_record_id: str,
    source_manager_snapshot_id: str, source_manager_checkpoint_sequence: int,
) -> dict:
    """Unsealed scalar projection; source verification must precede publication."""
    validate_profit_giveback_witness(witness)
    for identity in (source_entry_intent_id, batch_id, parent_record_id, source_manager_snapshot_id):
        UUID(identity)
    if (type(run_id) is not str or not run_id
            or type(source_manager_checkpoint_sequence) is not int
            or not 0 < source_manager_checkpoint_sequence < 2**64
            or any(type(getattr(witness, name)) is not int or not 0 < getattr(witness, name) < 2**64
                   for name in ('completed_close_int', 'prior_high_int'))):
        raise ValueError('Profit projection needs bounded source identity and prices')
    expected = profit_giveback_exit_intent(
        witness, financial, session_date=session_date, source_entry_intent_id=source_entry_intent_id)
    if intent != expected:
        raise ValueError('Profit projection differs from immutable factory intent')
    return {
        'record_id': str(uuid5(NAMESPACE_URL, f'{run_id}:{parent_record_id}:profit-giveback')),
        'parent_record_id': parent_record_id, 'run_id': run_id,
        'event_month': intent.event_time.astimezone(timezone.utc).strftime('%Y-%m-01'),
        'batch_id': batch_id, 'strategy_number': 31,
        'source_entry_intent_id': source_entry_intent_id, 'assignment_id': financial.assignment_id,
        'source_manager_snapshot_id': source_manager_snapshot_id,
        'source_manager_checkpoint_sequence': source_manager_checkpoint_sequence,
        **{f.name: getattr(witness, f.name) for f in fields(witness)},
    }


def restore_profit_giveback(row: dict) -> ProfitGivebackWitness:
    """Revalidate scalars after native row hash/source verification, not instead."""
    if type(row.get('strategy_number')) is not int or row['strategy_number'] != 31:
        raise ValueError('Profit witness belongs only to the prepared Strategy 31')
    integer_fields = {'boundary_ms', 'first_held_boundary_ms', 'completed_close_int',
                      'quote_age_us', 'prior_high_int', 'prior_high_through_boundary_ms'}
    converted = {}
    for f in fields(ProfitGivebackWitness):
        value = row[f.name]
        if f.name in integer_fields:
            if type(value) is not int:
                raise ValueError('Stored profit witness has noninteger producer authority')
            converted[f.name] = value
        else:
            converted[f.name] = float(value)
    witness = ProfitGivebackWitness(**converted)
    validate_profit_giveback_witness(witness)
    return witness
