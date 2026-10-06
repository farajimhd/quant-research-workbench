"""Normalized prepared management transport, never financial recovery authority.

Named scalar families retain all supported reducer inputs. Unknown producer or
level fields fail closed instead of being dropped. Historical arm/protection
and complete management producer attestation are still separate missing hooks.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone, date
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from . import arte_declared_native_command_v4 as entry_rows
from .arte_journal_schema import TableContract
from .arte_journal_writer import TypedJournalBatch
from .arte_intent_projection import strategy_intent_batch
from .journal_contract import JournalRecord, canonical_json
from .declared_native_management_submission import declared_management_submission, DeclaredNativeManagementSubmission
from .arte_declared_native_fixed_sources import DeclaredHistoricalPredecessor
from .declared_native_management_command import (DeclaredManagementContext, DeclaredExitInputs,
    DeclaredExitCommand, DeclaredProtectionInputs, DeclaredProtectionCommand, DeclaredSessionCommand, _equal)
from .declared_native_submission import DeclaredSubmissionBinding
from .strategy_one_stateful import StrategyOneFinancialView
from .strategy_engine import AssignmentStatus, StrategyPermissions
from .strategy_followthrough_failure import FollowThroughFailureInput
from .strategy_liquidity_fade_failure import LiquidityFadeCandle
from .strategy_one_position import ProtectionState, AcceptedResistance, ResistanceBreak
from .strategy_profit_giveback_arm import ProfitArmCandidate
from src.backend.backtest_declared_native_fixed_management import DeclaredManagementPolicy, DeclaredProfitArmReference
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation

CONTRACT = 'declared-native-management-companion@1'
_COMMON = (('record_id','UUID'),('parent_record_id','UUID'),('run_id','String'),
           ('event_month','Date'),('batch_id','UUID'))

def _table(name, columns):
    return TableContract(name, (*_COMMON,*columns,('content_hash','FixedString(64)')),
                         'toYYYYMM(event_month)','run_id,parent_record_id,record_id')

CONTEXT = _table('trading_declared_native_management_v1', (
    ('companion_contract','String'),('command_type','String'),('result_hash','FixedString(64)'),
    ('entry_record_id','UUID'),('entry_intent_id','UUID'),('strategy_id','String'),('revision','UInt32'),
    ('account_id','String'),('assignment_id','UUID'),('ticker','String'),('session_date','Date'),
    ('configuration_hash','FixedString(64)'),('managed_spec_hash','FixedString(64)'),
    ('core_spec_token','FixedString(64)'),('source_token','FixedString(64)'),('boundary_ms','UInt32'),
    ('first_held_boundary_ms','Nullable(UInt32)'),('source_build_id','String'),
    ('source_market_plan_token','FixedString(64)'),('source_bars_attempt_id','UUID'),
    ('source_indicators_attempt_id','UUID'),('source_liquidity_attempt_id','UUID'),
    ('predecessor_batch_id','UUID'),('predecessor_sequence','UInt64'),('predecessor_cursor','String'),
    ('portfolio_state_hash','FixedString(64)'),('broker_snapshot_hash','FixedString(64)'),
    ('financial_status','String'),('position_quantity','Float64'),('position_quantity_is_int','UInt8'),
    *((name,'UInt8') for name in ('pending_entry','pending_exit','pending_capital_request')),
    *((name,'UInt32') for name in ('completed_entries','reentry_not_before_ms','current_purchase_groups')),
    *((f'permission_{name}','UInt8') for name in ('observe','enter','add','reduce','exit','reenter')),
))
_COMPLETED = (('boundary_ms','UInt32'),('first_held_boundary_ms','UInt32'),('reference_ask','Float64'),
    ('initial_stop','Float64'),('completed_five_second_boundary_ms','Nullable(UInt32)'),
    ('completed_five_second_close_int','Nullable(UInt64)'),('price_valid','UInt8'),
    ('macd_line','Nullable(Float64)'),('macd_signal','Nullable(Float64)'),('bid','Nullable(Float64)'),
    ('ask','Nullable(Float64)'),('quote_age_us','Nullable(UInt64)'),('position_quantity','Float64'),
    ('position_quantity_is_int','UInt8'),('pending_exit','UInt8'))
COMPLETED = _table('trading_declared_native_management_completed_v1', _COMPLETED)
# These are the explicit scalar producer fields supported by this packet.
# Presence is distinct from nullable/unavailable and from a numeric zero.
_RESOLUTION = (('ticker','String'),('boundary_ms','UInt32'),('price_valid','UInt8'),('extremes_valid','UInt8'),
    ('quote_valid','UInt8'),('quote_timestamp_us','UInt64'),('open_int','UInt64'),('high_int','UInt64'),
    ('low_int','UInt64'),('close_int','UInt64'),('bid_int','UInt64'),('ask_int','UInt64'),
    ('bid_size','UInt64'),('ask_size','UInt64'),('trade_count','UInt64'),('volume','UInt64'),
    ('macd_line','Nullable(Float64)'),('macd_signal','Nullable(Float64)'))
RESOLUTION = _table('trading_declared_native_management_resolution_v1',(
    ('ordinal','UInt32'),('resolution_ms','UInt32'),
    *((name,'Nullable('+kind+')') if not kind.startswith('Nullable') else (name,kind) for name,kind in _RESOLUTION),
    *((name+'_present','UInt8') for name,_ in _RESOLUTION)))
CANDLE = _table('trading_declared_native_management_candle_v1', (
    ('ordinal','UInt32'),('boundary_ms','UInt32'),('trade_count','UInt64')))
ARM = _table('trading_declared_native_management_arm_v1', (
    ('source_token','FixedString(64)'),('snapshot_id','UUID'),('checkpoint_sequence','UInt64'),
    ('journal_batch_id','UUID'),('snapshot_hash','FixedString(64)'),('boundary_ms','UInt32'),
    ('first_held_boundary_ms','UInt32'),('reference_ask','Float64'),('initial_stop','Float64'),('high_int','UInt64')))
PROTECTION = _table('trading_declared_native_management_protection_v1', (
    ('now_ms','UInt32'),('bid','Float64'),('ask','Float64'),('tick','Float64'),
    ('low_boundary_ms','Nullable(UInt32)'),('low_int','Nullable(UInt64)'),
    ('low_price_valid','UInt8'),('low_extremes_valid','UInt8'),('price_bearing_bar','UInt8')))
STATE = _table('trading_declared_native_management_state_v1', (
    ('phase','String'),('boundary_ms','UInt32'),('stop','Float64'),('target','Float64'),
    ('earned_groups','UInt32'),('applied_groups','UInt32')))
ACCEPTED = _table('trading_declared_native_management_accepted_v1', (
    ('phase','String'),('group','String'),('ordinal','UInt32'),('unified_level_id','String'),
    ('lower','Nullable(Float64)'),('upper','Nullable(Float64)')))
_LEVEL = (('unified_level_id','String'),('lower','Float64'),('upper','Float64'),
          ('role','String'),('side','String'),('transition_from','String'))
LEVEL = _table('trading_declared_native_management_level_v1', (
    ('group','String'),('ordinal','UInt32'),('completed_boundary_ms','Nullable(UInt32)'),
    *((name,'Nullable('+kind+')') for name,kind in _LEVEL),
    *((name+'_present','UInt8') for name,_ in _LEVEL)))
TABLES = (CONTEXT,COMPLETED,RESOLUTION,CANDLE,ARM,PROTECTION,STATE,ACCEPTED,LEVEL)

@dataclass(frozen=True,slots=True)
class DeclaredManagementRows:
    bases: tuple[TypedJournalBatch,...]
    families: tuple

    def __post_init__(self):
        if (type(self.bases) is not tuple or not self.bases or len(self.bases)>2
                or any(type(b) is not TypedJournalBatch for b in self.bases)
                or type(self.families) is not tuple
                or tuple(n for n,_ in self.families)!=tuple(t.name for t in TABLES)):
            raise ValueError('Declared management normalized packet shape differs')
        copied=[]
        for table,(_,rows) in zip(TABLES,self.families,strict=True):
            if type(rows) is not tuple or len(rows)>65_536:
                raise ValueError('Declared management row inventory is not bounded/ordered')
            result=[]
            for row in rows:
                if not isinstance(row,Mapping): raise ValueError('Declared management row is not scalar')
                sealed=entry_rows._seal(table,{k:v for k,v in row.items() if k!='content_hash'})
                if row.get('content_hash')!=sealed['content_hash']: raise ValueError('Declared management row hash differs')
                result.append(sealed)
            copied.append((table.name,tuple(result)))
        object.__setattr__(self,'families',tuple(copied))

def _strict_number(value):
    if type(value) not in (int,float): raise ValueError('Declared numeric scalar alias')
    converted=float(value)
    if type(value) is int and int(converted)!=value:
        raise ValueError('Declared integer scalar cannot fit Float64 losslessly')
    return converted

def _mapping(value, schema):
    if not isinstance(value,Mapping) or not set(value)<=dict(schema).keys():
        raise ValueError('Declared producer/geometry field has no normalized contract')
    result={}
    for name,kind in schema:
        result[name+'_present']=int(name in value)
        result[name]=value.get(name)
        if name in value:
            entry_rows._scalar(value[name],kind)
    return result

def _unmapping(row,schema):
    if any(row[n+'_present'] not in (0,1) or not row[n+'_present'] and row[n] is not None for n,_ in schema):
        raise ValueError('Declared producer presence differs')
    return {n:row[n] for n,_ in schema if row[n+'_present']}

def _projection_rows(bases, submission, entry_packet, predecessor):
    command=submission.command; context=command.context; p=context.source
    event=bases[0].events[0]
    common=dict(parent_record_id=event['record_id'],run_id=context.run_id,
                event_month=event['event_month'],batch_id=bases[0].batch_id)
    collected={t.name:[] for t in TABLES}
    def put(table,key,values):
        collected[table.name].append(entry_rows._seal(table,dict(record_id=str(uuid5(NAMESPACE_URL,
            event['record_id']+':'+CONTRACT+':'+key)),**common,**values)))
    financial=context.financial
    if type(financial.status) is not AssignmentStatus or type(financial.permissions) is not StrategyPermissions:
        raise ValueError('Declared financial enum/permission types differ')
    row=dict(companion_contract=CONTRACT,command_type=type(command).__name__,
        result_hash=entry_rows._hash(_project(command.replay())),entry_record_id=entry_packet.base.events[0]['record_id'],
        entry_intent_id=p.intent_id,strategy_id=p.strategy_id,revision=p.revision,account_id=p.account_id,
        assignment_id=p.assignment_id,ticker=p.ticker,session_date=context.session_date.isoformat(),
        configuration_hash=submission.binding.configuration_hash,managed_spec_hash=entry_rows._hash(submission.spec.payload()),
        core_spec_token=submission.binding.execution_spec_token,source_token=context.source_token,
        boundary_ms=context.boundary_ms,first_held_boundary_ms=context.first_held_boundary_ms,
        **context.observation_source,predecessor_batch_id=predecessor.prior_batch_id,
        predecessor_sequence=predecessor.prior_sequence,predecessor_cursor=predecessor.prefix.source_cursor,
        portfolio_state_hash=predecessor.portfolio_state_hash,broker_snapshot_hash=predecessor.broker_snapshot_hash,
        financial_status=financial.status.value,position_quantity=_strict_number(financial.position_quantity),
        position_quantity_is_int=int(type(financial.position_quantity) is int))
    for name in ('pending_entry','pending_exit','pending_capital_request'):
        if type(getattr(financial,name)) is not bool: raise ValueError('Declared financial flag differs')
        row[name]=int(getattr(financial,name))
    for name in ('completed_entries','reentry_not_before_ms','current_purchase_groups'): row[name]=getattr(financial,name)
    for name in ('observe','enter','add','reduce','exit','reenter'):
        value=getattr(financial.permissions,name)
        if type(value) is not bool: raise ValueError('Declared permission alias')
        row['permission_'+name]=int(value)
    put(CONTEXT,'context',row)
    exits=command.inputs if type(command) is DeclaredExitCommand else command.exit_inputs if type(command) is DeclaredProtectionCommand else None
    if exits is not None:
        row={f.name:getattr(exits.completed,f.name) for f in fields(FollowThroughFailureInput)}
        row['position_quantity_is_int']=type(exits.completed.position_quantity) is int
        for n,k in _COMPLETED:
            if k=='UInt8':
                if type(row[n]) is not bool: raise ValueError('Declared completed flag alias')
                row[n]=int(row[n])
            elif k=='Float64' or k=='Nullable(Float64)':
                if row[n] is not None:
                    if n!='position_quantity' and type(row[n]) is not float:
                        raise ValueError('Declared completed Float64 scalar has an integer alias')
                    row[n]=_strict_number(row[n])
        put(COMPLETED,'completed',row)
        resolutions={} if not exits.ten else {10000:exits.ten}
        for i,candle in enumerate(exits.candles): put(CANDLE,str(i),dict(ordinal=i,boundary_ms=candle.boundary_ms,trade_count=candle.trade_count))
        if exits.prior_arm is not None:
            arm=exits.prior_arm; values={f.name:getattr(arm,f.name) for f in fields(arm) if f.name not in ('run_id','candidate')}
            values.update({f.name:getattr(arm.candidate,f.name) for f in fields(arm.candidate)
                if f.name not in ('account_id','assignment_id','ticker')})
            put(ARM,'arm',values)
    else: resolutions=command.resolutions
    for i,(resolution,values) in enumerate(sorted(resolutions.items())):
        if type(resolution) is not int: raise ValueError('Declared resolution alias')
        put(RESOLUTION,str(i),dict(ordinal=i,resolution_ms=resolution,**_mapping(values,_RESOLUTION)))
    if type(command) is DeclaredProtectionCommand:
        inputs=command.inputs
        values={f.name:getattr(inputs,f.name) for f in fields(inputs) if f.name not in ('previous','breaks','overhead_levels')}
        for name in ('low_price_valid','low_extremes_valid','price_bearing_bar'): values[name]=int(values[name])
        put(PROTECTION,'protection',values)
        for phase,state in (('prior',inputs.previous),('result',command.transition.state)):
            put(STATE,phase,{n:getattr(state,n) for n in ('boundary_ms','stop','target','earned_groups','applied_groups')}|dict(phase=phase))
            for i,identity in enumerate(sorted(state.accepted_ids)):
                put(ACCEPTED,phase+':id:'+str(i),dict(phase=phase,group='ids',ordinal=i,unified_level_id=identity,lower=None,upper=None))
            for group in ('pending_group','earned_group'):
                for i,accepted in enumerate(getattr(state,group)):
                    put(ACCEPTED,phase+':'+group+':'+str(i),dict(phase=phase,group=group,ordinal=i,
                        unified_level_id=accepted.unified_level_id,lower=accepted.lower,upper=accepted.upper))
        for i,broken in enumerate(inputs.breaks): put(LEVEL,'break:'+str(i),dict(group='breaks',ordinal=i,
            completed_boundary_ms=broken.completed_boundary_ms,**_mapping(broken.level,_LEVEL)))
        for i,level in enumerate(inputs.overhead_levels): put(LEVEL,'overhead:'+str(i),dict(group='overhead',ordinal=i,
            completed_boundary_ms=None,**_mapping(level,_LEVEL)))
    return tuple((t.name,tuple(collected[t.name])) for t in TABLES)

def _project(value):
    from src.backend.backtest_declared_native_fixed_management import _projection
    return _projection(value)

def _bind(entry_packet, *, resolver,spec,envelope,approval,market,entry_predecessor,predecessor,boundary):
    if type(predecessor) is not DeclaredHistoricalPredecessor:
        raise ValueError('Declared management requires exact predecessor request')
    facts=entry_rows.readback_declared_entry_source_equivalence(entry_packet,resolver=resolver,spec=spec,
        envelope=envelope,approval=approval,market=market,predecessor=entry_predecessor)
    predecessor.__post_init__()
    if (predecessor.run_id!=facts.proposal.run_id or predecessor.configuration_hash!=facts.configuration_hash
            or predecessor.market_plan_token!=market.token or predecessor.boundary_ms!=boundary
            or predecessor.prior_sequence<entry_packet.base.last_sequence
            or type(predecessor.prefix.last_sequence) is not int
            or type(predecessor.prefix.source_cursor) is not str or not predecessor.prefix.source_cursor
            or type(predecessor.prefix.batch_ids) is not tuple
            or len(set(predecessor.prefix.batch_ids))!=len(predecessor.prefix.batch_ids)):
        raise ValueError('Declared management predecessor/entry binding differs')
    for value in predecessor.prefix.batch_ids: entry_rows._scalar(value,'UUID')
    return facts

def _bases(records,submission,predecessor,attempt_id,batch_id,run_month,source_cursor):
    if type(records) is not tuple or len(records)!=len(submission.intents) or not records:
        raise ValueError('Declared complete ordered management records missing')
    result=[]
    for ordinal,(record,intent) in enumerate(zip(records,submission.intents,strict=True)):
        if (type(record) is not JournalRecord or type(record.sequence) is not int
                or record.sequence!=predecessor.prior_sequence+ordinal+1
                or record.run_id!=submission.command.context.run_id or record.account_id!=submission.account_id
                or record.category!='strategy' or record.entity_type!='declared_native_management_intent'
                or record.entity_id!=intent.intent_id or record.event_time!=intent.event_time
                or type(record.event_time) is not datetime or record.event_time.tzinfo is None
                or type(record.recorded_at) is not datetime or record.recorded_at.tzinfo is None
                or canonical_json(record.payload)!=canonical_json({**intent.payload(),
                    'strategy_id':submission.binding.identity.strategy_id,'strategy_revision':submission.binding.identity.revision})):
            raise ValueError('Declared management original record/request differs')
        for value in (record.record_id,attempt_id,batch_id): entry_rows._scalar(value,'UUID')
        batch=strategy_intent_batch(intent,run_id=record.run_id,run_month=run_month,account_id=record.account_id,
            attempt_id=attempt_id,batch_id=batch_id,prior_batch_id=predecessor.prior_batch_id,
            sequence=record.sequence,source_cursor=source_cursor,run_status='running',recorded_at=record.recorded_at,
            record_id=record.record_id,correlation_id='',causation_id='')
        result.append(replace(batch,events=(dict(batch.events[0],entity_type=record.entity_type),)))
    return tuple(result)

def project_declared_management(records,submission, *,entry_packet,resolver,spec,envelope,approval,market,
        entry_predecessor,predecessor,attempt_id,batch_id,run_month,source_cursor):
    if type(submission) is not DeclaredNativeManagementSubmission or submission.spec!=spec:
        raise ValueError('Declared management exact complete submission required')
    context=submission.command.context
    submission.verify(run_id=context.run_id,strategy_id=context.source.strategy_id,strategy_revision=context.source.revision,
        account_id=submission.account_id,session_date=context.session_date)
    facts=_bind(entry_packet,resolver=resolver,spec=spec,envelope=envelope,approval=approval,market=market,
        entry_predecessor=entry_predecessor,predecessor=predecessor,boundary=context.boundary_ms)
    if (not _equal(facts.proposal,context.source) or submission.binding.configuration_hash!=envelope['payload_hash']
            or batch_id!=predecessor.batch_id or source_cursor!=predecessor.prefix.source_cursor
            or type(run_month) is not date or run_month!=submission.event_time.astimezone(timezone.utc).date().replace(day=1)):
        raise ValueError('Declared management source/configuration/current envelope differs')
    bases=_bases(records,submission,predecessor,attempt_id,batch_id,run_month,source_cursor)
    return DeclaredManagementRows(bases,_projection_rows(bases,submission,entry_packet,predecessor))

def _one(rows):
    if len(rows)!=1: raise ValueError('Declared management singular coverage differs')
    return rows[0]

def readback_declared_management_transport(packet, *,entry_packet,resolver,spec,envelope,approval,market,
        entry_predecessor,predecessor):
    """Reload entry lineage and replay transport; financial/state claims unapproved."""
    if type(packet) is not DeclaredManagementRows: raise ValueError('Declared management packet type differs')
    packet.__post_init__(); rows={n:r for n,r in packet.families}; top=_one(rows[CONTEXT.name])
    facts=_bind(entry_packet,resolver=resolver,spec=spec,envelope=envelope,approval=approval,market=market,
        entry_predecessor=entry_predecessor,predecessor=predecessor,boundary=top['boundary_ms'])
    source,_,_,_=resolver.reload_entry_plan();p=facts.proposal
    prep=DeclaredEntryPreparation(p.run_id,p.assignment_id,p.account_id,source)
    binding=DeclaredSubmissionBinding(prep,date.fromisoformat(market.sessions[0]),spec.execution.entry_request,
        envelope['payload_hash'],entry_rows._hash(spec.execution.payload()))
    flags=('pending_entry','pending_exit','pending_capital_request')
    permissions=('observe','enter','add','reduce','exit','reenter')
    if any(top[n] not in (0,1) for n in (*flags,'position_quantity_is_int',*(f'permission_{n}' for n in permissions))):
        raise ValueError('Declared financial flags malformed')
    qty=top['position_quantity']
    if top['position_quantity_is_int']:
        if int(qty)!=qty: raise ValueError('Declared integer quantity lost precision')
        qty=int(qty)
    financial=StrategyOneFinancialView(p.assignment_id,p.account_id,p.ticker,AssignmentStatus(top['financial_status']),
        StrategyPermissions(**{n:bool(top['permission_'+n]) for n in permissions}),qty,
        *(bool(top[n]) for n in flags),*(top[n] for n in ('completed_entries','reentry_not_before_ms','current_purchase_groups')))
    context=DeclaredManagementContext(p.run_id,source.token,prep,binding.session_date,p,financial,
        DeclaredManagementPolicy(source.parent.capabilities),top['boundary_ms'],top['first_held_boundary_ms'],
        {n:top[n] for n in ('source_build_id','source_market_plan_token','source_bars_attempt_id','source_indicators_attempt_id','source_liquidity_attempt_id')})
    resolutions={r['resolution_ms']:_unmapping(r,_RESOLUTION) for r in rows[RESOLUTION.name]}
    kind=top['command_type']
    if kind=='DeclaredSessionCommand': command=DeclaredSessionCommand(context,resolutions)
    elif kind in ('DeclaredExitCommand','DeclaredProtectionCommand'):
        r=_one(rows[COMPLETED.name]);values={f.name:r[f.name] for f in fields(FollowThroughFailureInput)}
        for n in ('price_valid','pending_exit'):
            if values[n] not in (0,1): raise ValueError('Declared completed flag malformed')
            values[n]=bool(values[n])
        if r['position_quantity_is_int'] not in (0,1): raise ValueError('Declared completed quantity type flag malformed')
        if r['position_quantity_is_int']:
            if int(values['position_quantity'])!=values['position_quantity']:
                raise ValueError('Declared completed integer quantity lost precision')
            values['position_quantity']=int(values['position_quantity'])
        arm=None
        if rows[ARM.name]:
            r=_one(rows[ARM.name]);candidate=ProfitArmCandidate(p.account_id,p.assignment_id,p.ticker,
                *(r[n] for n in ('boundary_ms','first_held_boundary_ms','reference_ask','initial_stop','high_int')))
            arm=DeclaredProfitArmReference(p.run_id,r['source_token'],candidate,r['snapshot_id'],r['checkpoint_sequence'],r['journal_batch_id'],r['snapshot_hash'])
        exits=DeclaredExitInputs(FollowThroughFailureInput(**values),resolutions.get(10000,{}),
            tuple(LiquidityFadeCandle(r['boundary_ms'],r['trade_count']) for r in rows[CANDLE.name]),arm)
        if kind=='DeclaredExitCommand':
            selected=exits.replay(context)
            if selected is None: raise ValueError('Declared stored exit does not select an exit')
            command=DeclaredExitCommand(context,exits,*selected)
        else:
            r=_one(rows[PROTECTION.name]);state_row=next((r for r in rows[STATE.name] if r['phase']=='prior'),None)
            if state_row is None: raise ValueError('Declared protection prior state missing')
            accepted=[r for r in rows[ACCEPTED.name] if r['phase']=='prior']
            def group(name): return tuple(AcceptedResistance(r['unified_level_id'],r['lower'],r['upper']) for r in accepted if r['group']==name)
            state=ProtectionState(*(state_row[n] for n in ('boundary_ms','stop','target')),
                frozenset(r['unified_level_id'] for r in accepted if r['group']=='ids'),group('pending_group'),group('earned_group'),
                state_row['earned_groups'],state_row['applied_groups'])
            values={n:r[n] for n in ('now_ms','bid','ask','tick','low_boundary_ms','low_int')}
            for n in ('low_price_valid','low_extremes_valid','price_bearing_bar'):
                if r[n] not in (0,1): raise ValueError('Declared protection flag malformed')
                values[n]=bool(r[n])
            levels=rows[LEVEL.name]
            inputs=DeclaredProtectionInputs(state,**values,
                breaks=tuple(ResistanceBreak(r['completed_boundary_ms'],_unmapping(r,_LEVEL)) for r in levels if r['group']=='breaks'),
                overhead_levels=tuple(_unmapping(r,_LEVEL) for r in levels if r['group']=='overhead'))
            command=DeclaredProtectionCommand(context,exits,inputs,inputs.replay(context,exits))
    else: raise ValueError('Declared management command kind unsupported')
    submission=declared_management_submission(binding,spec,command)
    expected_rows=_projection_rows(packet.bases,submission,entry_packet,predecessor)
    if packet.families!=expected_rows: raise ValueError('Declared management rows/source/replay/order differ')
    records=tuple(JournalRecord(b.events[0]['record_id'],p.run_id,predecessor.prior_sequence+i+1,
        intent.event_time,datetime.fromisoformat(b.events[0]['recorded_at']),'strategy',
        'declared_native_management_intent',intent.intent_id,p.account_id,
        {**intent.payload(),'strategy_id':p.strategy_id,'strategy_revision':p.revision})
        for i,(b,intent) in enumerate(zip(packet.bases,submission.intents,strict=True)))
    expected=_bases(records,submission,predecessor,packet.bases[0].attempt_id,predecessor.batch_id,
        submission.event_time.astimezone(timezone.utc).date().replace(day=1),predecessor.prefix.source_cursor)
    if not _equal(packet.bases,expected): raise ValueError('Declared management original semantic envelope differs')
    return submission

def verify_declared_management_source_and_financial_admission(packet, *,resolver,predecessor,**scope):
    """Transport replay is not attested arm/protection/candle or financial state."""
    readback_declared_management_transport(packet,resolver=resolver,predecessor=predecessor,**scope)
    resolver.verify_historical_predecessor(predecessor)
    raise RuntimeError('Declared complete management producer/protection/arm authority is not installed')
