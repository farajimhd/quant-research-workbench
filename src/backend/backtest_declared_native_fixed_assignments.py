"""Prepared own-family assignment scope, independent of legacy executors.

The loader re-verifies normalized configuration and complete producer plans.
A typed policy/plan is not installed execution authority: the future own
manifest must seal this declaration and bind its run/source closure.
"""
from dataclasses import dataclass, asdict, field
from types import MappingProxyType
from hashlib import sha256
import json
from math import isfinite
from uuid import UUID, uuid5, NAMESPACE_URL

from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.backend.backtest_strategy_one_identity import certify_identity_plan, CertifiedIdentityPlan
from src.trading_runtime.runtime import RunMode
from src.backend.backtest_market_data import CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, verify_market_day_plan

ASSIGNMENT_CONTRACT = "declared-native-fixed-assignment-scope@1"
PERMISSION_NAMES = ("observe", "enter", "add", "reduce", "exit", "reenter")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DeclaredAssignmentPolicy:
    """Explicit preservation of automatic fixed-run assignment permissions.

    These are admission permissions; capabilities still own add/reentry rules.
    No caller-selected permission override or strategy-number selector exists.
    """
    policy_id: str = ASSIGNMENT_CONTRACT

    def __post_init__(self):
        if type(self.policy_id) is not str or self.policy_id != ASSIGNMENT_CONTRACT:
            raise ValueError("Declared assignment policy is unsupported")

    def payload(self):
        self.__post_init__()
        return dict(policy_id=self.policy_id, identity_rule="uuid5-own-run-strategy-revision-account-key-ticker@1",
                    population_rule="complete-certified-candidate-account-product@1",
                    permissions={name: True for name in PERMISSION_NAMES},
                    parameter_rule="exact-complete-sealed-strategy-parameters@1")


@dataclass(frozen=True, slots=True)
class DeclaredAssignmentScope:
    run_id: str
    assignment_id: str
    account_key: str
    account_id: str
    ticker: str
    conid: int
    strategy_id: str
    revision: int
    tick_size: float | int
    permissions: tuple[bool, ...]
    parameters_json: str

    def __post_init__(self):
        for value in (self.run_id, self.assignment_id):
            if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
                raise ValueError("Declared assignment UUID identity is invalid")
        if (any(type(v) is not str or not v or v != v.strip() for v in
                (self.account_key, self.account_id, self.ticker, self.strategy_id))
                or type(self.conid) is not int or self.conid <= 0
                or type(self.revision) is not int or self.revision <= 0
                or type(self.tick_size) not in (int, float) or not isfinite(self.tick_size) or self.tick_size <= 0
                or type(self.permissions) is not tuple or len(self.permissions) != len(PERMISSION_NAMES)
                or any(type(v) is not bool for v in self.permissions)
                or type(self.parameters_json) is not str):
            raise ValueError("Declared assignment scope is invalid")
        parameters=json.loads(self.parameters_json)
        if (_json(parameters) != self.parameters_json or type(parameters) is not dict
                or _json(parameters.get('execution',{}).get('tick_size')) != _json(self.tick_size)):
            raise ValueError("Declared assignment parameters differ from exact tick")
        expected=_assignment_id(self.run_id,self.strategy_id,self.revision,self.account_key,self.ticker)
        if expected != self.assignment_id:
            raise ValueError("Declared assignment identity differs from its complete scope")

    def payload(self):
        return asdict(self)


def _assignment_id(run_id, strategy_id, revision, account_key, ticker):
    # Own identity schema only; numeric revision never selects behavior.
    return str(uuid5(NAMESPACE_URL,_json((ASSIGNMENT_CONTRACT,run_id,strategy_id,revision,account_key,ticker))))


@dataclass(frozen=True, slots=True)
class DeclaredAssignmentPlan:
    run_id: str
    configuration_hash: str
    strategy_id: str
    revision: int
    parameters_json: str
    market_token: str
    identity_token: str
    candidate_token: str
    policy_json: str
    candidate_tickers: tuple[str, ...]
    account_bindings: tuple[tuple[str, str], ...]
    scopes: tuple[DeclaredAssignmentScope, ...]
    token: str
    _index: object = field(init=False,repr=False,compare=False)

    def payload(self):
        return {name:getattr(self,name) for name in
                ('run_id','configuration_hash','strategy_id','revision','parameters_json','market_token','identity_token','candidate_token',
                 'policy_json','candidate_tickers','account_bindings')} | {'scopes':[row.payload() for row in self.scopes]}

    def __post_init__(self):
        if type(self.run_id) is not str or str(UUID(self.run_id))!=self.run_id or not UUID(self.run_id).int:
            raise ValueError("Declared assignment plan run identity is invalid")
        if (type(self.parameters_json) is not str or type(json.loads(self.parameters_json)) is not dict
                or _json(json.loads(self.parameters_json))!=self.parameters_json):
            raise ValueError("Declared assignment plan parameters are invalid")
        if (type(self.scopes) is not tuple or type(self.candidate_tickers) is not tuple
                or tuple(sorted(set(self.candidate_tickers))) != self.candidate_tickers
                or len(self.candidate_tickers)>8192 or type(self.account_bindings) is not tuple
                or not self.account_bindings or len(self.account_bindings)>65535
                or any(type(row) is not tuple or len(row)!=2 or any(type(v) is not str or not v or v!=v.strip() for v in row) for row in self.account_bindings)
                or len({row[0] for row in self.account_bindings}) != len(self.account_bindings)
                or len({row[1] for row in self.account_bindings}) != len(self.account_bindings)
                or type(self.policy_json) is not str or self.policy_json != _json(DeclaredAssignmentPolicy().payload())):
            raise ValueError("Declared assignment plan has invalid complete scope")
        if (type(self.revision) is not int or self.revision<=0 or type(self.strategy_id) is not str or not self.strategy_id
                or any(type(value) is not str or len(value)!=64 or value=='0'*64 or any(c not in '0123456789abcdef' for c in value)
                       for value in (self.configuration_hash,self.market_token,self.identity_token,self.candidate_token))):
            raise ValueError("Declared assignment plan has foreign identity or source digests")
        expected=tuple((key,account,ticker) for key,account in self.account_bindings for ticker in self.candidate_tickers)
        actual=[]
        for row in self.scopes:
            if type(row) is not DeclaredAssignmentScope:
                raise ValueError("Declared assignment plan has a foreign scope type")
            row.__post_init__()
            if (row.run_id!=self.run_id or row.permissions != (True,)*len(PERMISSION_NAMES)
                    or row.strategy_id!=self.strategy_id or row.revision!=self.revision
                    or row.parameters_json!=self.parameters_json):
                raise ValueError("Declared assignment plan has foreign run or permissions")
            actual.append((row.account_key,row.account_id,row.ticker))
        if tuple(actual)!=expected or len({row.assignment_id for row in self.scopes})!=len(self.scopes):
            raise ValueError("Declared assignment plan is missing, duplicate or narrowed")
        if type(self.token) is not str or self.token != _hash(self.payload()):
            raise ValueError("Declared assignment plan content hash differs")
        object.__setattr__(self,"_index",MappingProxyType({row.assignment_id:row for row in self.scopes}))

    def resolve(self, assignment_id, *, account_id, ticker, strategy_id, revision):
        # Complete immutable plan is validated once at construction, including
        # full product/hash. Hot admission is a single exact identity lookup.
        if (any(type(v) is not str or not v for v in (assignment_id,account_id,ticker,strategy_id))
                or type(revision) is not int):
            raise ValueError("Declared assignment exact account/ticker/own revision binding differs")
        row=self._index.get(assignment_id)
        if row is None or (row.account_id,row.ticker,row.strategy_id,row.revision)!=(account_id,ticker,strategy_id,revision):
            raise ValueError("Declared assignment exact account/ticker/own revision binding differs")
        return row


def prepare_declared_assignment_plan(client, *, run_id, spec, envelope, approval, market):
    """Independently reloaded @3 configuration/scope, not installed approval."""
    from src.trading_runtime.declared_native_execution import (
        DeclaredNativeExecutionSpec, verify_declared_execution_configuration,
    )
    if type(spec) is not DeclaredNativeExecutionSpec or type(market) is not CertifiedMarketDayPlan:
        raise ValueError("Declared assignment preparation needs exact execution spec and market")
    verify_declared_execution_configuration(client,spec,envelope,approval=approval)
    return _prepare_verified_assignment_plan(client, run_id=run_id, spec=spec, envelope=envelope, market=market)


def prepare_declared_managed_assignment_plan(client, *, run_id, spec, envelope, approval, market):
    """Complete @4 verification precedes the identical dated source loader."""
    from src.trading_runtime.declared_native_managed_execution import (
        DeclaredNativeManagedExecutionSpec, verify_declared_managed_configuration,
    )
    if type(spec) is not DeclaredNativeManagedExecutionSpec or type(market) is not CertifiedMarketDayPlan:
        raise ValueError("Managed assignments need exact managed spec and market")
    verify_declared_managed_configuration(client, spec, envelope, approval=approval)
    return _prepare_verified_assignment_plan(client, run_id=run_id, spec=spec.execution,
                                              envelope=envelope, market=market)


def _prepare_verified_assignment_plan(client, *, run_id, spec, envelope, market):
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    from src.trading_runtime.arte_backtest_definition import load_backtest_definition
    from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan, RULE_DIGEST
    configuration=envelope['payload'];policy=spec.assignments
    if type(policy) is not DeclaredAssignmentPolicy:
        raise ValueError("Declared execution has foreign assignment policy")
    policy.__post_init__()
    native=load_typed_run_context(client,run_id)
    saved=load_backtest_definition(client,run_id,run_context=native)
    identity=spec.candidate.base.identity
    if (native['run_id']!=run_id or native['mode']!='backtest'
            or native['configuration_hash']!=envelope['payload_hash']
            or native['market_plan_token']!=market.token
            or native['strategy_id']!=identity.strategy_id
            or type(native['strategy_revision']) is not int or native['strategy_revision']!=identity.revision
            or market.sessions!=(native['session_date'],)
            or type(native['evaluation_interval_ms']) is not int
            or native['evaluation_interval_ms']!=market.execution_interval.milliseconds):
        raise ValueError("Declared assignment run/configuration identity differs")
    definition=saved['definition'];tickers=tuple(row['ticker'] for row in saved['tickers'])
    if (not ((definition['ticker_population_mode']=='market_plan' and not tickers)
                or (definition['ticker_population_mode']=='explicit' and tickers==market.tickers))
            or not market.tickers or len(market.tickers)>8192
            or tuple(sorted(set(market.tickers)))!=market.tickers
            or definition['final_session_date']!=native['session_date']
            or type(definition['end_local_ms']) is not int):
        raise ValueError("Declared assignment full population differs")
    end=definition['end_local_ms']-SESSION_OPEN_OFFSET_MS
    if not 0<end<=57_600_000 or end%100:
        raise ValueError("Declared assignment source end differs")
    if configuration.get('assignments'):
        raise ValueError("Declared assignments cannot inherit mutable configured roster")
    bindings=configuration['accounts']['bindings']
    if (type(bindings) is not list or any(type(row) is not dict
            or type(row.get('enabled',True)) is not bool
            or type(row.get('modes')) is not list or any(type(mode) is not str for mode in row['modes'])
            or type(row.get('account_key')) is not str or not row['account_key']
            for row in bindings)):
        raise ValueError("Declared assignment account bindings have invalid types")
    active=tuple(row['account_key'] for row in bindings if row.get('enabled',True) and 'backtest' in row['modes'])
    accounts=historical_simulated_account_ids(mode=RunMode.BACKTEST,configuration=configuration)
    if tuple(native['account_ids'])!=accounts or len(set(accounts))!=len(accounts):
        raise ValueError("Declared assignment simulated accounts differ from exact saved order")
    verify_market_day_plan(market,client)
    candidates=certify_candidate_plan(market,candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=end,client=client)
    if candidates.excluded_tickers:
        raise ValueError("Declared assignment exclusion product needs independent cohort metadata")
    identities=certify_identity_plan(market,client=client)
    if (type(identities) is not CertifiedIdentityPlan or identities.source_build_id!=market.build_id
            or identities.market_token!=market.token or identities.session_date!=native['session_date']
            or identities.tickers!=market.tickers):
        raise ValueError("Declared assignment identity differs from full certified market")
    selected=tuple(row.ticker for row in candidates.prepared)
    if (candidates.source_build_id!=market.build_id
            or tuple(sorted(set(selected)))!=selected or not set(selected)<=set(identities.tickers)):
        raise ValueError("Declared assignment candidates are foreign, duplicate or unordered")
    parameters=configuration['strategy']['parameters'];tick=parameters['execution']['tick_size']
    conids=dict(zip(identities.tickers,identities.conids,strict=True))
    rows=tuple(DeclaredAssignmentScope(run_id,_assignment_id(run_id,identity.strategy_id,
        identity.revision,key,ticker),key,account,ticker,conids[ticker],identity.strategy_id,identity.revision,tick,
        (True,)*len(PERMISSION_NAMES),_json(parameters))
        for key,account in zip(active,accounts,strict=True) for ticker in selected)
    saved_ids=tuple(row['assignment_id'] for row in saved['assignments'])
    if saved_ids and saved_ids!=tuple(row.assignment_id for row in rows):
        raise ValueError("Declared saved assignment membership differs from complete deterministic scope")
    fields=dict(run_id=run_id,configuration_hash=envelope['payload_hash'],strategy_id=identity.strategy_id,
        revision=identity.revision,parameters_json=_json(parameters),market_token=market.token,
        identity_token=identities.token,candidate_token=candidates.token,
        policy_json=_json(policy.payload()),candidate_tickers=selected,account_bindings=tuple(zip(active,accounts,strict=True)),scopes=rows)
    payload={name:value for name,value in fields.items() if name!='scopes'} | {'scopes':[row.payload() for row in rows]}
    return DeclaredAssignmentPlan(**fields,token=_hash(payload))


def resolve_declared_assignment(client, *, run_id, spec, envelope, approval, market,
                                assignment_id, account_id, ticker, strategy_id, revision):
    """Fresh source-equivalence resolution, not historical financial authority."""
    return prepare_declared_assignment_plan(client,run_id=run_id,spec=spec,envelope=envelope,
        approval=approval,market=market).resolve(assignment_id,account_id=account_id,
        ticker=ticker,strategy_id=strategy_id,revision=revision)
