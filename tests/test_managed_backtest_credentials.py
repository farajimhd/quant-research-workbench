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
