"""The backend must normalize SSL before opening a secure Keeper session."""
from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import run_backend


def test_backend_keeps_standard_ssl_context(monkeypatch):
    calls = []
    monkeypatch.setattr(run_backend, "ssl", SimpleNamespace(
        SSLContext=type("SSLContext", (), {"__module__": "ssl"})))
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(
        extract_from_ssl=lambda: calls.append("extract")))
    run_backend._restore_stdlib_ssl_for_keeper()
    assert calls == []


def test_backend_restores_injected_ssl_context(monkeypatch):
    calls = []
    monkeypatch.setattr(run_backend, "ssl", SimpleNamespace(
        SSLContext=type("SSLContext", (), {
            "__module__": "pip._vendor.truststore._api"})))
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(
        extract_from_ssl=lambda: calls.append("extract")))
    run_backend._restore_stdlib_ssl_for_keeper()
    assert calls == ["extract"]


def test_direct_backend_launch_applies_catalog_before_uvicorn(monkeypatch):
    from src.backend import managed_backtest_credentials
    from scripts import service_manager
    defaults = {"BACKTEST_V4_RUNNER_CREDENTIAL_FILE": "private/runner.env",
                "BACKTEST_V3_READ_CREDENTIAL_FILE": "private/read.env",
                "TRADING_JOURNAL_CREDENTIAL_FILE": "private/journal.env",
                "TRADING_KEEPER_LAN_HOST": "keeper.test"}
    monkeypatch.setattr(service_manager, "_load_catalog", lambda: ({"backend": SimpleNamespace(environment=defaults)}, {}))
    monkeypatch.setattr(managed_backtest_credentials, "load_managed_backtest_credentials", lambda **kw: False)
    for key in defaults:
        monkeypatch.delenv(key, raising=False)
    for suffix in ("URL", "USER", "PASSWORD"):
        monkeypatch.delenv("BACKTEST_V4_RUNNER_CLICKHOUSE_" + suffix, raising=False)
        monkeypatch.delenv("TRADING_JOURNAL_CLICKHOUSE_" + suffix, raising=False)
        monkeypatch.delenv("BACKTEST_V3_READ_CLICKHOUSE_" + suffix, raising=False)
    def run(*args, **kwargs):
        assert all(run_backend.os.environ[key] == value for key, value in defaults.items())
        assert args == ("src.backend.app:app",)
        assert kwargs["lifespan"] == "on"
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))
    monkeypatch.setattr(sys, "argv", ["run_backend.py"])
    assert run_backend.main() == 0


def test_direct_backend_launch_preserves_explicit_credentials(monkeypatch):
    from src.backend import managed_backtest_credentials
    from scripts import service_manager
    defaults = {"BACKTEST_V4_RUNNER_CREDENTIAL_FILE": "default/runner.env",
                "TRADING_JOURNAL_CREDENTIAL_FILE": "default/journal.env",
                "TRADING_KEEPER_LAN_HOST": "default-host"}
    monkeypatch.setattr(service_manager, "_load_catalog", lambda: ({"backend": SimpleNamespace(environment=defaults)}, {}))
    monkeypatch.setattr(managed_backtest_credentials, "load_managed_backtest_credentials", lambda **kw: False)
    env = {"BACKTEST_V4_RUNNER_CREDENTIAL_FILE": "selected/runner.env", "TRADING_KEEPER_LAN_HOST": "selected-host"}
    run_backend._apply_service_environment(env)
    assert env["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] == "selected/runner.env"
    assert env["TRADING_KEEPER_LAN_HOST"] == "selected-host"
    assert env["TRADING_JOURNAL_CREDENTIAL_FILE"] == "default/journal.env"


def test_direct_backend_launch_never_mixes_inline_and_file_defaults(monkeypatch):
    from src.backend import managed_backtest_credentials
    from scripts import service_manager
    defaults = {"BACKTEST_V4_RUNNER_CREDENTIAL_FILE": "default/runner.env",
                "TRADING_JOURNAL_CREDENTIAL_FILE": "default/journal.env"}
    monkeypatch.setattr(service_manager, "_load_catalog", lambda: ({"backend": SimpleNamespace(environment=defaults)}, {}))
    def workstation_bootstrap(environment):
        environment["BACKTEST_V4_RUNNER_CLICKHOUSE_USER"] = "backtest_v4_runner"
        return True
    monkeypatch.setattr(managed_backtest_credentials, "load_managed_backtest_credentials", workstation_bootstrap)
    env = {"TRADING_JOURNAL_CLICKHOUSE_PASSWORD": "explicit-test-value"}
    run_backend._apply_service_environment(env)
    assert "BACKTEST_V4_RUNNER_CREDENTIAL_FILE" not in env
    assert "TRADING_JOURNAL_CREDENTIAL_FILE" not in env
