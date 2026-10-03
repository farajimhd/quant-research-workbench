from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import pytest
from src.trading_runtime.domain import Execution,InstrumentContract
from src.backend.backtest_strategy_forty_four_performance import derive_leg_positions


def fills():
    start=datetime(2026,9,3,12,tzinfo=timezone.utc)
    instrument=InstrumentContract("conid:123",123,"TEST","STK","SMART","USD")
    def fill(identity,side,quantity,price,second,sequence):
        return Execution(identity,"A",instrument,side,Decimal(quantity),Decimal(price),
            start+timedelta(seconds=second),broker_order_id=identity,client_order_id=identity,
            commission=Decimal(1),commission_currency="USD",commission_status="final",
            strategy_id="squeeze-grid-strategy",strategy_revision=44,run_id="run",
            journal_sequence=sequence)
    return (fill("a","BUY","100","10",0,1),fill("b","BUY","10","10",0,2),
            fill("c","BUY","20","10",1,3),fill("d","SELL","100","11",2,4),
            fill("e","SELL","30","9",3,5))


def test_each_parent_is_one_position_with_own_partial_fills_fees_and_pnl():
    executions=fills(); ownership=dict(a="near",b="far",c="far",d="near",e="far")
    episodes,positions=derive_leg_positions(executions,ownership)
    assert len(episodes)==len(positions)==2
    assert len({row["lifecycle_id"] for row in positions})==2
    assert {row["instrument"]["instrument_id"] for row in positions}=={"conid:123"}
    assert {row.quantity for row in episodes}=={Decimal(100),Decimal(30)}
    assert {row.net_pnl for row in episodes}=={Decimal(98),Decimal(-33)}
    assert sum(row.net_pnl for row in episodes)==Decimal(65)
    assert sum(row.fees for row in episodes)==sum(row.commission for row in executions)
    _,open_positions=derive_leg_positions(executions[:3],ownership)
    assert len(open_positions)==2 and all(row["status"]=="open" for row in open_positions)


def test_leg_cannot_borrow_sibling_inventory_or_missing_lineage():
    executions=fills()
    with pytest.raises(ValueError,match="exact acquisition"):
        derive_leg_positions(executions,{})
    with pytest.raises(ValueError,match="sells more"):
        derive_leg_positions(executions,dict(a="near",b="far",c="far",d="far",e="near"))


def test_cold_projection_requests_exact_source_events_for_leg_exits(monkeypatch):
    from types import SimpleNamespace
    from src.backend import backtest_strategy_forty_four_performance as projection
    from src.backend import backtest_v4_saved_review as review
    from src.trading_runtime import arte_journal_writer as writer, arte_intent_projection as intents
    executions = tuple(replace(e,journal_sequence=10+e.journal_sequence) for e in fills())
    owners = dict(a="near",b="far",c="far",d="near",e="far")
    commands = tuple(dict(record_id=e.execution_id,account_id=e.account_id,
        client_order_id=e.client_order_id,ticker=e.instrument.symbol,conid=e.instrument.conid,
        sequence=6) for e in executions)
    monkeypatch.setattr(review,"_complete_detail_rows",lambda *a:commands)
    monkeypatch.setattr(writer,"load_committed_order_context_page",lambda *a,**k:
        {row["record_id"]:dict(source_intent_record_id=row["record_id"]) for row in a[2]})
    def recover(*a,**kwargs):
        assert kwargs["include_source_batch"] is True
        return tuple(SimpleNamespace(record_id=e.execution_id,account_id=e.account_id,
            sequence=1,intent=SimpleNamespace(intent_id=owners[e.execution_id],
                reason="strategy_forty_four_entry" if e.side=="BUY" else "strategy_forty_four_session_liquidation"),
            source_batch=SimpleNamespace(events=(dict(correlation_id=owners[e.execution_id]),)))
            for e in executions)
    monkeypatch.setattr(intents,"load_committed_strategy_intent_page",recover)
    episodes,positions=projection.derive_saved_leg_positions(None,None,executions)
    assert len(episodes)==len(positions)==2
    assert sum(row.net_pnl for row in episodes)==Decimal(65)
