"""Strategy 15 removes only the holding-age gate from exact parent 14."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest

from pipelines.strategy_one.strategy_fourteen_configuration import compile_strategy_fourteen_configuration
from pipelines.strategy_one.strategy_fifteen_configuration import compile_strategy_fifteen_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.numbered_fixed_strategy import followthrough_policy_payload
from src.trading_runtime.strategy_fifteen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, RULES, verify_strategy_fifteen_manifest,
)
from test_strategy_fourteen_configuration import parent as thirteenth_parent


def parent():
    envelope = compile_strategy_fourteen_configuration(
        thirteenth_parent(), approved_code_commit='d' * 40,
        approved_code_fingerprint='e' * 64, approval_reference='test-numbered-admission')
    return CertifiedStrategyOneConfiguration(
        PARENT_REVISION_ID.split(':')[1], PARENT_PAYLOAD_HASH,
        envelope['node_hash'], envelope['source_candidate_id'],
        envelope['source_candidate_hash'], 'test-only', envelope['payload'])


def compile_fifteen(source):
    return compile_strategy_fifteen_configuration(
        source, approved_code_commit='d' * 40,
        approved_code_fingerprint='e' * 64, approval_reference='all-holding-failure-exit')


def test_fifteenth_changes_only_followthrough_policy_and_preserves_parent():
    source = parent()
    original = deepcopy(source.payload)
    result = compile_fifteen(source)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_fifteen_manifest(result['payload']['strategy'])
    assert source.payload == original
    assert result['payload']['strategy']['parameters'] == original['strategy']['parameters']
    previous = original['strategy']['numbered_release']
    for key in ('momentum_policy', 'recent_bos_policy', 'session_policy',
                'activation_policy', 'add_policy', 'trailing_policy', 'target_policy',
                'entry_price_policy', 'entry_scope_policy'):
        assert manifest[key] == previous[key]
    assert manifest['followthrough_policy'] == followthrough_policy_payload()
    assert manifest['followthrough_policy'] != previous['followthrough_policy']
    assert not any('60' in str(value) for value in manifest['followthrough_policy'].values())
    assert RULES[-1] == 'strategy-fifteen-unlimited-followthrough-failure-v1'
    assert 'strategy-eleven-early-followthrough-failure-v1' not in RULES
    from src.trading_runtime.strategy_fourteen_release import RULES as parent_rules
    assert RULES[:-1] == tuple(rule for rule in parent_rules
                              if rule != 'strategy-eleven-early-followthrough-failure-v1')
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    assert numbered_strategy_parent(15) == 14


@pytest.mark.parametrize('field,value', [('payload_hash', 'f' * 64),
                                        ('attempt_id', '00000000-0000-0000-0000-000000000001')])
def test_exact_parent_identity_is_required(field, value):
    with pytest.raises(ValueError, match='exact pinned'):
        compile_fifteen(replace(parent(), **{field: value}))


def test_parent_assignments_cannot_be_inherited():
    source = parent()
    payload = deepcopy(source.payload)
    payload['assignments'] = [{'mutable': True}]
    with pytest.raises(ValueError, match='mutable assignments'):
        compile_fifteen(replace(source, payload=payload))


def test_resealed_time_limited_manifest_is_rejected():
    result = compile_fifteen(parent())
    strategy = deepcopy(result['payload']['strategy'])
    manifest = strategy['numbered_release']
    manifest['followthrough_policy']['maximum_holding_age_ms'] = 60_000
    manifest['manifest_hash'] = sha256(canonical_json({
        key: value for key, value in manifest.items() if key != 'manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='installed sealed contract'):
        verify_strategy_fifteen_manifest(strategy)


def test_cli_help_runs_without_credentials_or_publication():
    from pathlib import Path
    import subprocess
    import sys
    result = subprocess.run([
        sys.executable, '-B',
        str(Path('scripts/clickhouse/publish_strategy_fifteen_configuration.py')),
        '--help'], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert 'Strategy 15' in result.stdout
    assert '--apply' in result.stdout
    assert '--approved-code-commit' in result.stdout
