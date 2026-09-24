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


def install_journal(monkeypatch, commands):
    monkeypatch.setattr(recovery, "load_committed_prefix",
                        lambda _client, _run: SimpleNamespace(last_sequence=len(commands)))

    def page(_client, _prefix, *, after_sequence, limit):
        return tuple(command for command in commands
                     if command["sequence"] > after_sequence)[:limit]

    monkeypatch.setattr(recovery, "load_committed_order_command_page", page)


def command(sequence=1):
    return {"sequence": sequence, "account_id": "A", "client_order_id": f"C{sequence}",
            "conid": 101}


def test_command_audit_never_treats_absence_as_safe(monkeypatch):
    install_journal(monkeypatch, [command(1), command(2), command(3)])
    broker = Broker(
        orders=[SimpleNamespace(account="A", cOID="C1", conid=101)],
        trades=[SimpleNamespace(account="A", order_ref="C2", conid=101)],
    )
    result = asyncio.run(recovery.audit_committed_commands(None, broker, "run", page_size=1))
    assert (result.committed_commands, result.open_order_matches,
            result.execution_matches, result.unresolved_commands) == (3, 1, 1, 1)
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
