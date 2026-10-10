"""Issued initial-open scope; complete first verification, no cold admission."""
from collections import OrderedDict
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import wraps
from types import MappingProxyType
from sys import getsizeof
from weakref import WeakSet

_OPERATION = ContextVar('issued_initial_held_operation',default=None)
_READ = ContextVar('issued_initial_held_reader',default=None)
_ISSUED = WeakSet()

def _copy(value):
    """Independent typed descendants; never export a private mapping alias."""
    if value is None or type(value) in (str,int,float,bool,Decimal,date,datetime) or isinstance(value,Enum):return value
    if type(value) is dict:return {k:_copy(v) for k,v in value.items()}
    if type(value) is MappingProxyType:return MappingProxyType({k:_copy(v) for k,v in value.items()})
    if type(value) is tuple:return tuple(_copy(v) for v in value)
    if type(value) is list:return [_copy(v) for v in value]
    if type(value) is set:return {_copy(v) for v in value}
    if type(value) is frozenset:return frozenset(_copy(v) for v in value)
    if is_dataclass(value):return replace(value,**{f.name:_copy(getattr(value,f.name)) for f in fields(value)})
    raise ValueError('Unsupported initial-held inventory type: '+type(value).__name__)

def _nodes(value):
    if type(value) in (dict,MappingProxyType):return 1+sum(_nodes(v) for v in value.values())
    if type(value) in (tuple,list,set,frozenset):return sum(_nodes(v) for v in value)
    if is_dataclass(value):return sum(_nodes(getattr(value,f.name)) for f in fields(value))
    return 0

def _shape(value):
    if type(value) in (dict,MappingProxyType):return (type(value),tuple((type(k),k,_shape(v)) for k,v in value.items()))
    if type(value) in (tuple,list):return (type(value),tuple(_shape(v) for v in value))
    if type(value) in (set,frozenset):return (type(value),frozenset(_shape(v) for v in value))
    if is_dataclass(value):return (type(value),tuple((f.name,_shape(getattr(value,f.name))) for f in fields(value)))
    return type(value)

def _image(value):
    from .backtest_fixed_lot_management_reuse import _content
    return _content(value),_shape(value)

def _size(value):
    if type(value) in (dict,MappingProxyType):return getsizeof(value)+sum(getsizeof(k)+_size(v) for k,v in value.items())
    if type(value) in (tuple,list,set,frozenset):return getsizeof(value)+sum(_size(v) for v in value)
    if is_dataclass(value):return getsizeof(value)+sum(_size(getattr(value,f.name)) for f in fields(value))
    return getsizeof(value)

def _selected(source, *, proposal=False):
    from src.trading_runtime.initial_held_recovery_reuse_policy import PARAMETER,parse_declared_initial_held_reuse
    from src.trading_runtime.strategy_registry import numbered_strategy,fixed_strategy_executor
    from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
    if proposal:
        from src.trading_runtime.proposal_decision_inventory_reuse_policy import PARAMETER,INPUT,RULE,parse_declared_proposal_decision_reuse as parse_declared_initial_held_reuse
    payload=source.installed_payload
    if payload is None:return None
    if type(payload) is not dict or type(payload.get('strategy')) is not dict:
        raise ValueError('Initial-held installed strategy payload differs')
    strategy=payload['strategy']
    declaration=strategy.get('numbered_release',{}).get('contract',{})
    if not proposal:
        from src.trading_runtime.initial_held_recovery_reuse_policy import INPUT,RULE
    if (PARAMETER not in strategy['parameters'] and INPUT not in declaration.get('input_contracts',())
            and RULE not in declaration.get('rule_set_contracts',())):return None
    source.require_prepared_source();source.require_installed_admission()
    release=numbered_strategy(strategy['strategy_number'])
    policy=parse_declared_initial_held_reuse(release,strategy['parameters'].get(PARAMETER))
    if policy is None:return None
    if strategy['numbered_release']['contract']!=release.canonical_payload():raise ValueError('Initial-held installed declaration changed')
    contract=fixed_strategy_executor(release.executor_strategy_id,release.executor_revision).contract_factory()
    if type(contract) is not FixedStructuralLotSelectedExitStrategyContract or contract.release!=release or getattr(contract,'proposal_decision_inventory_reuse_policy' if proposal else 'initial_held_recovery_reuse_policy')!=policy:
        raise ValueError('Initial-held exact registered factory differs')
    contract.__post_init__()
    return policy

def _codes():
    from src.trading_runtime import fixed_structural_lot_entry_v4 as entry
    from src.trading_runtime import fixed_structural_lot_warm_proof as warm
    from src.trading_runtime import initial_held_recovery_reuse_policy as policy
    from .backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    names=('_copy','_nodes','_shape','_image','_size','_selected','_codes','initial_open_scope','owner_reader','context_request','inventory_read','verify_context_source','proposal_scope','_cold_loader','_bypass','_method_chain','_factory_current','_decoder_snapshot')
    funcs=[globals()[name] for name in names]
    funcs += [entry.FixedStructuralLotPublicationContext.verify_source,entry.FixedStructuralLotPublicationContext._verify_source_complete,warm.load_oms_groups,warm._load_oms_groups_complete,_Initial.require,_Initial.__init__]
    funcs += list(_method_chain(NativeFixedStructuralLotManagement.open))+list(_method_chain(NativeFixedStructuralLotManagement.propose))
    from .backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    funcs += [PreparedFixedStructuralLotSource.installed_payload.fget,PreparedFixedStructuralLotSource.require_prepared_source,PreparedFixedStructuralLotSource.require_installed_admission]
    from .backtest_fixed_lot_management_reuse import _policy_snapshot,_policy_value
    from src.trading_runtime import proposal_decision_inventory_reuse_policy as proposal
    funcs += [proposal_scope.__wrapped__,_policy_snapshot,_policy_value,proposal.require_declared_proposal_decision_reuse,proposal.parse_declared_proposal_decision_reuse,proposal.ProposalDecisionInventoryReusePolicy.__post_init__,proposal.ProposalDecisionInventoryReusePolicy.payload]
    funcs += [fields,is_dataclass,replace,getsizeof,policy.InitialHeldRecoveryReusePolicy.__post_init__,
        policy.InitialHeldRecoveryReusePolicy.payload,policy.require_declared_initial_held_reuse,
        policy.parse_declared_initial_held_reuse]
    from .backtest_fixed_lot_first_inventory_source_reuse import code_snapshot
    first_codes,first_bindings=code_snapshot()
    funcs += [fn for fn,_ in first_codes]
    return (tuple((fn,getattr(fn,'__code__',None)) for fn in funcs),
        (MappingProxyType,Decimal,Enum,date,datetime,OrderedDict,_OPERATION,_READ,_ISSUED,policy.InitialHeldRecoveryReusePolicy,first_bindings))

class _Initial:
    def __init__(self,proof,policy):
        self.proof,self.policy=proof,policy
        self.policy_content=policy.payload()
        self.codes=_codes();self.cache=OrderedDict();self.bytes=0
        from .backtest_fixed_lot_management_reuse import _policy_snapshot
        self.factory_snapshot=_policy_snapshot(proof.owner.operation.source)
        source=proof.owner.operation.source
        self.installed_image=(type(source.installed_json),source.installed_json)
        self.decoder_image=_decoder_snapshot()
    def require(self):
        if type(self) is not _Initial or self not in _ISSUED:raise ValueError('Unissued initial-held operation')
        if self.codes!=_codes() or self.policy.payload()!=self.policy_content:raise ValueError('Initial-held code or declaration changed')
        self.proof.require()
        source=self.proof.owner.operation.source
        current=_factory_current(self)
        if (current[2] is not self.factory_snapshot[2] or current[3] is not self.factory_snapshot[3]
                or current!=self.factory_snapshot
                or (type(source.installed_json),source.installed_json)!=self.installed_image
                or _decoder_snapshot()!=self.decoder_image):
            raise ValueError('Initial/proposal selected factory or payload changed')

def initial_open_scope(method):
    @wraps(method)
    def opened(owner,*args,**kwargs):
        policy=_selected(owner.operation.source)
        if policy is None:return method(owner,*args,**kwargs)
        from . import backtest_fixed_lot_management_reuse as reuse
        if _OPERATION.get() is not None:raise ValueError('Nested initial-held operation')
        # Original complete authority executes before the new scope exists.
        frontier=reuse._frontier(owner)
        snapshot=reuse._normalized_context_snapshot(owner)
        if len(snapshot[4])+len(snapshot[2])>policy.max_contexts:
            _bypass(owner,'context_budget');return _cold_loader(lambda:method(owner,*args,**kwargs))
        proof=reuse._Decision(owner,frontier);proof.normalized_snapshot=snapshot
        reuse._DECISIONS.add(proof)
        operation=_Initial(proof,policy);_ISSUED.add(operation)
        token=_OPERATION.set(operation);active=reuse._ACTIVE.set(proof)
        try:
            operation.require();result=method(owner,*args,**kwargs);operation.require();return result
        finally:
            reuse._ACTIVE.reset(active);_OPERATION.reset(token)
            _ISSUED.discard(operation);operation.cache.clear();operation.bytes=0
    return opened

def owner_reader(owner,loader):
    operation=_OPERATION.get()
    if operation is None:return loader()
    operation.require()
    if operation.proof.owner is not owner:raise ValueError('Foreign initial-held reader owner')
    token=_READ.set(operation)
    try:
        result=loader();operation.require();return result
    finally:_READ.reset(token)

def context_request(context):
    operation=_READ.get()
    if operation is None:return None
    operation.require()
    proof=operation.proof
    if context.source is not proof.owner.operation.source:return None
    binding=next((item for item in proof.normalized_snapshot[4] if item[0] is context),None)
    return None if binding is None else binding[1]

def inventory_read(client,prefix,kwargs,loader):
    operation=_READ.get()
    if operation is None:return loader()
    operation.require();proof=operation.proof
    if client is not proof.owner.client or prefix is not proof.prefix:
        _bypass(proof.owner,'foreign_reader');return _cold_loader(loader)
    contexts=kwargs.get('fixed_lot_contexts',())
    if type(contexts) is not tuple or tuple(contexts)!=proof.frontier[-2]:
        _bypass(proof.owner,'foreign_contexts');return _cold_loader(loader)
    from .backtest_fixed_lot_management_reuse import _content
    key=(_content({k:v for k,v in kwargs.items() if k!='fixed_lot_contexts'}),tuple(id(c) for c in contexts))
    saved=operation.cache.get(key)
    if saved is not None:
        value,content,size=saved
        if _image(value)!=content:raise ValueError('Private initial-held OMS inventory changed')
        operation.cache.move_to_end(key);result=_copy(value)
        if _image(result)!=content:raise ValueError('Initial-held inventory copy differs')
        operation.require();return result
    from .backtest_fixed_lot_first_inventory_source_reuse import load_first_inventory
    result,_=load_first_inventory(operation,loader,inventory_key=key)
    operation.require();content=_image(result);value=_copy(result)
    if _image(result)!=content or _image(value)!=content:raise ValueError('Initial-held inventory changed during transfer')
    size=_size(value)+_size(content)+_size(key)
    if _nodes(value)<=operation.policy.max_inventory_rows and size<=operation.policy.max_inventory_bytes:
        while operation.cache and (len(operation.cache)>=operation.policy.max_inventory_entries or operation.bytes+size>operation.policy.max_inventory_bytes):
            _,(_,_,removed)=operation.cache.popitem(last=False);operation.bytes-=removed
        operation.cache[key]=(value,content,size);operation.bytes+=size
    else:_bypass(proof.owner,'inventory_budget')
    return result


def _bypass(owner,reason):
    counts=getattr(owner,'_verification_reuse_bypass_counts',None)
    if counts is None:
        counts={};owner._verification_reuse_bypass_counts=counts
    counts[reason]=counts.get(reason,0)+1

def _cold_loader(loader):
    """Original independent verification cannot inherit an owned warm reader."""
    from . import backtest_fixed_lot_management_reuse as reuse
    read=_READ.set(None);active=reuse._ACTIVE.set(None);context=reuse._CONTEXT_OWNER.set(None)
    try:return loader()
    finally:
        reuse._CONTEXT_OWNER.reset(context);reuse._ACTIVE.reset(active);_READ.reset(read)

@contextmanager
def proposal_scope(owner,proof):
    """Optional memo only for the existing positively issued proposal proof."""
    from . import backtest_fixed_lot_management_reuse as reuse
    if proof is None:
        yield;return
    policy=_selected(owner.operation.source,proposal=True)
    if policy is None:
        yield;return
    proof.require()
    if proof.owner is not owner or reuse._ACTIVE.get() is not proof:
        raise ValueError('Proposal inventory owner/proof differs')
    if _OPERATION.get() is not None:raise ValueError('Nested proposal inventory operation')
    snapshot=proof.normalized_snapshot
    if len(snapshot[4])+len(snapshot[2])>policy.max_contexts:
        _bypass(owner,'context_budget');yield;return
    operation=_Initial(proof,policy);_ISSUED.add(operation);token=_OPERATION.set(operation)
    try:
        operation.require();yield;operation.require()
    finally:
        _OPERATION.reset(token);_ISSUED.discard(operation);operation.cache.clear();operation.bytes=0


def verify_context_source(context,loader):
    operation=_READ.get()
    if operation is None:return loader()
    request=context_request(context)
    if request is None:
        _bypass(operation.proof.owner,'foreign_context');return _cold_loader(loader)
    return request


def _method_chain(method):
    chain=[]
    for _ in range(8):
        if method in chain:raise ValueError('Cyclic management wrapper authority')
        chain.append(method)
        next_method=getattr(method,'__wrapped__',None)
        if next_method is None:return tuple(chain)
        method=next_method
    raise ValueError('Unbounded management wrapper authority')


def _factory_current(operation):
    """Only the already fully verified selected registry row, no payload decode."""
    from src.trading_runtime import strategy_registry as registry
    from .backtest_fixed_lot_management_reuse import _policy_value
    old=operation.factory_snapshot;source=operation.proof.owner.operation.source
    release=registry._NUMBERED_RELEASES[old[2].number]
    registration=registry._FIXED_REGISTRY[(old[2].executor_strategy_id,old[2].executor_revision)]
    values=[]
    for owner,name,original in old[6]:
        current=vars(owner)[name] if isinstance(owner,type) else getattr(owner,name)
        if isinstance(current,(staticmethod,classmethod)):current=current.__func__
        if owner is registry and name in ('_NUMBERED_RELEASES','_FIXED_REGISTRY','_REGISTRY'):
            values.append((current,(type(current),id(current))))
        else:values.append((current,_policy_value(current)))
    return (source.installed_json,source.selected_configuration_hash,release,registration,
        _policy_value(release),_policy_value(registration),old[6],tuple(values))

def _decoder_snapshot():
    from . import backtest_fixed_structural_lot_source as source
    from .backtest_fixed_lot_management_reuse import _policy_value
    module=source.json;decoder=module._default_decoder
    cls=module.JSONDecoder
    funcs=(module.loads,cls.__init__,cls.decode,cls.raw_decode)
    return (module,cls,module.decoder.JSONDecoder,decoder,
        tuple((fn,getattr(fn,'__code__',None)) for fn in funcs),
        tuple((key,_policy_value(value)) for key,value in sorted(vars(decoder).items())))
