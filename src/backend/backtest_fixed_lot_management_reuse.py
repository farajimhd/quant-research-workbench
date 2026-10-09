"""Issued entry snapshots and decision-local exact-frontier reads, not cold authority."""
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import RLock, get_ident
from weakref import WeakKeyDictionary, WeakSet, ref

from src.trading_runtime.fixed_lot_management_reuse_policy import installed_management_reuse_policy as _complete_management_policy

_ENTRIES = WeakKeyDictionary()
_LOCK = RLock()
_DECISIONS = WeakSet()
_OWNERS = WeakKeyDictionary()
_CONSTRUCTING = ContextVar('fixed_lot_management_constructor', default=None)
_ACTIVE = ContextVar('fixed_lot_management_read', default=None)
_CONTEXT_OWNER = ContextVar('fixed_lot_management_context_owner', default=None)
_PROPOSALS = WeakKeyDictionary()


def _snapshot_tree(value):
    """Byte-equivalent typed tree with exact primitive dispatch first."""
    if value is None or type(value) in (int,float,str,bool):
        return [type(value).__name__,value]
    from dataclasses import fields,is_dataclass
    from collections.abc import Mapping
    from datetime import date,datetime
    from decimal import Decimal
    from enum import Enum
    if is_dataclass(value):
        return [type(value).__name__,[[f.name,_snapshot_tree(getattr(value,f.name))] for f in fields(value)]]
    if isinstance(value,Mapping):
        return ['mapping',[[k,_snapshot_tree(v)] for k,v in sorted(value.items())]]
    if type(value) in (tuple,frozenset,list):
        return [type(value).__name__,[_snapshot_tree(v) for v in (sorted(value) if type(value) is frozenset else value)]]
    if type(value) in (date,datetime):
        return [type(value).__name__,value.isoformat()]
    if type(value) is Decimal:
        return ['Decimal',str(value)]
    if isinstance(value,Enum):
        return [type(value).__name__,_snapshot_tree(value.value)]
    raise ValueError('Unsupported selected management decision scalar: '+type(value).__name__)


def _content(value):
    from src.trading_runtime.journal_contract import canonical_json
    return canonical_json(_snapshot_tree(value))


def _entry_content(request):
    return _content((request.run_id, request.strategy_id, request.revision,
                     request.entry, request.original, request.intent))


def _entry_dependencies(request):
    from . import backtest_fixed_structural_lot_source as source
    from . import backtest_strategy_certified_price_break as price
    from . import backtest_strategy_episode_activity_source as episode
    from src.trading_runtime import fixed_structural_lot_entry as entry
    functions = (source.FixedStructuralLotRequest._verify_complete,
        source.PreparedFixedStructuralLotSource.request,
        source.PreparedFixedStructuralLotSource._request_complete,
        source.PreparedFixedStructuralLotSource.require_prepared_source,
        source.PreparedFixedStructuralLotSource.require_installed_admission, source.certified_episode_entry_intent,
        source._select_fixed_structural_lot_entry, source._intent_from_verified_entry,
        source._validate_entry_input, source.canonical_price_int,
        price.certified_price_entry_intent, price.bind_certified_price_break_proposal,
        price._source_parent_number, episode.certified_episode_activity_witness, entry._same_typed)
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.entry_momentum_growth import declared_momentum_policy
    from src.trading_runtime.numbered_fixed_strategy import declared_fixed_rule, numbered_fixed_strategy
    functions += (strategy_one_entry_intent,declared_momentum_policy,declared_fixed_rule,numbered_fixed_strategy)
    authority = request.source.price_authority
    plan = authority.plan
    objects = (request.source.intervals,authority,plan,plan.source,plan.source.parent,
               plan.momentum,plan.entry,authority.entry_activity_source,authority.entry_activity_source.gate,
               authority.entry_activity_source.gate.activity, authority.entry_spread_risk_source)
    attributes = tuple(value for obj in objects if obj is not None for value in type(obj).__dict__.values())
    methods = tuple(fn for value in attributes
        for fn in ((value.fget, value.fset, value.fdel) if type(value) is property else
                   (value.__func__,) if type(value) in (staticmethod, classmethod) else (value,))
        if callable(fn) and hasattr(fn, '__code__'))
    # Pin function *and* code identity: replacing __code__ in-place is a mutation.
    return (type(request),type(request.source),request.source.quotes,
            *objects,*(item for fn in functions+methods for item in (fn,fn.__code__)))


def _entry_source_facts(request):
    """Bounded actual per-entry source witnesses; never hash the whole tape.

    These are the same indexed facts consumed by the original complete native
    binder, including activity admission and all causal target levels. A change
    to a backing mutable array/dict is detected even when object identity stays.
    """
    source = request.source
    proposal = request.entry.proposal
    key = proposal.ticker,proposal.boundary_ms
    authority = source.price_authority
    plan = authority.plan
    from bisect import bisect_left
    index = bisect_left(source.intervals._tickers,proposal.ticker)
    if index >= len(source.intervals._tickers) or source.intervals._tickers[index] != proposal.ticker:
        raise ValueError('Verified entry ticker left its source inventory')
    cost = authority.entry_spread_risk_source
    if cost is not None:
        # This check remains independent and is not memoized as admission.
        cost.check_proposal(proposal)
    candidate_index = plan._index(*key)
    return _content((bool(plan.source.parent.eligible_mask[candidate_index]),
        bool(plan.eligible_mask[candidate_index]),
        plan._activations.get((proposal.ticker,proposal.episode_start_ms)),
        source._quotes.get(key), plan.source.market.sessions,
        plan.source.token, plan.token, source.intervals.token,
        source.intervals.coverage[index],
        source.intervals.levels(proposal.ticker,boundary_ms=proposal.boundary_ms),
        plan.momentum.lookup(*key), plan.source.parent.selection_witness(*key),
        plan.price_witness(*key),plan.entry.lookup(*key),
        authority.entry_activity_source.gate.admission_witness(*key)))


def verify_entry(request):
    """Only a successful actual complete verifier can issue a retained snapshot."""
    proof = _ACTIVE.get()
    if proof is None or proof.owner.operation.source is not request.source:
        # Cold and ordinary source consumers never acquire warm admission.
        request._verify_complete()
        return
    proof.require()
    _verify_management_entry(request)


def verify_management_entry(owner,request):
    from .backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    if (type(owner) is not NativeFixedStructuralLotManagement
            or owner.operation.source is not request.source
            or owner.publisher.writer._client is not owner.client
            or owner.publisher._fixed_lot_source is not request.source):
        raise ValueError('Entry reuse lacks exact management operation ownership')
    if installed_management_reuse_policy(request.source) is not None:
        if owner not in _OWNERS:
            raise ValueError('Entry reuse lacks constructed management owner')
        binding = _OWNERS[owner]()
        if (binding is None or getattr(owner, '_management_owner_binding', None) is not binding
                or owner.operation is not binding.operation or owner.publisher is not binding.publisher
                or owner.client is not binding.client):
            raise ValueError('Entry management constructor ownership changed')
    _verify_management_entry(request)


def _verify_management_entry(request):
    policy = installed_management_reuse_policy(request.source)
    if policy is None:
        request._verify_complete()
        return
    request.source.require_installed_admission()
    before = _entry_content(request)
    source_facts = _entry_source_facts(request)
    dependencies = _entry_dependencies(request)
    with _LOCK:
        retained = _ENTRIES.get(request.source)
        saved = None if retained is None else retained.get(id(request))
        if saved is not None and saved[0]() is None:
            retained.pop(id(request),None)
            saved = None
        if saved is not None:
            if saved[0]() is not request or saved[1] != before or saved[3] != source_facts or any(a is not b for a,b in zip(saved[2],dependencies,strict=True)):
                raise ValueError('Issued entry snapshot or dependency changed')
            retained.move_to_end(id(request))
            return
        request._verify_complete()
        if before != _entry_content(request) or source_facts != _entry_source_facts(request) or any(a is not b for a,b in zip(dependencies,_entry_dependencies(request),strict=True)):
            raise ValueError('Entry changed during complete verification')
        if retained is None:
            retained = OrderedDict()
            _ENTRIES[request.source] = retained
        source_ref = ref(request.source)
        identity = id(request)
        def discard(dead):
            with _LOCK:
                source = source_ref()
                values = None if source is None else _ENTRIES.get(source)
                if values is not None and identity in values and values[identity][0] is dead:
                    values.pop(identity,None)
        retained[identity] = (ref(request,discard), before, dependencies, source_facts)
        while len(retained) > policy.max_entries:
            retained.popitem(last=False)


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class _OwnerBinding:
    operation: object
    publisher: object
    client: object
    policy: object
    policy_snapshot: tuple


def _factory_dependencies(factory):
    """Issuer-only traversal of explicit selected factory references/imports."""
    import ast, inspect, textwrap, importlib
    from types import FunctionType, ModuleType
    pending = [factory]
    visited = set()
    bindings = []
    keys = set()
    def bind(owner, name, value):
        key = (id(owner),name)
        if key not in keys:
            keys.add(key)
            bindings.append((owner,name,value))
        if (isinstance(value,FunctionType) and value.__module__.startswith('src.trading_runtime.')
                and value.__module__ != 'src.trading_runtime.strategy_registry'):
            pending.append(value)
        elif isinstance(value,type) and value.__module__.startswith('src.trading_runtime.'):
            for attr,method in vars(value).items():
                if isinstance(method,(staticmethod,classmethod)):
                    method=method.__func__
                if isinstance(method,FunctionType):
                    bind(value,attr,method)
    while pending:
        function = pending.pop()
        if function in visited:
            continue
        visited.add(function)
        if len(visited)>512 or len(bindings)>4096:
            raise ValueError('Selected factory dependency graph is unbounded')
        module=importlib.import_module(function.__module__)
        for name in function.__code__.co_names:
            if name in function.__globals__:
                value=function.__globals__[name]
                if not isinstance(value,ModuleType):
                    if vars(module).get(name) is value:
                        bind(module,name,value)
        try:
            tree=ast.parse(textwrap.dedent(inspect.getsource(function)))
        except (OSError,TypeError):
            if inspect.unwrap(function).__code__.co_filename != "<string>":
                raise ValueError("Selected factory dependency source unavailable: "+function.__qualname__+":"+function.__code__.co_filename)
            continue  # Generated dataclass methods retain exact function/code pins.
        aliases={}
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                for alias in node.names:
                    target=importlib.import_module(alias.name)
                    aliases[alias.asname or alias.name.split('.')[0]]=target
            if isinstance(node,ast.ImportFrom):
                target=importlib.import_module('.'*node.level+(node.module or ''),function.__module__.rpartition('.')[0])
                for alias in node.names:
                    if alias.name=='*':
                        raise ValueError('Selected factory cannot use open dependency imports')
                    bind(target,alias.name,getattr(target,alias.name))
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id in aliases:
                target=aliases[node.value.id]
                bind(target,node.attr,getattr(target,node.attr))
    return tuple(bindings)


def _policy_value(value):
    from dataclasses import fields,is_dataclass
    if callable(value):
        return (type(value),value,getattr(value,'__code__',None))
    if is_dataclass(value):
        return (type(value),tuple((f.name,_policy_value(getattr(value,f.name))) for f in fields(value)))
    if type(value) in (tuple,list):
        return (type(value),tuple(_policy_value(v) for v in value))
    if type(value) is dict:
        return (dict,tuple((k,_policy_value(v)) for k,v in sorted(value.items())))
    if value is None or type(value) in (str,int,float,bool):
        return (type(value),value)
    # Never traverse arbitrary market/source objects through a factory guard.
    return (type(value),value)


def _policy_snapshot(source, dependencies=None):
    """Exact already-issued registry fields, no global catalog initialization."""
    from src.trading_runtime import strategy_registry as registry
    strategy=source.installed_payload['strategy']
    release=registry._NUMBERED_RELEASES[strategy['strategy_number']]
    registration=registry._FIXED_REGISTRY[(release.executor_strategy_id,release.executor_revision)]
    if dependencies is None:
        dependencies=_factory_dependencies(registration.contract_factory)
        dependencies += ((registry,'numbered_strategy',registry.numbered_strategy),
            (registry,'fixed_strategy_executor',registry.fixed_strategy_executor))
    values=[]
    for owner,name,original in dependencies:
        current=vars(owner)[name] if isinstance(owner,type) else getattr(owner,name)
        if isinstance(current,(staticmethod,classmethod)):
            current=current.__func__
        if owner is registry and name in ('_NUMBERED_RELEASES','_FIXED_REGISTRY','_REGISTRY'):
            # Other legitimate registrations cannot invalidate an issued selected row.
            values.append((current,(type(current),id(current))))
        else:
            values.append((current,_policy_value(current)))
    return (source.installed_json,source.selected_configuration_hash,release,registration,
        _policy_value(release),_policy_value(registration),dependencies,tuple(values))


def installed_management_reuse_policy(source):
    active = _ACTIVE.get()
    context = _CONTEXT_OWNER.get()
    owner = active.owner if active is not None else context[0] if context is not None else None
    if owner is None or owner.operation.source is not source:
        return _complete_management_policy(source)
    binding = _OWNERS.get(owner)
    binding = None if binding is None else binding()
    if binding is None:
        return _complete_management_policy(source)
    source.require_installed_admission()
    old = binding.policy_snapshot
    current = _policy_snapshot(source, old[6])
    if (current[:2] != old[:2] or current[2] is not old[2] or current[3] is not old[3]
            or current[4:6] != old[4:6]
            or any(a is not b or av != bv for (a,av),(b,bv) in zip(current[7],old[7],strict=True))):
        raise ValueError('Management installed policy/factory authority changed')
    binding.policy.__post_init__()
    return binding.policy


def management_owner_constructor(method):
    @wraps(method)
    def construct(owner, *args, **kwargs):
        token = _CONSTRUCTING.set(owner)
        try:
            return method(owner, *args, **kwargs)
        finally:
            _CONSTRUCTING.reset(token)
    return construct


def issue_management_owner(owner):
    from .backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    from .backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    if (type(owner) is not NativeFixedStructuralLotManagement or _CONSTRUCTING.get() is not owner
            or type(owner.operation) is not NativeFixedStructuralLotOperation
            or type(owner.publisher) is not BacktestTypedJournalPublisher
            or owner.publisher.writer._client is not owner.client
            or owner.publisher._fixed_lot_source is not owner.operation.source):
        raise ValueError('Exact constructed management owner required')
    policy = _complete_management_policy(owner.operation.source)
    if policy is not None:
        binding = _OwnerBinding(owner.operation, owner.publisher, owner.client, policy,
            _policy_snapshot(owner.operation.source))
        owner._management_owner_binding = binding
        _OWNERS[owner] = ref(binding)


def _context_frontier(owner):
    """Original exclusive-writer guards before context reads, without recursion."""
    from .backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    from .backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.fixed_structural_lot_profile import require_fixed_structural_lot_profile
    from uuid import UUID
    if (type(owner) is not NativeFixedStructuralLotManagement or owner not in _OWNERS
            or owner.publisher.writer._client is not owner.client
            or owner.publisher._fixed_lot_source is not owner.operation.source):
        raise ValueError('Management context lacks issued owner/source')
    binding = _OWNERS[owner]()
    if (binding is None or getattr(owner, '_management_owner_binding', None) is not binding
            or owner.operation is not binding.operation or owner.publisher is not binding.publisher
            or owner.client is not binding.client):
        raise ValueError('Management constructor ownership changed')
    source = owner.operation.source
    source.require_installed_admission()
    profile = require_fixed_structural_lot_profile(owner.client.fixed_structural_lot_profile)
    lease = owner.client.backtest_v4_lease
    dispatch = owner.client.typed_insert_dispatch
    if (profile.operation is not owner.operation or not isinstance(lease, BacktestV4KeeperLease)
            or lease.run_id != source.run_id or lease.epoch < 1
            or owner.client.typed_insert_strict is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or dispatch.keeper is not lease.owner._session.client):
        raise ValueError('Management context lacks exact live lease/profile')
    lease.assert_current()
    gate, _ = dispatch._read_gate(source.run_id)
    journal = owner.publisher.journal
    if (gate.mode != 'open' or gate.inflight or gate.registered
            or gate.active_batch_id != str(UUID(int=0)) or gate.compacted_through < 1
            or journal.latest_sequence(source.run_id) != gate.compacted_through
            or journal._fenced_sequence != gate.compacted_through):
        raise ValueError('Management context lacks exact committed frontier')
    return (source, profile, lease, lease.epoch, dispatch, _content(gate),
            owner.publisher, journal, get_ident(), _current_task())


def _current_task():
    import asyncio
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def request_from_management_source(source, proposal, loader):
    """Only owner context-authority calls reuse a fully rebound proposal."""
    active = _CONTEXT_OWNER.get()
    if active is None or active[0].operation.source is not source:
        return loader()
    owner, frontier = active
    if _context_frontier(owner) != frontier:
        raise ValueError('Management proposal context changed before source replay')
    policy = installed_management_reuse_policy(source)
    if policy is None:
        return loader()
    from .backtest_fixed_structural_lot_source import FixedStructuralLotRequest, _validate_entry_input
    source.require_prepared_source()
    _validate_entry_input(proposal, session_date=source.session_date, policy=source.policy,
                          intervals=source.intervals, tick=source.tick)
    key = _content(proposal)
    from dataclasses import replace
    values = _PROPOSALS.get(owner)
    saved = None if values is None else values.get(key)
    if saved is not None:
        entry, original, intent, content, facts, dependencies = saved
        # Preserve the actual caller proposal object, not an older identity.
        request = FixedStructuralLotRequest(source.run_id, source._strategy_id, source._revision,
            source, replace(entry, proposal=proposal), original, intent)
        if (content != _entry_content(request) or facts != _entry_source_facts(request)
                or any(a is not b for a,b in zip(dependencies,_entry_dependencies(request),strict=True))):
            raise ValueError('Management proposal snapshot facts or dependencies changed')
        values.move_to_end(key)
        return request
    request = loader()  # The actual complete positive native binder is mandatory.
    content, facts, dependencies = _entry_content(request), _entry_source_facts(request), _entry_dependencies(request)
    if _context_frontier(owner) != frontier:
        raise ValueError('Management context changed during complete source replay')
    if values is None:
        values = OrderedDict()
        _PROPOSALS[owner] = values
    values[key] = (request.entry, request.original, request.intent, content, facts, dependencies)
    while len(values) > policy.max_entries:
        values.popitem(last=False)
    return request


def _frontier(owner):
    from src.trading_runtime.fixed_structural_lot_warm_proof import _authority
    source = owner.operation.source
    source.require_installed_admission()
    # Context projection invokes request.verify: independent authority must not
    # recursively admit itself through the decision proof being checked.
    suspended = _ACTIVE.set(None)
    try:
        token = _CONTEXT_OWNER.set((owner, _context_frontier(owner)))
        try:
            actual = _authority(owner.client, source.run_id, 100_000, source.price_authority)
        finally:
            _CONTEXT_OWNER.reset(token)
    finally:
        _ACTIVE.reset(suspended)
    _, contexts, recoveries, lease, gate, scope = actual
    journal = owner.publisher.journal
    if (journal.latest_sequence(source.run_id) != gate.compacted_through
            or journal._fenced_sequence != gate.compacted_through):
        raise ValueError('Management reuse needs an exact fenced journal frontier')
    import asyncio
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (owner, owner.operation, owner.client, owner.publisher, journal, get_ident(), task,
            scope, _content(gate), tuple(contexts), tuple(recoveries))



def _normalized_context_snapshot(owner, bound_entries=None):
    """Only normalized entry/recovery content, never market arrays or tape."""
    from src.trading_runtime import fixed_structural_lot_entry_v4 as entry
    from src.trading_runtime import fixed_structural_lot_cold_recovery as cold
    from src.trading_runtime import fixed_structural_lot_warm_proof as warm
    from . import backtest_fixed_structural_lot_management as management
    contexts = tuple(owner.client.fixed_structural_lot_contexts)
    recoveries = tuple(getattr(owner.client, 'fixed_lot_recovery_contexts', ()))
    bound = []
    entries = []
    for context in contexts:
        request = None
        if bound_entries is not None:
            request = next((request for original,request in bound_entries if original is context), None)
            if request is None:
                raise ValueError('Management normalized context inventory changed')
        else:
            from .backtest_fixed_structural_lot_source import FixedStructuralLotRequest
            for saved in _PROPOSALS.get(owner, {}).values():
                selected_entry,original,intent = saved[:3]
                if intent.intent_id == context.record.entity_id and context.source is owner.operation.source:
                    request = FixedStructuralLotRequest(context.source.run_id,context.source._strategy_id,
                        context.source._revision,context.source,selected_entry,original,intent)
                    break
            if request is None:
                request = context.verify_source()  # Complete first authority, never warm admission.
        bound.append((context,request))
        entries.append((context, context.source, context.unit, context.record,
            type(context), type(context.unit), type(context.unit.packet), type(context.record),
            _content((context.unit, context.record)), _entry_content(request),
            _entry_source_facts(request), _entry_dependencies(request)))
    entries = tuple(entries)
    recovery = []
    for item in recoveries:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError('Management recovery batch binding changed')
        batch_id, context = item
        if type(context) is cold.FixedStructuralLotColdRecoveryContext:
            binding = cold._CONTEXTS.get(context)
            if (binding is None or binding[:4] != (context.source, context.batch_id,
                    context.before_sequence, context.graph_json) or binding[4] is not True
                    or context.batch_id != batch_id or context.source is not owner.operation.source):
                raise ValueError('Management cold recovery issuance changed')
            recovery.append((batch_id, context, context.source, type(context),
                _content((context.batch_id, context.before_sequence, context.graph_json, binding[4]))))
        elif type(context) is management.FixedStructuralLotRecoveryContext:
            stored = context.owner._recoveries.get(context)
            batches = context.owner._recovery_batches.get(context, {})
            content = management._recovery_content(context)
            request = context.request
            if (stored is None or stored[0] is not request
                    or stored[1] is not request.entry_request or stored[2] != content
                    or batch_id not in batches or request.entry_request.source is not owner.operation.source):
                raise ValueError('Management live recovery issuance changed')
            recovery.append((batch_id, context, context.owner, request, request.entry_request,
                type(context), content, _content(batches), _entry_content(request.entry_request),
                _entry_source_facts(request.entry_request), _entry_dependencies(request.entry_request)))
        else:
            raise ValueError('Management recovery context type changed')
    functions = [_snapshot_tree, _content, _entry_content, _entry_source_facts,
        _entry_dependencies, _context_frontier, _policy_snapshot, _factory_dependencies,
        installed_management_reuse_policy, _same_normalized_snapshot, _Decision.require,
        warm._authority, entry.fixed_lot_contexts_by_batch,
        entry.restore_fixed_structural_lot_entry, entry.project_fixed_structural_lot_entry,
        management.recovery_contexts_by_batch, management._recovery_content,
        management.NativeFixedStructuralLotManagement.require_recovery,
        cold.require_cold_recovery_context]
    classes = (entry.FixedStructuralLotPublicationContext, entry.V4FixedStructuralLotEntryBatch,
        cold.FixedStructuralLotColdRecoveryContext,
        *(type(context.unit.packet) for context in contexts))
    for cls in classes:
        functions.extend(value for value in cls.__dict__.values()
            if callable(value) and hasattr(value, '__code__'))
    return (_context_frontier(owner), entries, tuple(recovery),
        tuple((fn, fn.__code__) for fn in functions), tuple(bound))


def _same_normalized_snapshot(before, after):
    if before[0] != after[0] or len(before[1]) != len(after[1]) or len(before[2]) != len(after[2]):
        return False
    for old, new in zip(before[1], after[1], strict=True):
        if (any(a is not b for a,b in zip(old[:8], new[:8], strict=True)) or old[8:11] != new[8:11]
                or any(a is not b for a,b in zip(old[11],new[11],strict=True))):
            return False
    for old, new in zip(before[2], after[2], strict=True):
        if len(old) != len(new) or old[0] != new[0]:
            return False
        if len(old)==5:
            if any(a is not b for a,b in zip(old[1:4],new[1:4],strict=True)) or old[4]!=new[4]:
                return False
        elif len(old)==11:
            if (any(a is not b for a,b in zip(old[1:6],new[1:6],strict=True)) or old[6:10]!=new[6:10]
                    or any(a is not b for a,b in zip(old[10],new[10],strict=True))):
                return False
        else:
            return False
    return len(before[3]) == len(after[3]) and all(
        a is c and b is d for (a,b),(c,d) in zip(before[3], after[3], strict=True))


def _prefix_binding(prefix):
    """The verified prefix owns an immutable tuple of immutable batch IDs.

    Detect scalar replacement and tuple replacement in O(1); never recopy an
    ever-growing committed chain on a held decision. Full load certifies it.
    """
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    if (type(prefix) is not V4CommittedPrefix or type(prefix.batch_ids) is not tuple
            or type(prefix.last_sequence) is not int
            or any(type(getattr(prefix,k)) is not str for k in ('run_id','last_batch_id','source_cursor','status'))):
        raise ValueError('Management reuse prefix has changed scalar types')
    return (prefix.run_id,prefix.last_sequence,prefix.last_batch_id,prefix.source_cursor,
            prefix.status,prefix.batch_ids)


@dataclass(eq=False, slots=True, weakref_slot=True)
class _Decision:
    owner: object
    frontier: tuple
    prefix: object = None
    prefix_content: tuple | None = None
    groups: object = None
    rosters: object = None
    normalized_snapshot: object = None

    def __post_init__(self):
        self.groups = {}
        self.rosters = {}

    def require(self):
        if type(self) is not _Decision or self not in _DECISIONS:
            raise ValueError('Management read proof was not issued by proposal admission')
        current = _normalized_context_snapshot(self.owner, self.normalized_snapshot[4])
        if not _same_normalized_snapshot(self.normalized_snapshot, current):
            raise ValueError('Management read ownership, lease, head or context changed')
        if self.prefix is not None and (self.prefix_content[:-1] != _prefix_binding(self.prefix)[:-1]
                or self.prefix_content[-1] is not self.prefix.batch_ids):
            raise ValueError('Management reused prefix changed')


def _active(owner):
    proof = _ACTIVE.get()
    if proof is not None and proof.owner is owner:
        proof.require()
        return proof
    return None


def _owner_read_context(owner, loader):
    """Actual owner reader only; normalized authority still executes completely."""
    proof = _active(owner)
    if proof is None:
        return loader()
    frontier = _context_frontier(owner)
    token = _CONTEXT_OWNER.set((owner,frontier))
    # Original authority can replay historical predecessors. Its nested reads
    # must execute their complete verifiers, rather than inherit the current
    # decision's prefix-bound cache or recursively reuse its authority.
    suspended = _ACTIVE.set(None)
    try:
        result = loader()
        if _context_frontier(owner) != frontier:
            raise ValueError('Management reader frontier changed during original authority')
        proof.require()
        return result
    finally:
        _ACTIVE.reset(suspended)
        _CONTEXT_OWNER.reset(token)


def prefix_read(owner, loader):
    proof = _active(owner)
    if proof is None:
        return loader()
    if proof.prefix is None:
        prefix, contexts = _owner_read_context(owner, loader)
        proof.require()
        proof.prefix = prefix
        proof.prefix_content = _prefix_binding(prefix)
        return prefix, contexts
    return proof.prefix, proof.frontier[-2]


def group_read(owner, request, prefix, contexts, group_id, loader):
    proof = _active(owner)
    if proof is None:
        return loader()
    if prefix is not proof.prefix or tuple(contexts) != proof.frontier[-2]:
        raise ValueError('Management group crosses its exact prefix/context')
    key = (id(request), group_id)
    saved = proof.groups.get(key)
    if saved is None:
        value = _owner_read_context(owner, loader)
        proof.require()
        proof.groups[key] = (value, _content(value))
        return value
    if saved[1] != _content(saved[0]):
        raise ValueError('Management reused OMS residual/order content changed')
    return saved[0]


def roster_read(client, prefix, entry_request, arguments, loader):
    proof = _ACTIVE.get()
    if proof is None or entry_request is None:
        return loader()
    proof.require()
    if client is not proof.owner.client or prefix is not proof.prefix or entry_request.source is not proof.owner.operation.source:
        raise ValueError('Management roster crosses issued ownership')
    entry_request.verify()
    key = (id(entry_request), _content(arguments))
    saved = proof.rosters.get(key)
    if saved is None:
        value = _owner_read_context(proof.owner, loader)
        proof.require()
        proof.rosters[key] = (value, _content(value))
        return value
    if saved[1] != _content(saved[0]):
        raise ValueError('Management reused lot residual content changed')
    return saved[0]


def management_read_scope(method):
    """Owner-internal proposal token; commands discard it before post-submit reads."""
    import inspect
    async_method = inspect.iscoroutinefunction(method)

    def enter(owner, args):
        policy = installed_management_reuse_policy(owner.operation.source)
        if policy is None:
            return None, None
        if method.__name__ == 'propose':
            proof = _Decision(owner, _frontier(owner))
            _DECISIONS.add(proof)
            proof.normalized_snapshot = _normalized_context_snapshot(owner)
        else:
            request = args[0]
            proof = owner._management_reads.get(request)
            if proof is None:
                return None, None
            if method.__name__ == 'confirm' and request.intents:
                owner._management_reads.pop(request, None)
                return None, None
            proof.require()
        return proof, _ACTIVE.set(proof)

    def finish(owner, args, proof, result):
        if proof is None:
            return
        if method.__name__ == 'propose':
            proof.require()
            owner._management_reads[result] = proof
        elif method.__name__ == 'confirm' or (method.__name__ == 'verify_request' and args[0].intents):
            owner._management_reads.pop(args[0], None)

    def abort(owner,args):
        if method.__name__ != 'propose' and args:
            owner._management_reads.pop(args[0],None)

    if async_method:
        @wraps(method)
        async def wrapped(owner, *args, **kwargs):
            token = None
            try:
                proof, token = enter(owner,args)
                result = await method(owner,*args,**kwargs)
                finish(owner,args,proof,result)
                return result
            except BaseException:
                abort(owner,args)
                raise
            finally:
                if token is not None:
                    _ACTIVE.reset(token)
        return wrapped
    @wraps(method)
    def wrapped(owner,*args,**kwargs):
        token = None
        try:
            proof, token = enter(owner,args)
            result = method(owner,*args,**kwargs)
            finish(owner,args,proof,result)
            return result
        except BaseException:
            abort(owner,args)
            raise
        finally:
            if token is not None:
                _ACTIVE.reset(token)
    return wrapped
