"""Exact predecessor trading factory with an explicit preparation declaration."""
from dataclasses import fields
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
from .strategy_one_hundred_one_contract import strategy_one_hundred_one_contract
from .strategy_one_hundred_two_release import release_contract


def strategy_one_hundred_two_contract():
    prior = strategy_one_hundred_one_contract()
    values = {field.name: getattr(prior, field.name) for field in fields(prior)}
    release = release_contract()
    values.update(strategy_number=release.number, release=release)
    return FixedStructuralLotSelectedExitStrategyContract(**values)
