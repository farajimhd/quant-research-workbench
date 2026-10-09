"""Selected management owner: persisted per-leg outcomes, never aggregate ACKs.

This owner requires the installed entry capability. A requested price or a
nonnull OMS group is not confirmation. The fresh fenced prefix must contain
each exact live stop's effective outcome before reducer state is committed.
"""
from src.trading_runtime.selected_checkpoint_products import observe_owner_stage
from dataclasses import dataclass,fields,is_dataclass
from decimal import Decimal
from weakref import WeakKeyDictionary
from types import MappingProxyType
from collections.abc import Mapping
from datetime import date,datetime
from enum import Enum
from src.trading_runtime.journal_contract import canonical_json

from .backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
from .backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.fixed_structural_lot_state import (
    FixedStructuralLotProtectionState,FixedStructuralLotProtectionTransition,
    open_fixed_structural_lot_protection,advance_fixed_structural_lot_state,
    confirm_fixed_structural_lot_state,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from .backtest_fixed_lot_management_reuse import management_read_scope, prefix_read, group_read, verify_management_entry


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotManagementRequest:
    owner: object
    entry_request: object
    proposal: FixedStructuralLotProtectionTransition
    financial: StrategyOneFinancialView
    intents: tuple
    requested_legs: tuple

    def verify(self,runtime):
        if type(self.owner) is not NativeFixedStructuralLotManagement:
            raise ValueError('Exact installed lot management owner required')
        self.owner.verify_request(self,runtime)


@dataclass(frozen=True,slots=True)
class FixedStructuralLotAckLeg:
    group_id: str
    client_order_id: str
    broker_order_id: str
    request_index: int
    lot_id: str
    command_id: str
    effective_sequence: int
    effective_at: object
    price: float
    outcome_kind: str = 'acknowledged'


@dataclass(frozen=True,slots=True)
class FixedStructuralLotManagementReceipt:
    run_id: str
    through_sequence: int
    group_sequence: int
    legs: tuple[FixedStructuralLotAckLeg,...]
    state: FixedStructuralLotProtectionState


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotManagerCheckpoint:
    """Complete typed owner capture; normalized transport needs own sealing."""
    inherited: object
    selected_positions: tuple
    financials: tuple


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotRecoveryContext:
    """Issued old-command lineage for a new-clock observation, never an ACK."""
    owner: object
    request: FixedStructuralLotManagementRequest
    original_record_id: str
    original_batch_id: str
    original_sequence: int
    original_command: object
    requested_records: tuple
    through_sequence: int


def _small_tree(value):
    if is_dataclass(value):
        return [type(value).__name__,[[f.name,_small_tree(getattr(value,f.name))] for f in fields(value)]]
    if isinstance(value,Mapping):return ['mapping',[[k,_small_tree(v)] for k,v in sorted(value.items())]]
    if type(value) in (tuple,frozenset,list):
        return [type(value).__name__,[_small_tree(v) for v in (sorted(value) if type(value) is frozenset else value)]]
    if type(value) in (date,datetime):return [type(value).__name__,value.isoformat()]
    if type(value) is Decimal:return ['Decimal',str(value)]
    if isinstance(value,Enum):return [type(value).__name__,_small_tree(value.value)]
    if value is None or type(value) in (int,float,str,bool):return [type(value).__name__,value]
    raise ValueError('Unsupported selected management decision scalar: '+type(value).__name__)

def _request_content(request):
    """Freeze only the small selected decision, not whole market products."""
    return canonical_json(_small_tree((request.proposal,request.financial,request.intents,request.requested_legs)))


def _checkpoint_content(checkpoint):
    return canonical_json(_small_tree((checkpoint.inherited,checkpoint.selected_positions,checkpoint.financials)))


def _recovery_content(context):
    return canonical_json([_request_content(context.request),context.original_record_id,
        context.original_batch_id,context.original_sequence,context.original_command.payload(),
        [[r.record_id,r.run_id,r.sequence,r.event_time.isoformat(),r.account_id,r.payload]
         for r in context.requested_records],context.through_sequence])


def require_recovery_client(client,context):
    from src.trading_runtime.fixed_structural_lot_profile import require_fixed_structural_lot_profile
    if type(context) is not FixedStructuralLotRecoveryContext:
        raise ValueError('Exact selected recovery context required')
    profile=require_fixed_structural_lot_profile(getattr(client,'fixed_structural_lot_profile',None))
    context.owner.require_recovery(context,revalidate=False)
    if profile.operation.source is not context.request.entry_request.source or context.owner.client is not client:
        raise ValueError('Recovery client has foreign selected operation/configuration')
    return profile


def recovery_contexts_by_batch(run_id,contexts,*,max_commits=100_000):
    from uuid import UUID
    if type(contexts) is not tuple or len(contexts)>max_commits:
        raise ValueError('Selected recovery context inventory is unbounded')
    result={}
    for item in contexts:
        if type(item) is not tuple or len(item)!=2 or type(item[0]) is not str:
            raise ValueError('Selected recovery batch binding is malformed')
        batch_id,context=item
        from src.trading_runtime.fixed_structural_lot_cold_recovery import (
            FixedStructuralLotColdRecoveryContext,require_cold_recovery_context)
        if type(context) is FixedStructuralLotColdRecoveryContext:
            require_cold_recovery_context(context,run_id=run_id,batch_id=batch_id)
            if batch_id in result:raise ValueError('Duplicate cold recovery batch')
            result[batch_id]=context
            continue
        if str(UUID(batch_id))!=batch_id or type(context) is not FixedStructuralLotRecoveryContext:
            raise ValueError('Selected recovery batch identity differs')
        context.owner.require_recovery(context,revalidate=False)
        if (context.request.entry_request.source.run_id!=run_id
                or batch_id not in context.owner._recovery_batches.get(context,{}) or batch_id in result):
            raise ValueError('Selected recovery context has foreign/unissued/duplicate batch')
        result[batch_id]=context
    return result


class NativeFixedStructuralLotManagement:
    from .backtest_fixed_lot_management_reuse import management_owner_constructor

    @management_owner_constructor
    def __init__(self,*,operation,publisher,client):
        if (type(operation) is not NativeFixedStructuralLotOperation
                or type(publisher) is not BacktestTypedJournalPublisher
                or publisher.writer._client is not client
                or publisher._fixed_lot_source is not operation.source):
            raise ValueError('Selected management needs its actual bound source/publisher/client')
        operation.source.require_installed_admission()
        self.operation=operation
        self.publisher=publisher
        self.client=client
        self._states={}
        self.entries={}
        self.entry_rejections={}
        self.groups={}
        self.financials={}
        self._requests=WeakKeyDictionary()
        self._management_reads=WeakKeyDictionary()
        self._recoveries=WeakKeyDictionary()
        from .backtest_fixed_lot_management_reuse import issue_management_owner
        issue_management_owner(self)
        self._recovery_observations=WeakKeyDictionary()
        self._recovery_executions=WeakKeyDictionary()
        self._recovery_records=WeakKeyDictionary()
        self._recovery_batches=WeakKeyDictionary()
        self._request_recoveries=WeakKeyDictionary()
        self._checkpoints=WeakKeyDictionary()

    @property
    def states(self):
        return MappingProxyType(self._states)

    @observe_owner_stage('selected_prefix_read')
    def _prefix(self):
        return prefix_read(self, self._load_prefix)

    def _load_prefix(self):
        from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
        source=self.operation.source
        source.require_installed_admission()
        contexts=tuple(getattr(self.client,'fixed_structural_lot_contexts',()))
        if any(v.source is not source for v in contexts):
            raise ValueError('Selected management lacks exact committed entry contexts')
        from src.trading_runtime.fixed_structural_lot_warm_proof import selected, load_prefix
        if selected(source) and getattr(self.client,'backtest_v4_lease',None) is not None:
            return load_prefix(self.client,source.run_id),contexts
        prefix=load_verified_v4_prefix(self.client,source.run_id,fixed_lot_contexts=contexts,
            fixed_lot_recovery_contexts=tuple(getattr(self.client,'fixed_lot_recovery_contexts',())))
        return prefix,contexts

    def _arguments(self,request,prefix,contexts):
        request.verify()
        return dict(client=self.client,prefix=prefix,intervals=None,intent=request.intent,
            strategy_identity=(request.strategy_id,request.revision),entry_request=request,
            fixed_lot_contexts=contexts)

    @observe_owner_stage('selected_oms_read')
    def _group(self,request,prefix,contexts,group_id):
        return group_read(self,request,prefix,contexts,group_id,
            lambda: self._load_group(request,prefix,contexts,group_id))

    def _load_group(self,request,prefix,contexts,group_id):
        from src.trading_runtime.fixed_structural_lot_warm_proof import load_oms_groups
        groups=load_oms_groups(self.client,prefix,
            allowed_accounts=frozenset((request.entry.proposal.account_id,)),
            strategy_identity=(request.strategy_id,request.revision),fixed_lot_contexts=contexts)
        matched=tuple(v for v in groups if v.group['strategy_intent_id']==request.intent.intent_id)
        if len(matched)!=1 or matched[0].group['group_id']!=group_id:
            raise ValueError('Selected management original group is missing or ambiguous')
        return matched[0]

    @staticmethod
    def _legs(group):
        return tuple((group.group['group_id'],group.orders[v['request_index']].cOID,
            v['broker_order_id'],v['request_index'],v['slice_id'])
            for v in group.broker_bindings
            if v['role']=='protective_stop' and v['terminal']==0)

    def open(self,entry_request,*,group_id,boundary_ms=None):
        prefix,contexts=self._prefix()
        args=self._arguments(entry_request,prefix,contexts)
        self._group(entry_request,prefix,contexts,group_id)
        from src.trading_runtime.fixed_structural_lot_causal_clock import selected_clock
        state=open_fixed_structural_lot_protection(entry_request.entry,group_id=group_id,**args,
            **({'now_ms':boundary_ms} if selected_clock(entry_request) else {}))
        key=(entry_request.entry.proposal.account_id,entry_request.entry.proposal.assignment_id,
             entry_request.entry.proposal.ticker)
        if key in self.states:raise ValueError('Selected management position already open')
        self._states[key]=state
        self.entries[key]=entry_request
        self.groups[key]=group_id
        return state

    def register_entry(self,request,group_id):
        request.verify();request.source.require_installed_admission()
        if request.source is not self.operation.source or type(group_id) is not str or not group_id:
            raise ValueError('Selected manager entry owner differs')
        key=(request.entry.proposal.account_id,request.entry.proposal.assignment_id,request.entry.proposal.ticker)
        if key in self.entries:raise ValueError('Selected manager cannot overwrite an entry')
        self.entries[key]=request;self.groups[key]=group_id

    async def first_held(self,key,*,boundary_ms=None):
        self.publisher.enqueue_pending();await self.publisher.await_fence()
        request=self.entries[key]
        # open checks complete actual fills/acquisition ownership at the fence.
        return self.open(request,group_id=self.groups[key],boundary_ms=boundary_ms)

    async def retire(self,key):
        from src.trading_runtime.fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
        from src.trading_runtime.selected_checkpoint_products import selected,closing_checkpoint_read
        self.publisher.enqueue_pending();await self.publisher.await_fence()
        prefix,contexts=self._prefix();request=self.entries[key]
        if selected(request.source):
            closing=closing_checkpoint_read(self.client,prefix,entry_request=request,contexts=contexts)
            if closing is not None:
                if closing.aggregate_remaining or closing.cancellation_pending or closing.exit_pending:
                    raise ValueError('Selected manager cannot retire unresolved closing obligations')
                self._states.pop(key,None);self.entries.pop(key);self.groups.pop(key)
                self.financials.pop(key,None)
                return
        roster=load_fixed_structural_lot_stop_ceiling(**self._arguments(request,prefix,contexts),
            entry=request.entry,group_id=self.groups[key])
        if roster.ceiling is not None or roster.acquiring or any(q for _,q in roster.remaining):
            raise ValueError('Selected manager cannot retire unresolved lot ownership')
        self._states.pop(key,None);self.entries.pop(key);self.groups.pop(key)

    def observe_checkpoint_financial(self,financial):
        from src.trading_runtime.selected_checkpoint_products import selected
        if not selected(self.operation.source):
            return
        if type(financial) is not StrategyOneFinancialView:
            raise ValueError('Selected checkpoint needs exact received financial view')
        key=(financial.account_id,financial.assignment_id,financial.ticker)
        if key in self._states:
            request=self.entries[key]
            verify_management_entry(self,request)
            if request.source is not self.operation.source:
                raise ValueError('Selected financial observation has foreign source')
            self.financials[key]=financial

    @observe_owner_stage('selected_propose')
    @management_read_scope
    def propose(self,entry_request,financial,**inputs):
        from src.trading_runtime.strategy_one_protection_intent import strategy_one_protection_intents
        if type(financial) is not StrategyOneFinancialView:
            raise ValueError('Exact current financial view required')
        key=(financial.account_id,financial.assignment_id,financial.ticker)
        entry=entry_request.entry.proposal
        if key!=(entry.account_id,entry.assignment_id,entry.ticker) or key not in self.states:
            raise ValueError('Selected protection has foreign assignment')
        prefix,contexts=self._prefix()
        args=self._arguments(entry_request,prefix,contexts)
        proposal=advance_fixed_structural_lot_state(self.states[key],**args,**inputs)
        if Decimal(str(financial.position_quantity))!=sum((q for _,q in proposal.proposed.roster.remaining),Decimal(0)):
            raise ValueError('Selected protection financial quantity differs from actual residuals')
        intents=strategy_one_protection_intents(proposal.previous.protection,proposal.transition,financial,
            session_date=entry_request.source.session_date,bid=inputs['bid'],ask=inputs['ask'],
            strategy_number=entry_request.revision,stop_ceiling=float(proposal.proposed.roster.ceiling))
        group=self._group(entry_request,prefix,contexts,proposal.previous.roster.group_id)
        legs=self._legs(group)
        if intents and not legs:raise ValueError('Selected protection has no live stop legs')
        request=FixedStructuralLotManagementRequest(self,entry_request,proposal,financial,intents,legs)
        self._requests[request]=(entry_request,_request_content(request),prefix.last_sequence,prefix.last_batch_id,prefix.source_cursor)
        return request

    @observe_owner_stage('selected_owner_verify')
    @management_read_scope
    def verify_request(self,request,runtime):
        self._verify_issued_request(request)
        binding=self._requests[request]
        source=request.entry_request.source
        source.require_installed_admission()
        if (runtime.journal is not self.publisher.journal or runtime.run_id!=source.run_id
                or runtime.config.strategy_id!=request.entry_request.strategy_id
                or runtime.config.strategy_revision!=request.entry_request.revision
                or runtime.config.anchor_date!=source.session_date
                or request.financial.account_id not in runtime.config.account_ids):
            raise ValueError('Selected management runtime/source identity differs')
        prefix,contexts=self._prefix()
        if prefix.last_sequence!=binding[2] or self.publisher.journal.latest_sequence(source.run_id)!=prefix.last_sequence:
            raise ValueError('Selected management request has stale unfenced execution prefix')
        group=self._group(request.entry_request,prefix,contexts,request.proposal.previous.roster.group_id)
        if self._legs(group)!=request.requested_legs:
            raise ValueError('Selected management live requested legs changed')

    def _verify_issued_request(self,request):
        if type(request) is not FixedStructuralLotManagementRequest or request.owner is not self:
            raise ValueError('Exact own selected management request required')
        binding=self._requests.get(request)
        if binding is None or binding[0] is not request.entry_request or binding[1]!=_request_content(request):
            raise ValueError('Selected management request was not issued by its owner')

    def prepare_recovery(self,request,*,original_record_id):
        """Reload the complete old command and every requested leg at a fence.

        The new request must independently qualify at its fresh clock. This
        method authorizes no modification and creates no acknowledgement.
        """
        from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
        from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
        from uuid import UUID
        self._verify_issued_request(request)
        prefix,contexts=self._prefix()
        if prefix.last_sequence!=self._requests[request][2] or len(request.intents)!=1:
            raise ValueError('Recovery requires one fresh issued command at its exact fence')
        source=request.entry_request.source
        key=(request.financial.account_id,request.financial.assignment_id,request.financial.ticker)
        if (source is not self.operation.source or self.publisher._fixed_lot_source is not source
                or prefix.run_id!=source.run_id or self.entries.get(key) is not request.entry_request
                or (request.entry_request.strategy_id,request.entry_request.revision)!=
                   (source._strategy_id,source._revision)):
            raise ValueError('Recovery has foreign source/configuration/assignment authority')
        from src.trading_runtime.arte_oms_projection import load_committed_oms_admission_page
        group=self._group(request.entry_request,prefix,contexts,request.proposal.previous.roster.group_id)
        admission=load_committed_oms_admission_page(self.client,prefix,(group,))[group.sequence]
        if (group.group['strategy_id']!=request.entry_request.strategy_id
                or int(group.group['strategy_revision'])!=request.entry_request.revision
                or group.group['account_id']!=key[0]
                or admission['assignment_id']!=key[1]
                or admission['intent_id']!=request.entry_request.intent.intent_id
                or admission['account_id']!=key[0]):
            raise ValueError('Recovery has foreign normalized strategy/Portfolio assignment')
        rows=load_committed_strategy_intent_page(self.client,prefix,
            record_ids=(str(UUID(original_record_id)),),limit=1)
        if len(rows)!=1:
            raise ValueError('Recovery lacks its exact committed original command')
        old=rows[0];command=request.intents[0]
        if (old.account_id!=request.financial.account_id
                or old.sequence>=prefix.last_sequence
                or old.intent.intent_id==command.intent_id
                or old.intent.event_time>=command.event_time
                or (old.intent.ticker,old.intent.action,old.intent.invalidation_price,
                    old.intent.profit_target_price,old.intent.reason)!=
                   (command.ticker,'replace_protective_stop',command.invalidation_price,
                    None,command.reason)
                or old.intent.metadata):
            raise ValueError('Recovery substituted command identity, price or causal clock')
        history=load_complete_typed_protection_history(self.client,prefix,fixed_lot_contexts=contexts)
        requested=[]
        for group_id,client_id,broker_id,index,lot in request.requested_legs:
            matches=tuple(r for r in history.records if r.account_id==old.account_id
                and r.run_id==source.run_id
                and r.payload.get('strategy_id')==request.entry_request.strategy_id
                and r.payload.get('strategy_revision')==request.entry_request.revision
                and r.payload.get('order_group_id')==group_id
                and r.payload.get('client_order_id')==client_id
                and r.payload.get('order_id')==broker_id
                and r.payload.get('source_intent_id')==request.entry_request.intent.intent_id
                and r.payload.get('intent_id')==old.intent.intent_id
                and r.payload.get('action')=='replace_protective_stop'
                and r.payload.get('kind')=='stop' and r.payload.get('phase')=='requested'
                and r.payload.get('price')==old.intent.invalidation_price)
            if (len(matches)!=1 or not old.sequence<matches[0].sequence<=prefix.last_sequence
                    or matches[0].event_time!=old.intent.event_time):
                raise ValueError('Recovery lacks exact original requested protection lineage')
            requested.append(matches[0])
        context=FixedStructuralLotRecoveryContext(self,request,old.record_id,old.batch_id,
            old.sequence,old.intent,tuple(requested),prefix.last_sequence)
        self._recoveries[context]=(request,request.entry_request,_recovery_content(context))
        return context

    def require_recovery(self,context,*,revalidate=True):
        if type(context) is not FixedStructuralLotRecoveryContext or context.owner is not self:
            raise ValueError('Exact selected recovery context required')
        stored=self._recoveries.get(context)
        if stored is None or stored!=(context.request,context.request.entry_request,_recovery_content(context)):
            raise ValueError('Selected recovery context was not issued or was altered')
        context.request.entry_request.verify()
        if context.request.entry_request.source is not self.operation.source:
            raise ValueError('Selected recovery has foreign prepared source/configuration')
        context.request.entry_request.source.require_installed_admission()
        if revalidate:
            prefix,contexts=self._prefix()
            if prefix.run_id!=context.request.entry_request.source.run_id or prefix.last_sequence<context.through_sequence:
                raise ValueError('Selected recovery source/frontier regressed')
        return context

    @staticmethod
    def _broker_fact(order):
        return (str(order.orderId),order.account,order.cOID,order.ticker,order.conid,
            order.side,order.orderType,float(order.auxPrice or 0),float(order.filledQuantity),
            float(order.remainingQuantity),order.order_status)

    async def begin_recovery(self,context,runtime):
        """Take independent actual readback under the fresh public actor lane."""
        self.require_recovery(context)
        context.request.verify(runtime)
        if runtime.broker is not runtime.order_manager.broker or runtime.order_manager.journal is not self.publisher.journal:
            raise ValueError('Recovery has foreign actual broker/OMS transport')
        observed=tuple(await runtime.broker.live_orders())
        facts=tuple(self._broker_fact(order) for order in observed)
        if len({fact[0] for fact in facts})!=len(facts):
            raise ValueError('Recovery broker readback repeats an order identity')
        self._recovery_observations[context]=(runtime.order_manager,facts)
        self._recovery_records[context]={}
        self._request_recoveries[context.request]=context

    def bind_recovery_execution(self,context,runtime,source_intent,approved,decision):
        """Bind the actual Portfolio normalization before the first OMS effect."""
        from dataclasses import replace
        from src.trading_runtime.fixed_structural_lot_entry_v4 import _exact
        from src.trading_runtime.portfolio import PortfolioDecision,PortfolioDecisionStatus,_intent_correlation
        self.require_recovery(context)
        capture=self._recovery_observations.get(context)
        account=context.request.financial.account_id
        assignment=context.request.financial.assignment_id
        if (capture is None or capture[0] is not runtime.order_manager
                or runtime.portfolio.journal is not self.publisher.journal
                or not any(_exact(source_intent,v) for v in context.request.intents)
                or type(decision) is not PortfolioDecision
                or decision.status not in (PortfolioDecisionStatus.APPROVED,PortfolioDecisionStatus.RESIZED)):
            raise ValueError('Recovery lacks its actual Portfolio command decision')
        state=runtime.portfolio.states[account]
        policy=runtime.portfolio._policy(state)
        reservation=runtime.portfolio.reservations.get(decision.reservation_id)
        if (reservation is None or reservation.decision_id!=decision.decision_id
                or reservation.intent_id!=source_intent.intent_id or reservation.account_id!=account
                or reservation.assignment_id!=assignment or reservation.strategy_id!=runtime.config.strategy_id
                or reservation.ticker!=source_intent.ticker or reservation.action!=source_intent.action
                or reservation.quantity!=decision.approved_quantity
                or decision.account_id!=account or decision.account_key!=state.profile.account_key
                or (decision.policy_id,decision.policy_revision)!=(policy.policy_id,policy.revision)
                or decision.requested_quantity!=float(source_intent.quantity)
                or decision.approved_quantity!=float(source_intent.quantity)):
            raise ValueError('Recovery Portfolio decision/reservation differs from its complete owned position')
        base=replace(source_intent,metadata={**source_intent.metadata,'assignment_id':assignment})
        expected=replace(base,quantity=decision.approved_quantity,metadata={**base.metadata,
            'portfolio_account_key':state.profile.account_key,'portfolio_decision_id':decision.decision_id,
            'unprotected_backtest_authorized':base.metadata.get('contract') in runtime.portfolio.unprotected_backtest_contracts,
            'portfolio_policy':policy.identity,'portfolio_reservation_id':decision.reservation_id,
            'requested_quantity':decision.requested_quantity,'portfolio_fx_to_base':float(base.metadata.get('fx_to_base') or (1.0 if str(base.metadata.get('currency') or state.profile.base_currency).upper()==state.profile.base_currency else 0.0)),
            'correlation_id':_intent_correlation(runtime.run_id,base),'causation_id':decision.decision_id})
        if not _exact(expected,approved):
            raise ValueError('Recovery normalized command differs from its exact Portfolio authority')
        existing=self._recovery_executions.get(context)
        if existing is not None and not _exact(existing,approved):
            raise ValueError('Recovery execution command was already bound differently')
        self._recovery_executions[context]=approved

    def verify_recovery_execution(self,context,intent):
        from src.trading_runtime.fixed_structural_lot_entry_v4 import _exact
        self.require_recovery(context)
        approved=self._recovery_executions.get(context)
        if approved is None or not _exact(approved,intent):
            raise ValueError('Recovery has foreign normalized Portfolio command')

    def verify_recovery_roster(self,context,manager,intent,prepared):
        from src.trading_runtime.fixed_structural_lot_entry_v4 import _exact
        self.require_recovery(context)
        capture=self._recovery_observations.get(context)
        if capture is None or capture[0] is not manager or not _exact(intent,self._recovery_executions.get(context)):
            raise ValueError('Recovery has foreign actor or issued command')
        legs=[]
        for group,stops in prepared:
            for order,index,lot in stops:
                if self._broker_fact(order) not in capture[1] or float(order.auxPrice)>intent.invalidation_price:
                    raise ValueError('Recovery broker frontier changed before any effect')
                legs.append((group.group_id,group.orders[index].cOID,str(order.orderId),index,lot))
        if tuple(legs)!=context.request.requested_legs:
            raise ValueError('Recovery lacks its complete live owned protection roster')

    def record_recovery_readback(self,context,manager,intent,group,request,order,lot):
        """Persist an observation with old lineage; never manufacture a reply."""
        from src.trading_runtime.fixed_structural_lot_entry_v4 import _exact
        self.require_recovery(context)
        capture=self._recovery_observations.get(context)
        if (capture is None or capture[0] is not manager or not _exact(intent,self._recovery_executions.get(context))
                or self._broker_fact(order) not in capture[1]
                or (group.group_id,request.cOID,str(order.orderId),
                    group.broker_order_request_indexes[str(order.orderId)],lot) not in context.request.requested_legs
                or float(order.auxPrice)!=intent.invalidation_price):
            raise ValueError('Recovery readback differs from its actual source/owned leg')
        payload=dict(order_group_id=group.group_id,required_quantity=float(order.remainingQuantity),
            protected_quantity=float(order.remainingQuantity),status='repaired',ticker=intent.ticker,
            action=intent.action,intent_id=intent.intent_id,strategy_id=manager.strategy_id,
            strategy_revision=manager.strategy_revision,correlation_id=intent.intent_id,
            causation_id=context.original_record_id,actions=[dict(action='readback_protective_stop',
                order_id=str(order.orderId),quantity=float(order.remainingQuantity),
                stop_price=float(order.auxPrice),from_stop=float(request.auxPrice or 0),
                to_stop=float(order.auxPrice),response=[dict(observed_order_id=str(order.orderId),
                    observed_order_status=order.order_status.value,observed_local_order_id=order.cOID,
                    observed_account_id=order.account,observed_conid=order.conid)])])
        record=manager.journal.append(run_id=manager.run_id,category='order_management',
            entity_type='protection_reconciliation',entity_id=group.group_id,
            account_id=group.account_id,event_time=intent.event_time,payload=payload)
        self._recovery_records[context][record.record_id]=(record,canonical_json([
            record.run_id,record.sequence,record.account_id,record.event_time.isoformat(),record.payload]))
        manager.journal.bind_fixed_lot_recovery_record(record,context)
        return record

    def verify_recovery_record(self,context,record):
        self.require_recovery(context,revalidate=False)
        expected=self._recovery_records.get(context,{}).get(record.record_id)
        if expected is None or expected[1]!=canonical_json([
                record.run_id,record.sequence,record.account_id,record.event_time.isoformat(),record.payload]):
            raise ValueError('Recovery record was not emitted from its independent actual readback')

    def verify_recovery_graph(self,context,parents,actions,replies,events,*,run_id,batch_id):
        from src.trading_runtime.arte_protection_reconciliation_v4 import project_protection_reconciliation_v4
        from src.trading_runtime.arte_journal_writer import _canonical_typed_content,_typed_content_fields,_datetime_wire
        from src.trading_runtime.fixed_structural_lot_entry_v4 import _exact
        self.require_recovery(context,revalidate=False)
        if run_id!=context.request.entry_request.source.run_id:
            raise ValueError('Recovery graph has foreign run/configuration')
        own=tuple(reply for reply in replies if reply['reply_kind']=='broker_readback')
        action_ids={reply['parent_record_id'] for reply in own}
        own_actions=tuple(action for action in actions if action['record_id'] in action_ids)
        parent_ids={action['parent_record_id'] for action in own_actions}
        if len(parent_ids)!=1:
            raise ValueError('Recovery batch must carry one exact observation')
        identity=next(iter(parent_ids))
        stored=self._recovery_records.get(context,{}).get(identity)
        if stored is None:
            raise ValueError('Recovery graph has no independently issued observation')
        raw=stored[0]
        if raw.sequence<=context.through_sequence or raw.event_time<=context.original_command.event_time:
            raise ValueError('Recovery graph has old/future substituted command frontier')
        event=next((event for event in events if event['record_id']==identity),None)
        if event is None or event['causation_id']!=context.original_record_id:
            raise ValueError('Recovery graph lost its original committed command parent')
        expected=project_protection_reconciliation_v4(raw,attempt_id=event['attempt_id'],
            batch_id=batch_id,recovery_context=context)
        actual=(event,next(parent for parent in parents if parent['record_id']==identity),own_actions,own)
        from src.trading_runtime.arte_protection_reconciliation_v4 import RECONCILIATION,ACTION,REPLY
        contracts=('trading_event_v1',RECONCILIATION.name,ACTION.name,REPLY.name)
        def canonical_row(name,row):
            # Only DateTime64 columns declared UTC may decode stored wire values.
            content={k:v for k,v in row.items() if k!='content_hash'}
            for column,conversion,nullable,parameter in _typed_content_fields(name):
                value=content[column]
                if conversion=='datetime' and type(value) is str and '+' not in value and not value.endswith('Z'):
                    content[column]=_datetime_wire(value,parameter,stored_utc=True)+'+00:00'
            return _canonical_typed_content(name,content)
        for ordinal,name in enumerate(contracts):
            left=(actual[ordinal],) if ordinal<2 else actual[ordinal]
            right=(expected[ordinal],) if ordinal<2 else expected[ordinal]
            if not _exact(tuple(canonical_row(name,row) for row in left),
                    tuple(canonical_row(name,row) for row in right)):
                raise ValueError('Recovery graph differs from actual independent broker outcome')

    def bind_recovery_batch(self,context,unit):
        from uuid import NAMESPACE_URL,uuid5
        self.verify_recovery_graph(context,(unit.reconciliation,),unit.actions,unit.replies,
            unit.base.events,run_id=unit.base.run_id,batch_id=unit.base.batch_id)
        identity=unit.base.events[0]['record_id']
        raw=self._recovery_records[context][identity][0]
        if (unit.base.attempt_id!=self.publisher.attempt_id
                or unit.base.batch_id!=str(uuid5(NAMESPACE_URL,
                    f'arte-backtest-v1:{raw.run_id}:{self.publisher.attempt_id}:{raw.sequence}:{raw.record_id}'))):
            raise ValueError('Recovery batch substituted its actual native source identity')
        issued=self._recovery_batches.setdefault(context,{})
        expected=(identity,unit.base.first_sequence,unit.base.last_sequence)
        if unit.base.batch_id in issued and issued[unit.base.batch_id]!=expected:
            raise ValueError('Recovery batch identity conflicts with its original observation')
        issued[unit.base.batch_id]=expected

    def verify_deferral_source(self,request,source_batch,source_intent):
        """Bind a failed command to the actual original or committed source unit."""
        from datetime import datetime,timezone
        from uuid import NAMESPACE_URL,uuid5
        from src.trading_runtime.fixed_structural_lot_entry_v4 import _exact
        from src.trading_runtime.arte_journal_projection import project_journal_record
        self._verify_issued_request(request)
        source=request.entry_request.source
        if not any(_exact(command,source_intent) for command in request.intents):
            raise ValueError('Selected deferral substituted its owned command')
        committed=self.publisher._committed_strategy_intents.get(source_intent.intent_id)
        if committed is not None:
            if committed[0] is not source_batch or not _exact(committed[1],source_intent):
                raise ValueError('Selected deferral substituted its committed command unit')
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_commit_v4,prepare_commit_v4
            from src.trading_runtime.arte_journal_writer import _sealed_families
            actual,_=load_verified_commit_v4(self.client,run_id=source.run_id,batch_id=source_batch.batch_id)
            base,families=_sealed_families(source_batch)
            expected,_=prepare_commit_v4(run_id=source_batch.run_id,run_month=source_batch.run_month,
                attempt_id=source_batch.attempt_id,batch_id=source_batch.batch_id,
                prior_batch_id=source_batch.prior_batch_id,first_sequence=source_batch.first_sequence,
                last_sequence=source_batch.last_sequence,source_cursor=source_batch.source_cursor,
                status=source_batch.status,sealed_families=(*base,*families),committed_at=datetime.now(timezone.utc))
            if expected['content_hash']!=actual['content_hash']:
                raise ValueError('Selected deferral committed command content differs')
            return
        binding=self._requests[request]
        raw=tuple(r for r in self.publisher.journal.unfenced_records()
            if (r.category,r.entity_type,r.entity_id)==('strategy','strategy_intent',source_intent.intent_id))
        if len(raw)!=1 or raw[0].sequence!=binding[2]+1:
            raise ValueError('Selected deferral lacks its original sequential command source')
        record=raw[0]
        if (not _exact(self.publisher.journal.strategy_one_protection_for_record(record.record_id),source_intent)
                or record.payload!={**source_intent.payload(),'strategy_id':request.entry_request.strategy_id,
                                     'strategy_revision':request.entry_request.revision}):
            raise ValueError('Selected deferral original command fields differ')
        expected=project_journal_record(record,run_month=self.publisher.run_month,
            attempt_id=self.publisher.attempt_id,batch_id=str(uuid5(NAMESPACE_URL,
                f'arte-backtest-v1:{record.run_id}:{self.publisher.attempt_id}:{record.sequence}:{record.record_id}')),
            prior_batch_id=binding[3],source_cursor=binding[4],
            expected_config=self.publisher.expected_config,expected_mode='backtest')
        if not _exact(expected,source_batch):
            raise ValueError('Selected deferral substituted original command batch/content')

    @observe_owner_stage('selected_confirm')
    @management_read_scope
    async def confirm(self,request):
        """Read exact committed effects; failure retains the prior owner state."""
        from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history,_journal_instant
        self._verify_issued_request(request)
        self.publisher.enqueue_pending()
        await self.publisher.await_fence()
        prefix,contexts=self._prefix()
        group=self._group(request.entry_request,prefix,contexts,request.proposal.previous.roster.group_id)
        if self._legs(group)!=request.requested_legs:
            raise RuntimeError('Selected protection outcomes require reconciliation of changed legs')
        recovery=self._request_recoveries.get(request)
        from src.trading_runtime.empty_protection_confirmation_policy import (
            installed_empty_protection_confirmation_policy, requires_protection_ack_history)
        empty_policy=installed_empty_protection_confirmation_policy(request.entry_request.source)
        history=(load_complete_typed_protection_history(self.client,prefix,fixed_lot_contexts=contexts)
            if requires_protection_ack_history(empty_policy,command_count=len(request.intents),
                recovery_pending=recovery is not None) else None)
        readback_events={}
        if recovery is not None:
            from src.trading_runtime.arte_journal_reader import load_typed_event_page
            self.require_recovery(recovery,revalidate=False)
            stored=self._recovery_records.get(recovery,{})
            selected=tuple(sorted(row[0].sequence for row in stored.values()))
            if selected:
                events=load_typed_event_page(self.client,prefix,after_sequence=selected[0]-1,
                    selected_sequences=selected,limit=len(selected),fixed_lot_contexts=contexts)
                readback_events={item.event['record_id']:item for item in events}
        legs=[]
        for command in request.intents:
            for group_id,client_id,broker_id,index,lot in request.requested_legs:
                rows=tuple(r for r in history.records if r.category=='protection'
                    and r.account_id==request.financial.account_id
                    and r.payload.get('order_group_id')==group_id
                    and r.payload.get('client_order_id')==client_id
                    and r.payload.get('order_id')==broker_id
                    and r.payload.get('source_intent_id')==request.entry_request.intent.intent_id
                    and r.payload.get('intent_id')==command.intent_id
                    and r.payload.get('phase')=='effective'
                    and r.payload.get('action')=='replace_protective_stop'
                    and r.payload.get('kind')=='stop'
                    and r.payload.get('price')==command.invalidation_price)
                if recovery is not None:
                    matching=tuple(raw for raw,_ in self._recovery_records[recovery].values()
                        if raw.payload['actions'][0]['order_id']==broker_id)
                    if len(matching)==1:
                        raw=matching[0];actual=readback_events.get(raw.record_id)
                        if (actual is None or actual.event['causation_id']!=recovery.original_record_id
                                or actual.event['sequence']!=raw.sequence
                                or raw.event_time!=command.event_time or not raw.sequence<group.sequence
                                or raw.event_time>_journal_instant(group.group['updated_at'])
                                or group.orders[index].auxPrice!=command.invalidation_price):
                            raise RuntimeError('Selected recovery lacks its exact fresh-clock observation')
                        self.verify_recovery_record(recovery,raw)
                        legs.append(FixedStructuralLotAckLeg(group_id,client_id,broker_id,index,lot,
                            command.intent_id,raw.sequence,raw.event_time,float(command.invalidation_price),
                            'broker_readback'))
                        continue
                if (len(rows)!=1 or not rows[0].sequence<group.sequence
                        or rows[0].event_time!=command.event_time
                        or rows[0].event_time>_journal_instant(group.group['updated_at'])
                        or group.orders[index].auxPrice!=command.invalidation_price):
                    raise RuntimeError('Selected protection lacks an exact per-leg effective outcome')
                legs.append(FixedStructuralLotAckLeg(group_id,client_id,broker_id,index,lot,
                    command.intent_id,rows[0].sequence,rows[0].event_time,float(command.invalidation_price)))
        state=confirm_fixed_structural_lot_state(request.proposal,
            **self._arguments(request.entry_request,prefix,contexts),stop_confirmed=bool(request.intents))
        key=(request.financial.account_id,request.financial.assignment_id,request.financial.ticker)
        self._states[key]=state
        self.financials[key]=request.financial
        self._requests.pop(request,None)
        self._request_recoveries.pop(request,None)
        return FixedStructuralLotManagementReceipt(prefix.run_id,prefix.last_sequence,group.sequence,tuple(legs),state)

    def capture(self,manager,*,boundary_ms):
        from dataclasses import replace
        from .backtest_strategy_one_management import StrategyOneManagementRunner
        from src.trading_runtime.fixed_structural_lot_snapshot import project_fixed_structural_lot_snapshot
        from src.trading_runtime.selected_checkpoint_products import selected,closing_checkpoint_read,checkpoint_roster
        if type(manager) is not StrategyOneManagementRunner or manager._fixed_lot_owner is not self:
            raise ValueError('Checkpoint requires the actual bound selected manager')
        inherited=manager.capture_state(boundary_ms=boundary_ms)
        prefix,contexts=self._prefix()
        if set(self.states)!=set(manager._positions) or set(self.financials)!=set(self.states):
            raise ValueError('Selected checkpoint lacks complete owner financial/position state')
        rows=[];captured_states=[];captured_positions=dict(inherited.positions)
        for key,state in sorted(self.states.items()):
            if manager._positions[key]!=state.protection:
                raise ValueError('Selected checkpoint manager protection differs')
            request=self.entries[key]
            if selected(request.source):
                closing=closing_checkpoint_read(self.client,prefix,entry_request=request,contexts=contexts)
                if closing is not None:
                    fresh=checkpoint_roster(**self._arguments(request,prefix,contexts),
                        entry=state.entry,group_id=state.roster.group_id)
                    state=replace(state,roster=fresh,
                        protection=replace(state.protection,boundary_ms=boundary_ms))
                    captured_positions[key]=state.protection
            captured_states.append((key,state))
            rows.append((key,project_fixed_structural_lot_snapshot(state,
                **self._arguments(request,prefix,contexts))))
        # Highs, first-held clocks, closed/reentry facts and pending breaks
        # remain in the original complete manager capture. Profit-arm refs
        # are ephemeral, just as in the default owner; cold needs a new arm.
        inherited=replace(inherited,positions=tuple(sorted(captured_positions.items())))
        checkpoint=FixedStructuralLotManagerCheckpoint(inherited,tuple(rows),tuple(sorted(self.financials.items())))
        from datetime import datetime,timedelta,timezone
        from zoneinfo import ZoneInfo
        instant=(datetime.combine(self.operation.source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
            +timedelta(hours=4,milliseconds=boundary_ms)).astimezone(timezone.utc)
        captures=tuple(manager.runtime.portfolio.capture_recovery_snapshot(account,
            state_revision=prefix.last_sequence,snapshot_at=instant)
            for account in sorted(manager.runtime.portfolio.states))
        self._checkpoints[checkpoint]=(_checkpoint_content(checkpoint),tuple(captured_states),
            self.operation.source,prefix.last_sequence,prefix.last_batch_id,prefix.source_cursor,captures)
        return checkpoint

    def require_checkpoint(self,checkpoint):
        if type(checkpoint) is not FixedStructuralLotManagerCheckpoint:
            raise ValueError('Exact owner-issued selected manager checkpoint required')
        binding=self._checkpoints.get(checkpoint)
        if (binding is None or binding[0]!=_checkpoint_content(checkpoint)
                or binding[2] is not self.operation.source):
            raise ValueError('Selected manager checkpoint was not issued or its capture changed')
        self.operation.source.require_installed_admission()
        return binding

    def rebind_checkpoint(self,checkpoint,*,checkpoint_sequence,journal_batch_id):
        """Re-certify the frozen economic image at the new committed cursor."""
        from dataclasses import replace
        from src.trading_runtime.fixed_structural_lot_snapshot import project_fixed_structural_lot_snapshot
        from src.trading_runtime.selected_checkpoint_products import checkpoint_roster as load_fixed_structural_lot_stop_ceiling
        binding=self.require_checkpoint(checkpoint)
        prefix,contexts=self._prefix()
        if (type(checkpoint_sequence) is not int or checkpoint_sequence<binding[3]
                or prefix.last_sequence!=checkpoint_sequence or prefix.last_batch_id!=journal_batch_id):
            raise ValueError('Selected manager checkpoint needs its exact newly committed frontier')
        submitted=dict(checkpoint.inherited.submitted)
        positions=dict(checkpoint.inherited.positions)
        if set(positions)!=set(dict(binding[1])) or set(positions)!=set(dict(checkpoint.financials)):
            raise ValueError('Selected manager checkpoint lost its complete active inventory')
        rows=[];states=[]
        for key,state in binding[1]:
            request=self.operation.source.request(submitted[key])
            if state.protection!=positions[key]:
                raise ValueError('Selected frozen protection differs from original capture')
            arguments=self._arguments(request,prefix,contexts)
            fresh=load_fixed_structural_lot_stop_ceiling(**arguments,entry=state.entry,
                group_id=state.roster.group_id)
            prior=state.roster
            if (fresh.run_id,fresh.group_id,fresh.intent_id,fresh.entry_source_hash,
                    fresh.remaining,fresh.acquiring,fresh.ceiling)!=(prior.run_id,prior.group_id,
                    prior.intent_id,prior.entry_source_hash,prior.remaining,prior.acquiring,prior.ceiling):
                raise ValueError('Selected economic/protection inventory changed after checkpoint capture')
            rebound=replace(state,roster=fresh)
            rows.append((key,project_fixed_structural_lot_snapshot(rebound,**arguments)))
            states.append((key,rebound))
        result=FixedStructuralLotManagerCheckpoint(checkpoint.inherited,tuple(rows),checkpoint.financials)
        self._checkpoints[result]=(_checkpoint_content(result),tuple(states),self.operation.source,
            prefix.last_sequence,prefix.last_batch_id,prefix.source_cursor,binding[6])
        return result,prefix,contexts

    def freeze_checkpoint(self,checkpoint):
        """Own a by-value capture before an asynchronous checkpoint wait."""
        from copy import deepcopy
        binding=self.require_checkpoint(checkpoint)
        frozen=deepcopy(checkpoint)
        self._checkpoints[frozen]=(_checkpoint_content(frozen),*binding[1:])
        self.require_checkpoint(frozen)
        return frozen


    def restore(self,manager,checkpoint):
        from .backtest_strategy_one_management import StrategyOneManagementRunner
        from src.trading_runtime.fixed_structural_lot_snapshot import restore_fixed_structural_lot_snapshot
        if (type(manager) is not StrategyOneManagementRunner or manager._fixed_lot_owner is not self
                or type(checkpoint) is not FixedStructuralLotManagerCheckpoint
                or self.states or self.entries or self.financials
                or manager._positions or manager._submitted):
            raise ValueError('Fresh selected manager and complete checkpoint required')
        inherited=checkpoint.inherited
        positions=dict(inherited.positions);submitted=dict(inherited.submitted)
        identities=tuple(key for key,_ in checkpoint.selected_positions)
        if identities!=tuple(sorted(set(identities))) or set(identities)!=set(positions):
            raise ValueError('Selected checkpoint position inventory differs')
        financials=dict(checkpoint.financials)
        if tuple(financials)!=tuple(sorted(financials)) or len(financials)!=len(checkpoint.financials) or set(financials)!=set(positions):
            raise ValueError('Selected checkpoint financial inventory differs')
        prefix,contexts=self._prefix()
        states={};entries={};groups={}
        for key,rows in checkpoint.selected_positions:
            request=self.operation.source.request(submitted[key])
            state=restore_fixed_structural_lot_snapshot(rows,entry=request.entry,
                **self._arguments(request,prefix,contexts))
            financial=financials[key]
            from src.trading_runtime.selected_checkpoint_products import selected,checkpoint_aggregate_quantity
            expected_quantity=(checkpoint_aggregate_quantity(self.client,prefix,
                entry_request=request,contexts=contexts,roster=state.roster)
                if selected(self.operation.source)
                else sum((q for _,q in state.roster.remaining),Decimal(0)))
            if (state.protection!=positions[key] or type(financial) is not StrategyOneFinancialView
                    or (financial.account_id,financial.assignment_id,financial.ticker)!=key
                    or Decimal(str(financial.position_quantity))!=expected_quantity):
                raise ValueError('Selected checkpoint original/financial authority differs')
            states[key]=state;entries[key]=request;groups[key]=state.roster.group_id
        manager.restore_state(inherited,first_price_source=self.operation.source.price_authority)
        self._states=states;self.entries=entries;self.groups=groups;self.financials=financials
        manager._profit_arm_financials=dict(financials)
        # No prior mutable proof objects are reused or promoted by restoration.
        manager._profit_arm_references.clear()
