"""Actual catalog, semantic selector and pre-write immutable publication gates."""
from dataclasses import replace
from pathlib import Path

import pytest

from src.trading_runtime import strategy_registry as registry
from src.trading_runtime.declared_native_manifest import registered_manifest_authority
from src.trading_runtime.numbered_fixed_strategy import declared_automatic_ladder_release, automatic_ladder_qualification
from src.trading_runtime.strategy_ninety_seven_release import derive_strategy_ninety_seven_configuration, release_contract
from src.backend import backtest_declared_waiting_ladder_compatibility as compatibility
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope, publish_configuration
from test_strategy_sixty_four_release import source_fixture
from test_strategy_fifty_release import APPROVAL


def envelope():
    return derive_strategy_ninety_seven_configuration(source_fixture(), **APPROVAL)


def test_real_catalog_and_declared_semantics_preserve_crossing_baseline():
    for number, mode in ((65, 'vwap_cross'), (97, 'first_eligible_above_vwap')):
        release = registry.numbered_strategy(number)
        assert declared_automatic_ladder_release(number)
        assert automatic_ladder_qualification(release)[1] == mode
        assert registry.numbered_strategy_parent(number) == 42
        executor = registry.fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
        executor.verify()
        assert executor.contract_factory().automatic_market_policy['gate']['qualification_mode'] == mode
    assert _verified_numbered_envelope(envelope())[0] == envelope()['payload']


def test_ambiguous_or_missing_qualification_is_rejected():
    release = release_contract()
    for rules in ((), (*release.rule_set_contracts, 'completed-vwap-below-above-cross@1')):
        with pytest.raises(ValueError, match='exactly one supported qualification'):
            automatic_ladder_qualification(replace(release, rule_set_contracts=rules))


def test_registry_integrity_failure_is_not_unknown_identity(monkeypatch):
    monkeypatch.setattr(registry, 'numbered_strategy', lambda _: (_ for _ in ()).throw(ValueError('broken seal')))
    with pytest.raises(ValueError, match='broken seal'):
        registered_manifest_authority(97)


@pytest.mark.parametrize('mutation', ['parent', 'same_number_parent', 'prefix', 'factory'])
def test_metadata_and_factory_drift_rejected_before_publication(monkeypatch, mutation):
    release = registry.numbered_strategy(97)
    key = (release.executor_strategy_id, release.executor_revision)
    registration = registry.fixed_strategy_executor(*key)
    if mutation == 'parent':
        altered = replace(registration, manifest_authority=replace(registration.manifest_authority, parent_number=41))
    elif mutation == 'same_number_parent':
        parent = registration.manifest_authority.parent_release_factory()
        draft = replace(parent, behavior_specification=parent.behavior_specification + ' changed', approved_digest='')
        changed_parent = replace(draft, approved_digest=draft.digest())
        altered = replace(registration, manifest_authority=replace(
            registration.manifest_authority, parent_release_factory=lambda: changed_parent))
    elif mutation == 'prefix':
        altered = replace(registration, manifest_authority=replace(registration.manifest_authority, source_prefix='foreign-parent'))
    else:
        from src.trading_runtime.strategy_sixty_five_contract import strategy_sixty_five_contract
        altered = replace(registration, contract_factory=strategy_sixty_five_contract)
    monkeypatch.setitem(registry._FIXED_REGISTRY, key, altered)
    with pytest.raises(ValueError):
        _verified_numbered_envelope(envelope())


def test_unpublished_parent_fails_before_any_insert():
    class Reader:
        calls = []
        def execute(self, query):
            self.calls.append(query)
            assert query.startswith('SELECT ')
            return ''
    client = Reader()
    with pytest.raises(RuntimeError, match='exactly one immutable typed configuration release'):
        publish_configuration(client, object(), envelope())
    assert client.calls and not any(query.startswith('INSERT ') for query in client.calls)


def test_unsupported_source_drift_is_never_stripped():
    relative = 'trading_runtime/numbered_fixed_strategy.py'
    root = Path(compatibility.__file__).parents[2]
    source = (root / 'src' / relative).read_text(encoding='utf-8')
    changed = source.replace("return selected[0], rules[selected[0]]", "return selected[0], 'foreign'")
    assert changed != source
    assert compatibility.restore_reviewed_parent_source(changed, relative) == changed


def test_exact_current_configuration_reaches_retained_core_pin():
    from src.backend import backtest_fixed_v4_certification as core
    relative = 'src/backend/backtest_strategy_one_configuration.py'
    source = (Path(core.__file__).parents[2] / relative).read_text(encoding='utf-8')
    expected = core._DRAWDOWN_CORE_REVIEWED_AST[relative]
    assert core._reviewed_fixed_lot_core_projection(source, relative, '__module__', expected)
    assert core._reviewed_fixed_lot_configuration_projection(source, '__module__', expected)
    changed = source.replace("authority.source_prefix + ':'", "authority.source_prefix + '/'")
    assert changed != source
    assert not core._reviewed_fixed_lot_core_projection(changed, relative, '__module__', expected)
    assert not core._reviewed_fixed_lot_configuration_projection(changed, '__module__', expected)


@pytest.mark.parametrize('mutation', ['envelope', 'metadata', 'race'])
def test_compatibility_integrity_and_freshness(monkeypatch, mutation):
    original = Path.read_text
    reads = 0
    def read(path, *args, **kwargs):
        nonlocal reads
        source = original(path, *args, **kwargs)
        if path.name == 'backtest_declared_waiting_ladder_compatibility.py':
            reads += 1
            if mutation == 'envelope':
                return source + '\nunknown = True\n'
            if mutation == 'metadata':
                return source.replace("'current_ast': '", "'current_ast': '0", 1)
            if reads > 1:
                return source + '\n# raced\n'
        return source
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError):
        compatibility.restore_reviewed_parent_source('unrelated = True', 'foreign.py')
