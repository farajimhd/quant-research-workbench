"""Selected fixed-target component reducer and cold semantic guard.

The native installed manager/OMS owner is deliberately not activated by this
module. It must bind the declaration and durable source before admission.
"""
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .strategy_one_position import ProtectionState, ProtectionTransition, advance_protection
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FixedStructuralLotStopCeiling:
    """A committed roster observation, never financial/admission authority."""
    run_id: str
    group_id: str
    through_sequence: int
    group_sequence: int
    remaining: tuple[tuple[str, Decimal], ...]
    acquiring: tuple[str, ...]
    ceiling: Decimal | None
    intent_id: str
    entry_source_hash: str
    observed_boundary_ms: int


def load_fixed_structural_lot_stop_ceiling(client, prefix, *, entry, intervals,
                                         intent, group_id, strategy_identity,
                                         entry_request=None,fixed_lot_contexts=(),now_ms=None,
                                         _checkpoint_closing=None):
    """Freshly verify normalized OMS rows and preserve every frozen lot target.

    The prefix/OMS reader verifies committed content. Entry replay checks source
    equivalence, not installed configuration or historical financial authority.
    A native checkpoint must keep original entry target separate from this
    remaining-lot ceiling; it must not manufacture a target amendment.
    """
    from .arte_oms_projection import load_latest_committed_oms_groups
    from .fixed_structural_lot_entry import fixed_structural_lot_intent, _same_typed
    from .strategy_one_intent import strategy_one_entry_intent
    from .order_management import OrderManagementState
    from src.backend.backtest_market_data import market_day_boundary
    from .arte_journal_reader import _journal_instant
    from .squeeze_ladder_lots import LadderFillFact, ladder_lot_exposure
    if entry_request is None:
        if fixed_lot_contexts:
            raise ValueError('Selected roster context needs its exact native entry request')
        rebuilt=fixed_structural_lot_intent(
            strategy_one_entry_intent(entry.proposal,session_date=entry.session_date),
            entry,intervals=intervals,intent_id=intent.intent_id)
    else:
        from src.backend.backtest_fixed_structural_lot_source import FixedStructuralLotRequest
        from .fixed_structural_lot_entry_v4 import FixedStructuralLotPublicationContext
        if type(entry_request) is not FixedStructuralLotRequest:
            raise ValueError('Exact prepared native entry request required')
        entry_request.verify()
        if (entry_request.run_id!=prefix.run_id
                or (entry_request.strategy_id,entry_request.revision)!=strategy_identity
                or not _same_typed(entry_request.entry,entry)
                or type(fixed_lot_contexts) is not tuple or not fixed_lot_contexts
                or any(type(v) is not FixedStructuralLotPublicationContext
                       or v.source is not entry_request.source for v in fixed_lot_contexts)):
            raise ValueError('Native roster has foreign entry/source context')
        rebuilt=entry_request.intent
    if not _same_typed(rebuilt,intent) or type(group_id) is not str or not group_id:
        raise ValueError('Fixed lot roster has a foreign original request')
    groups=load_latest_committed_oms_groups(client,prefix,
        allowed_accounts=frozenset((entry.proposal.account_id,)),strategy_identity=strategy_identity,
        **({'fixed_lot_contexts':fixed_lot_contexts} if entry_request is not None else {}),
        **({'require_tactic':True} if _checkpoint_closing is not None else {}))
    selected=tuple(v for v in groups if v.group['group_id']==group_id)
    if len(selected)!=1:
        raise ValueError('Fixed lot roster is missing or duplicated')
    state=selected[0]
    g=state.group
    try:
        OrderManagementState(g['state'])
    except ValueError as exc:
        raise ValueError('Fixed lot roster state is unknown') from exc
    if _checkpoint_closing is not None:
        from .selected_checkpoint_products import require_closing_checkpoint_read
        require_closing_checkpoint_read(_checkpoint_closing,client=client,prefix=prefix,
            entry_request=entry_request,state=state)
    if (g['strategy_intent_id']!=intent.intent_id
            or g['protection_delegated']!=0 and _checkpoint_closing is None
            or g['state'] in ('outcome_unknown','rejected','policy_blocked')):
        raise ValueError('Fixed lot roster has unresolved or delegated ownership')
    slices=tuple(v.slice_id for v in intent.protection_profile.slices)
    targets={v.slice_id:Decimal(str(v.profit_target_price)) for v in intent.protection_profile.slices}
    if len(state.orders)!=len(state.order_slice_ids) or len(set(v.cOID for v in state.orders))!=len(state.orders):
        raise ValueError('Fixed lot request roster is malformed')
    for order,lot in zip(state.orders,state.order_slice_ids):
        if (lot not in targets or order.acctId!=entry.proposal.account_id
                or order.ticker!=entry.proposal.ticker or order.quantity is None):
            raise ValueError('Fixed lot request ownership differs')
        if order.side=='SELL' and order.orderType=='LMT' and Decimal(str(order.price))!=targets[lot]:
            raise ValueError('Fixed structural lot target was collapsed or amended')
    facts=[]
    acquiring=set()
    seen=set()
    indexes=set()
    roots=set()
    for row in state.broker_bindings:
        index=row['request_index']
        if (row['has_role']!=1 or row['has_slice']!=1 or type(index) is not int
                or not 0<=index<len(state.orders) or index in indexes
                or row['broker_order_id'] in seen or row['terminal'] not in (0,1)):
            raise ValueError('Fixed lot broker binding is missing or duplicated')
        indexes.add(index)
        seen.add(row['broker_order_id'])
        order=state.orders[index]
        lot=state.order_slice_ids[index]
        expected_role=('entry' if order.side=='BUY' and order.orderType=='LMT' else
                       'profit_target' if order.orderType=='LMT' else
                       'protective_stop' if order.orderType=='STP' else None)
        if row['slice_id']!=lot or expected_role is None or row['role']!=expected_role:
            raise ValueError('Fixed lot broker role differs from its request')
        if row['has_filled_quantity'] not in (0,1):
            raise ValueError('Fixed lot fill presence is malformed')
        quantity=Decimal(str(row['filled_quantity'])) if row['has_filled_quantity'] else Decimal(0)
        if not quantity.is_finite() or quantity<0 or quantity>Decimal(str(order.quantity)):
            raise ValueError('Fixed lot cumulative fill exceeds request')
        facts.append(LadderFillFact(row['broker_order_id'],lot,row['role'],quantity))
        if expected_role=='entry':
            if lot in roots:
                raise ValueError('Fixed lot acquisition root is duplicated')
            roots.add(lot)
            if row['terminal']==0 and quantity<Decimal(str(order.quantity)):
                acquiring.add(lot)
    if roots!=set(slices) or indexes!=set(range(len(state.orders))):
        raise ValueError('Fixed lot broker request coverage is incomplete')
    exposure=ladder_lot_exposure(slices,tuple(facts))
    remaining=tuple((lot,value.remaining) for lot,value in zip(slices,exposure))
    active={lot for lot,value in remaining if value>0}|acquiring
    ceiling=min((targets[lot] for lot in active),default=None)
    from .fixed_structural_lot_causal_clock import selected_clock, committed_observed_clock
    if selected_clock(entry_request):
        matching=tuple(context for context in fixed_lot_contexts
            if context.record.entity_id==entry_request.intent.intent_id)
        if len(matching)!=1 or matching[0].source is not entry_request.source:
            raise ValueError('Causal roster requires its exact committed entry source')
        observed=committed_observed_clock(client,prefix,state,session_date=entry.session_date,
            entry_boundary_ms=entry.proposal.boundary_ms,now_ms=now_ms,
            entry_sequence=matching[0].record.sequence)
    else:
        elapsed=_journal_instant(g['updated_at'])-market_day_boundary(entry.session_date,0)
        observed=(elapsed.days*86400+elapsed.seconds)*1000+elapsed.microseconds//1000
        if elapsed.microseconds%1000 or observed<entry.proposal.boundary_ms:
            raise ValueError('Fixed lot roster clock precedes its entry')
    return FixedStructuralLotStopCeiling(prefix.run_id,group_id,prefix.last_sequence,
        state.sequence,remaining,tuple(lot for lot in slices if lot in acquiring),ceiling,
        intent.intent_id,fixed_structural_lot_entry_hash(entry),observed)


def fixed_structural_lot_entry_hash(entry):
    """Deterministic typed source identity, not source approval."""
    from dataclasses import fields, is_dataclass
    from datetime import date
    from hashlib import sha256
    import json
    def tree(value):
        if is_dataclass(value):
            return [type(value).__name__,[[f.name,tree(getattr(value,f.name))] for f in fields(value)]]
        if type(value) in (str,int,float,bool,type(None)):
            return [type(value).__name__,value]
        if type(value) is date:
            return ['date',value.isoformat()]
        if type(value) is tuple:
            return ['tuple',[tree(v) for v in value]]
        raise ValueError('Unsupported fixed lot source identity type')
    return sha256(json.dumps(tree(entry),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def advance_fixed_structural_lot_protection(state, *, policy, **inputs):
    """Disable target advancement before computation, preserving earned stops."""
    if type(policy) is not FixedStructuralLotPolicy:
        raise ValueError('Exact declared fixed target policy required')
    policy.__post_init__()
    if 'allows_target_escalation' in inputs:
        raise ValueError('Fixed target declaration cannot be overridden')
    transition = advance_protection(state, allows_target_escalation=False, **inputs)
    require_fixed_structural_lot_transition(state, transition, policy=policy)
    return transition


def require_fixed_structural_lot_transition(previous, transition, *, policy):
    """Reject a collapsed/advanced target in cold or queued protection replay."""
    if (type(policy) is not FixedStructuralLotPolicy or type(previous) is not ProtectionState
            or type(transition) is not ProtectionTransition):
        raise ValueError('Exact fixed target protection transition required')
    policy.__post_init__()
    if transition.target_amendment is not None or transition.state.target != previous.target:
        raise ValueError('Fixed structural lot targets cannot be amended')


def require_fixed_structural_lot_command(intent, *, policy):
    """Apply before live submission and after normalized cold intent recovery."""
    from .signals import StrategyIntent
    if type(policy) is not FixedStructuralLotPolicy or type(intent) is not StrategyIntent:
        raise ValueError('Exact fixed target command required')
    policy.__post_init__()
    if intent.action == 'replace_profit_target' or (intent.action != 'enter_long'
                                                   and intent.profit_target_price is not None):
        raise ValueError('Fixed structural lot target command forbidden')
