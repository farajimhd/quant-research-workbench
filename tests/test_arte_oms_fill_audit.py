from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime import arte_oms_fill_audit as audit
from src.trading_runtime.arte_journal_writer import CommittedPrefix
from src.trading_runtime.ibkr_schema import Execution, OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


def _head():
    request = OrderRequest(
        acctId="DU1", conid=123, cOID="client-1", ticker="TEST",
        orderType="LMT", side="BUY", quantity=5, price=12.34)
    group = SimpleNamespace(group={"account_id": "DU1"}, orders=(request,),
                            broker_bindings=({"broker_order_id": "broker-1"},))
    return SimpleNamespace(group=group)


def _trade():
    return Execution(
        execution_id="fill-1", symbol="TEST", side="B",
        order_ref="client-1", trade_time=datetime(2026, 8, 18, 8, 5,
                                                tzinfo=timezone.utc),
        trade_time_r=1787040300000, size=2, price=12.34,
        order_id="broker-1", account="DU1", conid=123)


def _row():
    return {"sequence": 1, "account_id": "DU1", "execution_id": "fill-1",
            "strategy_id": STRATEGY_ID, "strategy_revision": STRATEGY_NUMBER,
            "client_order_id": "client-1", "broker_order_id": "broker-1",
            "conid": 123, "ticker": "TEST", "side": "B",
            "quantity": "2.0000000000", "price": "12.3400000000"}


class _Broker:
    def __init__(self, trades):
        self.items = trades

    async def trades(self, days=7):
        assert days == 7
        return self.items


def _prefix():
    batch = "00000000-0000-0000-0000-000000000001"
    return CommittedPrefix("live:DU1", 1, batch, "fill",
                           "running", (batch,))


def test_recent_strategy_one_fill_requires_exact_committed_row(monkeypatch):
    monkeypatch.setattr(audit, "load_committed_execution_page",
                        lambda *_args, **kwargs: (_row(),)
                        if kwargs["execution_ids"] == ("fill-1",) else ())
    result = asyncio.run(audit.audit_strategy_one_recent_fills(
        object(), _prefix(), (_head(),), _Broker([_trade()]), page_size=1))
    assert result == audit.RecentFillAudit(1, 1, 1)


@pytest.mark.parametrize("change", [
    {"price": "12.35"}, {"quantity": "3"}, {"broker_order_id": "other"},
    {"strategy_revision": 2},
])
def test_recent_strategy_one_fill_rejects_missing_or_drifted_row(monkeypatch, change):
    row = {**_row(), **change}
    monkeypatch.setattr(audit, "load_committed_execution_page",
                        lambda *_args, **kwargs: (row,)
                        if kwargs["execution_ids"] == ("fill-1",) else ())
    with pytest.raises(RuntimeError):
        asyncio.run(audit.audit_strategy_one_recent_fills(
            object(), _prefix(), (_head(),), _Broker([_trade()]), page_size=1))
