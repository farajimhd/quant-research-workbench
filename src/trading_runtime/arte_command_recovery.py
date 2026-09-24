"""Read-only, fail-closed audit of durable commands against broker evidence.

This is an admission gate, not an order replay mechanism. Broker history is
bounded and absence from it never proves that a command was not delivered.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from src.trading_runtime.arte_journal_writer import (
    load_committed_order_command_page, load_committed_prefix,
)
from src.trading_runtime.ibkr_schema import Execution, LiveOrder


class RecoveryBroker(Protocol):
    async def live_orders(self) -> list[LiveOrder]: ...
    async def trades(self, days: int = 7) -> list[Execution]: ...


@dataclass(frozen=True, slots=True)
class CommandRecoveryAudit:
    run_id: str
    committed_commands: int
    open_order_matches: int
    execution_matches: int
    unresolved_commands: int
    unresolved_sample: tuple[tuple[str, str], ...]

    @property
    def admission_safe(self) -> bool:
        # Seeing an order or execution is not yet a complete OMS state recovery.
        return self.committed_commands == 0


async def audit_committed_commands(
    client: Any, broker: RecoveryBroker, run_id: str, *,
    page_size: int = 500, sample_limit: int = 20,
) -> CommandRecoveryAudit:
    """Audit a verified journal prefix without writing or sending orders.

    ClickHouse verification runs off the event loop. The broker's open orders
    and recent executions are snapshots, not a guarantee of complete history.
    Unknown outcomes remain unresolved and must never be automatically resent.
    """
    if not 1 <= page_size <= 1000 or sample_limit < 0:
        raise ValueError("Recovery audit bounds are invalid")
    prefix = await asyncio.to_thread(load_committed_prefix, client, run_id)
    if prefix is None:
        raise RuntimeError("No verified committed journal prefix for recovery")
    orders, executions = await asyncio.gather(
        broker.live_orders(), broker.trades(days=7),
    )
    open_by_key: dict[tuple[str, str], LiveOrder] = {}
    for order in orders:
        key = (order.account, order.cOID)
        if not key[1] or key in open_by_key:
            raise RuntimeError("Broker open-order identity is missing or duplicated")
        open_by_key[key] = order
    trades_by_key: dict[tuple[str, str], list[Execution]] = {}
    for execution in executions:
        if execution.order_ref:
            trades_by_key.setdefault((execution.account, execution.order_ref), []).append(
                execution
            )
    cursor = 0
    total = open_matches = execution_matches = unresolved = 0
    sample: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    while True:
        page = await asyncio.to_thread(
            load_committed_order_command_page, client, prefix,
            after_sequence=cursor, limit=page_size,
        )
        if not page:
            break
        for command in page:
            key = (str(command["account_id"]), str(command["client_order_id"]))
            if not key[1] or key in seen:
                raise RuntimeError("Committed command identity is missing or duplicated")
            seen.add(key)
            total += 1
            expected_conid = int(command["conid"])
            order = open_by_key.get(key)
            matched_trades = trades_by_key.get(key, ())
            if order is not None:
                if order.conid != expected_conid:
                    raise RuntimeError("Broker order contradicts committed command contract")
                open_matches += 1
            if matched_trades:
                if any(trade.conid != expected_conid for trade in matched_trades):
                    raise RuntimeError("Broker execution contradicts committed command contract")
                execution_matches += 1
            if order is None and not matched_trades:
                unresolved += 1
                if len(sample) < sample_limit:
                    sample.append(key)
        cursor = int(page[-1]["sequence"])
    return CommandRecoveryAudit(
        run_id, total, open_matches, execution_matches, unresolved, tuple(sample),
    )
