"""Semantic runtime routing controls; no database or installed certification."""
from dataclasses import replace

import pytest

from src.trading_runtime.fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
from src.trading_runtime.strategy_ninety_two_contract import strategy_ninety_two_contract
from src.trading_runtime.strategy_ninety_three_contract import strategy_ninety_three_contract
from src.trading_runtime.strategy_ninety_three_release import release_contract
from src.trading_runtime.packet_validation_reuse_policy import INPUT, RULE
from src.backend import backtest_fixed_structural_lot_execution_v14 as execution


def test_exact_factory_selection_preserves_legacy_and_rejects_foreign_shape():
    legacy, successor = strategy_ninety_two_contract(), strategy_ninety_three_contract()
    assert require_declared_fixed_structural_lot_contract(legacy, legacy.release) is legacy
    assert require_declared_fixed_structural_lot_contract(successor, successor.release) is successor
    with pytest.raises(ValueError, match='exact declared'):
        require_declared_fixed_structural_lot_contract(legacy, successor.release)
    with pytest.raises(ValueError, match='exact declared'):
        require_declared_fixed_structural_lot_contract(successor, legacy.release)


def run_kwargs():
    return dict(plans=None, number=93, run_id='not-admitted', session_date=None,
        market=None, candidates=None, entry=None, seeds=None,
        through_boundary_ms=0, client_factory=lambda: pytest.fail('Database accessed'))


def test_unselected_release_delegates_to_unchanged_legacy_path(monkeypatch):
    from src.trading_runtime import strategy_registry
    kwargs = run_kwargs()
    kwargs['number'] = 92
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', lambda number: strategy_ninety_two_contract().release)
    captured = []
    def legacy(**values):
        captured.append(values)
        return 'legacy-result'
    monkeypatch.setattr(execution.legacy, 'prepare_fixed_structural_lot_session', legacy)
    assert execution.prepare_fixed_structural_lot_session(**kwargs) == 'legacy-result'
    assert captured == [kwargs]


@pytest.mark.parametrize('missing', [INPUT, RULE])
def test_incomplete_companions_fail_before_source_or_transport(monkeypatch, missing):
    from src.trading_runtime import strategy_registry
    release = release_contract()
    if missing == INPUT:
        release = replace(release, input_contracts=tuple(v for v in release.input_contracts if v != INPUT), approved_digest='')
    else:
        release = replace(release, rule_set_contracts=tuple(v for v in release.rule_set_contracts if v != RULE), approved_digest='')
    release = replace(release, approved_digest=release.digest())
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', lambda number: release)
    with pytest.raises(ValueError, match='exact source'):
        execution.prepare_fixed_structural_lot_session(**run_kwargs())
