"""Native selected manager state/source binding and deferred checkpoint requests.

The factory rechecks persisted market/seed/interval authority once. It creates
no installed release or writer capability. Requests remain pending until the
native service verifies actual manager/broker/financial checkpoint heads.
"""
from dataclasses import dataclass, fields, replace, asdict
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from weakref import WeakKeyDictionary
from bisect import bisect_right
import polars as pl

from .backtest_market_data import CertifiedMarketDayPlan, verify_market_day_plan, market_day_boundary
from .backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, certify_v7_interval_plan
from .structural_v7_seed import CertifiedSeedPlan, certified_seed_plan
from .backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
from .backtest_strategy_episode_activity_source import certified_episode_entry_intent
from .backtest_profit_armed_structural_rejection_source import load_completed_structural_rejection_lookup
from src.trading_runtime.profit_armed_structural_rejection_native_policy import native_structural_rejection_declaration
from src.trading_runtime.profit_armed_structural_rejection import (
    StructuralRejectionState, StructuralRejectionInput, StructuralRejectionWitness,
    FrozenResistance, CurrentQuote, reduce_structural_rejection)
from src.trading_runtime.profit_armed_structural_rejection_checkpoint import _tree
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView, StrategyOneEntryProposal

_OWNERS = WeakKeyDictionary()
_REQUESTS = WeakKeyDictionary()
_CAPTURES = WeakKeyDictionary()
_SOURCES = WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class PreparedStructuralRejectionSource:
    """Read-only certified source prepared before any financial actors/writer."""
    contract: object
    strategy_id: object
    strategy_revision: int
    run_id: str
    session_date: object
    market: CertifiedMarketDayPlan
    seeds: CertifiedSeedPlan
    intervals: CertifiedV7IntervalPlan
    price_authority: CertifiedPriceReadbackAuthority
    declaration: object
    lookup: object


def require_prepared_structural_rejection_source(source):
    if type(source) is not PreparedStructuralRejectionSource or source not in _SOURCES:
        raise ValueError('Unissued prepared structural rejection source')
    image,lookup_binding=_SOURCES[source]
    if (any(getattr(source,name) is not value for name,value in image)
            or not _same_lookup(source.lookup,lookup_binding)
            or source.price_authority.run_id!=source.run_id
            or native_structural_rejection_declaration(source.contract)!=source.declaration):
        raise ValueError('Prepared structural rejection source binding changed')
    return source


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key:_freeze(item) for key,item in value.items()})
    if isinstance(value, (list,tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, MappingProxyType):
        return {key:_plain(item) for key,item in value.items()}
    if isinstance(value,tuple):
        return tuple(_plain(item) for item in value)
    return value


def _lookup_binding(lookup):
    # Polars replaces its underlying frame on ordinary mutation. Capture both
    # maps and native frame identities without rescanning market rows per tick.
    return (lookup.sources, lookup.policy, lookup.through_boundary_ms,
            lookup._columns, lookup._clocks,
            tuple((key,frame,frame._df) for key,frame in lookup._columns.items()))


def _same_lookup(lookup,binding):
    sources,policy,through,columns,clocks,frames=binding
    return (lookup.sources is sources and lookup.policy is policy
            and lookup.through_boundary_ms==through and lookup._columns is columns
            and lookup._clocks is clocks
            and all(lookup._columns.get(key) is frame and frame._df is native
                    for key,frame,native in frames))


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _price(value):
    if type(value) not in (int,float) or not isfinite(value) or value <= 0:
        raise ValueError('Native canonical price required')
    scaled = Decimal(str(value))*10000
    if scaled != scaled.to_integral_value():
        raise ValueError('Native price is not on canonical 1/10000 lattice')
    return int(scaled)


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class StructuralRejectionCheckpointRequest:
    witness: StructuralRejectionWitness
    financial: StrategyOneFinancialView
    source_entry_intent_id: str
    geometry: object


def require_structural_rejection_request(request, *, owner=None):
    if type(request) is not StructuralRejectionCheckpointRequest or request not in _REQUESTS:
        raise ValueError('Unissued structural rejection request')
    issued_owner,key,source,state = _REQUESTS[request]
    require_native_structural_rejection_owner(issued_owner)
    if (owner is not None and owner is not issued_owner or issued_owner._pending.get(key) is not request
            or issued_owner._states.get(key) is not state
            or source != request.source_entry_intent_id or type(request.witness) is not StructuralRejectionWitness
            or type(request.financial) is not StrategyOneFinancialView
            or request.geometry is not issued_owner._geometries.get(key)
            or _digest(_plain(request.geometry))!=request.witness.resistance.geometry_hash):
        raise ValueError('Foreign/mutated structural rejection request')
    issued_owner._verify_entry(key,request.financial)
    return request


def require_native_structural_rejection_owner(owner):
    if type(owner) is not NativeStructuralRejectionManager or owner not in _OWNERS:
        raise ValueError('Unissued native structural rejection owner')
    manager,market,seeds,intervals,price,declaration,run,revision,lookup,lookup_binding,origin,caches,frames,source = _OWNERS[owner]
    require_prepared_structural_rejection_source(source)
    if (owner.manager is not manager or owner.market is not market or owner.seeds is not seeds
            or owner.intervals is not intervals or owner.price_authority is not price
            or owner.declaration != declaration or manager.runtime.run_id != run
            or manager.runtime.config.strategy_revision != revision
            or manager.runtime.config.strategy_id!=source.strategy_id
            or manager.runtime.config.anchor_date!=source.session_date
            or owner.lookup is not lookup or not _same_lookup(lookup,lookup_binding)
            or owner.origin_us!=origin
            or any(actual is not expected for actual,expected in zip(
                (owner._seed_units,owner._intervals,owner._coverage,owner._geometry_index,owner._valid_seconds),caches))
            or owner._index_frames is not frames
            or any(frame._df is not native for frame,native in frames)
            or native_structural_rejection_declaration(manager.contract) != declaration):
        raise ValueError('Native rejection source/configuration binding changed')
    return owner


def require_structural_rejection_capture(state, *, financial=None):
    from .backtest_strategy_one_management import StructuralRejectionManagementState
    if type(state) is not StructuralRejectionManagementState or state not in _CAPTURES:
        raise ValueError('Unissued structural rejection manager capture')
    owner,content,_=_CAPTURES[state]
    require_native_structural_rejection_owner(owner)
    if any(getattr(state,name) is not value for name,value in content):
        raise ValueError('Structural rejection capture changed after issuance')
    if financial is not None:
        if type(financial) is not StrategyOneFinancialView:
            raise ValueError('Selected manager capture lacks typed financial state')
        key=financial.account_id,financial.assignment_id,financial.ticker
        if key not in dict(state.submitted) or key not in dict(state.positions):
            raise ValueError('Selected manager capture lacks the actual financial position')
    return owner


def structural_rejection_capture_evidence(state):
    require_structural_rejection_capture(state)
    return _CAPTURES[state][2]


class NativeStructuralRejectionManager:
    def __init__(self,manager,market,seeds,intervals,price_authority,declaration,lookup):
        self.manager,self.market,self.seeds,self.intervals=manager,market,seeds,intervals
        self.price_authority,self.declaration,self.lookup=price_authority,declaration,lookup
        self._states,self._pending,self._entry,self._geometries={},{},{},{}
        self._firing={}
        self._entered={}
        self._seed_units=MappingProxyType({str(u['ticker']):_freeze(dict(u)) for u in seeds.units})
        self._intervals=MappingProxyType(dict(intervals.intervals))
        self._coverage=MappingProxyType({u.ticker:u for u in intervals.coverage})
        self._valid_seconds=MappingProxyType(dict(intervals.valid_seconds))
        self._geometry_index=MappingProxyType({ticker:pl.DataFrame([
            dict(index=index,level_id=row.level_id,role=row.role,
                 valid_from=row.valid_from_ms,valid_to=row.valid_to_ms,
                 lower_int=lower,upper_int=upper,price_int=midpoint)
            for index,row in enumerate(rows)
            for lower,upper,midpoint in (declaration.geometry_ints(row.lower,row.upper),)],schema={
                'index':pl.UInt32,'level_id':pl.String,'role':pl.String,
                'valid_from':pl.UInt64,'valid_to':pl.UInt64,
                'lower_int':pl.UInt64,'upper_int':pl.UInt64,'price_int':pl.UInt64})
            for ticker,rows in intervals.intervals})
        self._index_frames=tuple((frame,frame._df) for frame in self._geometry_index.values())
        self.origin_us=round(market_day_boundary(manager.runtime.config.anchor_date,0).timestamp()*1000000)

    def _verify_entry(self,key,financial):
        require_native_structural_rejection_owner(self)
        if (type(financial) is not StrategyOneFinancialView
                or key!=(financial.account_id,financial.assignment_id,financial.ticker)):
            raise ValueError('Native rejection financial identity mismatch')
        proposal=self.manager._submitted.get(key)
        first_held=self.manager._first_held_boundaries.get(key)
        if (type(proposal) is not StrategyOneEntryProposal
                or (proposal.account_id,proposal.assignment_id,proposal.ticker)!=key
                or proposal.strategy_number!=self.manager.contract.strategy_number
                or type(first_held) is not int or first_held<=proposal.boundary_ms
                or key not in self.manager._positions):
            raise ValueError('Native rejection lacks actual entry/first-held/protection')
        previous=self._entry.get(key)
        if previous is None:
            entry=certified_episode_entry_intent(self.price_authority,proposal,
                session_date=self.manager.runtime.config.anchor_date)
            self._entry[key]=(proposal,first_held,entry)
        else:
            if previous[:2]!=(proposal,first_held):
                raise ValueError('Native rejection original entry/held anchors changed')
            entry=previous[2]
        if key in self._states:
            state=self._states[key]
            if (state.position_intent_id!=entry.intent_id or state.account_id!=financial.account_id
                    or state.first_held_boundary_ms!=first_held
                    or state.original_ask_int!=_price(proposal.reference_ask)
                    or state.original_stop_int!=_price(proposal.initial_stop)):
                raise ValueError('Native rejection state changed its original anchors')
        return proposal,first_held,entry

    def begin_boundary(self,financial,boundary_ms):
        require_native_structural_rejection_owner(self)
        if type(financial) is not StrategyOneFinancialView:
            raise ValueError('Native rejection requires actual typed financial observation')
        key=financial.account_id,financial.assignment_id,financial.ticker
        if (type(boundary_ms) is not int or boundary_ms<0 or boundary_ms%100
                or boundary_ms>self.lookup.through_boundary_ms
                or boundary_ms<=self._entered.get(key,-1)):
            raise ValueError('Native rejection manager boundary is unordered/foreign')
        if self._pending:
            self.requests(boundary_ms=boundary_ms)
        self._entered[key]=boundary_ms

    def first_held(self,financial):
        key=financial.account_id,financial.assignment_id,financial.ticker
        proposal,first_held,entry=self._verify_entry(key,financial)
        if key in self._states:
            raise ValueError('Native rejection repeats first-held initialization')
        if len(self._states)>=65536:
            raise ValueError('Native rejection held inventory exceeds bound')
        self._states[key]=StructuralRejectionState(self.lookup.sources[financial.ticker],
            entry.intent_id,financial.account_id,self.origin_us,first_held,
            _price(proposal.reference_ask),_price(proposal.initial_stop),policy=self.declaration.policy)

    def retire(self,key):
        require_native_structural_rejection_owner(self)
        if key in self._pending:
            raise ValueError('Native rejection cannot retire unfenced pending decision')
        for inventory in (self._states,self._entry,self._geometries,self._entered,self._firing):
            inventory.pop(key,None)

    def _level(self,key,state,bar):
        if state.resistance is not None:
            return state.resistance
        prior=state.last_bar
        if (state.arm is None or prior is None or bar is None or not prior.price_valid
                or not bar.price_valid or prior.boundary_ms+self.declaration.policy.bar_resolution_ms!=bar.boundary_ms
                or not state.arm.boundary_ms<bar.boundary_ms):
            return None
        asof=bar.boundary_ms-100
        seconds=self._valid_seconds[key[2]]
        offset=bisect_right(seconds,asof)-1
        if offset<0 or asof-seconds[offset]>1000:
            return None
        input_second=seconds[offset]
        selected=self._geometry_index[key[2]].filter(
            (pl.col('role')=='resistance') & (pl.col('valid_from')<=input_second)
            & (pl.col('valid_to')>asof) & (pl.col('price_int')>=prior.close_int)
            & (pl.col('price_int')<bar.close_int)).sort(['price_int','level_id']).head(1)
        if selected.is_empty():
            return None
        chosen=selected.row(0,named=True)
        row=self._intervals[key[2]][chosen['index']]
        lower,upper,price=chosen['lower_int'],chosen['upper_int'],chosen['price_int']
        if (lower,upper,price)!=self.declaration.geometry_ints(row.lower,row.upper):
            raise ValueError('Indexed native geometry differs from exact scalar declared conversion')
        raw_confirmed=row.confirmed_at_ms
        origin_ms=self.origin_us//1000
        if raw_confirmed>origin_ms+asof:
            raise ValueError('Native resistance has future confirmation')
        unit=self._coverage[key[2]]; seed=self._seed_units[key[2]]
        if raw_confirmed<origin_ms:
            if (not row.historical or not seed.get('level_count')
                    or seed.get('session_date','')>=state.source.session_date
                    or seed.get('source_checkpoint_hash')!=unit.source_checkpoint_hash):
                raise ValueError('Historical resistance lacks actual certified prior seed')
            confirmed=0
        else:
            confirmed=raw_confirmed-origin_ms
        available=max(row.valid_from_ms,confirmed)
        provenance=dict(interval=asdict(row),source_interval_token=self.intervals.token,
            source_interval_attempt_id=unit.attempt_id,source_checkpoint_hash=unit.source_checkpoint_hash,
            decoded_seed_hash=unit.decoded_seed_hash,seed_source_plan_hash=unit.seed_source_plan_hash,
            seed_session=seed['session_date'],seed_available_at=str(seed['available_at']),
            reference_basis=self.declaration.resistance_price_basis,price_int=price,
            midpoint_arithmetic=self.declaration.midpoint_arithmetic,
            geometry_bound_conversion=self.declaration.geometry_bound_conversion)
        frozen=FrozenResistance(state.source,row.level_id,_digest(provenance),lower,upper,
            price,confirmed,available)
        self._geometries[key]=_freeze(provenance)
        return frozen

    def observe(self,financial,resolutions,boundary_ms):
        key=financial.account_id,financial.assignment_id,financial.ticker
        self._verify_entry(key,financial)
        if self._entered.get(key)!=boundary_ms or key not in self._states:
            raise ValueError('Native rejection did not enter the actual held manager boundary')
        policy=self.declaration.policy
        if boundary_ms%policy.decision_resolution_ms:
            return None
        state=self._states[key]
        if boundary_ms<=state.last_decision_boundary_ms:
            raise ValueError('Native rejection decision reduced more than once')
        bar=self.lookup.completed_at(key[2],boundary_ms)
        # Preserve identical nonfinite observation sharing at repeated clocks;
        # changed complete content still reaches the unchanged reducer guard.
        retained=tuple(b for b in (state.last_bar,state.arm,state.cross,state.cross_prior,*state.rejections)
                       if b is not None)
        def retained_bar(value):
            return next((old for old in retained if _tree(old)==_tree(value)),value)
        if bar is not None:
            bar=retained_bar(bar)
        activity=self.lookup.activity_at(key[2],boundary_ms)
        activity=tuple(retained_bar(b) for b in activity) if all(b is not None for b in activity) else ()
        row=resolutions.get(100);quote=None
        if row is not None:
            if (row.get('session_date')!=state.source.session_date or row.get('ticker')!=key[2]
                    or row.get('resolution_ms')!=100 or row.get('boundary_ms')!=boundary_ms
                    or type(row.get('quote_valid')) not in (int,bool) or row['quote_valid'] not in (0,1)):
                raise ValueError('Native quote differs from actual scheduler source/frontier')
            if row['quote_valid']:
                quote=CurrentQuote(state.source,row['quote_timestamp_us'],row['bid_int'],row['ask_int'])
        value=StructuralRejectionInput(state.source,boundary_ms,bar,
            self._level(key,state,bar),activity,quote,pending_exit=financial.pending_exit)
        next_state,witness=reduce_structural_rejection(state,value,policy=policy)
        self._states[key]=next_state
        if witness is not None:
            self._firing[key]=witness
            request=StructuralRejectionCheckpointRequest(witness,financial,
                next_state.position_intent_id,self._geometries[key])
            self._pending[key]=request
            _REQUESTS[request]=(self,key,next_state.position_intent_id,next_state)
            return request
        return None

    def requests(self,*,boundary_ms):
        requests=tuple(v for _,v in sorted(self._pending.items()))
        if any(v.witness.decision_boundary_ms!=boundary_ms for v in requests):
            raise ValueError('Native rejection pending decision must be fenced before advancing')
        for request in requests:
            require_structural_rejection_request(request,owner=self)
        return requests

    def complete_requests(self,requests,*,boundary_ms):
        if requests!=self.requests(boundary_ms=boundary_ms):
            raise ValueError('Native rejection completion changed issued pending inventory')
        self._pending.clear()

    def capture(self,*,boundary_ms):
        require_native_structural_rejection_owner(self)
        if set(self._states)!=set(self.manager._positions):
            raise ValueError('Selected reducer capture omits an actual held manager position')
        result=[]
        for key,state in sorted(self._states.items()):
            state.__post_init__()
            if state.last_decision_boundary_ms>boundary_ms or key not in self.manager._positions:
                raise ValueError('Native rejection capture has future/orphan held state')
            observed=self._entered.get(key)
            if type(observed) is not int or not state.last_decision_boundary_ms<=observed<=boundary_ms:
                raise ValueError('Selected reducer capture has a foreign observation frontier')
            status=('no_observation_gap' if observed<boundary_ms else
                    'processed' if state.last_decision_boundary_ms==boundary_ms else
                    'not_due' if boundary_ms%state.policy.decision_resolution_ms else 'inherited_priority_or_initial')
            result.append((key,state,status,observed))
        return tuple(result)

    def issue_capture(self,state):
        from .backtest_strategy_one_management import StructuralRejectionManagementState
        require_native_structural_rejection_owner(self)
        if (type(state) is not StructuralRejectionManagementState
                or state.structural_rejection_states!=self.capture(boundary_ms=state.boundary_ms)
                or state.structural_rejection_requests!=self.requests(boundary_ms=state.boundary_ms)
                or state.submitted!=tuple(sorted(self.manager._submitted.items()))
                or state.positions!=tuple(sorted(self.manager._positions.items()))):
            raise ValueError('Selected capture differs from actual native owner/manager')
        evidence=MappingProxyType({key:(self._firing.get(key),
            self._geometries.get(key)) for key,_,_,_ in state.structural_rejection_states})
        _CAPTURES[state]=(self,tuple((f.name,getattr(state,f.name)) for f in fields(state)),evidence)
        return state


def prepare_structural_rejection_source(client,*,contract,strategy_id,strategy_revision,
        run_id,session_date,market,seeds,intervals,price_authority,through_boundary_ms):
    """Certify the complete source once, without constructing financial actors."""
    from src.trading_runtime.numbered_fixed_strategy import resolve_numbered_fixed_strategy
    declaration=native_structural_rejection_declaration(contract)
    if declaration is None:
        raise ValueError('Undeclared structural rejection native preparation')
    if (resolve_numbered_fixed_strategy(strategy_id,strategy_revision)!=contract
            or type(market) is not CertifiedMarketDayPlan or type(seeds) is not CertifiedSeedPlan
            or type(intervals) is not CertifiedV7IntervalPlan
            or type(price_authority) is not CertifiedPriceReadbackAuthority
            or price_authority.run_id!=run_id
            or market.sessions!=(session_date.isoformat(),)
            or price_authority.plan.source.market.build_id!=market.build_id
            or price_authority.plan.source.market.token!=market.token):
        raise ValueError('Native rejection lacks exact configured source/entry authority')
    verify_market_day_plan(market,client)
    # The interval scope is the already certified complete execution source,
    # not a caller-supplied candidate-survivor intersection.
    tickers=tuple(row.ticker for row in intervals.coverage)
    from .backtest_market_data import project_market_day_plan
    interval_market=project_market_day_plan(market,tickers)
    fresh_seeds=certified_seed_plan(interval_market,client)
    if fresh_seeds!=seeds:
        raise ValueError('Native rejection prior seed authority changed')
    fresh=certify_v7_interval_plan(interval_market,fresh_seeds,session_date=market.sessions[0],
        candidate_tickers=tickers,client=client)
    if fresh!=intervals:
        raise ValueError('Native rejection interval authority changed')
    lookup=load_completed_structural_rejection_lookup(client,plan=market,
        session_date=session_date,tickers=tickers,policy=declaration.policy,
        run_id=run_id,interval_plan_token=intervals.token,
        through_boundary_ms=through_boundary_ms)
    source=PreparedStructuralRejectionSource(contract,strategy_id,strategy_revision,run_id,
        session_date,market,fresh_seeds,fresh,price_authority,declaration,lookup)
    _SOURCES[source]=(tuple((f.name,getattr(source,f.name)) for f in fields(source)),_lookup_binding(lookup))
    return source


def bind_prepared_structural_rejection_manager(manager,source):
    """Bind only the already certified operation to its exact actual runtime."""
    from .backtest_strategy_one_management import StrategyOneManagementRunner
    source=require_prepared_structural_rejection_source(source)
    if (type(manager) is not StrategyOneManagementRunner or manager.contract!=source.contract
            or manager.runtime.config.strategy_id!=source.strategy_id
            or manager.runtime.config.strategy_revision!=source.strategy_revision
            or manager.runtime.run_id!=source.run_id
            or manager.runtime.config.anchor_date!=source.session_date
            or manager._submitted or manager._positions):
        raise ValueError('Prepared rejection source differs from empty actual manager/runtime')
    owner=NativeStructuralRejectionManager(manager,source.market,source.seeds,source.intervals,
        source.price_authority,source.declaration,source.lookup)
    _OWNERS[owner]=(manager,source.market,source.seeds,source.intervals,source.price_authority,source.declaration,
                    manager.runtime.run_id,manager.runtime.config.strategy_revision,
                    source.lookup,_lookup_binding(source.lookup),owner.origin_us,
                    (owner._seed_units,owner._intervals,owner._coverage,owner._geometry_index,owner._valid_seconds),
                    owner._index_frames,source)
    return owner


def prepare_native_structural_rejection_manager(manager,client,*,market,seeds,intervals,price_authority,
                                               through_boundary_ms):
    from .backtest_strategy_one_management import StrategyOneManagementRunner
    if type(manager) is not StrategyOneManagementRunner:
        raise ValueError('Native rejection requires actual shared manager')
    source=prepare_structural_rejection_source(client,contract=manager.contract,
        strategy_id=manager.runtime.config.strategy_id,strategy_revision=manager.runtime.config.strategy_revision,
        run_id=manager.runtime.run_id,session_date=manager.runtime.config.anchor_date,
        market=market,seeds=seeds,intervals=intervals,price_authority=price_authority,
        through_boundary_ms=through_boundary_ms)
    return bind_prepared_structural_rejection_manager(manager,source)
