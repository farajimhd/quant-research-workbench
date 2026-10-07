"""Selected source-bound protection; no installed actor or financial admission.

Every operation reloads the committed roster. Native installation still needs
the preparation-once source owner and durable complete configuration binding.
Original target is geometry, never a surrogate for the remaining stop ceiling.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from .fixed_structural_lot_entry import FixedStructuralLotEntry
from .fixed_structural_lot_management import (
    FixedStructuralLotStopCeiling, fixed_structural_lot_entry_hash,
    load_fixed_structural_lot_stop_ceiling, require_fixed_structural_lot_transition,
)
from .strategy_one_position import (
    ProtectionState, ProtectionTransition, advance_protection, confirm_protection_transition,
)


@dataclass(frozen=True,slots=True)
class FixedStructuralLotProtectionState:
    entry: FixedStructuralLotEntry
    protection: ProtectionState
    roster: FixedStructuralLotStopCeiling

    def __post_init__(self):
        if (type(self.entry) is not FixedStructuralLotEntry or type(self.protection) is not ProtectionState
                or type(self.roster) is not FixedStructuralLotStopCeiling
                or self.roster.entry_source_hash!=fixed_structural_lot_entry_hash(self.entry)
                or type(self.protection.target) is not float
                or self.protection.target!=self.entry.proposal.initial_target
                or self.roster.ceiling is None or not 0<self.protection.stop<float(self.roster.ceiling)
                or self.protection.stop<self.entry.proposal.initial_stop
                or type(self.protection.boundary_ms) is not int
                or self.protection.boundary_ms<self.entry.proposal.boundary_ms
                or self.roster.observed_boundary_ms>self.protection.boundary_ms):
            raise ValueError('Fixed lot protection lacks its original source or active ceiling')
        if (type(self.roster.through_sequence) is not int or type(self.roster.group_sequence) is not int
                or not 1<=self.roster.group_sequence<=self.roster.through_sequence
                or type(self.roster.observed_boundary_ms) is not int
                or type(self.roster.remaining) is not tuple or type(self.roster.acquiring) is not tuple
                or type(self.roster.ceiling) is not Decimal or not self.roster.ceiling.is_finite()):
            raise ValueError('Fixed lot roster scalar types differ')
        from .strategy_one_protection_snapshot import _validate_state
        _validate_state(self.protection,boundary_ms=self.protection.boundary_ms,
                        stop_ceiling=float(self.roster.ceiling))
        ids=tuple('lot-'+str(i+1) for i in range(self.entry.policy.count))
        targets={lot:Decimal(str(target.price)) for lot,target in zip(ids,self.entry.targets)}
        if (tuple(lot for lot,_ in self.roster.remaining)!=ids
                or len(set(self.roster.acquiring))!=len(self.roster.acquiring)
                or not set(self.roster.acquiring)<=set(ids)
                or any(type(qty) is not Decimal or not qty.is_finite() or qty<0
                       for _,qty in self.roster.remaining)):
            raise ValueError('Fixed lot residual inventory is malformed')
        active={lot for lot,qty in self.roster.remaining if qty>0}|set(self.roster.acquiring)
        if not active or self.roster.ceiling!=min(targets[lot] for lot in active):
            raise ValueError('Fixed lot ceiling differs from remaining ownership')


@dataclass(frozen=True,slots=True)
class FixedStructuralLotProtectionTransition:
    previous: FixedStructuralLotProtectionState
    proposed: FixedStructuralLotProtectionState
    transition: ProtectionTransition

    def __post_init__(self):
        if (type(self.previous) is not FixedStructuralLotProtectionState
                or type(self.proposed) is not FixedStructuralLotProtectionState
                or type(self.transition) is not ProtectionTransition):
            raise ValueError('Exact selected fixed lot transition fields required')
        self.previous.__post_init__()
        self.proposed.__post_init__()
        if self.proposed.entry!=self.previous.entry or self.proposed.protection!=self.transition.state:
            raise ValueError('Fixed lot transition changed its entry source')
        require_fixed_structural_lot_transition(self.previous.protection,self.transition,
                                               policy=self.previous.entry.policy)


def _fresh(state,*,client,prefix,intervals,intent,strategy_identity,now_ms,
           entry_request=None,fixed_lot_contexts=()):
    state.__post_init__()
    if type(now_ms) is not int:
        raise ValueError('Fixed lot operation needs exact causal clock')
    roster=load_fixed_structural_lot_stop_ceiling(client,prefix,entry=state.entry,intervals=intervals,
        intent=intent,group_id=state.roster.group_id,strategy_identity=strategy_identity,
        **({'entry_request':entry_request,'fixed_lot_contexts':fixed_lot_contexts}
           if entry_request is not None or fixed_lot_contexts else {}))
    old=state.roster
    if ((roster.run_id,roster.intent_id,roster.entry_source_hash)!=(old.run_id,old.intent_id,old.entry_source_hash)
            or roster.through_sequence<old.through_sequence or roster.group_sequence<old.group_sequence
            or roster.observed_boundary_ms>now_ms or roster.ceiling is None):
        raise ValueError('Fixed lot operation has stale, future or flat roster')
    oldactive={lot for lot,qty in old.remaining if qty>0}|set(old.acquiring)
    newactive={lot for lot,qty in roster.remaining if qty>0}|set(roster.acquiring)
    if not newactive<=oldactive or not set(roster.acquiring)<=set(old.acquiring):
        raise ValueError('Fixed lot retired ownership cannot be reacquired')
    return roster


def open_fixed_structural_lot_protection(entry,*,client,prefix,intervals,intent,
                                       group_id,strategy_identity,entry_request=None,fixed_lot_contexts=()):
    roster=load_fixed_structural_lot_stop_ceiling(client,prefix,entry=entry,intervals=intervals,
        intent=intent,group_id=group_id,strategy_identity=strategy_identity,
        **({'entry_request':entry_request,'fixed_lot_contexts':fixed_lot_contexts}
           if entry_request is not None or fixed_lot_contexts else {}))
    protection=ProtectionState(max(entry.proposal.boundary_ms,roster.observed_boundary_ms),
                               entry.proposal.initial_stop,entry.proposal.initial_target)
    return FixedStructuralLotProtectionState(entry,protection,roster)


def advance_fixed_structural_lot_state(state,*,client,prefix,intervals,intent,
                                      strategy_identity,entry_request=None,fixed_lot_contexts=(),**inputs):
    if type(state) is not FixedStructuralLotProtectionState:
        raise ValueError('Exact selected fixed lot state required')
    if 'stop_ceiling' in inputs or 'allows_target_escalation' in inputs:
        raise ValueError('Fixed lot source ceiling or target policy cannot be overridden')
    roster=_fresh(state,client=client,prefix=prefix,intervals=intervals,intent=intent,
                  strategy_identity=strategy_identity,now_ms=inputs['now_ms'],
                  entry_request=entry_request,fixed_lot_contexts=fixed_lot_contexts)
    transition=advance_protection(state.protection,stop_ceiling=float(roster.ceiling),
                                 allows_target_escalation=False,**inputs)
    selected=FixedStructuralLotProtectionState(state.entry,transition.state,roster)
    return FixedStructuralLotProtectionTransition(state,selected,transition)


def confirm_fixed_structural_lot_state(proposal,*,client,prefix,intervals,intent,
                                      strategy_identity,stop_confirmed,target_confirmed=False,
                                      entry_request=None,fixed_lot_contexts=()):
    if type(proposal) is not FixedStructuralLotProtectionTransition:
        raise ValueError('Exact selected fixed lot transition required')
    proposal.__post_init__()
    if target_confirmed is not False:
        raise ValueError('Fixed structural targets cannot be confirmed as amended')
    roster=_fresh(proposal.previous,client=client,prefix=prefix,intervals=intervals,intent=intent,
                  strategy_identity=strategy_identity,now_ms=proposal.proposed.protection.boundary_ms,
                  entry_request=entry_request,fixed_lot_contexts=fixed_lot_contexts)
    state=confirm_protection_transition(proposal.previous.protection,proposal.transition,
        target_confirmed=False,stop_confirmed=stop_confirmed,stop_ceiling=float(roster.ceiling))
    return FixedStructuralLotProtectionState(proposal.previous.entry,state,roster)
