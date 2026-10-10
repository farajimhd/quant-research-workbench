"""Exact inherited factory; labels and immutable source identity only differ."""
from dataclasses import fields

from .strategy_one_hundred_nine_contract import strategy_one_hundred_nine_contract
from .strategy_one_hundred_ten_release import release_contract
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract


def strategy_one_hundred_ten_contract():
    prior = strategy_one_hundred_nine_contract()
    values = {field.name: getattr(prior, field.name) for field in fields(prior)}
    release = release_contract()
    values.update(strategy_number=release.number, release=release)
    return FixedStructuralLotSelectedExitStrategyContract(**values)
