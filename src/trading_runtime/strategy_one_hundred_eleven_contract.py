"""Unpublished exact inherited factory with one writer replay declaration."""
from dataclasses import fields
from .strategy_one_hundred_ten_contract import strategy_one_hundred_ten_contract
from .strategy_one_hundred_eleven_release import release_contract, publication_source_reuse_policy
from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract


def strategy_one_hundred_eleven_contract():
    prior = strategy_one_hundred_ten_contract()
    values = {field.name: getattr(prior, field.name) for field in fields(prior)}
    release = release_contract()
    values.update(strategy_number=release.number, release=release,
        publication_source_reuse_policy=publication_source_reuse_policy())
    return FixedStructuralLotSelectedExitStrategyContract(**values)
