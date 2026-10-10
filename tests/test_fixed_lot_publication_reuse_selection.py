"""Declaration boundary tests; issuer and factory validity are controlled seams.

These tests do not certify a native source or publish a strategy.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend.backtest_fixed_lot_publication_reuse import selected_publication_source_reuse_policy
from src.trading_runtime.publication_source_reuse_policy import INPUT, RULE, PARAMETER, PublicationSourceReusePolicy
from src.trading_runtime.strategy_registry import NumberedStrategyRelease
from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract


def setup(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_source as native
    from src.trading_runtime import strategy_registry as registry
    release = NumberedStrategyRelease(111, 'fixture-executor', 1, '100ms',
        (INPUT,), (RULE,), 'Declaration boundary fixture', '')
    release = replace(release, approved_digest=release.digest())
    policy = PublicationSourceReusePolicy(4096)
    factory = object.__new__(FixedStructuralLotSelectedExitStrategyContract)
    object.__setattr__(factory, 'release', release)
    object.__setattr__(factory, 'publication_source_reuse_policy', policy)
    monkeypatch.setattr(FixedStructuralLotSelectedExitStrategyContract, '__post_init__', lambda self: None)
    monkeypatch.setattr(native, 'require_native_fixed_structural_lot_source', lambda source: source)
    monkeypatch.setattr(registry, 'numbered_strategy', lambda number: release)
    monkeypatch.setattr(registry, 'fixed_strategy_executor', lambda *args: SimpleNamespace(contract_factory=lambda: factory))
    source = SimpleNamespace(installed_payload={'strategy': {'strategy_number': 111,
        'strategy_id': 'strategy-one-111', 'revision': 111,
        'parameters': {PARAMETER: policy.payload()},
        'numbered_release': {'contract': release.canonical_payload()}}},
        require_installed_admission=lambda: None, _revision=111, _strategy_id='strategy-one-111')
    return source, factory, policy


def test_unclaimed_payload_never_invokes_native_issuance():
    source = SimpleNamespace(installed_payload={'strategy': {'parameters': {}}})
    assert selected_publication_source_reuse_policy(source) is None


def test_selected_boundary_requires_matching_declared_factory(monkeypatch):
    source, factory, policy = setup(monkeypatch)
    assert selected_publication_source_reuse_policy(source) == policy
    object.__setattr__(factory, 'publication_source_reuse_policy', PublicationSourceReusePolicy(8192))
    with pytest.raises(ValueError, match='typed factory'):
        selected_publication_source_reuse_policy(source)


def test_unissued_source_rejects_before_factory_selection(monkeypatch):
    source, factory, policy = setup(monkeypatch)
    from src.backend import backtest_fixed_structural_lot_source as native
    def reject(source):
        raise ValueError('Unissued source')
    monkeypatch.setattr(native, 'require_native_fixed_structural_lot_source', reject)
    with pytest.raises(ValueError, match='Unissued'):
        selected_publication_source_reuse_policy(source)


@pytest.mark.parametrize('field,value', [('revision', 42), ('strategy_id', 'other'),
    ('strategy_number', 42)])
def test_cross_identity_or_release_payload_rejects(monkeypatch, field, value):
    source, factory, policy = setup(monkeypatch)
    source.installed_payload['strategy'][field] = value
    with pytest.raises(ValueError):
        selected_publication_source_reuse_policy(source)
