"""One complete own-source packet and its contiguous scalar journal unit.

Management semantic bases are fragments of ONE original batch. They have the
same committed predecessor, so the unpublished-microbatch rekeying coalescer
does not apply. Concatenation preserves every original row and hash verbatim.
This structural transport is neither installed nor financial authority.
"""
from dataclasses import dataclass, field, fields
from datetime import date
from uuid import UUID

from .arte_journal_writer import TypedJournalBatch
from . import arte_declared_native_command_v4 as entry
from . import arte_declared_native_management_v4 as management
from .arte_declared_native_entry_readback import _COUNTS as ENTRY_COUNTS
from src.backend.backtest_declared_native_fixed_entry import declared_entry_intent_identity


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('Declared unit requires canonical UUID strings')


def _combine(packet):
    if type(packet) is entry.DeclaredEntryRows:
        bases, tables, entity = (packet.base,), entry.TABLES, 'declared_native_intent'
    elif type(packet) is management.DeclaredManagementRows:
        bases, tables, entity = packet.bases, management.TABLES, 'declared_native_management_intent'
    else:
        raise ValueError('Declared unit requires an exact complete packet type')
    packet.__post_init__()
    if not bases or len(bases) > 65_536:
        raise ValueError('Declared unit semantic inventory is empty or unbounded')
    first = bases[0]
    headers = ('run_id','run_month','attempt_id','batch_id','prior_batch_id','source_cursor','status')
    for name in ('run_id','attempt_id','batch_id','prior_batch_id'):
        _uuid(getattr(first,name))
    if (type(first.run_month) is not date or first.run_month.day != 1
            or type(first.source_cursor) is not str or not first.source_cursor or first.status != 'running'):
        raise ValueError('Declared unit original batch header differs')
    populated = {'events','intents','intent_slices'}
    row_fields = tuple(f.name for f in fields(TypedJournalBatch) if f.name not in
                       (*headers,'first_sequence','last_sequence'))
    record_ids, intent_ids, slice_ids = set(), set(), set()
    next_sequence = first.first_sequence
    for base in bases:
        if type(base) is not TypedJournalBatch or any(
                type(getattr(base,n)) is not type(getattr(first,n)) or getattr(base,n) != getattr(first,n)
                for n in headers):
            raise ValueError('Declared unit fragments have foreign batch headers')
        if (type(base.first_sequence) is not int or type(base.last_sequence) is not int
                or base.first_sequence < 1 or base.first_sequence != next_sequence
                or base.last_sequence != base.first_sequence
                or len(base.events) != 1 or len(base.intents) != 1):
            raise ValueError('Declared unit semantic sequences/coverage differ')
        next_sequence += 1
        if any(type(getattr(base,n)) is not tuple or n not in populated and getattr(base,n)
               for n in row_fields):
            raise ValueError('Declared unit contains unexpected shared families')
        event, intent = base.events[0], base.intents[0]
        if (event['entity_type'] != entity or event['category'] != 'strategy'
                or type(event['sequence']) is not int or event['sequence'] != base.first_sequence
                or event['correlation_id'] != '' or event['causation_id'] != ''
                or event['entity_id'] != intent['intent_id']
                or event['account_id'] != intent['account_id']):
            raise ValueError('Declared unit original event/intent link differs')
        for row in (*base.events,*base.intents,*base.intent_slices):
            _uuid(row['record_id'])
            if (row['run_id'] != first.run_id
                    or row['batch_id'] != first.batch_id
                    or type(row['event_month']) is not str or row['event_month'] != first.run_month.isoformat()):
                raise ValueError('Declared unit scalar envelope differs')
        if (event['record_id'] != intent['record_id'] or event['record_id'] in record_ids
                or intent['intent_id'] in intent_ids):
            raise ValueError('Declared unit original intent identity duplicates/differs')
        record_ids.add(event['record_id']); intent_ids.add(intent['intent_id'])
        if event['attempt_id'] != first.attempt_id:
            raise ValueError('Declared unit original event attempt differs')
        if any(r['parent_record_id'] != event['record_id'] for r in base.intent_slices):
            raise ValueError('Declared unit protection-slice parent differs')
        if (type(intent['protection_slice_count']) is not int
                or intent['protection_slice_count'] != len(base.intent_slices)
                or tuple(r['ordinal'] for r in base.intent_slices) != tuple(range(len(base.intent_slices)))
                or any(type(r['ordinal']) is not int or r['account_id'] != event['account_id'] for r in base.intent_slices)):
            raise ValueError('Declared unit protection-slice count/account/order differs')
        for row in base.intent_slices:
            if row['record_id'] in slice_ids or row['record_id'] in record_ids:
                raise ValueError('Declared unit protection-slice identity duplicates')
            slice_ids.add(row['record_id'])
    families = packet.families
    if type(families) is not tuple or tuple(n for n,_ in families) != tuple(t.name for t in tables):
        raise ValueError('Declared unit companion table identity differs')
    if len(families[0][1]) != 1:
        raise ValueError('Declared unit requires its unique complete companion root')
    for _, rows in families:
        companion_ids = set()  # record identity is table-qualified, not global
        for row in rows:
            _uuid(row['record_id'])
            if (row['run_id'] != first.run_id or row['event_month'] != first.run_month.isoformat()
                    or row['batch_id'] != first.batch_id or row['parent_record_id'] != first.events[0]['record_id']
                    or row['record_id'] in companion_ids or row['record_id'] in record_ids):
                raise ValueError('Declared unit companion envelope/parent/identity differs')
            companion_ids.add(row['record_id'])
    top = families[0][1][0]
    if any(event['account_id'] != top['account_id'] or intent['account_id'] != top['account_id']
           or intent['ticker'] != top['ticker']
           for base in bases for event,intent in zip(base.events,base.intents,strict=True)):
        raise ValueError('Declared unit source root/account/ticker differs')
    if type(packet) is entry.DeclaredEntryRows:
        if top['intent_id'] != first.intents[0]['intent_id']:
            raise ValueError('Declared unit entry source root/intent differs')
        original_identity = declared_entry_intent_identity(first.run_id,top['strategy_number'],
            top['strategy_id'],top['revision'],top['source_token'],top['assignment_id'],
            top['account_id'],top['ticker'],top['boundary_ms'],top['episode_start_ms'])
        if top['intent_id'] != original_identity:
            raise ValueError('Declared unit entry own identity/request link differs')
        if tuple(len(r) for _,r in families) != ENTRY_COUNTS:
            raise ValueError('Declared unit complete entry family coverage differs')
    else:
        counts = {name:len(rows) for name,rows in families}
        kind = top['command_type']
        if kind not in ('DeclaredExitCommand','DeclaredProtectionCommand','DeclaredSessionCommand'):
            raise ValueError('Declared unit unsupported management command')
        expected = (1,1,2) if kind == 'DeclaredProtectionCommand' else (0,0,0) if kind == 'DeclaredSessionCommand' else (1,0,0)
        if (counts[management.COMPLETED.name],counts[management.PROTECTION.name],counts[management.STATE.name]) != expected:
            raise ValueError('Declared unit complete management family coverage differs')
    for _,rows in families:
        if rows and 'ordinal' in rows[0] and 'phase' not in rows[0] and 'group' not in rows[0]:
            if tuple(row['ordinal'] for row in rows) != tuple(range(len(rows))):
                raise ValueError('Declared unit companion ordinal ordering differs')
    if (top['companion_contract'] != (entry.CONTRACT if type(packet) is entry.DeclaredEntryRows else management.CONTRACT)
            or top['predecessor_batch_id'] != first.prior_batch_id
            or type(top['predecessor_sequence']) is not int or top['predecessor_sequence'] != first.first_sequence-1
            or top['predecessor_cursor'] != first.source_cursor
            or top['account_id'] != first.events[0]['account_id']):
        raise ValueError('Declared unit source root/predecessor differs')
    combined = TypedJournalBatch(**{n:getattr(first,n) for n in headers},
        first_sequence=first.first_sequence,last_sequence=bases[-1].last_sequence,
        **{n:tuple(row for b in bases for row in getattr(b,n)) for n in row_fields})
    return combined


@dataclass(frozen=True, slots=True)
class DeclaredNativeV4Unit:
    """Structural transport only; management identity needs fresh full context.

The shared semantic rows do not carry own strategy/revision declarations.
Fresh preparation binds them through the full managed spec/original entry;
that still does not attest committed financial/protection predecessors.
"""
    packet: entry.DeclaredEntryRows | management.DeclaredManagementRows
    base: TypedJournalBatch = field(init=False)

    def __post_init__(self):
        fresh = _combine(self.packet)
        if hasattr(self,'base'):
            if not entry._exact(self.base,fresh):
                raise ValueError('Declared unit cached scalar base differs from complete packet')
        else:
            object.__setattr__(self,'base',fresh)


def prepare_declared_native_v4_unit(packet, **source_context):
    """Mandatory source-equivalence preparation, without approving financials.

Future publication additionally needs independently verified committed
financial/protection predecessor and installed source authority. No such
publication hook is opened here or by the structural constructor.
"""
    if type(packet) is entry.DeclaredEntryRows:
        entry.readback_declared_entry_source_equivalence(packet,**source_context)
    elif type(packet) is management.DeclaredManagementRows:
        management.readback_declared_management_transport(packet,**source_context)
    else:
        raise ValueError('Declared unit requires an exact complete packet type')
    return DeclaredNativeV4Unit(packet)
