"""Exact prepared factory; no registered or installed native admission."""
from dataclasses import fields

from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
from .strategy_ninety_eight_contract import strategy_ninety_eight_contract
from .strategy_ninety_nine_release import release_contract, SELECTED_EXIT_POLICY


def strategy_ninety_nine_contract():
    inherited = strategy_ninety_eight_contract()
    release = release_contract()
    values = {field.name: getattr(inherited, field.name) for field in fields(inherited)}
    values.update(strategy_number=release.number, release=release,
                  selected_exit_publication_policy=SELECTED_EXIT_POLICY)
    return FixedStructuralLotSelectedExitStrategyContract(**values)
