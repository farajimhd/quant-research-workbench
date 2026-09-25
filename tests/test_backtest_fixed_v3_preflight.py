"""V3 running, cold-read and terminal principals have distinct exact grants."""
from __future__ import annotations

import pytest

from src.backend import backtest_fixed_v3_preflight as subject


def test_v3_profiles_assign_running_and_terminal_writes_disjointly(monkeypatch):
    observed = []
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda _client, *, tables: observed.append(
                            ("layout", {table.name for table in tables})))
    monkeypatch.setattr(subject, "journal_permission_preflight",
                        lambda _client, **kwargs: observed.append(
                            ("grants", kwargs["journal_tables"])))
    monkeypatch.setattr(subject, "_exact_grants",
                        lambda _client, writable: observed.append(
                            ("exact", writable)))
    subject.running_v3_preflight(object())
    running = observed[-1][1]
    subject.terminal_v3_preflight(object())
    terminal = observed[-1][1]
    subject.read_v3_preflight(object())
    assert observed[-1] == ("exact", frozenset())
    assert "trading_commit_v3" in running and "trading_commit_v3" not in terminal
    assert "trading_backtest_squeeze_episode_v1" in running
    assert "trading_backtest_terminal_commit_v3" in terminal
    assert "trading_backtest_terminal_commit_v3" not in running
    assert "trading_portfolio_snapshot_v1" in terminal
    assert "trading_portfolio_snapshot_v1" not in running


def test_v3_exact_grants_rejects_extra_terminal_insert():
    class Client:
        def execute(self, sql):
            if sql == "SELECT currentUser()":
                return "terminal_user"
            if sql == "SHOW GRANTS FINAL":
                return ("GRANT INSERT ON arte.trading_backtest_terminal_commit_v3 "
                        "TO terminal_user\n"
                        "GRANT INSERT ON arte.trading_commit_v3 TO terminal_user")
            raise AssertionError(sql)
    with pytest.raises(RuntimeError, match="extra INSERT"):
        subject._exact_grants(
            Client(), frozenset({"trading_backtest_terminal_commit_v3"}))
