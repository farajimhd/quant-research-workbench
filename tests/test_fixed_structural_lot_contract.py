"""Exact semantic factory selection controls; registry transport is a fixture."""
from types import SimpleNamespace

import pytest

from src.trading_runtime import strategy_registry
from src.trading_runtime.numbered_fixed_strategy import (
    DeclaredFixedStrategyContract, numbered_fixed_strategy,
)
from src.trading_runtime.strategy_seventy_seven_contract import strategy_seventy_seven_contract


def bind(monkeypatch, contract):
    release = strategy_seventy_seven_contract().release
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', lambda number: release)
    monkeypatch.setattr(strategy_registry, 'fixed_strategy_executor',
        lambda identity, number: SimpleNamespace(contract_factory=lambda: contract))
    return release.number


def test_exact_selected_factory_uses_declared_capabilities(monkeypatch):
    contract = strategy_seventy_seven_contract()
    assert numbered_fixed_strategy(bind(monkeypatch, contract)) is contract
    assert contract.fixed_structural_lot_policy.count == 3
    assert not contract.allows_adds


@pytest.mark.parametrize('plain', [False, True])
def test_source_marker_cannot_admit_foreign_or_plain_factory(monkeypatch, plain):
    selected = strategy_seventy_seven_contract()
    contract = (DeclaredFixedStrategyContract(selected.strategy_number, selected.strategy_id,
        selected.execution_interval, selected.release, selected.policy_json) if plain else object())
    number = bind(monkeypatch, contract)
    with pytest.raises(ValueError, match='Selected fixed-lot factory'):
        numbered_fixed_strategy(number)
