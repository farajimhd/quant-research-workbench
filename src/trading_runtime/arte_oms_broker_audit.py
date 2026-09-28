"""Read-only cold check of recovered Strategy 1 OMS open-order bindings.

This is necessary but not sufficient for live admission. Positions, fills,
command outcomes, and the in-memory OMS projection still require reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, LiveOrder
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


class OpenOrderBroker(Protocol):
    async def live_orders(self) -> list[LiveOrder]: ...


@dataclass(frozen=True, slots=True)
class OmsOpenBindingAudit:
    group_count: int
    binding_count: int
    open_matches: int
    terminal_absences: int


def _amount_equal(left: Any, right: Any) -> bool:
    try:
        first, second = Decimal(str(left)), Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False
    return first.is_finite() and second.is_finite() and first == second


def _broker_quantity_balances(order: LiveOrder) -> bool:
    try:
        filled = Decimal(str(order.filledQuantity))
        remaining = Decimal(str(order.remainingQuantity))
    except (InvalidOperation, ValueError):
        return False
    return (filled.is_finite() and remaining.is_finite()
            and filled >= 0 and remaining >= 0
            and _amount_equal(filled + remaining, order.totalSize))


async def audit_strategy_one_open_oms_bindings(
    heads: tuple[Any, ...], broker: OpenOrderBroker, *,
    allowed_accounts: frozenset[str] | None = None,
) -> OmsOpenBindingAudit:
    """Fail closed if broker open orders differ from verified typed OMS heads."""
    if (allowed_accounts is not None and
            (type(allowed_accounts) is not frozenset or not allowed_accounts
             or any(type(account) is not str or not account
                    for account in allowed_accounts))):
        raise ValueError("Broker OMS audit needs exact account membership")
    strategy_prefix = f"{STRATEGY_ID[:14]}-v{STRATEGY_NUMBER}-"
    expected_by_broker: dict[tuple[str, str], tuple[Any, Any]] = {}
    expected_by_client: dict[tuple[str, str], Any] = {}
    for head in heads:
        group = head.group
        account = group.group["account_id"]
        for request in group.orders:
            key = (account, request.cOID)
            if not key[1] or key in expected_by_client:
                raise RuntimeError("Recovered OMS client order identity is missing or duplicated")
            expected_by_client[key] = request
        for binding in group.broker_bindings:
            key = (account, str(binding["broker_order_id"]))
            if not key[1] or key in expected_by_broker:
                raise RuntimeError("Recovered OMS broker identity is missing or duplicated")
            index = binding["request_index"]
            if index is None or not 0 <= int(index) < len(group.orders):
                raise RuntimeError("Recovered OMS broker binding lacks an exact order")
            expected_by_broker[key] = (binding, group.orders[int(index)])

    open_by_broker: dict[tuple[str, str], LiveOrder] = {}
    for order in await broker.live_orders():
        if allowed_accounts is not None and order.account not in allowed_accounts:
            continue
        key = (order.account, str(order.orderId))
        if not key[1] or key in open_by_broker:
            raise RuntimeError("Broker open-order identity is missing or duplicated")
        open_by_broker[key] = order
        if ((str(order.cOID or "").startswith(strategy_prefix)
             or str(order.parentId or "").startswith(strategy_prefix))
                and key not in expected_by_broker):
            raise RuntimeError("Strategy 1 broker order lacks a recovered OMS binding")
        client_key = (order.account, order.cOID)
        if client_key in expected_by_client and key not in expected_by_broker:
            raise RuntimeError("Strategy 1 broker order is absent from recovered OMS bindings")

    matches = terminal_absences = 0
    for key, (binding, request) in expected_by_broker.items():
        current = open_by_broker.get(key)
        if bool(int(binding["terminal"])):
            if current is not None:
                raise RuntimeError("Terminal OMS binding is still open at broker")
            terminal_absences += 1
            continue
        if current is None:
            raise RuntimeError("Nonterminal OMS binding is absent from broker open orders")
        if (current.order_status not in OPEN_ORDER_STATUSES
                or current.cOID != request.cOID
                or current.conid != request.conid
                or current.ticker.upper() != request.ticker.upper()
                or current.side.upper() != request.side.upper()
                or current.orderType.upper() != request.orderType.upper()
                or current.tif.upper() != request.tif.upper()
                or current.outsideRTH != request.outsideRTH
                or current.parentId != request.parentId
                or not _broker_quantity_balances(current)
                or (request.quantity is not None
                    and not _amount_equal(current.totalSize, request.quantity))
                or (request.price is not None
                    and not _amount_equal(current.price, request.price))
                or (request.auxPrice is not None
                    and not _amount_equal(current.auxPrice, request.auxPrice))
                or not _amount_equal(current.filledQuantity,
                                     binding["filled_quantity"])):
            raise RuntimeError("Broker open order contradicts recovered OMS binding")
        matches += 1
    return OmsOpenBindingAudit(len(heads), len(expected_by_broker),
                               matches, terminal_absences)
