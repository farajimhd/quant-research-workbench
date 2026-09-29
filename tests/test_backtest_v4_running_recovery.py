import pytest

from src.backend import backtest_v4_running_recovery as subject
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_oms_projection import (
    RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchSnapshotRows


RUN = "00000000-0000-0000-0000-000000000a01"
BATCH = "00000000-0000-0000-0000-000000000a02"
PREFIX = V4CommittedPrefix(RUN, 7, BATCH, "2026-08-18:100",
                           "running", (BATCH,))


def _install(monkeypatch, *, open_orders=(), oms=(), moved=False):
    broker = BrokerMatchSnapshotRows(
        {"checkpoint_sequence": 7, "boundary_ms": 100},
        ({"account_id": "DU1"},), (), tuple(open_orders), (), ())
    monkeypatch.setattr(subject, "load_v4_running_portfolio_images",
                        lambda *_a, **_k: (PREFIX, {"DU1": {"state_hash": "a" * 64}}))
    monkeypatch.setattr(subject, "load_attested_manager_snapshot",
                        lambda *_a, **_k: StrategyOneManagementState(100, (), (), ()))
    monkeypatch.setattr(subject, "load_attested_broker_match_snapshot",
                        lambda *_a, **_k: broker)
    monkeypatch.setattr(subject, "load_recovered_strategy_one_oms_lineage",
                        lambda *_a, **_k: oms)
    monkeypatch.setattr(subject, "load_verified_v4_prefix",
                        lambda *_a, **_k: None if moved else PREFIX)


def test_v4_running_recovery_joins_exact_empty_oms(monkeypatch):
    _install(monkeypatch)
    result = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object())
    assert result.prefix == PREFIX
    assert result.manager.boundary_ms == 100
    assert result.oms == ()


def test_v4_running_recovery_rejects_orphan_broker_order(monkeypatch):
    _install(monkeypatch, open_orders=({"broker_order_id": "missing",
                                        "client_order_id": "missing"},))
    with pytest.raises(RuntimeError, match="lacks exact OMS lineage"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object())


def _open_order_lineage(*, terminal=0):
    request = OrderRequest(acctId="DU1", conid=123, orderType="LMT",
                           side="BUY", quantity=1, cOID="co-1", ticker="WFF",
                           price=10)
    group = RecoveredOmsGroupState(6, "intent-1", {"account_id": "DU1"},
                                   (request,), (), (), ({
                                       "broker_order_id": "broker-1",
                                       "request_index": 0,
                                       "terminal": terminal},), (), ())
    lineage = RecoveredStrategyOneOmsLineage(group, object(), (request,), 7)
    return lineage


def test_v4_running_recovery_joins_open_order_to_oms(monkeypatch):
    lineage = _open_order_lineage()
    _install(monkeypatch, open_orders=({"broker_order_id": "broker-1",
                                        "client_order_id": "co-1",
                                        "account_id": "DU1", "conid": 123,
                                        "ticker": "WFF"},), oms=(lineage,))
    result = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object())
    assert result.oms == (lineage,)


def test_v4_running_recovery_rejects_terminal_oms_binding(monkeypatch):
    lineage = _open_order_lineage(terminal=1)
    _install(monkeypatch, open_orders=({"broker_order_id": "broker-1",
                                        "client_order_id": "co-1",
                                        "account_id": "DU1", "conid": 123,
                                        "ticker": "WFF"},), oms=(lineage,))
    with pytest.raises(RuntimeError, match="lacks exact OMS lineage"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object())


def test_v4_running_recovery_rejects_moved_prefix(monkeypatch):
    _install(monkeypatch, moved=True)
    with pytest.raises(RuntimeError, match="prefix moved"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object())
