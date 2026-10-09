"""Exact inherited factory with a typed declared structural management cadence."""
from dataclasses import fields
from .strategy_one_hundred_six_contract import strategy_one_hundred_six_contract
from .strategy_one_hundred_seven_release import release_contract,management_cadence_policy
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract

def strategy_one_hundred_seven_contract():
    prior=strategy_one_hundred_six_contract();values={f.name:getattr(prior,f.name) for f in fields(prior)}
    release=release_contract();values.update(strategy_number=release.number,release=release,
        management_cadence_policy=management_cadence_policy())
    return FixedStructuralLotSelectedExitStrategyContract(**values)
