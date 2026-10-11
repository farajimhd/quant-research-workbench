"""Bounded pending native decisions; checkpoint and execution remain separate."""
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from uuid import UUID,NAMESPACE_URL,uuid5

import polars as pl

from .backtest_market_data import market_day_boundary
from .backtest_native_channel_management_request import build_native_channel_management_request
from .backtest_native_channel_manager_capture import capture_native_channel_manager_rows
from src.trading_runtime.native_acquisition_clock import acquisition_clock_from_entry_executions
from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.journal_contract import canonical_json


@dataclass(frozen=True,slots=True)
class _PendingPosition:
    request: object
    prepared: object
    group: object
    executions: tuple
    original_entry: object
    expected_conid: int
    boundary_ms: int


@dataclass(frozen=True,slots=True)
class NativeChannelPendingCapture:
    """Frozen queue input; the worker must still cold-verify its publication."""
    run_id: str
    session_date: date
    policy: NativeChannelManagementPolicy
    boundary_ms: int
    items: tuple

    def __post_init__(self):
        if (type(self.run_id) is not str or not self.run_id or type(self.session_date) is not date
                or type(self.policy) is not NativeChannelManagementPolicy
                or type(self.boundary_ms) is not int or self.boundary_ms<0 or self.boundary_ms%100
                or type(self.items) is not tuple or len(self.items)>self.policy.max_pending_requests):
            raise ValueError('Native pending capture requires its complete bounded immutable scope')
        self.policy.__post_init__()
        keys=[]
        for pair in self.items:
            if type(pair) is not tuple or len(pair)!=2:
                raise ValueError('Native pending capture has a malformed position record')
            key,item=pair
            if type(item) is not _PendingPosition or type(key) is not tuple or len(key)!=3:
                raise ValueError('Native pending capture requires exact position observations')
            view=item.request.financial
            if (key!=(view.account_id,view.assignment_id,view.ticker)
                    or item.boundary_ms!=self.boundary_ms or item.request.policy!=self.policy.exit_policy
                    or item.request.acquisition.session_date!=self.session_date):
                raise ValueError('Native pending capture crossed a position, policy or decision boundary')
            keys.append(key)
        if keys!=sorted(set(keys)):
            raise ValueError('Native pending capture repeats or reorders position ownership')

    def capture(self,inherited,*,journal_batch_id):
        self.__post_init__()
        if inherited.snapshot['boundary_ms']!=self.boundary_ms:
            raise ValueError('Native frozen inventory differs from its inherited checkpoint boundary')
        owner=NativeChannelManagementOwner(run_id=self.run_id,session_date=self.session_date,policy=self.policy)
        owner._pending=dict(self.items)
        return owner.capture(inherited,journal_batch_id=journal_batch_id)


@dataclass(frozen=True,slots=True)
class NativeChannelManagerCheckpoint:
    """Controller-frozen inherited manager and complete native pending inventory."""
    inherited: object
    pending: NativeChannelPendingCapture

    def __post_init__(self):
        from .backtest_strategy_one_management import OriginalRiskManagementState
        if (type(self.inherited) is not OriginalRiskManagementState
                or type(self.pending) is not NativeChannelPendingCapture
                or self.inherited.boundary_ms!=self.pending.boundary_ms):
            raise ValueError('Native checkpoint requires its exact inherited and pending boundary')
        self.pending.__post_init__()
        sources=dict(self.inherited.submitted)
        held=dict(self.inherited.positions)
        if any(key not in held or sources.get(key)!=item.original_entry for key,item in self.pending.items):
            raise ValueError('Native checkpoint pending position differs from active inherited entry ownership')


class NativeChannelManagementOwner:
    """Keep exact accepted observations until the controller drains them.

    Supplying these source observations grants no order or checkpoint authority.
    The selected publisher must independently attest every persisted input.
    """
    def __init__(self,*,run_id,session_date,policy):
        if (type(run_id) is not str or not run_id or type(session_date) is not date
                or type(policy) is not NativeChannelManagementPolicy):
            raise ValueError('Native management owner requires exact run, session and declaration')
        policy.__post_init__()
        self.run_id,self.session_date,self.policy=run_id,session_date,policy
        self._pending={}

    def stage(self,prepared,quote,*,financial,original_entry,group,executions,
            source_entry_intent_id,expected_conid,boundary_ms,position_index=None):
        if type(executions) is not tuple:
            raise ValueError('Native management requires complete immutable execution observations')
        clock=acquisition_clock_from_entry_executions(group,executions,
            session_date=self.session_date,as_of=market_day_boundary(self.session_date,boundary_ms),
            source_entry_intent_id=source_entry_intent_id,expected_conid=expected_conid)
        request=build_native_channel_management_request(prepared,quote,policy=self.policy.exit_policy,
            financial=financial,acquisition=clock,original_entry=original_entry,
            boundary_ms=boundary_ms,position_index=position_index)
        key=(financial.account_id,financial.assignment_id,financial.ticker)
        if self._pending and any(item.boundary_ms!=boundary_ms for item in self._pending.values()):
            raise ValueError('Native management cannot advance beyond undrained pending decisions')
        previous=self._pending.get(key)
        if request is None:
            if previous is not None:
                raise ValueError('Native management retry withdrew its pending decision')
            return None
        item=_PendingPosition(request,prepared,group,executions,original_entry,expected_conid,boundary_ms)
        if previous is not None:
            if previous!=item:
                raise ValueError('Native management retry changed its frozen pending observation')
            return previous.request
        if len(self._pending)>=self.policy.max_pending_requests:
            raise ValueError('Native management pending inventory exceeds declared capacity')
        self._pending[key]=item
        return request

    def pending_requests(self,*,boundary_ms):
        if any(item.boundary_ms!=boundary_ms for item in self._pending.values()):
            raise ValueError('Native pending inventory differs from its completed decision boundary')
        return tuple(item.request for _,item in sorted(self._pending.items()))

    def freeze(self,*,boundary_ms):
        self.pending_requests(boundary_ms=boundary_ms)
        return NativeChannelPendingCapture(self.run_id,self.session_date,self.policy,
            boundary_ms,tuple(sorted(self._pending.items())))

    def capture(self,inherited,*,journal_batch_id):
        root=inherited.snapshot
        if (root['run_id']!=self.run_id or root['session_date']!=self.session_date.isoformat()
                or type(journal_batch_id) is not str or str(UUID(journal_batch_id))!=journal_batch_id
                or UUID(journal_batch_id).int==0):
            raise ValueError('Native pending capture differs from its exact inherited frontier')
        self.pending_requests(boundary_ms=root['boundary_ms'])
        requests,contexts={},{}
        for key,item in sorted(self._pending.items()):
            request=item.request
            decision_id=str(uuid5(NAMESPACE_URL,canonical_json(dict(run_id=self.run_id,
                batch_id=journal_batch_id,owner=key,source_entry=request.acquisition.source_entry_intent_id,
                decision_day_ms=request.decision['decision_day_ms'],source_token=request.source_plan.token,
                policy=self.policy.payload()))))
            columns=['build_id','session_date','ticker','attempt_id','decision_day_ms','pending_exit',
                'quote_day_us','bid_int','ask_int']
            if self.policy.exit_policy.quote.threshold_mode=='below_original_risk_threshold':
                columns+=['original_ask','initial_stop']
            quotes=pl.DataFrame([{name:request.decision[name] for name in columns}]).cast({
                'decision_day_ms':pl.Int64,'quote_day_us':pl.Int64,'bid_int':pl.UInt64,'ask_int':pl.UInt64})
            contexts[decision_id]=dict(prepared=item.prepared,policy=self.policy.exit_policy,quotes=quotes,
                group=item.group,executions=item.executions,original_entry=item.original_entry,
                source_entry_intent_id=request.acquisition.source_entry_intent_id,current_financial=request.financial,
                session_date=self.session_date,run_id=self.run_id,batch_id=journal_batch_id,
                parent_record_id=root['snapshot_id'],decision_record_id=decision_id,
                as_of=market_day_boundary(self.session_date,item.boundary_ms),
                max_rows_per_family=self.policy.max_rows_per_family,expected_conid=item.expected_conid)
            requests[decision_id]=request
        rows=capture_native_channel_manager_rows(inherited,requests,contexts,policy=self.policy)
        return rows,MappingProxyType({key:MappingProxyType(value) for key,value in contexts.items()})

    def complete(self,requests,*,boundary_ms):
        pending=self.pending_requests(boundary_ms=boundary_ms)
        if type(requests) is not tuple or len(requests)!=len(pending) or any(
                supplied is not expected for supplied,expected in zip(requests,pending,strict=True)):
            raise ValueError('Native management completion differs from its exact pending inventory')
        self._pending.clear()
