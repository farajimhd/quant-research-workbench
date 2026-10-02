"""Strategy38 preserves trading policies while pinning its immutable parent."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_thirty_seven_release as parent
from src.trading_runtime import strategy_thirty_eight_release as child
from src.trading_runtime.journal_contract import canonical_json
from test_strategy_thirty_seven_release import published_parent_fixture
from test_strategy_thirty_three_configuration import APPROVAL


def source_fixture():
    result = parent.derive_strategy_thirty_seven_configuration(published_parent_fixture(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'])


def test_exact_parent_and_trading_policy_preservation():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_thirty_eight_configuration(source, **APPROVAL)
    assert source.payload == before
    previous, release = parent.release_contract(), child.release_contract()
    release.verify()
    assert release.number == release.executor_revision == 38
    assert release.rule_set_contracts == previous.rule_set_contracts
    assert release.input_contracts == previous.input_contracts
    assert release.evaluation_interval == previous.evaluation_interval
    assert child.INHERITED_POLICIES == parent.INHERITED_POLICIES
    assert child.EPISODE_ACTIVITY_POLICY == parent.EPISODE_ACTIVITY_POLICY
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][name] == before[name]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - identity:
        assert result['payload']['strategy'][name] == before['strategy'][name]
    assert result['payload_hash'] == sha256(canonical_json(result['payload']).encode()).hexdigest()
    assert child.verify_prepared_strategy_thirty_eight_manifest(result['payload']['strategy'])


def test_parent_identity_and_payload_are_both_required():
    source = source_fixture()
    for foreign in (object(), replace(source, payload_hash='f' * 64),
                    replace(source, attempt_id='00000000-0000-0000-0000-000000000001')):
        with pytest.raises(ValueError, match='exact pinned'):
            child.verify_exact_parent(foreign)


def test_installed_capabilities_preserve_parent_session_and_order_policy():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    previous, current = numbered_fixed_strategy(37), numbered_fixed_strategy(38)
    for name in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing',
                 'allows_target_escalation', 'caps_entry_at_reference_ask'):
        assert getattr(current, name) == getattr(previous, name)
    for boundary in (0, 100, 19_499_900, 19_500_000, 19_740_000, 19_800_000,
                     43_200_000, 43_200_100, 57_000_000, 57_300_000, 57_600_000):
        for name in ('entry_allowed', 'acquisition_cutoff', 'liquidation_due'):
            assert getattr(current, name)(boundary) == getattr(previous, name)(boundary)


@pytest.mark.parametrize('policy', [*child.INHERITED_POLICIES, 'episode_activity_policy'])
def test_resealed_trading_policy_mutation_is_rejected(policy):
    strategy = child.derive_strategy_thirty_eight_configuration(source_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'changed': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='pinned policy'):
        child.verify_prepared_strategy_thirty_eight_manifest(strategy)


def test_installed_compiler_requires_full_inherited_execution_proof():
    from pipelines.strategy_one.strategy_thirty_eight_configuration import compile_strategy_thirty_eight_configuration
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
    from src.trading_runtime.strategy_registry import numbered_strategy, numbered_strategy_parent
    result = compile_strategy_thirty_eight_configuration(source_fixture(), **APPROVAL)
    assert numbered_strategy(38) == child.release_contract()
    assert numbered_strategy_parent(38) == 37
    assert is_numbered_fixed_configuration(result['payload'])
    assert child.verify_strategy_thirty_eight_manifest(result['payload']['strategy'])
    assert len(certify_numbered_fixed_v4_projection(38)) == 64
    with pytest.raises(ValueError):
        numbered_strategy(42)


@pytest.mark.parametrize('relative', [
    'src/trading_runtime/strategy_thirty_eight_release.py',
    'pipelines/strategy_one/strategy_thirty_eight_configuration.py',
    'src/trading_runtime/strategy_registry.py',
    'src/trading_runtime/numbered_fixed_strategy.py',
    'src/backend/backtest_strategy_one_configuration.py',
    'pipelines/strategy_one/configuration_publisher.py',
    'scripts/clickhouse/publish_strategy_thirty_eight_configuration.py',
])
def test_release_certificate_rejects_unreviewed_source(relative, tmp_path):
    from pathlib import Path
    from src.backend.backtest_strategy_thirty_eight_certification import certify_strategy_thirty_eight_source
    changed = tmp_path / 'changed.py'
    changed.write_text(Path(relative).read_text(encoding='utf-8') + '\nUNREVIEWED_CHANGE = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned release source changed'):
        certify_strategy_thirty_eight_source(source_overrides={relative: changed})
