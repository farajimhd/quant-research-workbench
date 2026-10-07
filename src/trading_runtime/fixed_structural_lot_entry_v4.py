"""Normalized selected entry source equivalence, never financial admission.

The original journal event and common scalar semantic intent are unchanged.
Three named families retain geometry and complete typed derivation/proposal
trees. Cold replay requires a freshly issued complete source preparation.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from uuid import UUID, NAMESPACE_URL, uuid5

from .fixed_structural_lot_entry_schema import CONTRACT, COMMON, ENTRY, LOT, NODE, TABLES, TREE_KINDS
from .journal_contract import JournalRecord, canonical_json
from .arte_intent_projection import strategy_intent_batch
from .arte_journal_writer import TypedJournalBatch
from .strategy_one_configuration_tree import encode_nodes, decode_nodes
from .strategy_one_stateful import StrategyOneEntryProposal
from .strategy_rising_momentum_witness import RisingMomentumWitness, CompletedMomentumObservation
from .strategy_initial_strong_momentum import InitialMomentumSelectionWitness, InitialStrongMomentumWitness
from .strategy_initial_price_break import FirstSetupPriceBreakWitness
from src.backend.backtest_fixed_structural_lot_source import FixedStructuralLotRequest, PreparedFixedStructuralLotSource

_NATIVE = {t.__name__:t for t in (StrategyOneEntryProposal,RisingMomentumWitness,
    CompletedMomentumObservation,InitialMomentumSelectionWitness,InitialStrongMomentumWitness,FirstSetupPriceBreakWitness)}


def _hash(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def _exact(left,right):
    if isinstance(left,Mapping) and isinstance(right,Mapping):
        return left.keys()==right.keys() and all(_exact(left[k],right[k]) for k in left)
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_exact(getattr(left,f.name),getattr(right,f.name)) for f in fields(left))
    if type(left) in (tuple,list):
        return len(left)==len(right) and all(_exact(a,b) for a,b in zip(left,right))
    return left==right


def _proposal_tree(value):
    if is_dataclass(value):
        if type(value) not in _NATIVE.values():
            raise ValueError('Unsupported native proposal class')
        return {'type':type(value).__name__,'fields':{f.name:_proposal_tree(getattr(value,f.name)) for f in fields(value)}}
    if type(value) is tuple:
        return {'tuple':[_proposal_tree(v) for v in value]}
    if value is None or type(value) in (str,int,float,bool):
        return value
    raise ValueError('Unsupported native proposal scalar')


def _decode_proposal(value):
    if type(value) is dict:
        if set(value)=={'tuple'} and type(value['tuple']) is list:
            return tuple(_decode_proposal(v) for v in value['tuple'])
        if set(value)!={'type','fields'} or type(value['type']) is not str or value['type'] not in _NATIVE:
            raise ValueError('Unknown native proposal tree class or field')
        cls=_NATIVE[value['type']]
        if type(value['fields']) is not dict or set(value['fields'])!={f.name for f in fields(cls)}:
            raise ValueError('Native proposal tree fields differ')
        return cls(**{k:_decode_proposal(v) for k,v in value['fields'].items()})
    if value is None or type(value) in (str,int,float,bool):
        return value
    raise ValueError('Unknown native proposal tree scalar')


def _check_row(table,row):
    if not isinstance(row,Mapping) or set(row)!={n for n,_ in table.columns}:
        raise ValueError('Fixed lot companion column inventory differs')
    for name,kind in table.columns:
        value=row[name]
        if kind.startswith('Nullable('):
            if value is None: continue
            kind=kind[9:-1]
        if kind.startswith('UInt'):
            if type(value) is not int or not 0 <= value < 2**int(kind[4:]):
                raise ValueError('Fixed lot companion integer scalar differs')
        elif kind=='Int64':
            if type(value) is not int or not -(2**63)<=value<2**63:
                raise ValueError('Fixed lot companion signed scalar differs')
        elif kind=='Float64':
            if type(value) is not float or not isfinite(value):
                raise ValueError('Fixed lot companion Float64 scalar differs')
        elif kind=='UUID':
            if type(value) is not str or str(UUID(value))!=value or not UUID(value).int:
                raise ValueError('Fixed lot companion UUID differs')
        elif kind=='Date':
            if type(value) is not str or date.fromisoformat(value).isoformat()!=value:
                raise ValueError('Fixed lot companion date differs')
        elif kind=='FixedString(64)':
            if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Fixed lot companion hash differs')
        elif kind=='String' and type(value) is not str:
            raise ValueError('Fixed lot companion text differs')
    if row['content_hash']!=_hash({k:v for k,v in row.items() if k!='content_hash'}):
        raise ValueError('Fixed lot companion content hash differs')


def _seal(table,row):
    row={**row,'content_hash':_hash(row)}
    _check_row(table,row)
    return MappingProxyType(row)


@dataclass(frozen=True,slots=True)
class FixedStructuralLotEntryRows:
    root: MappingProxyType
    lots: tuple[MappingProxyType,...]
    nodes: tuple[MappingProxyType,...]

    def __post_init__(self):
        if (type(self.root) is not MappingProxyType or type(self.lots) is not tuple
                or type(self.nodes) is not tuple or not 2<=len(self.lots)<=32 or not 3<=len(self.nodes)<=30000
                or any(type(r) is not MappingProxyType for r in (*self.lots,*self.nodes))):
            raise ValueError('Immutable bounded fixed lot rows required')
        for table,rows in ((ENTRY,(self.root,)),(LOT,self.lots),(NODE,self.nodes)):
            for row in rows: _check_row(table,row)
        root=self.root
        if root['companion_contract']!=CONTRACT or root['record_id']!=root['root_record_id']:
            raise ValueError('Fixed lot root identity differs')
        if root['lot_count']!=len(self.lots) or root['lot_hash']!=_hash([dict(r) for r in self.lots]):
            raise ValueError('Fixed lot child count/hash differs')
        if tuple(r['ordinal'] for r in self.lots)!=tuple(range(len(self.lots))):
            raise ValueError('Fixed lot child order differs')
        for row in (*self.lots,*self.nodes):
            if any(row[k]!=root[k] for k in ('parent_record_id','root_record_id','run_id','event_month','batch_id','sequence')):
                raise ValueError('Fixed lot child parent/envelope differs')
        if len({r['record_id'] for r in (*self.lots,*self.nodes)})!=len(self.lots)+len(self.nodes):
            raise ValueError('Fixed lot child identity duplicates')
        if root['configuration_nodes_hash']!=_hash([dict(r) for r in self.nodes]):
            raise ValueError('Fixed lot complete node hash differs')
        trees={kind:[] for kind in TREE_KINDS}
        ordering=[]
        for row in self.nodes:
            if row['tree_kind'] not in trees:
                raise ValueError('Unknown fixed lot node tree')
            ordering.append((TREE_KINDS.index(row['tree_kind']),row['node_id']))
            trees[row['tree_kind']].append({k:v for k,v in row.items() if k not in {n for n,_ in COMMON}|{'tree_kind','content_hash'}})
        if ordering!=sorted(ordering):
            raise ValueError('Fixed lot node trees are out of order')
        counts=('parent_node_count','selected_node_count','proposal_node_count')
        for kind,count in zip(TREE_KINDS,counts):
            if len(trees[kind])!=root[count]:
                raise ValueError('Fixed lot node tree population differs')
            decode_nodes(trees[kind])
        proposal=decode_nodes(trees['proposal'])
        if root['proposal_hash']!=_hash(proposal):
            raise ValueError('Fixed lot proposal hash differs')
        if type(_decode_proposal(proposal)) is not StrategyOneEntryProposal:
            raise ValueError('Exact native proposal tree required')

    def families(self):
        return ((ENTRY.name,(self.root,)),(LOT.name,self.lots),(NODE.name,self.nodes))


def project_fixed_structural_lot_entry(record,batch,request):
    if type(record) is not JournalRecord or type(batch) is not TypedJournalBatch or type(request) is not FixedStructuralLotRequest:
        raise ValueError('Exact original record, semantic batch and selected request required')
    request.verify()
    if (record.run_id!=request.run_id or record.account_id!=request.entry.proposal.account_id
            or record.entity_type!='fixed_structural_lot_entry_intent' or record.category!='strategy'
            or record.entity_id!=request.intent.intent_id
            or not _exact(record.payload,{**request.intent.payload(),'strategy_id':request.strategy_id,'strategy_revision':request.revision})):
        raise ValueError('Original selected journal record differs from request')
    expected=strategy_intent_batch(request.intent,run_id=batch.run_id,run_month=batch.run_month,
        account_id=record.account_id,attempt_id=batch.attempt_id,batch_id=batch.batch_id,
        prior_batch_id=batch.prior_batch_id,sequence=record.sequence,source_cursor=batch.source_cursor,
        run_status=batch.status,recorded_at=record.recorded_at,record_id=record.record_id,
        correlation_id='',causation_id='')
    expected=replace(expected,events=({**expected.events[0],'entity_type':record.entity_type},))
    if not _exact(expected,batch):
        raise ValueError('Selected semantic base or original event lineage differs')
    entry,source=request.entry,request.source
    root_id=str(uuid5(NAMESPACE_URL,f'{CONTRACT}:{record.record_id}'))
    common=dict(parent_record_id=record.record_id,root_record_id=root_id,run_id=record.run_id,
        event_month=batch.events[0]['event_month'],batch_id=batch.batch_id,sequence=record.sequence)
    lots=tuple(_seal(LOT,dict(common,record_id=str(uuid5(NAMESPACE_URL,f'{root_id}:lot:{i}')),
        ordinal=i,slice_id=request.intent.protection_profile.slices[i].slice_id,
        weight_numerator=source.policy.weights[i][0],weight_denominator=source.policy.weights[i][1],
        initial_stop=entry.proposal.initial_stop,fixed_target=t.price,level_id=t.level_id,lower=t.lower,upper=t.upper,
        confirmed_at_ms=t.confirmed_at_ms,historical=int(t.historical),role=t.role,transition_from=t.transition_from))
        for i,t in enumerate(entry.targets))
    payloads=(source.parent_payload,source.selected_payload,_proposal_tree(entry.proposal))
    node_sets=tuple(encode_nodes(value) for value in payloads)
    nodes=tuple(_seal(NODE,dict(common,record_id=str(uuid5(NAMESPACE_URL,f'{root_id}:{kind}:{row["node_id"]}')),
        tree_kind=kind,**row)) for kind,rows in zip(TREE_KINDS,node_sets) for row in rows)
    q=source._quotes[(entry.proposal.ticker,entry.proposal.boundary_ms)]
    root=dict(common,record_id=root_id,companion_contract=CONTRACT,intent_id=request.intent.intent_id,
        original_intent_id=request.original.intent_id,strategy_id=request.strategy_id,revision=request.revision,
        account_id=record.account_id,assignment_id=entry.proposal.assignment_id,session_date=entry.session_date.isoformat(),
        ticker=entry.proposal.ticker,boundary_ms=entry.proposal.boundary_ms,episode_start_ms=entry.proposal.episode_start_ms,
        reference_ask=entry.proposal.reference_ask,initial_stop=entry.proposal.initial_stop,original_target=entry.proposal.initial_target,
        target_level_id=entry.proposal.target_level_id,tick=entry.tick,**{k:getattr(entry,k) for k in (
        'source_build_id','interval_token','source_attempt_id','bars_attempt_id','source_checkpoint_hash','decoded_seed_hash',
        'seed_source_plan_hash','seed_input_policy','split_evidence_hash','clock_count','interval_count','clock_hash','interval_hash')},
        **{k:getattr(q,k) for k in ('market_plan_token','gate_token','broker_attempt_id','bid_int','ask_int','quote_timestamp_us','quote_valid')},
        parent_configuration_hash=source.parent_payload_hash,
        **{k:getattr(source,k) for k in ('parent_attempt_id','parent_token','parent_node_hash',
        'parent_source_candidate_id','parent_source_candidate_hash','selected_configuration_hash')},
        policy_version=source.policy.version,lot_count=source.policy.count,
        **{k:getattr(source.policy,k) for k in ('allocation','target_selection','target_price_rule','target_management',
        'stop_management','aggregate_exit','entry_reentry','profile_id')},lot_hash=_hash([dict(r) for r in lots]),
        parent_node_count=len(node_sets[0]),selected_node_count=len(node_sets[1]),proposal_node_count=len(node_sets[2]),
        configuration_nodes_hash=_hash([dict(r) for r in nodes]),proposal_hash=_hash(payloads[2]))
    return FixedStructuralLotEntryRows(_seal(ENTRY,root),lots,nodes)


def fixed_structural_lot_semantic_batch(record,request,*,run_month,attempt_id,batch_id,
        prior_batch_id,source_cursor):
    """Shared scalar projection directly, preserving original own event type."""
    if type(record) is not JournalRecord or type(request) is not FixedStructuralLotRequest:
        raise ValueError('Exact retained own record/request required')
    request.verify()
    base=strategy_intent_batch(request.intent,run_id=record.run_id,run_month=run_month,
        account_id=record.account_id,attempt_id=attempt_id,batch_id=batch_id,prior_batch_id=prior_batch_id,
        sequence=record.sequence,source_cursor=source_cursor,run_status='running',
        recorded_at=record.recorded_at,record_id=record.record_id)
    base=replace(base,events=({**base.events[0],'entity_type':record.entity_type},))
    # Full original record/payload equality is deliberately checked by project.
    packet=project_fixed_structural_lot_entry(record,base,request)
    return V4FixedStructuralLotEntryBatch(base,packet)


def restore_fixed_structural_lot_entry(rows,*,record,batch,source):
    """Source equivalence only; source must come from a fresh complete factory."""
    if type(rows) is not FixedStructuralLotEntryRows or type(source) is not PreparedFixedStructuralLotSource:
        raise ValueError('Exact normalized selected packet and freshly prepared source required')
    rows.__post_init__()
    source.require_prepared_source()
    tree=tuple({k:v for k,v in row.items() if k not in {n for n,_ in COMMON}|{'tree_kind','content_hash'}}
               for row in rows.nodes if row['tree_kind']=='proposal')
    proposal=_decode_proposal(decode_nodes(tree))
    request=source.request(proposal)
    expected=project_fixed_structural_lot_entry(record,batch,request)
    if not _exact(rows,expected):
        raise ValueError('Normalized selected source differs from fresh complete replay')
    return request


@dataclass(frozen=True,slots=True)
class V4FixedStructuralLotEntryBatch:
    base: TypedJournalBatch
    packet: FixedStructuralLotEntryRows

    def __post_init__(self):
        if type(self.base) is not TypedJournalBatch or type(self.packet) is not FixedStructuralLotEntryRows:
            raise ValueError('Exact selected V4 source wrapper required')
        self.packet.__post_init__()
        root=self.packet.root
        if (len(self.base.events)!=1 or len(self.base.intents)!=1 or self.base.status!='running'
                or root['run_id']!=self.base.run_id or root['batch_id']!=self.base.batch_id
                or root['sequence']!=self.base.events[0]['sequence']
                or root['parent_record_id']!=self.base.events[0]['record_id']
                or root['intent_id']!=self.base.intents[0]['intent_id']
                or root['account_id']!=self.base.intents[0]['account_id']
                or root['ticker']!=self.base.intents[0]['ticker']):
            raise ValueError('Selected V4 packet is detached from original semantic base')


@dataclass(frozen=True,slots=True)
class FixedStructuralLotPublicationContext:
    """Exact source-equivalence context; its installed admission remains closed."""
    unit: V4FixedStructuralLotEntryBatch
    record: JournalRecord
    source: PreparedFixedStructuralLotSource

    @property
    def base(self):
        return self.unit.base

    def verify_source(self):
        if (type(self.unit) is not V4FixedStructuralLotEntryBatch or type(self.record) is not JournalRecord
                or type(self.source) is not PreparedFixedStructuralLotSource):
            raise ValueError('Exact selected publication source context required')
        self.unit.__post_init__()
        return restore_fixed_structural_lot_entry(self.unit.packet,record=self.record,
            batch=self.unit.base,source=self.source)

    def verify_admission(self):
        self.verify_source()
        self.source.require_installed_admission()


def sealed_fixed_structural_lot_families(unit):
    from .arte_journal_writer import _sealed_families, _canonical_typed_content
    if type(unit) is not V4FixedStructuralLotEntryBatch:
        raise ValueError('Exact selected V4 source unit required')
    base=_sealed_families(unit.base,fixed_lot_unit=unit)
    for name,rows in unit.packet.families():
        for row in rows:
            content={k:v for k,v in row.items() if k!='content_hash'}
            if _hash(_canonical_typed_content(name,content))!=row['content_hash']:
                raise ValueError('Selected companion differs from shared typed scalar projection')
    return base,(*base,*unit.packet.families())


def verify_fixed_structural_lot_publication_graph(batch,base,families,context):
    names={t.name for t in TABLES}
    own={name:rows for name,rows in families if name in names and rows}
    events=dict(base).get('trading_event_v1',())
    selected=any(r.get('entity_type')=='fixed_structural_lot_entry_intent' for r in events)
    if not own and not selected:
        if context is not None:
            raise ValueError('Foreign selected context cannot approve an ordinary batch')
        return
    if (type(context) is not FixedStructuralLotPublicationContext or context.unit.base is not batch
            or set(own)!=names):
        raise ValueError('Own lot publication requires complete exact companion/source context')
    context.verify_admission()
    expected_base,expected=sealed_fixed_structural_lot_families(context.unit)
    if not _exact(base,expected_base) or not _exact(tuple(families),tuple(expected)):
        raise ValueError('Selected publication graph differs from original complete source')


def verify_fixed_structural_lot_cold_graph(rows,context,commit):
    own={t.name:rows.get(t.name,()) for t in TABLES}
    events=rows.get('trading_event_v1',())
    selected=any(r.get('entity_type')=='fixed_structural_lot_entry_intent' for r in events)
    if not any(own.values()) and not selected:
        if context is not None:
            raise ValueError('Foreign selected context cannot approve ordinary cold recovery')
        return
    if type(context) is not FixedStructuralLotPublicationContext:
        raise ValueError('Own lot cold recovery requires fresh complete source context')
    context.verify_admission()
    base,expected=sealed_fixed_structural_lot_families(context.unit)
    if (context.unit.base.run_id!=commit['run_id'] or context.unit.base.batch_id!=commit['batch_id']
            or context.unit.base.first_sequence!=commit['first_sequence']
            or context.unit.base.last_sequence!=commit['last_sequence']):
        raise ValueError('Own lot cold commit differs from original source envelope')
    # SQL transport may render integral Float64 as integer or UInt64 as text.
    # Normalize only through the declared shared table contract; never infer
    # missing values or tolerate extra fields, booleans or changed content.
    from .arte_journal_writer import _canonical_typed_content
    for name,wanted in (*base,*context.unit.packet.families()):
        if name not in {'trading_event_v1','trading_strategy_intent_v1','trading_intent_protection_slice_v1',*own}:
            continue
        observed=rows.get(name,())
        if len(observed)!=len(wanted):
            raise ValueError('Own lot cold family is incomplete')
        by_id={r['record_id']:r for r in observed}
        if len(by_id)!=len(observed):
            raise ValueError('Own lot cold family duplicates')
        for row in wanted:
            actual=by_id.get(row['record_id'])
            if actual is None or set(actual)!=set(row):
                raise ValueError('Own lot cold family identity/columns differ')
            content=_canonical_typed_content(name,{k:v for k,v in actual.items() if k!='content_hash'},stored_utc=True)
            canonical={**content,'content_hash':actual['content_hash']}
            expected_content=_canonical_typed_content(name,{k:v for k,v in row.items() if k!='content_hash'})
            expected_row={**expected_content,'content_hash':row['content_hash']}
            if not _exact(canonical,expected_row):
                raise ValueError('Own lot cold content differs from fresh source replay')


def publish_fixed_structural_lot_entry_v4(client,context):
    """Production gate intentionally closed until an own release is installed."""
    if type(context) is not FixedStructuralLotPublicationContext:
        raise ValueError('Exact own lot publication context required')
    context.verify_admission()
    from .fixed_structural_lot_profile import require_fixed_structural_lot_client_context
    require_fixed_structural_lot_client_context(client,context)
    base,families=sealed_fixed_structural_lot_families(context.unit)
    from .arte_journal_commit_v4 import _publish_sealed_batch_v4
    return _publish_sealed_batch_v4(client,context.unit.base,base,families,fixed_lot_context=context)


def fixed_lot_contexts_by_batch(run_id, contexts, *, max_commits):
    if type(contexts) is not tuple or len(contexts)>max_commits:
        raise ValueError('Bounded immutable selected cold contexts required')
    result={}
    for context in contexts:
        if type(context) is not FixedStructuralLotPublicationContext:
            raise ValueError('Exact selected cold context required')
        context.verify_source()
        base=context.unit.base
        if base.run_id!=run_id or base.batch_id in result:
            raise ValueError('Foreign or duplicate selected cold batch context')
        result[base.batch_id]=context
    return result
