"""Prepared compiler routing; substituted registry is not source approval."""
from copy import deepcopy

import pytest

from tests.test_complete_market_release import prepared
from src.backend import backtest_fixed_structural_lot_configuration as compiler
from src.trading_runtime.strategy_ninety_eight_contract import strategy_ninety_eight_contract


def test_declared_compiler_reconstructs_full_version18_tree(monkeypatch):
    parent, arguments, _, expected = prepared()
    factory = strategy_ninety_eight_contract()
    monkeypatch.setattr(compiler, 'numbered_strategy', lambda number:
        factory.release if number == factory.strategy_number else arguments['parent_release'])
    monkeypatch.setattr(compiler, 'numbered_strategy_parent', lambda number: parent.strategy_number)
    monkeypatch.setattr(compiler, 'declared_fixed_structural_lot_contract', lambda number: factory)
    actual = compiler.derive_registered_fixed_structural_lot_configuration(parent,
        number=factory.strategy_number, **{key: arguments[key] for key in (
            'approved_code_commit', 'approved_code_fingerprint', 'approval_reference')})
    assert actual == expected


@pytest.mark.parametrize('change', [None, 'window', 'empty', 'extra'])
def test_normalized_policy_companions_and_exact_factory_values(monkeypatch, change):
    _, arguments, _, _ = prepared()
    from tests.test_fixed_structural_lot_configuration_routing import source_fixture
    from src.trading_runtime.fixed_structural_lot_release_v18 import derive_fixed_structural_lot_release
    expected = derive_fixed_structural_lot_release(source_fixture(), **arguments)
    factory = strategy_ninety_eight_contract()
    monkeypatch.setattr(compiler, 'declared_fixed_structural_lot_contract', lambda number: factory)
    monkeypatch.setattr(compiler, 'numbered_strategy_parent', lambda number: 42)
    strategy = deepcopy(expected['payload']['strategy'])
    # The normalized runtime projection retains execution/sizing and selected
    # policies; full release derivation separately checks all parent economics.
    parameters = strategy['parameters']
    strategy['parameters'] = {key: value for key, value in parameters.items() if key in (
        'execution', 'sizing', 'fixed_structural_lot_policy', 'fixed_structural_lot_parent',
        'packet_validation_reuse_policy', 'projected_configuration_reuse_policy',
        'owned_scalar_snapshot_policy', 'empty_protection_confirmation_policy', 'complete_market_window_policy')}
    parameters = strategy['parameters']
    if change == 'window':
        parameters['complete_market_window_policy']['window_span_ms'] = 100
    elif change == 'empty':
        parameters['empty_protection_confirmation_policy']['omitted'] = 'all checks'
    elif change == 'extra':
        parameters['unreviewed'] = True
    if change is None:
        assert compiler.verify_fixed_structural_lot_configuration(strategy) is factory
    else:
        with pytest.raises(ValueError):
            compiler.verify_fixed_structural_lot_configuration(strategy)
