"""Exact inherited factory with a typed declared initial-held verification reuse."""
from dataclasses import fields
from .strategy_one_hundred_seven_contract import strategy_one_hundred_seven_contract
from .strategy_one_hundred_eight_release import release_contract,initial_held_recovery_reuse_policy,proposal_decision_inventory_reuse_policy
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract

def strategy_one_hundred_eight_contract():
    prior=strategy_one_hundred_seven_contract();values={f.name:getattr(prior,f.name) for f in fields(prior)}
    release=release_contract();values.update(strategy_number=release.number,release=release,
        initial_held_recovery_reuse_policy=initial_held_recovery_reuse_policy(),proposal_decision_inventory_reuse_policy=proposal_decision_inventory_reuse_policy())
    return FixedStructuralLotSelectedExitStrategyContract(**values)
