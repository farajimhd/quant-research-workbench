from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.trading_runtime import arte_command_recovery as recovery


class Broker:
    def __init__(self, orders=(), trades=(), error=None):
        self.orders = list(orders)
        self.executions = list(trades)
        self.error = error

    async def live_orders(self):
        if self.error:
            raise self.error
        return self.orders

    async def trades(self, days=7):
        assert days == 7
        return self.executions


def install_journal(monkeypatch, commands, transitions=()):
    monkeypatch.setattr(recovery, "load_committed_prefix",
                        lambda _client, _run: SimpleNamespace(last_sequence=max(
                            (row["sequence"] for row in (*commands, *transitions)), default=0,
                        )))

    def page(_client, _prefix, *, after_sequence, limit):
        return tuple(command for command in commands
                     if command["sequence"] > after_sequence)[:limit]

    monkeypatch.setattr(recovery, "load_committed_order_command_page", page)

    def transition_page(_client, _prefix, *, after_sequence, limit):
        return tuple(row for row in transitions if row["sequence"] > after_sequence)[:limit]

    monkeypatch.setattr(recovery, "load_committed_order_transition_page", transition_page)


def command(sequence=1):
    return {"sequence": sequence, "account_id": "A", "client_order_id": f"C{sequence}",
            "command_id": f"cmd-{sequence}", "conid": 101}


def test_command_audit_never_treats_absence_as_safe(monkeypatch):
    install_journal(monkeypatch, [command(1), command(2), command(3)])
    broker = Broker(
        orders=[SimpleNamespace(account="A", cOID="C1", conid=101)],
        trades=[SimpleNamespace(account="A", order_ref="C2", conid=101)],
    )
    result = asyncio.run(recovery.audit_committed_commands(None, broker, "run", page_size=1))
    assert (result.committed_commands, result.open_order_matches,
            result.execution_matches, result.terminal_transition_matches,
            result.unresolved_commands) == (3, 1, 1, 0, 1)
    assert result.unresolved_sample == (("A", "C3"),)
    assert not result.admission_safe


def test_command_audit_does_not_enable_admission_from_open_order(monkeypatch):
    install_journal(monkeypatch, [command()])
    result = asyncio.run(recovery.audit_committed_commands(
        None, Broker(orders=[SimpleNamespace(account="A", cOID="C1", conid=101)]), "run",
    ))
    assert result.unresolved_commands == 0
    assert not result.admission_safe


def test_command_audit_rejects_broker_contradiction(monkeypatch):
    install_journal(monkeypatch, [command()])
    with pytest.raises(RuntimeError, match="contradicts"):
        asyncio.run(recovery.audit_committed_commands(
            None, Broker(orders=[SimpleNamespace(account="A", cOID="C1", conid=999)]),
            "run",
        ))


def test_command_audit_propagates_broker_failure(monkeypatch):
    install_journal(monkeypatch, [command()])
    with pytest.raises(ConnectionError):
        asyncio.run(recovery.audit_committed_commands(
            None, Broker(error=ConnectionError()), "run",
        ))


def test_latest_committed_terminal_transition_resolves_absent_broker_order(monkeypatch):
    install_journal(monkeypatch, [command()], transitions=[
        {"sequence": 2, "account_id": "A", "command_id": "cmd-1",
         "client_order_id": "C1", "conid": 101, "terminal": 1},
    ])
    result = asyncio.run(recovery.audit_committed_commands(None, Broker(), "run"))
    assert result.terminal_transition_matches == 1
    assert result.unresolved_commands == 0
    assert not result.admission_safe  # Full OMS state recovery remains separate.


def test_later_nonterminal_transition_overrides_prior_terminal(monkeypatch):
    install_journal(monkeypatch, [command()], transitions=[
        {"sequence": 2, "account_id": "A", "command_id": "cmd-1",
         "client_order_id": "C1", "conid": 101, "terminal": 1},
        {"sequence": 3, "account_id": "A", "command_id": "cmd-1",
         "client_order_id": "C1", "conid": 101, "terminal": 0},
    ])
    result = asyncio.run(recovery.audit_committed_commands(None, Broker(), "run"))
    assert result.terminal_transition_matches == 0
    assert result.unresolved_commands == 1


def test_orphan_transition_fails_recovery(monkeypatch):
    install_journal(monkeypatch, [command()], transitions=[
        {"sequence": 2, "account_id": "A", "command_id": "other",
         "client_order_id": "C9", "conid": 101, "terminal": 1},
    ])
    with pytest.raises(RuntimeError, match="no matching order command"):
        asyncio.run(recovery.audit_committed_commands(None, Broker(), "run"))
