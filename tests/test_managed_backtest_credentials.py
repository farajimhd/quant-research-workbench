"""App bootstrap keeps provisioned Strategy 1 secrets out of source and logs."""
from __future__ import annotations

import pytest

from src.backend.managed_backtest_credentials import (
    load_managed_backtest_credentials,
)


def _write_files(tmp_path):
    (tmp_path / "trading_journal.env").write_text(
        "TRADING_JOURNAL_CLICKHOUSE_URL=http://example.test:18123\n"
        "TRADING_JOURNAL_CLICKHOUSE_USER=journal\n"
        "TRADING_JOURNAL_CLICKHOUSE_PASSWORD=private-j\n",
        encoding="utf-8")
    (tmp_path / "backtest_v4_runner.env").write_text(
        "BACKTEST_V4_RUNNER_CLICKHOUSE_URL=http://example.test:18123\n"
        "BACKTEST_V4_RUNNER_CLICKHOUSE_USER=backtest_v4_runner\n"
        "BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD=private-v4\n",
        encoding="utf-8")
    (tmp_path / "backtest_v3_read.env").write_text("private read credential",
                                                   encoding="utf-8")


def test_workstation_loads_exact_private_files(tmp_path):
    _write_files(tmp_path)
    env = {}
    assert load_managed_backtest_credentials(
        environment=env, workstation="DESKTOP-SAAI85T",
        secret_root=tmp_path)
    assert env["BACKTEST_V4_RUNNER_CLICKHOUSE_USER"] == "backtest_v4_runner"
    assert env["TRADING_JOURNAL_CLICKHOUSE_PASSWORD"] == "private-j"
    assert env["BACKTEST_V3_READ_CREDENTIAL_FILE"] == str(
        tmp_path / "backtest_v3_read.env")


def test_optional_program_runner_uses_exact_private_keys_without_generic_write_role(tmp_path):
    from src.backend.program_app_credentials import program_app_writer_factory
    _write_files(tmp_path)
    (tmp_path / 'program_v4_runner.env').write_text(
        'PROGRAM_V4_RUNNER_CLICKHOUSE_URL=http://example.test:18123\n'
        'PROGRAM_V4_RUNNER_CLICKHOUSE_USER=program_v4_runner\n'
        'PROGRAM_V4_RUNNER_CLICKHOUSE_PASSWORD=private-program\n', encoding='utf-8')
    env = {}
    assert load_managed_backtest_credentials(environment=env, workstation='DESKTOP-SAAI85T', secret_root=tmp_path)
    assert env['PROGRAM_V4_RUNNER_CLICKHOUSE_USER'] == 'program_v4_runner'
    with pytest.raises(ValueError, match='explicit publication role'):
        program_app_writer_factory('program_v4_runner', query_limits={})
    (tmp_path / 'program_v4_runner.env').write_text('UNRELATED_SECRET=private\n', encoding='utf-8')
    untouched = {}
    with pytest.raises(ValueError, match='credential contract'):
        load_managed_backtest_credentials(environment=untouched, workstation='DESKTOP-SAAI85T', secret_root=tmp_path)
    assert untouched == {}


def test_other_host_or_missing_file_does_not_change_environment(tmp_path):
    _write_files(tmp_path)
    env = {}
    assert not load_managed_backtest_credentials(
        environment=env, workstation="LAPTOP", secret_root=tmp_path)
    assert not env
    (tmp_path / "backtest_v4_runner.env").unlink()
    assert not load_managed_backtest_credentials(
        environment=env, workstation="DESKTOP-SAAI85T",
        secret_root=tmp_path)
    assert not env


def test_conflicting_or_malformed_credentials_fail_without_partial_load(tmp_path):
    _write_files(tmp_path)
    env = {"BACKTEST_V4_RUNNER_CLICKHOUSE_USER": "different"}
    with pytest.raises(ValueError, match="conflicts"):
        load_managed_backtest_credentials(
            environment=env, workstation="DESKTOP-SAAI85T",
            secret_root=tmp_path)
    assert env == {"BACKTEST_V4_RUNNER_CLICKHOUSE_USER": "different"}
    (tmp_path / "backtest_v4_runner.env").write_text(
        "BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD=private-v4\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        load_managed_backtest_credentials(
            environment={}, workstation="DESKTOP-SAAI85T",
            secret_root=tmp_path)


def _write_program_file(tmp_path):
    (tmp_path / "backtest_v4_program_runner.env").write_text(
        "BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_URL=http://example.test:18123\n"
        "BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_USER=backtest_v4_program_runner\n"
        "BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_PASSWORD=private-program\n",
        encoding="utf-8")


def test_launcher_bootstrap_reaches_dedicated_program_writer(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import run_backend, service_manager
    from src.backend import managed_backtest_credentials as credentials
    from src.trading_runtime import clickhouse_transport
    from src.trading_runtime.arte_journal_writer import _v4_runner_credentials
    _write_files(tmp_path)
    _write_program_file(tmp_path)
    original = credentials.load_managed_backtest_credentials
    monkeypatch.setattr(credentials, "load_managed_backtest_credentials", lambda **kw:
        original(**kw, workstation="DESKTOP-SAAI85T", secret_root=tmp_path))
    monkeypatch.setattr(service_manager, "_load_catalog", lambda:
        ({"backend": SimpleNamespace(environment={})}, None))
    monkeypatch.setattr(clickhouse_transport, "workstation_ipv4_transport", lambda url: url)
    env = {}
    run_backend._apply_service_environment(env)
    monkeypatch.setattr("src.trading_runtime.arte_journal_writer.os.environ", env)
    assert _v4_runner_credentials(program_decisions=True) == (
        "http://example.test:18123", "backtest_v4_program_runner", "private-program")
    assert _v4_runner_credentials() == (
        "http://example.test:18123", "backtest_v4_runner", "private-v4")


@pytest.mark.parametrize("kind", ["malformed_program", "program_conflict", "reader_conflict"])
def test_optional_principal_and_reader_validation_are_atomic(tmp_path, kind):
    _write_files(tmp_path)
    _write_program_file(tmp_path)
    env = {}
    if kind == "malformed_program":
        (tmp_path / "backtest_v4_program_runner.env").write_text(
            "BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_PASSWORD=private-program\n")
    elif kind == "program_conflict":
        env["BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_USER"] = "other"
    else:
        env["BACKTEST_V3_READ_CREDENTIAL_FILE"] = "other"
    before = dict(env)
    with pytest.raises(ValueError):
        load_managed_backtest_credentials(environment=env,
            workstation="DESKTOP-SAAI85T", secret_root=tmp_path)
    assert env == before
