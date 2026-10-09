from dataclasses import fields, replace

import pytest

from src.trading_runtime.strategy_ninety_eight_contract import strategy_ninety_eight_contract
from src.trading_runtime.strategy_ninety_nine_contract import strategy_ninety_nine_contract
from src.trading_runtime.strategy_ninety_nine_release import release_contract
from src.trading_runtime.selected_exit_publication_policy import INPUT, RULE
from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract


def test_prepared_successor_preserves_complete_parent_trading_fields():
    previous = strategy_ninety_eight_contract()
    current = strategy_ninety_nine_contract()
    assert type(current) is FixedStructuralLotSelectedExitStrategyContract
    assert current.strategy_number == 99
    assert current.release == release_contract()
    for field in fields(previous):
        if field.name not in {'strategy_number', 'release'}:
            assert getattr(current, field.name) == getattr(previous, field.name)
    assert current.release.input_contracts == (*previous.release.input_contracts, INPUT)
    assert current.release.rule_set_contracts == (*previous.release.rule_set_contracts, RULE)


def test_prepared_factory_rejects_missing_policy_and_foreign_release():
    current = strategy_ninety_nine_contract()
    with pytest.raises(ValueError, match='typed selected exit'):
        replace(current, selected_exit_publication_policy=None)
    previous = strategy_ninety_eight_contract()
    with pytest.raises(ValueError, match='paired'):
        replace(current, strategy_number=previous.strategy_number, release=previous.release)
