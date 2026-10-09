"""Exact Strategy99 trading factory with declared management-read reuse."""
from dataclasses import fields

from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
from .strategy_ninety_nine_contract import strategy_ninety_nine_contract
from .strategy_one_hundred_one_release import release_contract, MANAGEMENT_REUSE_POLICY


def strategy_one_hundred_one_contract():
    inherited = strategy_ninety_nine_contract()
    values = {field.name: getattr(inherited, field.name) for field in fields(inherited)}
    release = release_contract()
    values.update(strategy_number=release.number, release=release,
                  management_reuse_policy=MANAGEMENT_REUSE_POLICY)
    return FixedStructuralLotSelectedExitStrategyContract(**values)
