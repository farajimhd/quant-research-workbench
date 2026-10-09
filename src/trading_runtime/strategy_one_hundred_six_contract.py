"""Exact inherited factory with a typed declared acquisition quota."""
from dataclasses import fields
from .strategy_one_hundred_five_contract import strategy_one_hundred_five_contract
from .strategy_one_hundred_six_release import release_contract,acquisition_policy
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
from .portfolio_acquisition_contract import SessionAcquisitionQuotaPolicy

def strategy_one_hundred_six_contract():
    prior=strategy_one_hundred_five_contract();values={f.name:getattr(prior,f.name) for f in fields(prior)}
    release=release_contract();values.update(strategy_number=release.number,release=release,
        session_acquisition_quota=acquisition_policy())
    return FixedStructuralLotSelectedExitStrategyContract(**values)
