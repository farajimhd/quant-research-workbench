"""Selected manager transport; pure projection alone grants no authority.

The existing manager children and protection schemas retain their shape. The
separate selected seal binds their complete owned lot inventory. Writers must
receive an independently issued selected checkpoint, never these rows alone.
"""
from datetime import date
from dataclasses import dataclass
from typing import Mapping
from uuid import uuid5,NAMESPACE_URL
import json
from hashlib import sha256
from weakref import WeakKeyDictionary
from .strategy_one_position import ProtectionState
from .strategy_one_protection_snapshot import (
    ProtectionSnapshotRows,_price,_digest,canonical_protection_snapshot_rows,_serialize_protection_snapshot,
)


def validate_manager_protection_rows(rows,*,run_id,session_date,checkpoint_sequence,boundary_ms,positions):
    """Private encoder seam: prove complete scalar/geometry/root equality."""
    if type(rows) is not ProtectionSnapshotRows:
        raise ValueError('Selected manager needs exact precomputed protection rows')
    expected=_serialize_protection_snapshot(run_id=run_id,session_date=session_date,
        checkpoint_sequence=checkpoint_sequence,boundary_ms=boundary_ms,positions=positions)
    if canonical_protection_snapshot_rows(rows)!=canonical_protection_snapshot_rows(expected):
        raise ValueError('Selected manager protection rows differ from its complete capture')
    return rows


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotManagerPublication:
    owner: object
    checkpoint: object
    run_id: str
    sequence: int
    batch_id: str
    rows_json: str


_PUBLICATIONS=WeakKeyDictionary()


def selected_manager_head_path(run_id):
    from .keeper_ownership import _ROOT,_identity
    _identity(run_id,'run')
    return f'{_ROOT}/fixed-structural-lot-manager-snapshot/v1/{sha256(run_id.encode()).hexdigest()}/head'


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def issue_manager_publication(owner,checkpoint,*,client,sequence,batch_id):
    """Freeze a source-issued image at the actual complete journal frontier."""
    from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
    from .strategy_one_management_snapshot import _project_manager_snapshot_scalar,SOURCE,BREAK,HIGH,CLOSED,FIRST_HELD
    from .fixed_structural_lot_manager_schema import PARENT
    from .fixed_structural_lot_snapshot import ROOT,LOT,RESISTANCE
    from .strategy_one_protection_snapshot import TABLES as PROTECTION_TABLES
    profile=require_fixed_structural_lot_profile(getattr(client,'fixed_structural_lot_profile',None))
    if profile.operation is not owner.operation or client is not owner.client:
        raise ValueError('Selected manager publication has foreign client/source operation')
    rebound,prefix,_=owner.rebind_checkpoint(checkpoint,checkpoint_sequence=sequence,journal_batch_id=batch_id)
    source=owner.operation.source
    state=rebound.inherited
    protection=_serialize_protection_snapshot(run_id=source.run_id,session_date=source.session_date,
        checkpoint_sequence=sequence,boundary_ms=state.boundary_ms,
        positions={(key[0],key[2],key[1]):value for key,value in state.positions})
    inherited=_project_manager_snapshot_scalar(run_id=source.run_id,session_date=source.session_date,
        checkpoint_sequence=sequence,state=state,_protection_rows=protection)
    # Empty first-held inventory is still explicit in the selected V3 prefix.
    seal={k:v for k,v in inherited.snapshot.items() if k!='content_hash'}
    seal.setdefault('first_held_count',len(inherited.first_held_boundaries))
    seal.setdefault('first_held_hash',_digest([v['content_hash'] for v in inherited.first_held_boundaries]))
    inventory=[];own_roots=[];lots=[];resistances=[]
    for key,rows in rebound.selected_positions:
        inventory.append([*key,rows.root['snapshot_id'],rows.root['content_hash']])
        own_roots.append(rows.root);lots.extend(rows.lots);resistances.extend(rows.resistances)
    seal.update(selected_configuration_hash=source.selected_configuration_hash,
        selected_position_count=len(inventory),selected_position_hash=_digest(inventory))
    seal['content_hash']=_digest(seal)
    families={SOURCE.name:inherited.sources,BREAK.name:inherited.pending_breaks,
        HIGH.name:inherited.position_highs,CLOSED.name:inherited.closed_positions,
        FIRST_HELD.name:inherited.first_held_boundaries,
        PROTECTION_TABLES[0].name:(protection.snapshot,),
        PROTECTION_TABLES[1].name:protection.states,
        PROTECTION_TABLES[2].name:protection.resistances,
        ROOT.name:tuple(own_roots),LOT.name:tuple(lots),RESISTANCE.name:tuple(resistances),
        PARENT.name:(seal,)}
    # JSON is an immutable wire image; returned row dictionaries are copies.
    result=FixedStructuralLotManagerPublication(owner,rebound,source.run_id,sequence,batch_id,_canonical(families))
    _PUBLICATIONS[result]=(owner,rebound,source,client,result.rows_json,prefix.source_cursor,
        result.run_id,result.sequence,result.batch_id)
    return result


def require_manager_publication(context,*,client=None):
    if type(context) is not FixedStructuralLotManagerPublication:
        raise ValueError('Exact issued selected manager publication required')
    binding=_PUBLICATIONS.get(context)
    if binding is None or (context.owner,context.checkpoint,context.rows_json,
            context.run_id,context.sequence,context.batch_id)!=binding[:2]+(binding[4],)+binding[6:]:
        raise ValueError('Selected manager publication was copied or altered')
    context.owner.require_checkpoint(context.checkpoint)
    if context.owner.operation.source is not binding[2] or (client is not None and client is not binding[3]):
        raise ValueError('Selected manager publication has foreign source/client')
    return json.loads(context.rows_json)


def verify_manager_insert(context,*,client,table,rows,run_id,sequence,batch_id,snapshot_hash):
    families=require_manager_publication(context,client=client)
    from .fixed_structural_lot_manager_schema import PARENT
    if ((run_id,sequence,batch_id,snapshot_hash)!=(context.run_id,context.sequence,context.batch_id,
            families[PARENT.name][0]['content_hash']) or table not in families
            or _canonical(rows)!=_canonical(families[table])):
        raise ValueError('Selected manager INSERT differs from its exact issued inventory')
    return frozenset(families)


def _verify_financial_checkpoint(context,client,session):
    """Independently join committed Portfolio and broker/OMS checkpoint roots."""
    from .arte_portfolio_snapshot import load_portfolio_snapshot
    from .strategy_one_broker_match_snapshot import (
        ManagedBrokerMatchHeadReader,load_unattested_broker_match_snapshot,float64_from_bits)
    from .strategy_one_oms_observation_snapshot import (
        ManagedOmsObservationHeadReader,load_unattested_oms_observation_snapshot)
    from .arte_journal_projection import load_latest_backtest_cursor
    from datetime import datetime,timedelta,timezone
    from zoneinfo import ZoneInfo
    context.owner.require_checkpoint(context.checkpoint)
    prefix,_=context.owner._prefix()
    cursor=load_latest_backtest_cursor(client,prefix)
    boundary=context.checkpoint.inherited.boundary_ms
    source=context.owner.operation.source
    if (prefix.last_sequence!=context.sequence or prefix.last_batch_id!=context.batch_id
            or type(cursor) is not dict or cursor.get('event_sequence')!=context.sequence or cursor.get('batch_id')!=context.batch_id
            or cursor.get('boundary_ms')!=boundary or cursor.get('session_date')!=source.session_date.isoformat()):
        raise ValueError('Selected manager financial checkpoint has a foreign committed cursor')
    instant=(datetime.combine(source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
        +timedelta(hours=4,milliseconds=boundary)).astimezone(timezone.utc)
    financials=dict(context.checkpoint.financials)
    # Include all source-run accounts, including explicit empty inventory.
    from .arte_journal_writer import _verify_run_identity
    accounts=tuple(_verify_run_identity(client,context.run_id)['account_ids'])
    for account in accounts:
        loaded=load_portfolio_snapshot(client,run_id=context.run_id,account_id=account,state_revision=context.sequence)
        if loaded is None or datetime.fromisoformat(loaded['snapshot_at'])!=instant:
            raise ValueError('Selected manager requires the matching committed Portfolio checkpoint first')
        from dataclasses import replace
        from .arte_portfolio_snapshot import prepare_captured_portfolio_snapshot,_snapshot_rows,_state_hash
        captures=context.owner.require_checkpoint(context.checkpoint)[6]
        matched=[v for v in captures if v.account_id==account]
        if len(matched)!=1:
            raise ValueError('Selected manager lacks actual frozen Portfolio account capture')
        prepared=prepare_captured_portfolio_snapshot(replace(matched[0],state_revision=context.sequence))
        expected=_snapshot_rows(prepared.run_id,prepared.account_id,prepared.state_revision,
            prepared.snapshot_month,prepared.rows)
        if loaded['state_hash']!=_state_hash(expected):
            raise ValueError('Selected manager frozen Portfolio cash/assignment/reservations differ from committed checkpoint')
    for reader,loader,root_name in (
            (ManagedBrokerMatchHeadReader(session),load_unattested_broker_match_snapshot,'snapshot'),
            (ManagedOmsObservationHeadReader(session),load_unattested_oms_observation_snapshot,'root')):
        head=reader.read_head(run_id=context.run_id)
        if (head.checkpoint_sequence,head.journal_batch_id)!=(context.sequence,context.batch_id):
            raise ValueError('Selected manager requires matching broker/OMS checkpoint heads first')
        rows=loader(client,run_id=context.run_id,checkpoint_sequence=context.sequence)
        root=getattr(rows,root_name)
        if (root['content_hash']!=head.snapshot_hash or root['boundary_ms']!=boundary
                or root['session_date']!=source.session_date.isoformat()
                or reader.read_head(run_id=context.run_id)!=head):
            raise ValueError('Selected manager broker/OMS checkpoint content changed')
        if root_name=='snapshot':
            from decimal import Decimal
            held={(v['account_id'],v['ticker']):float64_from_bits(v['quantity_f64_bits'],'quantity')
                for v in rows.positions if float64_from_bits(v['quantity_f64_bits'],'quantity')!=0}
            nonzero=[v for v in rows.positions if float64_from_bits(v['quantity_f64_bits'],'quantity')!=0]
            if len(held)!=len(nonzero) or set(held)!={(key[0],key[2]) for key,v in financials.items() if v.position_quantity!=0}:
                raise ValueError('Selected manager complete broker position inventory differs')
            for key,financial in financials.items():
                matches=[v for v in rows.positions if (v['account_id'],v['ticker'])==(key[0],key[2])]
                if len(matches)>1 or sum(float64_from_bits(v['quantity_f64_bits'],'quantity') for v in matches)!=financial.position_quantity:
                    raise ValueError('Selected manager financial quantity differs from committed broker')
    from .arte_oms_projection import load_latest_committed_oms_groups
    from .fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
    contexts=tuple(getattr(client,'fixed_structural_lot_contexts',()))
    groups=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset(accounts),
        strategy_identity=(source._strategy_id,source._revision),require_tactic=True,fixed_lot_contexts=contexts)
    from .selected_checkpoint_products import selected as selected_products,checkpoint_acquisitions,checkpoint_roster,checkpoint_acquisition_active,checkpoint_aggregate_quantity
    if selected_products(source):
        groups=checkpoint_acquisitions(client,prefix,source=source,contexts=contexts,
            allowed_accounts=frozenset(accounts))
        load_fixed_structural_lot_stop_ceiling=checkpoint_roster
    entries={str(v.unit.base.intents[0]['intent_id']):v for v in contexts}
    active={}
    for group in groups:
        payload=group.group
        entry=entries.get(str(payload['strategy_intent_id']))
        if entry is None:
            raise ValueError('Selected manager OMS acquisition lacks its committed own entry')
        request=entry.verify_source()
        if request.source is not source:
            raise ValueError('Selected manager OMS source operation differs')
        roster=load_fixed_structural_lot_stop_ceiling(entry=request.entry,group_id=payload['group_id'],
            **context.owner._arguments(request,prefix,contexts))
        is_active=(checkpoint_acquisition_active(client,prefix,entry_request=request,
            contexts=contexts,roster=roster) if selected_products(source)
            else any(q>0 for _,q in roster.remaining) or any(v for _,v in roster.acquiring))
        if is_active:
            key=(request.entry.proposal.account_id,request.entry.proposal.assignment_id,request.entry.proposal.ticker)
            if payload['account_id']!=key[0]:
                raise ValueError('Selected manager OMS account differs from its certified entry')
            if key in active:
                raise ValueError('Selected manager OMS repeats an active acquisition')
            if selected_products(source) and (key not in financials or
                    checkpoint_aggregate_quantity(client,prefix,entry_request=request,
                        contexts=contexts,roster=roster)!=Decimal(str(financials[key].position_quantity))):
                raise ValueError('Selected closing aggregate differs from actual committed financial quantity')
            active[key]=roster
    expected_states=dict(context.owner.require_checkpoint(context.checkpoint)[1])
    if set(active)!=set(expected_states) or any(active[key]!=state.roster for key,state in expected_states.items()):
        raise ValueError('Selected manager complete active/acquiring OMS roster differs from its issued inventory')
    return prefix


def publish_manager_publication(client,session,context):
    """Children/readback first, selected parent and separate Keeper head last."""
    from .keeper_session import ManagedKeeperSession
    from .arte_journal_writer import _insert,_wire_row,_CONTRACTS,_literal
    from .arte_typed_insert_dispatch import TypedInsertDispatch,_manager_token
    from .strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    from .fixed_structural_lot_manager_schema import PARENT
    from src.backend.backtest_market_data import assert_select_only
    families=require_manager_publication(context,client=client)
    dispatch=getattr(client,'typed_insert_dispatch',None)
    if (not isinstance(session,ManagedKeeperSession) or not session.writable
            or not isinstance(dispatch,TypedInsertDispatch) or dispatch.keeper is not session.client
            or getattr(client,'typed_insert_strict',False) is not True):
        raise ValueError('Selected manager requires its fenced writer')
    _verify_financial_checkpoint(context,client,session)
    class Reader(ManagedManagerSnapshotHeadReader):
        path=staticmethod(selected_manager_head_path)
    reader=Reader(session)
    previous=reader.read_head(run_id=context.run_id) if session.client.exists(reader.path(context.run_id)) is not None else None
    seal=families[PARENT.name][0]
    if previous is not None and previous.checkpoint_sequence>context.sequence:
        raise ValueError('Selected manager publication would rewind its head')
    operations=[]
    from .fixed_structural_lot_snapshot import ROOT,LOT,RESISTANCE
    from .strategy_one_protection_snapshot import TABLES as PROTECTION_TABLES
    protection_root=PROTECTION_TABLES[0].name
    roots={PARENT.name,ROOT.name,protection_root}
    ordered=(tuple((name,values) for name,values in families.items() if name not in roots)
        +((ROOT.name,families[ROOT.name]),(protection_root,families[protection_root]),
          (PARENT.name,families[PARENT.name])))
    for name,expected in ordered:
        if expected:
            token=_manager_token(context.run_id,context.sequence,seal['content_hash'],name)
            if previous is None or previous.checkpoint_sequence!=context.sequence:
                _insert(client,name,tuple(expected),token,journal_profile='backtest_v4',
                    dispatch_sequence=context.sequence,dispatch_batch_id=context.batch_id,
                    dispatch_manager_snapshot_hash=seal['content_hash'],dispatch_fixed_lot_manager_context=context)
                operations.append((name,token))
        contract=_CONTRACTS[name]
        ids=tuple(sorted({row['snapshot_id'] for row in expected}))
        if ids:
            predicate='snapshot_id IN ('+','.join(f'toUUID({_literal(v)})' for v in ids)+')'
        elif 'checkpoint_sequence' in dict(contract.columns):
            predicate=f'checkpoint_sequence={context.sequence}'
        elif 'through_sequence' in dict(contract.columns):
            predicate=f'through_sequence={context.sequence}'
        else:
            # Empty orphan child IDs have no selected parent; no content may
            # become authoritative without an exact parent inventory member.
            continue
        columns=','.join(f'toString({key}) AS {key}' if 'Decimal(' in kind else key for key,kind in contract.columns)
        sql=assert_select_only(f'SELECT {columns} FROM arte.{name} WHERE run_id={_literal(context.run_id)} AND {predicate} LIMIT {len(expected)+1} FORMAT JSONEachRow')
        observed=tuple(json.loads(v) for v in client.execute(sql).splitlines() if v.strip())
        if len(observed)!=len(expected) or sorted((_canonical(_wire_row(name,v)) for v in observed))!=sorted((_canonical(_wire_row(name,v)) for v in expected)):
            raise ValueError(f'Selected manager exact readback differs: {name}')
    if previous is not None and previous.checkpoint_sequence==context.sequence:
        if previous.journal_batch_id!=context.batch_id or previous.snapshot_hash!=seal['content_hash']:
            raise ValueError('Selected manager repeat conflicts with its selected head')
        return previous
    for table,token in operations:
        dispatch.seal_verified_operation(run_id=context.run_id,table=table,token=token,
            batch_id=context.batch_id,batch_last_sequence=context.sequence,manager_snapshot=True)
    dispatch.compact_verified_fixed_lot_manager_snapshot(client=client,context=context,operations=tuple(operations),previous=previous)
    head=reader.read_head(run_id=context.run_id)
    if (head.checkpoint_sequence,head.journal_batch_id,head.snapshot_hash)!=(context.sequence,context.batch_id,seal['content_hash']):
        raise ValueError('Selected manager head differs after publication')
    return head


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotColdManagerImage:
    """Read-only verified state; deliberately not a live publication/request."""
    source: object
    run_id: str
    sequence: int
    batch_id: str
    inherited: object
    selected_positions: tuple
    financial_roots: tuple


_COLD_IMAGES=WeakKeyDictionary()


def require_cold_manager_image(image,*,source):
    if type(image) is not FixedStructuralLotColdManagerImage:
        raise ValueError('Exact verified cold manager image required')
    binding=_COLD_IMAGES.get(image)
    from src.backend.backtest_fixed_structural_lot_management import _small_tree
    content=_canonical(_small_tree((image.inherited,image.selected_positions,image.financial_roots)))
    if binding!=(source,image.run_id,image.sequence,image.batch_id,content) or image.source is not source:
        raise ValueError('Cold manager image is unissued, changed or foreign')
    source.require_installed_admission()
    return image


def load_cold_manager_image(client,session,*,source,fixed_lot_contexts=(),recovery_contexts=()):
    """Fresh source + complete committed cursor and normalized state, SELECT only.

    No live owner/checkpoint registry is consulted. Recovery batches require
    independently issued cold graph contexts; absence remains fail-closed.
    """
    from .arte_journal_commit_v4 import load_verified_v4_prefix
    from .arte_journal_projection import load_latest_backtest_cursor
    from .arte_journal_writer import _literal,_verify_run_identity
    from .strategy_one_management_snapshot import (ManagedManagerSnapshotHeadReader,
        ManagerSnapshotRows,SOURCE,BREAK,HIGH,CLOSED,FIRST_HELD,_restore_manager_snapshot_scalar)
    from .strategy_one_protection_snapshot import _load_protection_snapshot_rows
    from .fixed_structural_lot_manager_schema import PARENT
    from .fixed_structural_lot_snapshot import ROOT,LOT,RESISTANCE,FixedStructuralLotSnapshotRows,restore_fixed_structural_lot_snapshot
    from .arte_portfolio_snapshot import load_portfolio_snapshot
    from .strategy_one_broker_match_snapshot import ManagedBrokerMatchHeadReader,load_unattested_broker_match_snapshot,float64_from_bits
    from .strategy_one_oms_observation_snapshot import ManagedOmsObservationHeadReader,load_unattested_oms_observation_snapshot
    from .arte_oms_projection import load_latest_committed_oms_groups
    from src.backend.backtest_market_data import assert_select_only
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    if type(fixed_lot_contexts) is not tuple or any(v.source is not source for v in fixed_lot_contexts):
        raise ValueError('Cold manager entry context has foreign fresh source')
    cold_recoveries=[]
    if recovery_contexts:
        from .fixed_structural_lot_cold_recovery import FixedStructuralLotColdRecoveryContext
        if any(type(v[1]) is not FixedStructuralLotColdRecoveryContext for v in recovery_contexts):
            raise ValueError('Fresh cold manager cannot reuse live recovery issuance')
    prefix=load_verified_v4_prefix(client,source.run_id,first_price_source=source.price_authority,
        fixed_lot_contexts=fixed_lot_contexts,fixed_lot_recovery_contexts=recovery_contexts,
        _fixed_lot_cold_source=source,_cold_recovery_context_sink=cold_recoveries)
    if prefix is None:raise ValueError('Cold selected manager lacks committed source prefix')
    # Nested historical side-product reads retain this completed cold walk's
    # authentic inventory without changing the current-head or verifier authority.
    from .selected_checkpoint_products import _HistoricalReads,_HISTORICAL_READ_ISSUER
    client=_HistoricalReads(client,source,prefix,fixed_lot_contexts,
        tuple(recovery_contexts)+tuple(cold_recoveries),issuer=_HISTORICAL_READ_ISSUER)
    cursor=load_latest_backtest_cursor(client,prefix)
    if prefix is None or type(cursor) is not dict or cursor['event_sequence']!=prefix.last_sequence or cursor['batch_id']!=prefix.last_batch_id:
        raise ValueError('Cold selected manager lacks complete committed cursor')
    class Reader(ManagedManagerSnapshotHeadReader):
        path=staticmethod(selected_manager_head_path)
    reader=Reader(session);head=reader.read_head(run_id=source.run_id)
    if (head.checkpoint_sequence,head.journal_batch_id)!=(prefix.last_sequence,prefix.last_batch_id):
        raise ValueError('Cold selected manager head has foreign cursor')
    def read(contract,predicate,count):
        if type(count) is not int or not 0<=count<=100_000:
            raise ValueError('Cold manager child cardinality is unbounded')
        columns=','.join(f'toString({k}) AS {k}' if 'Decimal(' in t else k for k,t in contract.columns)
        sql=assert_select_only(f'SELECT {columns} FROM arte.{contract.name} WHERE run_id={_literal(source.run_id)} AND {predicate} LIMIT {count+1} FORMAT JSONEachRow')
        values=tuple(json.loads(v) for v in client.execute(sql).splitlines() if v.strip())
        if len(values)!=count:raise ValueError('Cold selected manager exact inventory differs: '+contract.name)
        return values
    seal=read(PARENT,f'checkpoint_sequence={prefix.last_sequence}',1)[0]
    if (seal['content_hash']!=head.snapshot_hash or seal['content_hash']!=_digest({k:v for k,v in seal.items() if k!='content_hash'})
            or seal['boundary_ms']!=cursor['boundary_ms'] or seal['session_date']!=source.session_date.isoformat()
            or seal['selected_configuration_hash']!=source.selected_configuration_hash):
        raise ValueError('Cold manager seal differs from source/cursor/head')
    own=read(ROOT,f'through_sequence={prefix.last_sequence}',seal['selected_position_count'])
    entries={v.base.intents[0]['intent_id']:v for v in fixed_lot_contexts}
    selected=[];states={};inventory=[]
    for root in sorted(own,key=lambda v:(v['account_id'],v['assignment_id'],v['ticker'])):
        key=(root['account_id'],root['assignment_id'],root['ticker'])
        context=entries.get(root['intent_id'])
        if context is None:raise ValueError('Cold manager position lacks verified original entry')
        request=context.verify_source()
        predicate='snapshot_id=toUUID('+_literal(root['snapshot_id'])+')'
        rows=FixedStructuralLotSnapshotRows(root,read(LOT,predicate,root['lot_count']),read(RESISTANCE,predicate,root['resistance_count']))
        state=restore_fixed_structural_lot_snapshot(rows,entry=request.entry,client=client,prefix=prefix,
            intervals=source.intervals,intent=request.intent,strategy_identity=(request.strategy_id,request.revision),
            entry_request=request,fixed_lot_contexts=fixed_lot_contexts)
        if key in states:raise ValueError('Cold selected manager duplicates position')
        states[key]=state;selected.append((key,rows))
        inventory.append([*key,root['snapshot_id'],root['content_hash']])
    if _digest(inventory)!=seal['selected_position_hash']:
        raise ValueError('Cold selected manager inventory hash differs')
    predicate='snapshot_id=toUUID('+_literal(seal['snapshot_id'])+')'
    inherited_seal={k:v for k,v in seal.items() if k not in {'selected_configuration_hash','selected_position_count','selected_position_hash','content_hash'}}
    if seal['source_count']==0 and seal['first_held_count']==0:
        inherited_seal.pop('first_held_count');inherited_seal.pop('first_held_hash')
    inherited_seal['content_hash']=_digest(inherited_seal)
    protection=_load_protection_snapshot_rows(client,run_id=source.run_id,checkpoint_sequence=prefix.last_sequence,
        _selected_positions={(key[0],key[2],key[1]):state.protection for key,state in states.items()})
    inherited_rows=ManagerSnapshotRows(inherited_seal,read(SOURCE,predicate,seal['source_count']),
        read(BREAK,predicate,seal['pending_break_count']),protection,read(HIGH,predicate,seal['position_high_count']),
        read(CLOSED,predicate,seal['closed_position_count']),read(FIRST_HELD,predicate,seal['first_held_count']))
    inherited=_restore_manager_snapshot_scalar(inherited_rows,_selected_positions={
        (key[0],key[2],key[1]):state.protection for key,state in states.items()})
    from dataclasses import replace
    submitted=[]
    for key,proposal in inherited.submitted:
        matches=[v.verify_source().entry.proposal for v in fixed_lot_contexts
            if (v.unit.packet.root['account_id'],v.unit.packet.root['assignment_id'],v.unit.packet.root['ticker'])==key
            and v.unit.packet.root['boundary_ms']==proposal.boundary_ms]
        if len(matches)!=1:
            raise ValueError('Cold manager lacks complete original submitted entry source')
        submitted.append((key,matches[0]))
    inherited=replace(inherited,submitted=tuple(submitted))
    from .strategy_one_management_snapshot import _project_manager_snapshot_scalar
    if _project_manager_snapshot_scalar(run_id=source.run_id,session_date=source.session_date,
            checkpoint_sequence=prefix.last_sequence,state=inherited,_protection_rows=protection)!=inherited_rows:
        raise ValueError('Cold original entry source differs from inherited manager rows')
    groups=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset(_verify_run_identity(client,source.run_id)['account_ids']),
        strategy_identity=(source._strategy_id,source._revision),require_tactic=True,fixed_lot_contexts=fixed_lot_contexts)
    active=set()
    from .fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
    from .selected_checkpoint_products import selected as selected_products,checkpoint_acquisitions,checkpoint_roster,checkpoint_aggregate_quantity,checkpoint_acquisition_active
    if selected_products(source):
        groups=checkpoint_acquisitions(client,prefix,source=source,contexts=fixed_lot_contexts,
            allowed_accounts=frozenset(_verify_run_identity(client,source.run_id)['account_ids']))
        load_fixed_structural_lot_stop_ceiling=checkpoint_roster
    for group in groups:
        context=entries.get(group.group['strategy_intent_id'])
        if context is None:raise ValueError('Cold manager has unexpected OMS source inventory')
        request=context.verify_source();p=request.entry.proposal
        roster=load_fixed_structural_lot_stop_ceiling(client,prefix,entry=request.entry,intervals=source.intervals,
            intent=request.intent,group_id=group.group['group_id'],strategy_identity=(request.strategy_id,request.revision),
            entry_request=request,fixed_lot_contexts=fixed_lot_contexts)
        is_active=(checkpoint_acquisition_active(client,prefix,entry_request=request,
            contexts=fixed_lot_contexts,roster=roster) if selected_products(source)
            else any(q>0 for _,q in roster.remaining) or roster.acquiring)
        if is_active:
            key=(p.account_id,p.assignment_id,p.ticker)
            if key in active or key not in states or states[key].roster!=roster:
                raise ValueError('Cold manager complete active OMS roster differs')
            active.add(key)
    if active!=set(states):raise ValueError('Cold manager omits active/acquiring OMS lot')
    from datetime import datetime,timedelta,timezone
    from zoneinfo import ZoneInfo
    instant=(datetime.combine(source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
        +timedelta(hours=4,milliseconds=seal['boundary_ms'])).astimezone(timezone.utc)
    financial=[]
    for account in _verify_run_identity(client,source.run_id)['account_ids']:
        loaded=load_portfolio_snapshot(client,run_id=source.run_id,account_id=account,state_revision=prefix.last_sequence)
        if loaded is None or datetime.fromisoformat(loaded['snapshot_at'])!=instant:
            raise ValueError('Cold manager lacks same-cursor Portfolio root/time')
        financial.append(('portfolio',account,loaded['state_hash'],loaded['snapshot_at']))
    for reader_type,loader,root_field in ((ManagedBrokerMatchHeadReader,load_unattested_broker_match_snapshot,'snapshot'),
            (ManagedOmsObservationHeadReader,load_unattested_oms_observation_snapshot,'root')):
        financial_reader=reader_type(session);financial_head=financial_reader.read_head(run_id=source.run_id)
        rows=loader(client,run_id=source.run_id,checkpoint_sequence=prefix.last_sequence);root=getattr(rows,root_field)
        if (financial_head.checkpoint_sequence,financial_head.journal_batch_id,financial_head.snapshot_hash)!=(
                prefix.last_sequence,prefix.last_batch_id,root['content_hash']) or root['boundary_ms']!=seal['boundary_ms'] or root['session_date']!=source.session_date.isoformat() or financial_reader.read_head(run_id=source.run_id)!=financial_head:
            raise ValueError('Cold manager financial root/head changed or has foreign cursor')
        if root_field=='snapshot':
            held={(v['account_id'],v['ticker']):float64_from_bits(v['quantity_f64_bits'],'quantity') for v in rows.positions if float64_from_bits(v['quantity_f64_bits'],'quantity')!=0}
            expected={(key[0],key[2]):float(sum(q for _,q in state.roster.remaining)) for key,state in states.items() if any(q>0 for _,q in state.roster.remaining)}
            if selected_products(source):
                quantities={key:checkpoint_aggregate_quantity(client,prefix,
                    entry_request=entries[state.roster.intent_id].verify_source(),
                    contexts=fixed_lot_contexts,roster=state.roster) for key,state in states.items()}
                expected={(key[0],key[2]):float(quantity) for key,quantity in quantities.items() if quantity}
            if len(held)!=sum(float64_from_bits(v['quantity_f64_bits'],'quantity')!=0 for v in rows.positions) or held!=expected:
                raise ValueError('Cold manager broker inventory differs from complete lots')
        financial.append((root_field,root['content_hash']))
    if reader.read_head(run_id=source.run_id)!=head:raise ValueError('Cold selected manager head changed during read')
    result=FixedStructuralLotColdManagerImage(source,source.run_id,prefix.last_sequence,prefix.last_batch_id,inherited,tuple(selected),tuple(financial))
    from src.backend.backtest_fixed_structural_lot_management import _small_tree
    _COLD_IMAGES[result]=(source,result.run_id,result.sequence,result.batch_id,
        _canonical(_small_tree((result.inherited,result.selected_positions,result.financial_roots))))
    return result
