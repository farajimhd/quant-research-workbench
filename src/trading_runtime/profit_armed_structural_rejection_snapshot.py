"""Lossless normalized selected manager companion, not a writer capability.

Four closed child concerns and a versioned manager parent preserve the complete
causal reducer tree. Native publication must additionally attest inherited
entry/protection and same-cursor Broker/OMS/Portfolio roots before selecting
this parent. No table is installed here.
"""
from dataclasses import dataclass, fields, asdict, replace
from hashlib import sha256
import json
from struct import pack, unpack
from uuid import UUID
from datetime import date
import re

from .arte_journal_schema import TableContract
from .profit_armed_structural_rejection import (
    RejectionSource, StructuralRejectionPolicy, StructuralRejectionState,
    HeldBar, FrozenResistance, CurrentQuote, StructuralRejectionWitness)
from .profit_armed_structural_rejection_checkpoint import _validate_state_witness, _tree
from .strategy_one_management_snapshot import PARENT_V3

COMMON=(('snapshot_id','UUID'),('run_id','String'),('snapshot_month','Date'),
        ('checkpoint_sequence','UInt64'),('account_id','String'),
        ('assignment_id','String'),('ticker','String'))
SOURCE=(('source_session_date','Date'),('source_build_id','String'),
        ('source_market_plan_token','FixedString(64)'),('source_bars_attempt_id','UUID'),
        ('source_indicators_attempt_id','UUID'),('source_liquidity_attempt_id','UUID'),
        ('source_interval_plan_token','FixedString(64)'))
POLICY=(('policy_id','String'),('decision_resolution_ms','UInt32'),('bar_resolution_ms','UInt32'),
        ('arm_risk_numerator','UInt16'),('arm_risk_denominator','UInt16'),
        ('rejection_count','UInt8'),('activity_count','UInt8'),('recent_activity_multiplier','UInt8'),
        ('maximum_quote_age_us','UInt32'))


def _contract(name,columns,tail):
    return TableContract(name,(*COMMON,*columns,('content_hash','FixedString(64)')),
        'toYYYYMM(snapshot_month)','run_id, checkpoint_sequence, account_id, assignment_id, ticker, '+tail)


STATE=_contract('trading_structural_rejection_state_v1',(
    ('state_kind','String'),*SOURCE,*POLICY,('position_intent_id','UUID'),
    ('session_origin_us','UInt64'),('first_held_boundary_ms','UInt32'),
    ('original_ask_int','UInt64'),('original_stop_int','UInt64'),
    ('last_decision_boundary_ms','UInt32'),('cursor_status','String'),('last_observed_boundary_ms','UInt32'),
    ('fired','UInt8'),('has_firing_witness','UInt8'),('witness_decision_boundary_ms','UInt32'),
    ('quote_observed_at_us','UInt64'),('quote_bid_int','UInt64'),('quote_ask_int','UInt64')),'state_kind')
BAR=_contract('trading_structural_rejection_bar_v1',(
    ('boundary_ms','UInt32'),('resolution_ms','UInt32'),('close_int','UInt64'),('high_int','UInt64'),
    ('trade_count','UInt64'),('price_valid','UInt8'),('extremes_valid','UInt8'),
    ('macd_line_bits','Nullable(UInt64)'),('macd_signal_bits','Nullable(UInt64)')),'boundary_ms')
LINK=_contract('trading_structural_rejection_bar_link_v1',(
    ('state_kind','String'),('bar_role','String'),('ordinal','UInt8'),('boundary_ms','UInt32')),
    'state_kind, bar_role, ordinal')
LEVEL=_contract('trading_structural_rejection_level_v1',(
    ('level_id','String'),('geometry_hash','FixedString(64)'),('lower_int','UInt64'),
    ('upper_int','UInt64'),('price_int','UInt64'),('confirmed_boundary_ms','UInt32'),
    ('available_boundary_ms','UInt32'),('role','String'),('interval_ordinal','UInt32'),
    ('valid_from_ms','UInt32'),('valid_to_ms','UInt32'),('raw_lower_bits','UInt64'),
    ('raw_upper_bits','UInt64'),('raw_role','String'),('transition_from','String'),
    ('confirmed_epoch_ms','UInt64'),('historical','UInt8'),('source_interval_attempt_id','UUID'),
    ('source_checkpoint_hash','FixedString(64)'),('decoded_seed_hash','FixedString(64)'),
    ('seed_source_plan_hash','FixedString(64)'),('seed_session','Date'),
    ('seed_available_at','String'),('reference_basis','String'),('midpoint_arithmetic','String'),
    ('geometry_bound_conversion','String')),'level_id')
CHILDREN=(STATE,BAR,LINK,LEVEL)
PARENT=TableContract('trading_structural_rejection_manager_snapshot_v1',(
    *PARENT_V3.columns[:-1],('declaration_hash','FixedString(64)'),
    *((item,kind) for prefix in ('rejection_state','rejection_bar','rejection_link','rejection_level')
      for item,kind in ((prefix+'_count','UInt32'),(prefix+'_hash','FixedString(64)'))),
    PARENT_V3.columns[-1]),PARENT_V3.partition,PARENT_V3.order)
TABLES=(*CHILDREN,PARENT)
_PREFIXES=('rejection_state','rejection_bar','rejection_link','rejection_level')
MAX_COLD_RESPONSE_BYTES=64*1024*1024
MAX_COLD_TOTAL_BYTES=128*1024*1024


def load_unattested_structural_rejection_snapshot(client,*,run_id,checkpoint_sequence):
    """Bounded complete historical read; caller must prove prefix and source.

    A selected parent is read directly, without borrowing a legacy manager
    seal. The inherited root is reconstructed and independently rehashed from
    its exact retained columns, then all inherited children are restored.
    Content integrity is not a journal-head or market-source attestation.
    """
    from src.backend.backtest_market_data import assert_select_only
    from .arte_journal_writer import _literal,_wire_row,typed_row
    from .strategy_one_management_snapshot import (
        ManagerSnapshotRows,SOURCE as ENTRY_SOURCE,BREAK,HIGH,CLOSED,FIRST_HELD,
        restore_manager_snapshot)
    from .strategy_one_protection_snapshot import load_protection_snapshot_rows
    if (type(run_id) is not str or str(UUID(run_id))!=run_id or UUID(run_id).int==0
            or type(checkpoint_sequence) is not int or not 0<checkpoint_sequence<2**64
            or not callable(getattr(client,'execute',None))):
        raise ValueError('Structural rejection cold read requires exact run and cursor')

    class BoundedReader:
        total=0

        def execute(self,sql):
            assert_select_only(sql)
            result=client.execute(sql)
            if type(result) is not str or len(result)>MAX_COLD_RESPONSE_BYTES:
                raise ValueError('Structural rejection cold response exceeds text byte bound')
            count=len(result.encode('utf-8'))
            self.total+=count
            if count>MAX_COLD_RESPONSE_BYTES or self.total>MAX_COLD_TOTAL_BYTES:
                raise ValueError('Structural rejection cold response exceeds aggregate byte bound')
            return result

    bounded=BoundedReader()

    def read(contract,predicate,limit):
        columns=','.join(f'toString({name}) AS {name}' if 'Decimal(' in kind else name
                         for name,kind in contract.columns)
        sql=assert_select_only(f'SELECT {columns} FROM arte.{contract.name} WHERE {predicate} '
            f'ORDER BY {contract.order} LIMIT {limit} FORMAT JSONEachRow')
        raw=tuple(json.loads(line) for line in bounded.execute(sql).splitlines() if line.strip())
        if len(raw)>=limit:
            raise ValueError('Structural rejection cold family exceeds exact cardinality')
        return tuple(_wire_row(contract.name,row) for row in raw)

    scope=f'run_id={_literal(run_id)} AND checkpoint_sequence={checkpoint_sequence}'
    seals=read(PARENT,scope,2)
    if len(seals)!=1:
        raise ValueError('Structural rejection cold read lacks exactly one own parent')
    root=seals[0]
    if (root['run_id']!=run_id or root['checkpoint_sequence']!=checkpoint_sequence
            or _sealed(PARENT,{k:v for k,v in root.items() if k!='content_hash'})!=root):
        raise ValueError('Structural rejection cold parent differs from exact hash/run/cursor')
    predicate=f"snapshot_id=toUUID({_literal(root['snapshot_id'])})"

    def children(contract,prefix,maximum=100000):
        count=root[prefix+'_count']
        if type(count) is not int or not 0<=count<=maximum:
            raise ValueError('Structural rejection cold child inventory exceeds bound')
        values=read(contract,predicate,count+1)
        if len(values)!=count:
            raise ValueError('Structural rejection cold family is incomplete')
        return values

    inherited_root=typed_row(PARENT_V3.name,
        {name:root[name] for name,_ in PARENT_V3.columns if name!='content_hash'})
    inherited=ManagerSnapshotRows(inherited_root,
        children(ENTRY_SOURCE,'source'),children(BREAK,'pending_break'),
        load_protection_snapshot_rows(bounded,run_id=run_id,checkpoint_sequence=checkpoint_sequence),
        children(HIGH,'position_high'),children(CLOSED,'closed_position'),
        children(FIRST_HELD,'first_held'))
    restore_manager_snapshot(inherited)
    rows=StructuralRejectionSnapshotRows(root,inherited,
        *(children(contract,prefix,2000000) for contract,prefix in zip(CHILDREN,_PREFIXES)))
    _verify_rows(rows)
    return rows


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _bits(value):
    if value is None: return None
    if type(value) is not float:
        raise ValueError('Certified native MACD/geometry requires exact Float64')
    return unpack('<Q',pack('<d',value))[0]


def _float(value):
    if value is None: return None
    if type(value) is not int or not 0<=value<2**64:
        raise ValueError('Malformed Float64 bit pattern')
    return unpack('<d',pack('<Q',value))[0]


def _sealed(contract,value):
    if set(value)!={name for name,_ in contract.columns if name!='content_hash'}:
        raise ValueError('Selected normalized row differs from exact contract')
    for name,kind in contract.columns:
        if name=='content_hash': continue
        item=value[name]
        if kind.startswith('Nullable('):
            if item is None: continue
            kind=kind[9:-1]
        integer=re.fullmatch(r'UInt(8|16|32|64)',kind)
        if integer:
            if type(item) is not int or not 0<=item<2**int(integer[1]):
                raise ValueError('Selected normalized integer exceeds exact schema: '+name)
        elif kind=='UUID':
            if type(item) is not str or str(UUID(item))!=item or UUID(item).int==0:
                raise ValueError('Selected normalized identity is not canonical UUID: '+name)
        elif kind=='Date':
            if type(item) is not str or date.fromisoformat(item).isoformat()!=item:
                raise ValueError('Selected normalized date is not canonical: '+name)
        elif kind=='FixedString(64)':
            if type(item) is not str or not re.fullmatch('[0-9a-f]{64}',item):
                raise ValueError('Selected normalized digest is malformed: '+name)
        elif kind=='String':
            if type(item) is not str:
                raise ValueError('Selected normalized String is malformed: '+name)
        else:
            raise ValueError('Unknown selected normalized schema type')
    return dict(value,content_hash=_digest(value))


def _common(root,key):
    return {**{name:root[name] for name in ('snapshot_id','run_id','snapshot_month','checkpoint_sequence')},
            **dict(zip(('account_id','assignment_id','ticker'),key))}


@dataclass(frozen=True,slots=True)
class StructuralRejectionSnapshotRows:
    snapshot: dict
    inherited: object
    states: tuple
    bars: tuple
    links: tuple
    levels: tuple


def _state_rows(common,state,status,observed,witness,geometry):
    _validate_state_witness(state,witness)
    records=[];observations={};links=[];levels=[]
    for kind,current in (('current',state),*(() if witness is None else (('predecessor',witness.predecessor),))):
        source=current.source;policy=current.policy
        row=dict(common,state_kind=kind,source_session_date=source.session_date,source_build_id=source.market_build_id,
            source_market_plan_token=source.market_plan_token,source_bars_attempt_id=source.bars_attempt_id,
            source_indicators_attempt_id=source.indicators_attempt_id,source_liquidity_attempt_id=source.liquidity_attempt_id,
            source_interval_plan_token=source.interval_plan_token,policy_id=policy.policy_id,
            decision_resolution_ms=policy.decision_resolution_ms,bar_resolution_ms=policy.bar_resolution_ms,
            arm_risk_numerator=policy.arm_original_risk[0],arm_risk_denominator=policy.arm_original_risk[1],
            rejection_count=policy.rejection_count,activity_count=policy.activity_count,
            recent_activity_multiplier=policy.recent_activity_multiplier,maximum_quote_age_us=policy.maximum_quote_age_us,
            position_intent_id=current.position_intent_id,session_origin_us=current.session_origin_us,
            first_held_boundary_ms=current.first_held_boundary_ms,original_ask_int=current.original_ask_int,
            original_stop_int=current.original_stop_int,last_decision_boundary_ms=current.last_decision_boundary_ms,
            cursor_status=status if kind=='current' else 'internal_predecessor',fired=int(current.fired),
            last_observed_boundary_ms=observed,
            has_firing_witness=int(kind=='current' and witness is not None),
            witness_decision_boundary_ms=witness.decision_boundary_ms if kind=='current' and witness else 0,
            quote_observed_at_us=witness.quote.observed_at_us if kind=='current' and witness else 0,
            quote_bid_int=witness.quote.bid_int if kind=='current' and witness else 0,
            quote_ask_int=witness.quote.ask_int if kind=='current' and witness else 0)
        records.append(_sealed(STATE,row))
        roles={'last_bar':(() if current.last_bar is None else (current.last_bar,)),
            'arm':(() if current.arm is None else (current.arm,)),
            'cross':(() if current.cross is None else (current.cross,)),
            'cross_prior':(() if current.cross_prior is None else (current.cross_prior,)),
            'rejection':current.rejections,
            'activity':witness.activity if kind=='current' and witness else ()}
        for role,values in roles.items():
            for ordinal,bar in enumerate(values):
                if bar.source!=source or bar.resolution_ms!=policy.bar_resolution_ms:
                    raise ValueError('Foreign normalized observation source/resolution')
                value=dict(common,boundary_ms=bar.boundary_ms,resolution_ms=bar.resolution_ms,
                    close_int=bar.close_int,high_int=bar.high_int,trade_count=bar.trade_count,
                    price_valid=int(bar.price_valid),extremes_valid=int(bar.extremes_valid),
                    macd_line_bits=_bits(bar.macd_line),macd_signal_bits=_bits(bar.macd_signal))
                sealed=_sealed(BAR,value)
                old=observations.setdefault(bar.boundary_ms,sealed)
                if old!=sealed: raise ValueError('Conflicting same-clock normalized observations')
                links.append(_sealed(LINK,dict(common,state_kind=kind,bar_role=role,ordinal=ordinal,boundary_ms=bar.boundary_ms)))
        if current.resistance is not None:
            level=current.resistance
            if geometry is None or _digest(geometry)!=level.geometry_hash:
                raise ValueError('Frozen resistance lacks complete exact native geometry')
            raw=geometry['interval']
            levelrow=dict(common,level_id=level.level_id,geometry_hash=level.geometry_hash,
                lower_int=level.lower_int,upper_int=level.upper_int,price_int=level.price_int,
                confirmed_boundary_ms=level.confirmed_boundary_ms,available_boundary_ms=level.available_boundary_ms,
                role=level.role,interval_ordinal=raw['ordinal'],valid_from_ms=raw['valid_from_ms'],valid_to_ms=raw['valid_to_ms'],
                raw_lower_bits=_bits(raw['lower']),raw_upper_bits=_bits(raw['upper']),raw_role=raw['role'],
                transition_from=raw['transition_from'],confirmed_epoch_ms=raw['confirmed_at_ms'],historical=int(raw['historical']),
                **{name:geometry[name] for name in ('source_interval_attempt_id','source_checkpoint_hash','decoded_seed_hash',
                    'seed_source_plan_hash','seed_session','seed_available_at','midpoint_arithmetic',
                    'geometry_bound_conversion')},reference_basis=geometry['reference_basis'])
            sealed=_sealed(LEVEL,levelrow)
            if levels and levels[0]!=sealed: raise ValueError('Changed frozen predecessor geometry')
            if not levels: levels.append(sealed)
    return tuple(records),tuple(observations[key] for key in sorted(observations)),tuple(links),tuple(levels)


def project_structural_rejection_snapshot(*,run_id,session_date,checkpoint_sequence,state):
    """Project an actual issued manager capture; no standalone state admission."""
    from src.backend.backtest_profit_armed_structural_rejection_management import require_structural_rejection_capture, structural_rejection_capture_evidence, _plain
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from .strategy_one_management_snapshot import project_manager_snapshot
    owner=require_structural_rejection_capture(state)
    if (run_id!=owner.manager.runtime.run_id or session_date!=owner.manager.runtime.config.anchor_date
            or type(checkpoint_sequence) is not int or checkpoint_sequence<=0 or state.original_risk_requests):
        raise ValueError('Selected checkpoint differs from native owner/cursor or conflicts with another pending family')
    base=StrategyOneManagementState(**{f.name:getattr(state,f.name) for f in fields(StrategyOneManagementState)})
    inherited=project_manager_snapshot(run_id=run_id,session_date=session_date,checkpoint_sequence=checkpoint_sequence,
        state=base,first_price_source=owner.price_authority)
    children=[[],[],[],[]]
    evidence=structural_rejection_capture_evidence(state)
    for key,current,status,observed in state.structural_rejection_states:
        witness,geometry=evidence[key]
        generated=_state_rows(_common(inherited.snapshot,key),current,status,observed,witness,
            _plain(geometry) if geometry is not None else None)
        for output,rows in zip(children,generated): output.extend(rows)
    children=[tuple(sorted(rows,key=lambda row:tuple(row[name] for name in
        ('account_id','assignment_id','ticker',*tail)))) for rows,tail in zip(children,
        (('state_kind',),('boundary_ms',),('state_kind','bar_role','ordinal'),('level_id',)))]
    parent={key:value for key,value in inherited.snapshot.items() if key!='content_hash'}
    parent['declaration_hash']=_digest(owner.declaration.payload())
    # Flat selected captures still declare the complete parent shape.
    parent.setdefault('first_held_count',len(inherited.first_held_boundaries))
    parent.setdefault('first_held_hash',_digest([r['content_hash'] for r in inherited.first_held_boundaries]))
    for prefix,rows in zip(_PREFIXES,children):
        parent[prefix+'_count']=len(rows);parent[prefix+'_hash']=_digest([r['content_hash'] for r in rows])
    return StructuralRejectionSnapshotRows(_sealed(PARENT,parent),inherited,*children)


def _verify_rows(rows):
    if type(rows) is not StructuralRejectionSnapshotRows:
        raise ValueError('Exact selected normalized checkpoint rows required')
    root=rows.snapshot
    if _sealed(PARENT,{k:v for k,v in root.items() if k!='content_hash'})!=root:
        raise ValueError('Selected manager parent hash changed')
    inherited={k:v for k,v in root.items() if k in dict(PARENT_V3.columns) and k!='content_hash'}
    original={k:v for k,v in rows.inherited.snapshot.items() if k!='content_hash'}
    original.setdefault('first_held_count',len(rows.inherited.first_held_boundaries))
    original.setdefault('first_held_hash',_digest([r['content_hash'] for r in rows.inherited.first_held_boundaries]))
    if inherited!=original:
        raise ValueError('Selected manager parent differs from complete inherited root')
    result=[]
    for contract,prefix,children,tail in zip(CHILDREN,_PREFIXES,
        (rows.states,rows.bars,rows.links,rows.levels),
        (('state_kind',),('boundary_ms',),('state_kind','bar_role','ordinal'),('level_id',))):
        if type(children) is not tuple or len(children)>2000000:
            raise ValueError('Selected child inventory exceeds declared bound')
        keys=[]
        for row in children:
            if (type(row) is not dict or _sealed(contract,{k:v for k,v in row.items() if k!='content_hash'})!=row
                    or any(row[name]!=root[name] for name in ('snapshot_id','run_id','snapshot_month','checkpoint_sequence'))):
                raise ValueError('Foreign/mutated selected checkpoint child')
            keys.append(tuple(row[name] for name in ('account_id','assignment_id','ticker',*tail)))
        if keys!=sorted(set(keys)):
            raise ValueError('Selected normalized children repeat or change order')
        if (root[prefix+'_count']!=len(children)
                or root[prefix+'_hash']!=_digest([r['content_hash'] for r in children])):
            raise ValueError('Selected normalized inventory count/hash differs')
        result.append(children)
    return result


def restore_structural_rejection_snapshot(rows,*,expected_bindings,declaration,expected_geometries):
    """Losslessly decode against independent fresh entry/source/policy pins.

    This deterministic read contract issues no native owner or order capability.
    The native cold adapter must obtain these bindings from certified source
    and the complete inherited/financial checkpoint, then install the result.
    """
    from .profit_armed_structural_rejection_checkpoint import StructuralRejectionCheckpointBinding
    from .profit_armed_structural_rejection_native_policy import NativeStructuralRejectionDeclaration
    states,bars,links,levels=_verify_rows(rows)
    root=rows.snapshot
    if (type(declaration) is not NativeStructuralRejectionDeclaration
            or root['declaration_hash']!=_digest(declaration.payload()) or type(expected_bindings) is not dict
            or type(expected_geometries) is not dict):
        raise ValueError('Selected parent differs from independent declared policy')
    def key(row): return row['account_id'],row['assignment_id'],row['ticker']
    current_keys={key(row) for row in states if row['state_kind']=='current'}
    if current_keys!=set(expected_bindings) or len(current_keys)>65536:
        raise ValueError('Selected checkpoint differs from complete actual held inventory')
    if any(key(row) not in current_keys for family in (states,bars,links,levels) for row in family):
        raise ValueError('Orphan selected checkpoint child')
    grouped=[]
    for family in (states,bars,links,levels):
        index={}
        for row in family: index.setdefault(key(row),[]).append(row)
        grouped.append({identity:tuple(records) for identity,records in index.items()})
    state_index,bar_index,link_index,level_index=grouped
    decoded=[]
    for identity in sorted(current_keys):
        binding=expected_bindings[identity]
        if (type(binding) is not StructuralRejectionCheckpointBinding
                or binding.checkpoint_sequence!=root['checkpoint_sequence']
                or binding.source.run_id!=root['run_id'] or binding.policy!=declaration.policy
                or binding.account_id!=identity[0] or binding.source.ticker!=identity[2]):
            raise ValueError('Selected checkpoint lacks independent original source/cursor binding')
        records=state_index[identity]
        bykind={r['state_kind']:r for r in records}
        if set(bykind) not in ({'current'},{'current','predecessor'}):
            raise ValueError('Unknown or missing normalized state kind')
        current=bykind['current']
        if type(current['has_firing_witness']) is not int or current['has_firing_witness'] not in (0,1):
            raise ValueError('Malformed normalized witness cardinality')
        if ('predecessor' in bykind)!=bool(current['has_firing_witness']):
            raise ValueError('Missing/orphan firing predecessor')
        observations={}
        for row in bar_index.get(identity,()):
            if (type(row['price_valid']) is not int or row['price_valid'] not in (0,1)
                    or type(row['extremes_valid']) is not int or row['extremes_valid'] not in (0,1)):
                raise ValueError('Malformed producer validity flag')
            observations[row['boundary_ms']]=HeldBar(binding.source,row['boundary_ms'],row['close_int'],
                row['high_int'],row['trade_count'],bool(row['price_valid']),_float(row['macd_line_bits']),
                _float(row['macd_signal_bits']),row['resolution_ms'],bool(row['extremes_valid']))
        levelrows=level_index.get(identity,())
        if len(levelrows)>1: raise ValueError('Changed or duplicate frozen resistance')
        level=geometry=None
        if levelrows:
            row=levelrows[0]
            if type(row['historical']) is not int or row['historical'] not in (0,1):
                raise ValueError('Malformed historical geometry flag')
            geometry=dict(interval=dict(level_id=row['level_id'],ordinal=row['interval_ordinal'],
                valid_from_ms=row['valid_from_ms'],valid_to_ms=row['valid_to_ms'],lower=_float(row['raw_lower_bits']),
                upper=_float(row['raw_upper_bits']),role=row['raw_role'],transition_from=row['transition_from'],
                confirmed_at_ms=row['confirmed_epoch_ms'],historical=bool(row['historical'])),
                source_interval_token=binding.source.interval_plan_token,reference_basis=row['reference_basis'],price_int=row['price_int'],
                **{name:row[name] for name in ('source_interval_attempt_id','source_checkpoint_hash','decoded_seed_hash',
                    'seed_source_plan_hash','seed_session','seed_available_at','midpoint_arithmetic',
                    'geometry_bound_conversion')})
            if _digest(geometry)!=row['geometry_hash']:
                raise ValueError('Frozen geometry differs from complete provenance')
            if geometry!=expected_geometries.get(identity):
                raise ValueError('Frozen geometry differs from independent certified as-of source')
            lower=geometry['interval']['lower'];upper=geometry['interval']['upper']
            lower_int,upper_int,midpoint=declaration.geometry_ints(lower,upper)
            if (row['lower_int']!=lower_int or row['upper_int']!=upper_int
                    or row['price_int']!=midpoint or row['reference_basis']!=declaration.resistance_price_basis
                    or row['midpoint_arithmetic']!=declaration.midpoint_arithmetic
                    or row['geometry_bound_conversion']!=declaration.geometry_bound_conversion):
                raise ValueError('Frozen resistance pricing differs from declared raw geometry')
            level=FrozenResistance(binding.source,row['level_id'],row['geometry_hash'],row['lower_int'],row['upper_int'],
                row['price_int'],row['confirmed_boundary_ms'],row['available_boundary_ms'],row['role'])
        elif identity in expected_geometries:
            raise ValueError('Missing independently certified frozen geometry')
        decoded_states={};activities={}
        role_index={}
        for row in link_index.get(identity,()):
            role_index.setdefault((row['state_kind'],row['bar_role']),[]).append(row)
        allowed_roles=('last_bar','arm','cross','cross_prior','rejection','activity')
        if any(kind not in bykind or role not in allowed_roles for kind,role in role_index):
            raise ValueError('Unknown normalized observation role/state')
        for kind,row in bykind.items():
            source=RejectionSource(root['run_id'],identity[2],row['source_session_date'],row['source_build_id'],
                row['source_market_plan_token'],row['source_bars_attempt_id'],row['source_indicators_attempt_id'],
                row['source_liquidity_attempt_id'],row['source_interval_plan_token'])
            policy=StructuralRejectionPolicy(row['policy_id'],row['decision_resolution_ms'],row['bar_resolution_ms'],
                (row['arm_risk_numerator'],row['arm_risk_denominator']),row['rejection_count'],row['activity_count'],
                row['recent_activity_multiplier'],row['maximum_quote_age_us'])
            if (source!=binding.source or policy!=binding.policy
                    or any(row[name]!=getattr(binding,name) for name in ('position_intent_id','session_origin_us',
                        'first_held_boundary_ms','original_ask_int','original_stop_int'))):
                raise ValueError('Selected normalized state changed original source/entry/parameters')
            roles={}
            for role in allowed_roles:
                selected=role_index.get((kind,role),())
                if [r['ordinal'] for r in selected]!=list(range(len(selected))):
                    raise ValueError('Missing or duplicate normalized observation ordinal')
                if any(r['boundary_ms'] not in observations for r in selected):
                    raise ValueError('Missing linked source observation')
                roles[role]=tuple(observations[r['boundary_ms']] for r in selected)
                limit=policy.rejection_count if role=='rejection' else policy.activity_count if role=='activity' else 1
                if len(selected)>limit: raise ValueError('Normalized role exceeds declared count')
            if type(row['fired']) is not int or row['fired'] not in (0,1):
                raise ValueError('Malformed normalized fired flag')
            def optional(role): return roles[role][0] if roles[role] else None
            decoded_states[kind]=StructuralRejectionState(source,row['position_intent_id'],identity[0],row['session_origin_us'],
                row['first_held_boundary_ms'],row['original_ask_int'],row['original_stop_int'],row['last_decision_boundary_ms'],
                optional('last_bar'),optional('arm'),level if optional('cross') else None,optional('cross'),optional('cross_prior'),
                roles['rejection'],bool(row['fired']),policy)
            activities[kind]=roles['activity']
        result=decoded_states['current'];witness=None
        if current['has_firing_witness']:
            witness=StructuralRejectionWitness(binding.policy,decoded_states['predecessor'],current['witness_decision_boundary_ms'],
                result.arm,result.resistance,result.cross,result.cross_prior,result.rejections,activities['current'],
                CurrentQuote(binding.source,current['quote_observed_at_us'],current['quote_bid_int'],current['quote_ask_int']))
        _validate_state_witness(result,witness)
        status=current['cursor_status']
        observed=current['last_observed_boundary_ms']
        if not result.last_decision_boundary_ms<=observed<=root['boundary_ms']:
            raise ValueError('Selected normalized observation frontier is contradictory')
        expected_status=('no_observation_gap' if observed<root['boundary_ms'] else
            'processed' if result.last_decision_boundary_ms==root['boundary_ms'] else
            'not_due' if root['boundary_ms']%result.policy.decision_resolution_ms else 'inherited_priority_or_initial')
        if status!=expected_status or result.last_decision_boundary_ms>root['boundary_ms']:
            raise ValueError('Selected state frontier/status differs from manager checkpoint')
        generated=_state_rows(_common(root,identity),result,status,observed,witness,geometry)
        for expected,index in zip(generated,grouped):
            observed=index.get(identity,())
            if sorted(expected,key=lambda r:r['content_hash'])!=sorted(observed,key=lambda r:r['content_hash']):
                raise ValueError('Selected decoded graph fails exact complete re-projection')
        decoded.append((identity,result,status,witness,geometry,observed))
    return tuple(decoded)
