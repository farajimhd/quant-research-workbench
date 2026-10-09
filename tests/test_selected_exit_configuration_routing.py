"""Prepared registry seam only; no installed/source approval is granted."""
from types import SimpleNamespace

from src.backend import backtest_fixed_structural_lot_configuration as subject
from src.trading_runtime.strategy_ninety_nine_contract import strategy_ninety_nine_contract
from src.trading_runtime.strategy_ninety_nine_release import derive_strategy_ninety_nine_configuration
from tests.test_fixed_structural_lot_configuration_routing import source_fixture
from tests.test_strategy_fifty_release import APPROVAL


def test_declared_shared_compiler_matches_complete_successor_compiler(monkeypatch):
    contract = strategy_ninety_nine_contract()
    original_release = subject.numbered_strategy
    original_parent = subject.numbered_strategy_parent
    original_factory = subject.fixed_strategy_executor
    monkeypatch.setattr(subject, 'numbered_strategy',
        lambda number: contract.release if number == 99 else original_release(number))
    monkeypatch.setattr(subject, 'numbered_strategy_parent',
        lambda number: 42 if number == 99 else original_parent(number))
    monkeypatch.setattr(subject, 'fixed_strategy_executor',
        lambda identity, revision: SimpleNamespace(contract_factory=strategy_ninety_nine_contract)
        if revision == 99 else original_factory(identity, revision))
    parent = source_fixture()
    shared = subject.derive_registered_fixed_structural_lot_configuration(
        parent, number=99, **APPROVAL)
    direct = derive_strategy_ninety_nine_configuration(parent, **APPROVAL)
    assert shared == direct
    assert shared['payload']['strategy']['parameters']['selected_exit_publication_policy'] == (
        contract.selected_exit_publication_policy.payload())
