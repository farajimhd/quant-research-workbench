"""Operation-local causal lot preparation; never installed financial authority.

The public factory reloads the immutable parent configuration and complete V7
product. Per-proposal work then uses the parent's actual entry rebind and causal
indexed geometry; it does not rehash the full ticker-day. Cold consumers must
call the factory afresh, rather than approving a persisted token by itself.
"""
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import json
from math import isfinite
from uuid import UUID, NAMESPACE_URL, uuid5
from weakref import WeakKeyDictionary
from threading import RLock
from types import MappingProxyType

from .backtest_strategy_one_configuration import certify_numbered_configuration, CertifiedStrategyOneConfiguration
from .backtest_strategy_one_v7_interval_store import certify_v7_interval_plan, CertifiedV7IntervalPlan
from .backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
from .backtest_strategy_episode_activity_source import certified_episode_entry_intent
from .backtest_market_data import verify_market_day_plan, _literal, market_day_boundary, SESSION_OPEN_OFFSET_MS
from src.trading_runtime.entry_spread_risk import canonical_price_int
from src.trading_runtime.fixed_structural_lot_entry import (
    FixedStructuralLotEntry, FixedStructuralLotTargetCountIneligible, _validate_interval_ticker, _validate_entry_input,
    _select_fixed_structural_lot_entry, _intent_from_verified_entry, _same_typed,
)
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash

CONTRACT = 'fixed-structural-lot-configuration@1'
_ISSUED = WeakKeyDictionary()
_ENTRY_REJECTIONS = WeakKeyDictionary()
_ISSUE_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class FixedStructuralLotQuote:
    ticker: str
    boundary_ms: int
    source_build_id: str
    market_plan_token: str
    gate_token: str
    broker_attempt_id: str
    bid_int: int
    ask_int: int
    quote_timestamp_us: int
    quote_valid: int


def _load_quotes(market, authority, *, client):
    """Same qualified sparse source@2 key as the native scheduler's 100ms row.

    This loader has no spread policy or reduction: it only retains the actual
    completed quote whose bucket ends at the admitted candidate boundary.
    """
    verify_market_day_plan(market, client=client)
    gate = authority.entry_activity_source.gate
    units = {(u.session_date, u.ticker):u for u in market.units if u.stage == 'broker_100ms'}
    candidate_attempts = {u.ticker:u.source_attempts[2] for u in authority.plan.candidates.coverage}
    facts = tuple(f for i,f in enumerate(gate.facts) if gate.rejection_mask[i] == 0)
    result = []
    for start in range(0,len(facts),512):
        requested = {}
        for fact in facts[start:start+512]:
            unit = units.get((market.sessions[0],fact.ticker))
            if unit is None or candidate_attempts.get(fact.ticker) != unit.attempt_id:
                raise ValueError('Native candidate and completed broker quote attempts differ')
            key = (fact.ticker,(fact.boundary_ms+SESSION_OPEN_OFFSET_MS)//100-1,unit.attempt_id)
            if key in requested:
                raise ValueError('Selected native quote keys duplicate')
            requested[key] = fact
        scope=','.join(f'({_literal(t)},{b},toUUID({_literal(a)}))' for t,b,a in requested)
        sql=('SELECT l.ticker AS ticker,l.bucket_index AS bucket_index,'
             'toString(l.attempt_id) AS liquidity_attempt_id,l.bid_int AS bid_int,l.ask_int AS ask_int,'
             'l.quote_timestamp_us AS quote_timestamp_us,l.quote_valid AS quote_valid '
             'FROM arte.liquidity_100ms_v1 AS l '
             f'WHERE l.build_id={_literal(market.build_id)} AND l.session_date=toDate({_literal(market.sessions[0])}) '
             f'AND (l.ticker,l.bucket_index,l.attempt_id) IN ({scope}) '
             'SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow')
        seen={}
        for row in (json.loads(line) for line in client.execute(sql).splitlines() if line.strip()):
            if (set(row) != {'ticker','bucket_index','liquidity_attempt_id','bid_int','ask_int','quote_timestamp_us','quote_valid'}
                    or type(row['ticker']) is not str or type(row['liquidity_attempt_id']) is not str
                    or any(type(row[k]) is not int for k in ('bucket_index','bid_int','ask_int','quote_timestamp_us','quote_valid'))):
                raise ValueError('Selected native quote scalar shape differs')
            key=(row['ticker'],row['bucket_index'],row['liquidity_attempt_id'])
            if key not in requested or key in seen:
                raise ValueError('Selected native quote is foreign or duplicate')
            fact=requested[key]
            now=int(market_day_boundary(date.fromisoformat(market.sessions[0]),fact.boundary_ms).timestamp()*1_000_000)
            if (row['quote_valid'] not in (0,1) or min(row['bid_int'],row['ask_int'],row['quote_timestamp_us']) < 0
                    or row['quote_timestamp_us'] > now
                    or row['quote_valid'] == 1 and not 0 < row['bid_int'] <= row['ask_int']):
                raise ValueError('Selected native quote violates completed causal boundary')
            seen[key]=FixedStructuralLotQuote(fact.ticker,fact.boundary_ms,market.build_id,
                market.token,gate.token,key[2],row['bid_int'],row['ask_int'],
                row['quote_timestamp_us'],row['quote_valid'])
        if set(seen) != set(requested):
            raise ValueError('Selected native quote source is incomplete')
        result.extend(seen[key] for key in requested)
    return tuple(sorted(result,key=lambda q:(q.ticker,q.boundary_ms)))


def derive_fixed_structural_lot_configuration(parent, policy, *, installed_configuration=None):
    """Complete explicit component declaration, distinct from its parent release."""
    if type(parent) is not dict or type(policy) is not FixedStructuralLotPolicy:
        raise ValueError('Exact parent configuration and lot declaration required')
    policy.__post_init__()
    # Roundtrip the shared typed tree to reject unsupported or nonfinite values.
    encode_nodes(parent)
    tick = parent.get('strategy', {}).get('parameters', {}).get('execution', {}).get('tick_size')
    if type(tick) not in (int, float) or not isfinite(tick) or tick <= 0:
        raise ValueError('Certified inherited execution tick is missing or invalid')
    selected={'contract': CONTRACT, 'inherited_configuration': json.loads(canonical_json(parent)),
            'fixed_structural_lot_policy': policy.payload(), 'execution_tick': float(tick)}
    if installed_configuration is not None:
        if type(installed_configuration) is not dict:
            raise ValueError('Complete installed configuration tree required')
        encode_nodes(installed_configuration)
        selected['installed_configuration']=json.loads(canonical_json(installed_configuration))
    return selected


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FixedStructuralLotRequest:
    run_id: str
    strategy_id: str
    revision: int
    source: 'PreparedFixedStructuralLotSource'
    entry: FixedStructuralLotEntry
    original: StrategyIntent
    intent: StrategyIntent

    def verify(self):
        if type(self.source) is not PreparedFixedStructuralLotSource:
            raise ValueError('Exact operation-local prepared lot source required')
        from .backtest_fixed_lot_management_reuse import verify_entry
        verify_entry(self)

    def _verify_complete(self):
        if type(self.source) is not PreparedFixedStructuralLotSource:
            raise ValueError('Exact operation-local prepared lot source required')
        actual = self.source.request(self.entry.proposal)
        if (type(self.run_id) is not str or type(self.strategy_id) is not str
                or type(self.revision) is not int
                or self.run_id != actual.run_id or self.strategy_id != actual.strategy_id
                or self.revision != actual.revision
                or not _same_typed(self.entry, actual.entry)
                or not _same_typed(self.original, actual.original)
                or not _same_typed(self.intent, actual.intent)):
            raise ValueError('Selected lot request differs from complete causal replay')


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class PreparedFixedStructuralLotSource:
    run_id: str
    session_date: date
    parent_attempt_id: str
    parent_token: str
    parent_payload_hash: str
    parent_node_hash: str
    parent_source_candidate_id: str
    parent_source_candidate_hash: str
    parent_json: str
    selected_json: str
    selected_configuration_hash: str
    policy: FixedStructuralLotPolicy
    tick: float
    intervals: CertifiedV7IntervalPlan
    price_authority: CertifiedPriceReadbackAuthority
    validated_ticker_count: int
    quotes: tuple[FixedStructuralLotQuote, ...]
    installed_json: str = ''
    _strategy_id: str = field(init=False, repr=False)
    _revision: int = field(init=False, repr=False)
    _quotes: object = field(init=False, repr=False)

    def __post_init__(self):
        if (type(self.run_id) is not str or str(UUID(self.run_id)) != self.run_id
                or type(self.session_date) is not date or type(self.policy) is not FixedStructuralLotPolicy
                or type(self.tick) is not float or not isfinite(self.tick) or self.tick <= 0
                or type(self.intervals) is not CertifiedV7IntervalPlan
                or type(self.price_authority) is not CertifiedPriceReadbackAuthority
                or self.price_authority.run_id != self.run_id
                or type(self.validated_ticker_count) is not int
                or self.validated_ticker_count != len(self.intervals._tickers)
                or self.intervals.session_date != self.session_date.isoformat()):
            raise ValueError('Prepared lot source identity/types differ')
        parent, selected = self.parent_payload, self.selected_payload
        strategy = (self.installed_payload if self.installed_json else parent)['strategy']
        if (type(strategy.get('strategy_id')) is not str or not strategy['strategy_id']
                or type(strategy.get('revision')) is not int or strategy['revision'] <= 0
                or type(strategy.get('strategy_number')) is not int
                or strategy.get('strategy_number') != strategy['revision']
                or self.parent_json != canonical_json(parent)
                or self.selected_json != canonical_json(selected)
                or sha256(self.parent_json.encode()).hexdigest() != self.parent_payload_hash
                or node_hash(encode_nodes(parent)) != self.parent_node_hash
                or not _same_typed(selected, derive_fixed_structural_lot_configuration(parent, self.policy,
                    installed_configuration=self.installed_payload if self.installed_json else None))
                or sha256(self.selected_json.encode()).hexdigest() != self.selected_configuration_hash):
            raise ValueError('Prepared lot complete configuration derivation differs')
        if self.tick != selected['execution_tick']:
            raise ValueError('Prepared lot tick differs from certified inherited execution')
        for value in (self.parent_token, self.parent_payload_hash, self.parent_node_hash,
                      self.parent_source_candidate_hash, self.selected_configuration_hash):
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Prepared lot configuration reference differs')
        if (type(self.parent_attempt_id) is not str or str(UUID(self.parent_attempt_id)) != self.parent_attempt_id
                or type(self.parent_source_candidate_id) is not str or not self.parent_source_candidate_id):
            raise ValueError('Prepared lot immutable parent reference differs')
        object.__setattr__(self, '_strategy_id', strategy['strategy_id'])
        object.__setattr__(self, '_revision', strategy['revision'])
        if (type(self.quotes) is not tuple or any(type(q) is not FixedStructuralLotQuote for q in self.quotes)
                or tuple((q.ticker,q.boundary_ms) for q in self.quotes) != tuple(sorted(set((q.ticker,q.boundary_ms) for q in self.quotes)))):
            raise ValueError('Prepared native quote inventory differs')
        object.__setattr__(self,'_quotes',MappingProxyType({(q.ticker,q.boundary_ms):q for q in self.quotes}))

    @property
    def parent_payload(self):
        return json.loads(self.parent_json)

    @property
    def installed_payload(self):
        return json.loads(self.installed_json) if self.installed_json else None

    @property
    def selected_payload(self):
        return json.loads(self.selected_json)

    def request(self, proposal):
        from .backtest_fixed_lot_management_reuse import request_from_management_source
        return request_from_management_source(self, proposal, lambda: self._request_complete(proposal))

    def _request_complete(self, proposal):
        """Rebind native entry facts, with no full-source validation in this path."""
        self.require_prepared_source()
        _validate_entry_input(proposal, session_date=self.session_date, policy=self.policy,
                              intervals=self.intervals, tick=self.tick)
        if (proposal.strategy_number != self._revision
                or self.price_authority.run_id != self.run_id):
            raise ValueError('Proposal differs from prepared parent configuration/run')
        quote = self._quotes.get((proposal.ticker,proposal.boundary_ms))
        fact = self.price_authority.plan.entry.lookup(proposal.ticker,proposal.boundary_ms)
        if (quote is None or quote.quote_valid != 1
                or canonical_price_int(proposal.reference_ask) != quote.ask_int
                or proposal.reference_ask != quote.ask_int / 10_000
                or proposal.initial_stop != fact.stop_price or proposal.initial_target != fact.target_price
                or proposal.target_level_id != fact.target_level_id
                or proposal.bos_break_boundary_ms != fact.bos_break_boundary_ms
                or proposal.bos_support_level_id != fact.bos_support_level_id):
            raise ValueError('Proposal differs from certified current quote or entry geometry')
        # This is the actual native factory: price, momentum, activity and held
        # source fences must all match. No caller-supplied original is approved.
        original = certified_episode_entry_intent(self.price_authority, proposal,
                                                  session_date=self.session_date)
        try:
            entry = _select_fixed_structural_lot_entry(proposal, session_date=self.session_date,
                policy=self.policy, intervals=self.intervals, tick=self.tick)
        except FixedStructuralLotTargetCountIneligible as error:
            if type(error) is not FixedStructuralLotTargetCountIneligible:
                raise
            with _ISSUE_LOCK:
                _ENTRY_REJECTIONS[error] = (self, proposal, original, error.required, error.available)
            raise
        own_id = str(uuid5(NAMESPACE_URL, ':'.join((CONTRACT, self.run_id,
            self.selected_configuration_hash, original.intent_id))))
        intent = _intent_from_verified_entry(original, entry, intent_id=own_id, expected=original)
        return FixedStructuralLotRequest(self.run_id, self._strategy_id, self._revision,
                                         self, entry, original, intent)

    def require_installed_admission(self):
        from .backtest_fixed_structural_lot_native import require_installed_source
        require_installed_source(self)

    def verified_entry_rejection(self, error, proposal):
        """Only this issued source's validated native proposal may be counted."""
        self.require_installed_admission()
        with _ISSUE_LOCK:
            binding = _ENTRY_REJECTIONS.get(error)
        if (type(error) is not FixedStructuralLotTargetCountIneligible or binding is None
                or binding[0] is not self or binding[1] is not proposal
                or binding[3:] != (error.required, error.available)
                or error.required != self.policy.count):
            raise ValueError('Unissued or altered structural entry ineligibility')
        return (f'fixed-structural-lot-entry@1:target_count_ineligible:'
                f'required={error.required}:available={error.available}')

    def require_prepared_source(self):
        # This operation-local object capability is issued ONLY below, after
        # real complete certifier calls. A self-consistent constructor, hash,
        # dataclasses.replace or caller boolean cannot issue a capability.
        with _ISSUE_LOCK:
            issued = _ISSUED.get(self)
        if issued is None:
            raise ValueError('Lot source was not issued by fresh complete preparation')
        scalar, intervals, authority, quotes = issued
        if (scalar != _source_identity(self) or self.intervals is not intervals
                or self.price_authority is not authority or self.quotes is not quotes):
            raise ValueError('Issued prepared lot source was altered')


def _source_identity(source):
    return tuple(getattr(source, name) for name in (
        'run_id', 'session_date', 'parent_attempt_id', 'parent_token', 'parent_payload_hash',
        'parent_node_hash', 'parent_source_candidate_id', 'parent_source_candidate_hash',
        'parent_json', 'selected_json', 'selected_configuration_hash', 'policy', 'tick',
        'validated_ticker_count', '_strategy_id', '_revision','installed_json'))


def require_native_fixed_structural_lot_source(source):
    """Exact separately issued full or empty source; no structural coercion."""
    from .backtest_fixed_structural_lot_empty import PreparedEmptyFixedStructuralLotSource
    if type(source) not in (PreparedFixedStructuralLotSource, PreparedEmptyFixedStructuralLotSource):
        raise ValueError('Exact factory-issued fixed-lot source required')
    source.require_prepared_source()
    return source


def prepare_fixed_structural_lot_source(client, *, run_id, parent_number, session_date,
        policy, market, seeds, price_authority, tick=None, installed_number=None):
    """Fresh complete configuration/product preparation, once per operation."""
    if (type(run_id) is not str or str(UUID(run_id)) != run_id
            or type(parent_number) is not int or type(session_date) is not date
            or (type(policy) is not FixedStructuralLotPolicy and not (policy is None and type(installed_number) is int))
            or type(price_authority) is not CertifiedPriceReadbackAuthority
            or price_authority.run_id != run_id):
        raise ValueError('Exact prepared lot run/source declaration required')
    if policy is not None:
        policy.__post_init__()
    price_authority.__post_init__()
    native_market = price_authority.plan.source.market
    if (native_market.token != market.token or native_market.build_id != market.build_id
            or native_market.sessions != market.sessions or native_market.tickers != market.tickers):
        raise ValueError('Prepared source market differs from actual native entry source')
    parent = certify_numbered_configuration(client, parent_number)
    if type(parent) is not CertifiedStrategyOneConfiguration:
        raise ValueError('Actual complete parent configuration certificate required')
    payload = parent.payload
    if (type(payload) is not dict or payload['strategy']['revision'] != parent_number
            or sha256(canonical_json(payload).encode()).hexdigest() != parent.payload_hash
            or node_hash(encode_nodes(payload)) != parent.node_hash):
        raise ValueError('Parent configuration content differs from its certificate')
    installed=None
    if installed_number is not None:
        from .backtest_fixed_structural_lot_native import load_installed_configuration
        installed,declared_policy,own_proof=load_installed_configuration(client,number=installed_number,parent=parent)
        if policy is None:
            policy=declared_policy
        elif declared_policy!=policy:
            raise ValueError('Caller lot policy differs from actual installed declaration')
    selected = derive_fixed_structural_lot_configuration(payload, policy,
        installed_configuration=installed.payload if installed is not None else None)
    declared_tick = selected['execution_tick']
    if tick is not None and (type(tick) is not float or tick != declared_tick):
        raise ValueError('Caller tick differs from certified inherited execution')
    tick = declared_tick
    # The actual market inventory, rather than a candidate/position subset,
    # determines preparation scope.
    intervals = certify_v7_interval_plan(market, seeds, session_date=session_date.isoformat(),
        candidate_tickers=market.tickers, client=client)
    if type(intervals) is not CertifiedV7IntervalPlan:
        raise ValueError('Actual complete interval source certificate required')
    if intervals.session_date != session_date.isoformat() or intervals.source_build_id != market.build_id:
        raise ValueError('Prepared interval build/session differs')
    for ticker in intervals._tickers:
        _validate_interval_ticker(intervals, session_date=session_date, ticker=ticker)
    quotes = _load_quotes(market,price_authority,client=client)
    selected_json = canonical_json(selected)
    source = PreparedFixedStructuralLotSource(run_id, session_date, parent.attempt_id, parent.token,
        parent.payload_hash, parent.node_hash, parent.source_candidate_id, parent.source_candidate_hash,
        canonical_json(payload), selected_json, sha256(selected_json.encode()).hexdigest(),
        policy, tick, intervals, price_authority, len(intervals._tickers),quotes,
        canonical_json(installed.payload) if installed is not None else '')
    with _ISSUE_LOCK:
        _ISSUED[source] = (_source_identity(source), intervals, price_authority,quotes)
    if installed is not None:
        from .backtest_fixed_structural_lot_native import _issue_installed_source
        _issue_installed_source(source,installed,own_proof)
    return source
