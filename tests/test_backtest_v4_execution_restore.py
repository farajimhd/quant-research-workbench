"""Cold execution restoration must agree with simulator-produced fills."""
from datetime import datetime, timezone

import pytest

from src.backend.backtest_v4_execution_restore import reconstruct_broker_executions
from src.trading_runtime.ibkr_schema import OrderRequest


def _facts():
    request = OrderRequest(
        acctId="DU1", conid=123, ticker="WFF", side="BUY",
        orderType="MKT", quantity=10, cOID="co-1",
        raw={"canonical_strategy_id": "1", "canonical_strategy_revision": 1},
    )
    at = datetime(2026, 8, 18, 14, 0, 0, 100000, tzinfo=timezone.utc)
    fill = dict(sequence=7, execution_id="SIM-1", account_id="DU1",
                client_order_id="co-1", broker_order_id="SIM-ORDER-1",
                conid=123, ticker="WFF", side="B", quantity="4.0",
                price="10.0", currency="USD",
                source_event_time=at.isoformat())
    fee = dict(sequence=8, execution_id="SIM-1", account_id="DU1",
               commission="0.05", currency="USD", status="final")
    return request, fill, fee


def test_normalized_facts_restore_simulator_execution():
    request, fill, fee = _facts()
    result = reconstruct_broker_executions(
        (fill,), (fee,), requests_by_coid={"co-1": request},
        coid_by_broker_id={"SIM-ORDER-1": "co-1"},
        next_execution_id=2)
    assert result == [dict(
        execution_id="SIM-1", symbol="WFF", side="B", order_ref="co-1",
        trade_time=fill["source_event_time"],
        trade_time_r=1787061600100, size=4.0, price=10.0,
        order_id="SIM-ORDER-1", account="DU1", conid=123,
        commission=0.05, currency="USD", raw={
            "strategy_id": "1", "canonical_strategy_revision": 1,
            "canonical_run_id": "", "canonical_metadata": {},
        })]


@pytest.mark.parametrize("change,match", [
    ({"execution_id": "SIM-2"}, "fee identities differ"),
    ({"client_order_id": "other"}, "exact OMS request"),
    ({"side": "S"}, "exact OMS request"),
])
def test_execution_restoration_rejects_inconsistent_fill(change, match):
    request, fill, fee = _facts()
    with pytest.raises(RuntimeError, match=match):
        reconstruct_broker_executions(
            ({**fill, **change},), (fee,),
            requests_by_coid={"co-1": request},
            coid_by_broker_id={"SIM-ORDER-1": "co-1"},
            next_execution_id=2)


def test_execution_restoration_requires_final_commission_and_gapless_ids():
    request, fill, fee = _facts()
    with pytest.raises(RuntimeError, match="final matching commission"):
        reconstruct_broker_executions(
            (fill,), ({**fee, "status": "pending"},),
            requests_by_coid={"co-1": request},
            coid_by_broker_id={"SIM-ORDER-1": "co-1"},
            next_execution_id=2)
    with pytest.raises(RuntimeError, match="count differs"):
        reconstruct_broker_executions(
            (fill,), (fee,), requests_by_coid={"co-1": request},
            coid_by_broker_id={"SIM-ORDER-1": "co-1"},
            next_execution_id=3)


def test_historical_execution_read_preserves_prefix_and_excludes_future_fills_and_fees(monkeypatch):
    from src.backend import backtest_v4_execution_restore as module
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    request, fill, fee = _facts()
    prefix = V4CommittedPrefix("run", 12, "batch", "source", "running", ("batch",))
    def fills(client, actual_prefix, **bounds):
        assert actual_prefix is prefix and bounds == dict(after_sequence=0, limit=1000)
        return (fill, {**fill, "sequence": 10, "execution_id": "SIM-2"})
    def fees(client, actual_prefix, **bounds):
        assert actual_prefix is prefix
        return (fee, {**fee, "sequence": 11, "execution_id": "SIM-2"},
            {**fee, "sequence": 12, "commission": "9.0"})
    monkeypatch.setattr(module, "load_committed_execution_page", fills)
    monkeypatch.setattr(module, "load_committed_commission_page", fees)
    scope = dict(requests_by_coid={"co-1": request},
        coid_by_broker_id={"SIM-ORDER-1": "co-1"}, next_execution_id=2)
    result = module.load_v4_broker_executions(object(), prefix, through_sequence=8, **scope)
    assert len(result) == 1 and result[0]["commission"] == .05
    assert prefix.last_sequence == 12
    with pytest.raises(RuntimeError, match="fee identities differ"):
        module.load_v4_broker_executions(object(), prefix, through_sequence=7, **scope)
    for invalid in (-1, 13, True):
        with pytest.raises(ValueError, match="scope"):
            module.load_v4_broker_executions(object(), prefix, through_sequence=invalid, **scope)
