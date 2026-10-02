"""Exercise the actual CLI's bounded plan and source-admission paths."""
import sys
from types import SimpleNamespace

import pytest

from scripts.clickhouse import publish_strategy_forty_configuration as command
from test_strategy_forty_release import source_fixture as parent


def test_dry_plan_uses_exact_parent_and_does_not_dispatch_publication(monkeypatch, capsys):
    from scripts.clickhouse import smoke_strategy_one_backtest as credentials
    from src.backend import backtest_v3_clients as clients
    source = parent()
    reader = SimpleNamespace(close=lambda: None)
    calls = []
    monkeypatch.setattr(sys, 'argv', ['publish40', '--approved-code-commit', 'd'*40,
                                    '--approval-reference', 'test-only'])
    monkeypatch.setattr(command, 'approved_source', lambda commit: 'e'*64)
    monkeypatch.setattr(credentials, '_load_private_credentials', lambda: None)
    monkeypatch.setattr(clients, 'v3_client', lambda role: reader)
    def certify(actual, number):
        assert actual is reader and number == 39
        calls.append(number)
        return source
    monkeypatch.setattr(command, 'certify_numbered_configuration', certify)
    assert command.main() == 0 and calls == [39]
    output = capsys.readouterr().out
    assert 'Strategy 40 plan:' in output and 'no publication performed' in output


def test_uncommitted_source_rejection_precedes_database_access(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['publish40', '--approved-code-commit', 'd'*40,
                                    '--approval-reference', 'test-only'])
    def reject(commit):
        raise ValueError('uncommitted reviewed source')
    monkeypatch.setattr(command, 'approved_source', reject)
    assert command.main() == 1
    assert 'uncommitted reviewed source' in capsys.readouterr().err


def test_receiver_requires_workstation_before_reading_input(monkeypatch):
    monkeypatch.setattr(command.platform, 'node', lambda: 'LAPTOP')
    with pytest.raises(RuntimeError, match='workstation-only'):
        command.receive()


def test_clean_commit_still_requires_complete_strategy40_source_certification(monkeypatch):
    calls = []
    monkeypatch.setattr(command.subprocess, 'check_output',
        lambda args, **kwargs: 'd'*40 if args[1] == 'rev-parse' else '')
    def reject(number):
        calls.append(number)
        raise ValueError('uncertified native source')
    monkeypatch.setattr(command, 'certify_numbered_fixed_v4_projection', reject)
    with pytest.raises(ValueError, match='uncertified native source'):
        command.approved_source('d'*40)
    assert calls == [40]


def test_verified_envelope_preserves_exact_parent_and_rejects_foreign_provenance():
    from src.trading_runtime.strategy_forty_release import derive_strategy_forty_configuration
    envelope = derive_strategy_forty_configuration(parent(),
        approved_code_commit='d'*40, approved_code_fingerprint='e'*64, approval_reference='test-only')
    payload, nodes = command._verified_numbered_envelope(envelope)
    assert payload['strategy']['strategy_number'] == 40
    assert len(nodes) == envelope['node_count']
    with pytest.raises(ValueError, match='provenance'):
        command._verified_numbered_envelope(dict(envelope, source_candidate_hash='f'*64))
