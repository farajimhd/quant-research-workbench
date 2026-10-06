"""Persisted legacy exit identities survive declaration-selected extensions."""
from dataclasses import replace

import pytest

from src.trading_runtime import strategy_registry as registry
from src.trading_runtime.strategy_sixty_four_release import release_contract
from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_reason
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_reason
from src.trading_runtime.strategy_profit_giveback_exit import profit_giveback_reason


@pytest.mark.parametrize('function,suffix', [
    (confirmed_ah_reason, 'confirmed_ah_failure'),
    (liquidity_fade_reason, 'liquidity_fade_failure'),
    (profit_giveback_reason, 'profit_giveback'),
])
@pytest.mark.parametrize('number,label', [(42, 'forty_two'), (57, 'fifty_seven')])
def test_existing_saved_reason_identity_unchanged(function, suffix, number, label):
    assert function(number) == f'strategy_{label}_{suffix}'


@pytest.mark.parametrize('function,suffix,rule', [
    (confirmed_ah_reason, 'confirmed_ah_failure', 'strategy.confirmed-ah-risk-failure.v1'),
    (liquidity_fade_reason, 'liquidity_fade_failure', 'strategy-thirty-five-completed-liquidity-fade-v1'),
    (profit_giveback_reason, 'profit_giveback', 'strategy-thirty-one-original-risk-profit-giveback-v1'),
])
def test_generic_identity_requires_corresponding_declared_rule(monkeypatch, function, suffix, rule):
    release = replace(release_contract(), number=7001, executor_revision=7001, approved_digest='')
    release = replace(release, approved_digest=release.digest())
    monkeypatch.setattr(registry, 'numbered_strategy', lambda number: release)
    assert function(7001) == f'strategy_7001_{suffix}'
    removed = replace(release, rule_set_contracts=tuple(r for r in release.rule_set_contracts if r != rule), approved_digest='')
    removed = replace(removed, approved_digest=removed.digest())
    monkeypatch.setattr(registry, 'numbered_strategy', lambda number: removed)
    with pytest.raises(ValueError):
        function(7001)
