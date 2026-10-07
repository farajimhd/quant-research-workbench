"""Versioned normalized selected protection, source-equivalence only.

Schema draft: no installation, writer registration, commit or financial gate.
Cold restoration independently rereads the roster and entry source before it
compares the canonical rows. A stored ceiling/hash is never approval.
"""
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from hashlib import sha256
import json
from uuid import UUID, NAMESPACE_URL, uuid5

from .arte_journal_schema import TableContract
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
from .fixed_structural_lot_state import FixedStructuralLotProtectionState
from .strategy_one_position import ProtectionState, AcceptedResistance

POLICY_COLUMNS=tuple((name,'UInt32' if name in ('version','count') else 'String')
                     for name in FixedStructuralLotPolicy().payload())
COMMON=(('snapshot_id','UUID'),('run_id','String'),('snapshot_month','Date'))
ROOT=TableContract('trading_fixed_structural_lot_protection_v1',COMMON+(
 ('session_date','Date'),('group_id','String'),('intent_id','UUID'),('account_id','String'),
 ('assignment_id','String'),('ticker','String'),('entry_source_hash','FixedString(64)'),
 ('source_build_id','String'),('source_attempt_id','UUID'),('bars_attempt_id','UUID'),
 ('interval_token','FixedString(64)'),('source_checkpoint_hash','FixedString(64)'),
 ('decoded_seed_hash','FixedString(64)'),('seed_source_plan_hash','FixedString(64)'),
 ('seed_input_policy','String'),('split_evidence_hash','FixedString(64)'),
 ('clock_count','UInt32'),('interval_count','UInt32'),('clock_hash','FixedString(64)'),
 ('interval_hash','FixedString(64)'),('entry_boundary_ms','UInt32'),('episode_start_ms','UInt32'),
 ('reference_ask','Decimal(38,18)'),('initial_stop','Decimal(38,18)'),
 ('original_target','Decimal(38,18)'),('target_level_id','String'),('tick','Decimal(38,18)'),
 ('boundary_ms','UInt32'),('stop','Decimal(38,18)'),('ceiling','Decimal(38,18)'),
 ('through_sequence','UInt64'),('group_sequence','UInt64'),('observed_boundary_ms','UInt32'),
 ('accepted_count','UInt32'),('pending_count','UInt32'),('earned_groups','UInt32'),
 ('applied_groups','UInt32'),('lot_count','UInt32'),('resistance_count','UInt32'))+POLICY_COLUMNS+(
 ('children_hash','FixedString(64)'),('content_hash','FixedString(64)')),
 'toYYYYMM(snapshot_month)','run_id, through_sequence, snapshot_id')
LOT=TableContract('trading_fixed_structural_lot_protection_lot_v1',COMMON+(
 ('ordinal','UInt32'),('lot_id','String'),('weight_numerator','UInt32'),('weight_denominator','UInt32'),
 ('level_id','String'),('lower','Decimal(38,18)'),('upper','Decimal(38,18)'),
 ('confirmed_at_ms','UInt64'),('historical','UInt8'),('role','String'),('transition_from','String'),
 ('fixed_target','Decimal(38,18)'),('remaining','Decimal(38,18)'),('acquiring','UInt8')),
 'toYYYYMM(snapshot_month)','run_id, snapshot_id, ordinal')
RESISTANCE=TableContract('trading_fixed_structural_lot_protection_resistance_v1',COMMON+(
 ('ordinal','UInt32'),('level_id','String'),('role','String'),
 ('lower','Nullable(Decimal(38,18))'),('upper','Nullable(Decimal(38,18))')),
 'toYYYYMM(snapshot_month)','run_id, snapshot_id, ordinal')
TABLES=(ROOT,LOT,RESISTANCE)


@dataclass(frozen=True,slots=True)
class FixedStructuralLotSnapshotRows:
    root: dict
    lots: tuple[dict,...]
    resistances: tuple[dict,...]


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _decimal(value):
    try:
        with localcontext() as ctx:
            ctx.prec=80
            v=Decimal(str(value))
            if not v.is_finite() or v<0 or v>=Decimal('1e20') or v.as_tuple().exponent < -18:
                raise ValueError('Fixed lot snapshot decimal is malformed')
            return format(v.quantize(Decimal('1e-18')),'f')
    except DecimalException as exc:
        raise ValueError('Fixed lot snapshot decimal is malformed') from exc


def _row(contract,row):
    if type(row) is not dict or set(row)!=set(name for name,_ in contract.columns):
        raise ValueError('Fixed lot snapshot columns differ')
    for name,kind in contract.columns:
        value=row[name]
        if kind.startswith('UInt'):
            bits=int(kind[4:])
            if type(value) is not int or not 0<=value<2**bits:
                raise ValueError('Fixed lot snapshot integer aliases forbidden')
        elif 'Decimal' in kind:
            if value is None and kind.startswith('Nullable'):
                continue
            if type(value) is not str or value!=_decimal(value):
                raise ValueError('Fixed lot snapshot decimal must be canonical text')
        elif type(value) is not str:
            raise ValueError('Fixed lot snapshot scalar type differs')
        elif kind=='UUID' and str(UUID(value))!=value:
            raise ValueError('Fixed lot snapshot UUID differs')
        elif kind=='FixedString(64)' and (len(value)!=64 or any(v not in '0123456789abcdef' for v in value)):
            raise ValueError('Fixed lot snapshot hash differs')
    return row


def project_fixed_structural_lot_snapshot(state,*,client,prefix,intervals,intent,strategy_identity,
                                        entry_request=None,fixed_lot_contexts=()):
    if type(state) is not FixedStructuralLotProtectionState:
        raise ValueError('Exact selected fixed lot snapshot state required')
    state.__post_init__()
    entry=state.entry
    fresh=load_fixed_structural_lot_stop_ceiling(client,prefix,entry=entry,intervals=intervals,
        intent=intent,group_id=state.roster.group_id,strategy_identity=strategy_identity,
        **({'entry_request':entry_request,'fixed_lot_contexts':fixed_lot_contexts}
           if entry_request is not None or fixed_lot_contexts else {}))
    if fresh!=state.roster:
        raise ValueError('Fixed lot snapshot needs exact fresh roster predecessor')
    p=state.protection
    snapshot_id=str(uuid5(NAMESPACE_URL,
        f'fixed-structural-lot-protection-v1:{fresh.run_id}:{fresh.group_id}:{fresh.through_sequence}:{p.boundary_ms}'))
    common=dict(snapshot_id=snapshot_id,run_id=fresh.run_id,snapshot_month=entry.session_date.replace(day=1).isoformat())
    lots=tuple(dict(common,ordinal=i,lot_id=lot,weight_numerator=weight[0],weight_denominator=weight[1],
        level_id=target.level_id,lower=_decimal(target.lower),upper=_decimal(target.upper),
        confirmed_at_ms=target.confirmed_at_ms,historical=int(target.historical),role=target.role,
        transition_from=target.transition_from,fixed_target=_decimal(target.price),remaining=_decimal(quantity),
        acquiring=int(lot in fresh.acquiring)) for i,((lot,quantity),target,weight) in enumerate(
            zip(fresh.remaining,entry.targets,entry.policy.weights)))
    latest={v.unified_level_id for v in (*p.earned_group,*p.pending_group)}
    geometry=[(identity,'prior',None,None) for identity in sorted(p.accepted_ids-latest)]
    geometry.extend((v.unified_level_id,'earned',_decimal(v.lower),_decimal(v.upper)) for v in p.earned_group)
    geometry.extend((v.unified_level_id,'pending',_decimal(v.lower),_decimal(v.upper)) for v in p.pending_group)
    resistances=tuple(dict(common,ordinal=i,level_id=identity,role=role,lower=lower,upper=upper)
                      for i,(identity,role,lower,upper) in enumerate(geometry))
    root=dict(common,session_date=entry.session_date.isoformat(),group_id=fresh.group_id,intent_id=fresh.intent_id,
        account_id=entry.proposal.account_id,assignment_id=entry.proposal.assignment_id,ticker=entry.proposal.ticker,
        entry_source_hash=fresh.entry_source_hash,**{name:getattr(entry,name) for name in
          ('source_build_id','source_attempt_id','bars_attempt_id','interval_token','source_checkpoint_hash',
           'decoded_seed_hash','seed_source_plan_hash','seed_input_policy','split_evidence_hash','clock_count',
           'interval_count','clock_hash','interval_hash')},
        entry_boundary_ms=entry.proposal.boundary_ms,episode_start_ms=entry.proposal.episode_start_ms,
        reference_ask=_decimal(entry.proposal.reference_ask),initial_stop=_decimal(entry.proposal.initial_stop),
        original_target=_decimal(p.target),target_level_id=entry.proposal.target_level_id,tick=_decimal(entry.tick),
        boundary_ms=p.boundary_ms,stop=_decimal(p.stop),ceiling=_decimal(fresh.ceiling),
        through_sequence=fresh.through_sequence,group_sequence=fresh.group_sequence,
        observed_boundary_ms=fresh.observed_boundary_ms,accepted_count=len(p.accepted_ids),pending_count=len(p.pending_group),
        earned_groups=p.earned_groups,applied_groups=p.applied_groups,lot_count=len(lots),resistance_count=len(resistances),
        **entry.policy.payload(),children_hash=_digest([lots,resistances]))
    root['content_hash']=_digest(root)
    _row(ROOT,root)
    for row in lots: _row(LOT,row)
    for row in resistances: _row(RESISTANCE,row)
    return FixedStructuralLotSnapshotRows(root,lots,resistances)


def restore_fixed_structural_lot_snapshot(rows,*,entry,client,prefix,intervals,intent,strategy_identity,
                                        entry_request=None,fixed_lot_contexts=()):
    if (type(rows) is not FixedStructuralLotSnapshotRows or type(rows.lots) is not tuple
            or type(rows.resistances) is not tuple):
        raise ValueError('Exact selected normalized rows required')
    root=_row(ROOT,rows.root)
    for row in rows.lots: _row(LOT,row)
    for row in rows.resistances: _row(RESISTANCE,row)
    if (root['content_hash']!=_digest({k:v for k,v in root.items() if k!='content_hash'})
            or root['children_hash']!=_digest([rows.lots,rows.resistances])
            or len(rows.lots)!=root['lot_count'] or len(rows.resistances)!=root['resistance_count']):
        raise ValueError('Fixed lot normalized seal differs')
    earned=tuple(AcceptedResistance(v['level_id'],float(v['lower']),float(v['upper']))
                 for v in rows.resistances if v['role']=='earned')
    pending=tuple(AcceptedResistance(v['level_id'],float(v['lower']),float(v['upper']))
                  for v in rows.resistances if v['role']=='pending')
    protection=ProtectionState(root['boundary_ms'],float(root['stop']),float(root['original_target']),
        frozenset(v['level_id'] for v in rows.resistances),pending,earned,root['earned_groups'],root['applied_groups'])
    fresh=load_fixed_structural_lot_stop_ceiling(client,prefix,entry=entry,intervals=intervals,intent=intent,
        group_id=root['group_id'],strategy_identity=strategy_identity,
        **({'entry_request':entry_request,'fixed_lot_contexts':fixed_lot_contexts}
           if entry_request is not None or fixed_lot_contexts else {}))
    state=FixedStructuralLotProtectionState(entry,protection,fresh)
    expected=project_fixed_structural_lot_snapshot(state,client=client,prefix=prefix,intervals=intervals,
        intent=intent,strategy_identity=strategy_identity,
        **({'entry_request':entry_request,'fixed_lot_contexts':fixed_lot_contexts}
           if entry_request is not None or fixed_lot_contexts else {}))
    if expected!=rows:
        raise ValueError('Fixed lot snapshot differs from fresh source or roster')
    return state
