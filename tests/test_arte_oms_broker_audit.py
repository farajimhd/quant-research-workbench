from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_oms_broker_audit import (
    OmsOpenBindingAudit, audit_strategy_one_open_oms_bindings,
)
from src.trading_runtime.ibkr_schema import LiveOrder, OrderRequest, OrderStatus


def _head(*, terminal: bool = False):
    request = OrderRequest(acctId="DU1", conid=123, cOID="strategy-1-entry",
                           ticker="TEST", orderType="LMT", side="BUY",
                           quantity=5, price=12.34)
    group = SimpleNamespace(group={"account_id": "DU1"}, orders=(request,),
                            broker_bindings=({"broker_order_id": "broker-1",
                                              "request_index": 0,
                                              "filled_quantity": "0",
                                              "terminal": int(terminal)},))
    return SimpleNamespace(group=group)


def _open_order():
    return LiveOrder(account="DU1", orderId="broker-1", conid=123,
                     ticker="TEST", side="BUY", orderType="LMT", tif="DAY",
                     totalSize=5, filledQuantity=0, remainingQuantity=5,
                     avgPrice=0, order_status=OrderStatus.SUBMITTED,
                     cOID="strategy-1-entry", price=12.34)


class _Broker:
    def __init__(self, orders):
        self.orders = orders

    async def live_orders(self):
        return self.orders


def test_open_oms_binding_matches_exact_broker_snapshot() -> None:
    audit = asyncio.run(audit_strategy_one_open_oms_bindings(
        (_head(),), _Broker([_open_order()])))
    assert audit == OmsOpenBindingAudit(1, 1, 1, 0)


@pytest.mark.parametrize("orders,terminal,reason", [
    ([], False, "absent"),
    ([_open_order()], True, "still open"),
    ([replace(_open_order(), price=12.35)], False, "contradicts"),
    ([replace(_open_order(), filledQuantity=1)], False, "contradicts"),
    ([replace(_open_order(), remainingQuantity=4)], False, "contradicts"),
    ([replace(_open_order(), orderId="unbound")], False, "absent from recovered"),
])
def test_open_oms_binding_fails_closed_on_broker_drift(orders, terminal, reason) -> None:
    with pytest.raises(RuntimeError, match=reason):
        asyncio.run(audit_strategy_one_open_oms_bindings(
            (_head(terminal=terminal),), _Broker(orders)))


def test_terminal_oms_binding_requires_no_broker_open_order() -> None:
    audit = asyncio.run(audit_strategy_one_open_oms_bindings(
        (_head(terminal=True),), _Broker([])))
    assert audit == OmsOpenBindingAudit(1, 1, 0, 1)
