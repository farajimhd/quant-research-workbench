"""Read-only recent-fill reconciliation for recovered Strategy 1 OMS groups.

Broker trade history is bounded. A match here is necessary, never sufficient,
for live admission or complete historical execution recovery.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.trading_runtime.arte_journal_writer import (
    VerifiedPrefix, load_committed_execution_page,
)
from src.trading_runtime.ibkr_schema import Execution
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


class RecentTradeBroker(Protocol):
    async def trades(self, days: int = 7) -> list[Execution]: ...


@dataclass(frozen=True, slots=True)
class RecentFillAudit:
    committed_fills: int
    broker_strategy_fills: int
    exact_matches: int


def _same_amount(left: Any, right: Any) -> bool:
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False
    return a.is_finite() and b.is_finite() and a == b


async def audit_strategy_one_recent_fills(
    client: Any, prefix: VerifiedPrefix, heads: tuple[Any, ...],
    broker: RecentTradeBroker, *, page_size: int = 500,
    max_committed_fills: int = 20_000,
) -> RecentFillAudit:
    """Require every recent broker fill for a recovered order in ClickHouse."""
    if (not 1 <= page_size <= 999
            or max_committed_fills < page_size):
        raise ValueError("Recent fill audit bounds are invalid")
    by_client: set[tuple[str, str]] = set()
    by_broker: set[tuple[str, str]] = set()
    for head in heads:
        group = head.group
        account = group.group["account_id"]
        for order in group.orders:
            key = (account, order.cOID)
            if not key[1] or key in by_client:
                raise RuntimeError("Recovered fill audit has duplicate client identity")
            by_client.add(key)
        for binding in group.broker_bindings:
            key = (account, str(binding["broker_order_id"]))
            if not key[1] or key in by_broker:
                raise RuntimeError("Recovered fill audit has duplicate broker identity")
            by_broker.add(key)

    recent: dict[tuple[str, str], Execution] = {}
    execution_ids: set[str] = set()
    for trade in await broker.trades(days=7):
        client_key = (trade.account, trade.order_ref)
        broker_key = (trade.account, trade.order_id)
        if client_key not in by_client and broker_key not in by_broker:
            continue
        key = (trade.account, trade.execution_id)
        if not key[1] or key in recent or trade.execution_id in execution_ids:
            raise RuntimeError("Broker execution identity is missing or duplicated")
        recent[key] = trade
        execution_ids.add(trade.execution_id)
    if len(recent) > max_committed_fills:
        raise RuntimeError("Recent broker fill audit exceeds its row budget")
    if not recent:
        return RecentFillAudit(0, 0, 0)

    committed: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_ids = sorted(execution_ids)
    for offset in range(0, len(ordered_ids), page_size):
        ids = tuple(ordered_ids[offset:offset + page_size])
        page = await asyncio.to_thread(
            load_committed_execution_page, client, prefix,
            limit=len(ids) + 1, execution_ids=ids)
        for row in page:
            key = (str(row["account_id"]), str(row["execution_id"]))
            if key in committed or key not in recent:
                raise RuntimeError("Committed recent execution identity is duplicated or unexpected")
            committed[key] = row
    if set(committed) != set(recent):
        raise RuntimeError("Recent broker execution lacks a committed Strategy 1 fill")
    for key, trade in recent.items():
        row = committed.get(key)
        if (row is None
                or row["strategy_id"] != STRATEGY_ID
                or int(row["strategy_revision"]) != STRATEGY_NUMBER
                or row["client_order_id"] != trade.order_ref
                or row["broker_order_id"] != trade.order_id
                or int(row["conid"]) != trade.conid
                or str(row["ticker"]).upper() != trade.symbol.upper()
                or str(row["side"]).upper() != trade.side.upper()
                or not _same_amount(row["quantity"], trade.size)
                or not _same_amount(row["price"], trade.price)):
            raise RuntimeError("Recent broker fill differs from committed Strategy 1 execution")
    return RecentFillAudit(len(committed), len(recent), len(recent))
