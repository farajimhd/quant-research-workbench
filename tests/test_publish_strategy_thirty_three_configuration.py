"""Exercise the actual CLI's bounded plan and source-admission paths."""
import sys
from types import SimpleNamespace

import pytest

from scripts.clickhouse import publish_strategy_thirty_three_configuration as command
from test_strategy_thirty_three_configuration import parent


def test_dry_plan_uses_exact_parent_and_does_not_dispatch_publication(monkeypatch, capsys):
    from scripts.clickhouse import smoke_strategy_one_backtest as credentials
    from src.backend import backtest_v3_clients as clients
    source = parent()
    reader = SimpleNamespace(close=lambda: None)
    calls = []
    monkeypatch.setattr(sys, 'argv', ['publish33', '--approved-code-commit', 'd'*40,
                                    '--approval-reference', 'test-only'])
    monkeypatch.setattr(command, 'approved_source', lambda commit: 'e'*64)
    monkeypatch.setattr(credentials, '_load_private_credentials', lambda: None)
    monkeypatch.setattr(clients, 'v3_client', lambda role: reader)
    def certify(actual, number):
        assert actual is reader and number == 32
        calls.append(number)
        return source
    monkeypatch.setattr(command, 'certify_numbered_configuration', certify)
    assert command.main() == 0 and calls == [32]
    output = capsys.readouterr().out
    assert 'Strategy 33 plan:' in output and 'no publication performed' in output


def test_uncommitted_source_rejection_precedes_database_access(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['publish33', '--approved-code-commit', 'd'*40,
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


def test_clean_commit_still_requires_complete_strategy32_source_certification(monkeypatch):
    calls = []
    monkeypatch.setattr(command.subprocess, 'check_output',
        lambda args, **kwargs: 'd'*40 if args[1] == 'rev-parse' else '')
    def reject(number):
        calls.append(number)
        raise ValueError('uncertified native source')
    monkeypatch.setattr(command, 'certify_numbered_fixed_v4_projection', reject)
    with pytest.raises(ValueError, match='uncertified native source'):
        command.approved_source('d'*40)
    assert calls == [33]
