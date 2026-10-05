"""Strategy46 producer compiler, sealed envelope and bounded publication CLI."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import sys
from types import SimpleNamespace

import pytest

from pipelines.strategy_one.strategy_forty_six_configuration import compile_strategy_forty_six_configuration
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from scripts.clickhouse import publish_strategy_forty_six_configuration as command
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
from src.trading_runtime import strategy_forty_six_release as release
from test_strategy_forty_six_release import source_fixture, APPROVAL


def test_compiler_preserves_parent_and_requires_installed_projection(monkeypatch):
    from src.backend import backtest_fixed_v4_certification as certification
    calls = []
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection',
        lambda number: calls.append(number))
    source = source_fixture()
    before = deepcopy(source.payload)
    envelope = compile_strategy_forty_six_configuration(source, **APPROVAL)
    assert source.payload == before and calls == [46]
    assert envelope == release.derive_strategy_forty_six_configuration(source, **APPROVAL)
    assert envelope['payload']['strategy']['numbered_release']['source_revision_id'] == release.PARENT_REVISION_ID


def test_compiler_propagates_native_source_rejection(monkeypatch):
    from src.backend import backtest_fixed_v4_certification as certification
    def reject(number):
        assert number == 46
        raise ValueError('uncertified native source')
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', reject)
    with pytest.raises(ValueError, match='uncertified'):
        compile_strategy_forty_six_configuration(source_fixture(), **APPROVAL)


@pytest.mark.parametrize('field,value', [('payload_hash', 'f'*64),
    ('attempt_id', '00000000-0000-0000-0000-000000000001')])
def test_compiler_rejects_foreign_exact_source_before_projection(field, value, monkeypatch):
    from src.backend import backtest_fixed_v4_certification as certification
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection',
        lambda number: pytest.fail('foreign source reached certification'))
    with pytest.raises(ValueError, match='exact pinned'):
        compile_strategy_forty_six_configuration(replace(source_fixture(), **{field:value}), **APPROVAL)


def test_verified_envelope_accepts46_and_rejects_changed_source_and_content():
    envelope = release.derive_strategy_forty_six_configuration(source_fixture(), **APPROVAL)
    payload, nodes = _verified_numbered_envelope(envelope)
    assert payload['strategy']['strategy_number'] == 46 and len(nodes) == envelope['node_count']
    with pytest.raises(ValueError, match='provenance'):
        _verified_numbered_envelope(dict(envelope, source_candidate_hash='f'*64))
    with pytest.raises(ValueError, match='seal'):
        _verified_numbered_envelope(dict(envelope, node_hash='f'*64))


def test_resealed_policy_mutation_is_not_a_valid_envelope():
    envelope = release.derive_strategy_forty_six_configuration(source_fixture(), **APPROVAL)
    manifest = envelope['payload']['strategy']['numbered_release']
    manifest['early_original_risk_failure_policy']['unapproved'] = True
    manifest['manifest_hash'] = sha256(canonical_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest()
    nodes = encode_nodes(envelope['payload'])
    envelope.update(payload_hash=sha256(canonical_json(envelope['payload']).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes))
    with pytest.raises(ValueError):
        _verified_numbered_envelope(envelope)


@pytest.mark.parametrize('number', [43, 44, 45])
def test_withdrawn_numbers_remain_rejected(number):
    envelope = release.derive_strategy_forty_six_configuration(source_fixture(), **APPROVAL)
    envelope['payload']['strategy'].update(strategy_number=number, revision=number)
    with pytest.raises(ValueError):
        _verified_numbered_envelope(envelope)


def test_cli_dry_plan_reads42_and_never_dispatches(monkeypatch, capsys):
    from scripts.clickhouse import smoke_strategy_one_backtest as credentials
    from src.backend import backtest_v3_clients as clients
    reader = SimpleNamespace(close=lambda: None)
    source = source_fixture()
    monkeypatch.setattr(sys, 'argv', ['publish46', '--approved-code-commit', 'd'*40,
        '--approval-reference', 'test-only'])
    monkeypatch.setattr(command, 'approved_source', lambda commit: 'e'*64)
    monkeypatch.setattr(credentials, '_load_private_credentials', lambda: None)
    monkeypatch.setattr(clients, 'v3_client', lambda role: reader)
    def certify(actual, number):
        assert actual is reader and number == 42
        return source
    monkeypatch.setattr(command, 'certify_numbered_configuration', certify)
    monkeypatch.setattr(command, 'compile_strategy_forty_six_configuration',
        lambda actual, **approval: release.derive_strategy_forty_six_configuration(actual, **approval))
    monkeypatch.setattr(command.subprocess, 'run', lambda *args, **kwargs: pytest.fail('dry run dispatched'))
    assert command.main() == 0
    output = capsys.readouterr().out
    assert 'Strategy 46 plan:' in output and 'no publication performed' in output


def test_cli_dirty_source_rejects_before_database(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['publish46', '--approved-code-commit', 'd'*40,
        '--approval-reference', 'test-only'])
    def reject(commit):
        raise ValueError('uncommitted reviewed source')
    monkeypatch.setattr(command, 'approved_source', reject)
    assert command.main() == 1
    assert 'uncommitted reviewed source' in capsys.readouterr().err


def test_cli_clean_commit_still_requires_current46_source_proof(monkeypatch):
    monkeypatch.setattr(command.subprocess, 'check_output',
        lambda args, **kwargs: 'd'*40 if args[1] == 'rev-parse' else '')
    def reject(number):
        assert number == 46
        raise ValueError('uncertified native source')
    monkeypatch.setattr(command, 'certify_numbered_fixed_v4_projection', reject)
    with pytest.raises(ValueError, match='uncertified'):
        command.approved_source('d'*40)


def test_receiver_is_workstation_only_before_input(monkeypatch):
    monkeypatch.setattr(command.platform, 'node', lambda: 'LAPTOP')
    with pytest.raises(RuntimeError, match='workstation-only'):
        command.receive()
