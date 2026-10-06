"""The declared adapter chooses semantic rules independently of numeric identity."""
from dataclasses import replace

import pytest

from src.trading_runtime import numbered_fixed_strategy as adapter
from src.trading_runtime import strategy_registry as registry
from src.trading_runtime.strategy_sixty_four_release import release_contract
from src.trading_runtime.strategy_forty_two_release import release_contract as legacy_release


def sealed_identity(template, number):
    draft = replace(template, number=number, executor_revision=number, approved_digest='')
    return replace(draft, approved_digest=draft.digest())


@pytest.mark.parametrize('number', [7001, 9003])
def test_same_semantic_rule_at_distinct_release_identities(monkeypatch, number):
    release = sealed_identity(release_contract(), number)
    def lookup(requested):
        assert requested == number
        release.verify()
        return release
    monkeypatch.setattr(registry, 'numbered_strategy', lookup)
    assert adapter.declared_fixed_rule(number, 'entry-spread-at-most-one-quarter-original-risk@1')
    assert adapter.declared_fixed_rule(number, 'strategy-nine-followthrough-failure-v1')
    assert not adapter.declared_fixed_rule(number, 'unreviewed-rule@1')


def test_legacy_release_cannot_acquire_declared_adapter_authority(monkeypatch):
    release = legacy_release()
    monkeypatch.setattr(registry, 'numbered_strategy', lambda number: release)
    assert not adapter.declared_fixed_rule(release.number)
    assert not adapter.declared_fixed_rule(release.number, release.rule_set_contracts[0])


@pytest.mark.parametrize('number,rule', [(True, None), ('7001', None), (7001, True), (None, None)])
def test_invalid_request_types_never_consult_registry(monkeypatch, number, rule):
    monkeypatch.setattr(registry, 'numbered_strategy', lambda *args: pytest.fail('Invalid types reached registry'))
    assert not adapter.declared_fixed_rule(number, rule)


def test_unpublished_identity_has_no_adapter_authority(monkeypatch):
    def missing(number):
        raise ValueError(f'Strategy {number} is not published')
    monkeypatch.setattr(registry, 'numbered_strategy', missing)
    assert not adapter.declared_fixed_rule(7001)
