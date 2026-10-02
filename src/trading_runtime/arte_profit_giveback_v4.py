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
        from .strategy_profit_giveback_exit import profit_giveback_reason
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
                or intent['action']!='exit' or intent['reason']!=profit_giveback_reason(row['strategy_number'])
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
    strategy_number: int = 31,
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
        witness, financial, session_date=session_date, source_entry_intent_id=source_entry_intent_id,
        strategy_number=strategy_number)
    if intent != expected:
        raise ValueError('Profit projection differs from immutable factory intent')
    return {
        'record_id': str(uuid5(NAMESPACE_URL, f'{run_id}:{parent_record_id}:profit-giveback')),
        'parent_record_id': parent_record_id, 'run_id': run_id,
        'event_month': intent.event_time.astimezone(timezone.utc).strftime('%Y-%m-01'),
        'batch_id': batch_id, 'strategy_number': strategy_number,
        'source_entry_intent_id': source_entry_intent_id, 'assignment_id': financial.assignment_id,
        'source_manager_snapshot_id': source_manager_snapshot_id,
        'source_manager_checkpoint_sequence': source_manager_checkpoint_sequence,
        **{f.name: getattr(witness, f.name) for f in fields(witness)},
    }


def restore_profit_giveback(row: dict) -> ProfitGivebackWitness:
    """Revalidate scalars after native row hash/source verification, not instead."""
    if type(row.get('strategy_number')) is not int or row['strategy_number'] not in (31, 32, 33, 34, 35, 36, 37):
        raise ValueError('Profit witness belongs only to Strategy 31 through 36')
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


def seal_profit_giveback_rows(client, rows, intents, events, *, prefix, first_price_source=None):
    """Complete source/factory sealing; table registration is a separate gate."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    from .arte_journal_writer import typed_row, _canonical_typed_content
    from .arte_intent_projection import project_strategy_intent
    from .strategy_profit_giveback_exit import profit_giveback_reason
    from .strategy_profit_giveback_source import load_profit_giveback_checkpoint
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_engine import AssignmentStatus, StrategyPermissions
    from .arte_journal_commit_v4 import V4CommittedPrefix
    reasons = {profit_giveback_reason(number) for number in (31, 32, 33, 34, 35, 36, 37)}
    parents={str(x['record_id']):x for x in intents if x['reason'] in reasons}
    event_map={str(x['record_id']):x for x in events}
    if (len(event_map)!=len(events) or len({str(x['record_id']) for x in intents})!=len(intents)
            or len(rows)!=len(parents)):
        raise ValueError('Profit exit witness is missing or extra')
    if rows and (type(prefix) is not V4CommittedPrefix or prefix.status!='running'
            or not prefix.batch_ids or prefix.last_batch_id!=prefix.batch_ids[-1]
            or any(row['run_id']!=prefix.run_id
                   or type(row['source_manager_checkpoint_sequence']) is not int
                   or not 0<row['source_manager_checkpoint_sequence']<=prefix.last_sequence
                   for row in rows)
            or not events or prefix.last_sequence>=min(event['sequence'] for event in events)):
        raise ValueError('Profit sealing requires an independently verified preceding prefix')
    result=[];seen=set()
    for row in rows:
        identity=str(row['parent_record_id'])
        if identity in seen or identity not in parents or identity not in event_map:
            raise ValueError('Profit witness lacks a unique exit event parent')
        seen.add(identity)
        parent,event=parents[identity],event_map[identity]
        witness=restore_profit_giveback(row)
        stamp=datetime.fromisoformat(str(event['event_time']).replace('Z','+00:00'))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        local=stamp.astimezone(ZoneInfo('America/New_York'))
        elapsed=round((local-datetime.combine(local.date(),time(4),local.tzinfo)).total_seconds()*1000)
        if (row['run_id']!=parent['run_id'] or row['batch_id']!=parent['batch_id']
                or str(row['record_id'])!=str(uuid5(NAMESPACE_URL,f'{row["run_id"]}:{identity}:profit-giveback'))
                or row['event_month']!=parent['event_month']
                or parent['intent_id']!=event['entity_id'] or parent['action']!='exit'
                or elapsed!=witness.boundary_ms
                or row['source_manager_checkpoint_sequence']>=event['sequence']):
            raise ValueError('Profit scalar witness differs from exit event authority')
        financial=StrategyOneFinancialView(row['assignment_id'],event['account_id'],parent['ticker'],
            AssignmentStatus.WATCHING,StrategyPermissions(),float(parent['quantity']),False,False,False,1)
        load_profit_giveback_checkpoint(client,prefix,row,financial,first_price_source=first_price_source)
        expected=profit_giveback_exit_intent(witness,financial,session_date=local.date(),
            source_entry_intent_id=str(row['source_entry_intent_id']), strategy_number=row['strategy_number'])
        content={k:v for k,v in parent.items() if k!='content_hash'}
        expected_content={**content,**{k:v for k,v in project_strategy_intent(expected).core.items() if k!='event_time'}}
        if (_canonical_typed_content('trading_strategy_intent_v1',content,stored_utc=True)
                !=_canonical_typed_content('trading_strategy_intent_v1',expected_content,stored_utc=True)):
            raise ValueError('Profit exit differs from immutable factory content')
        result.append(typed_row(PROFIT_GIVEBACK.name,{k:v for k,v in row.items() if k!='content_hash'}))
    return tuple(result)
