"""Exact inherited factory with an explicit first-inventory source scope."""
from dataclasses import fields
from .strategy_one_hundred_eight_contract import strategy_one_hundred_eight_contract
from .strategy_one_hundred_nine_release import release_contract, first_inventory_source_reuse_policy
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract


def strategy_one_hundred_nine_contract():
    prior = strategy_one_hundred_eight_contract()
    values = {field.name: getattr(prior, field.name) for field in fields(prior)}
    release = release_contract()
    values.update(strategy_number=release.number, release=release,
                  first_inventory_source_reuse_policy=first_inventory_source_reuse_policy())
    return FixedStructuralLotSelectedExitStrategyContract(**values)
