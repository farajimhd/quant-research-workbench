from dataclasses import replace
from copy import deepcopy
from hashlib import sha256

import pytest

from src.trading_runtime import strategy_thirty_six_release as parent
from src.trading_runtime import strategy_thirty_seven_release as child
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from test_strategy_thirty_six_release import source_fixture
from test_strategy_thirty_three_configuration import APPROVAL
from src.trading_runtime.journal_contract import canonical_json


def published_parent_fixture():
    result = parent.derive_strategy_thirty_six_configuration(source_fixture(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'])


def test_prepared_contract_preserves_parent_and_declares_only_episode_veto():
    previous, release = parent.release_contract(), child.release_contract()
    release.verify()
    assert release.number == release.executor_revision == 37
    assert release.input_contracts == previous.input_contracts
    assert release.evaluation_interval == previous.evaluation_interval
    assert release.rule_set_contracts[:-1] == previous.rule_set_contracts
    assert release.rule_set_contracts[-1] == child.EPISODE_ACTIVITY_POLICY['policy_id']
    assert child.INHERITED_POLICIES == {**parent.INHERITED_POLICIES,
                                       'entry_activity_policy': parent.ENTRY_ACTIVITY_POLICY}
    with pytest.raises(ValueError):
        replace(release, behavior_specification='changed').verify()


def test_exact_parent_is_required_before_future_publication():
    source = published_parent_fixture()
    assert child.verify_exact_parent(source)
    for foreign in (object(), replace(source, payload_hash='f' * 64),
                    replace(source, attempt_id='00000000-0000-0000-0000-000000000001')):
        with pytest.raises(ValueError, match='exact pinned'):
            child.verify_exact_parent(foreign)


def test_installed_contract_matches_exact_release_and_next_number_is_closed():
    from src.trading_runtime.strategy_registry import numbered_strategy
    assert numbered_strategy(37) == child.release_contract()
    with pytest.raises(ValueError):
        numbered_strategy(38)


def test_exact_parent_derivation_preserves_cash_costs_and_other_trading_settings():
    source = published_parent_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_thirty_seven_configuration(source, **APPROVAL)
    assert source.payload == before
    for name in before.keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert result['payload'][name] == before[name]
    identity = {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'}
    for name in before['strategy'].keys() - identity:
        assert result['payload']['strategy'][name] == before['strategy'][name]
    assert result['payload_hash'] == sha256(canonical_json(result['payload']).encode()).hexdigest()
    assert child.verify_prepared_strategy_thirty_seven_manifest(result['payload']['strategy'])


@pytest.mark.parametrize('policy', [*child.INHERITED_POLICIES, 'episode_activity_policy'])
def test_resealed_policy_mutation_cannot_change_prepared_release(policy):
    strategy = child.derive_strategy_thirty_seven_configuration(published_parent_fixture(), **APPROVAL)['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy] = {'changed': True}
    manifest['manifest_hash'] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='pinned policy'):
        child.verify_prepared_strategy_thirty_seven_manifest(strategy)


def test_installed_compiler_requires_full_inherited_and_episode_source_proof():
    from pipelines.strategy_one.strategy_thirty_seven_configuration import compile_strategy_thirty_seven_configuration
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
    result = compile_strategy_thirty_seven_configuration(published_parent_fixture(), **APPROVAL)
    assert is_numbered_fixed_configuration(result['payload'])
    assert child.verify_strategy_thirty_seven_manifest(result['payload']['strategy'])
    assert len(certify_numbered_fixed_v4_projection(37)) == 64


@pytest.mark.parametrize('relative', [
    'src/trading_runtime/strategy_episode_activity_veto.py',
    'src/backend/backtest_strategy_episode_activity_gate.py',
    'src/backend/backtest_strategy_episode_activity_source.py',
    'src/trading_runtime/strategy_thirty_seven_release.py',
    'pipelines/strategy_one/strategy_thirty_seven_configuration.py',
    'pipelines/strategy_one/configuration_publisher.py',
    'scripts/clickhouse/publish_strategy_thirty_seven_configuration.py',
])
def test_episode_source_certificate_rejects_modified_authority(relative, tmp_path):
    from pathlib import Path
    from src.backend.backtest_strategy_episode_activity_certification import certify_episode_activity_source
    original = Path(relative).read_text()
    changed = tmp_path / 'changed.py'
    changed.write_text(original + '\nUNREVIEWED_CHANGE = True\n')
    with pytest.raises(ValueError, match='pinned episode source changed'):
        certify_episode_activity_source(source_overrides={relative: changed})
