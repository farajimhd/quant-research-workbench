"""Strategy 1 uses the shared Portfolio with a disk-free Backtest journal."""
import asyncio
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_broker_acknowledgement_v4 import project_broker_acknowledgement_v4
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_oms_projection import oms_group_state_batch
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.order_management import BrokerCommunicationPolicy, OrderManagementEngine
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.risk import RiskAuthority


def test_strategy_one_initial_admission_uses_no_sqlite_or_disk():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)

    async def exercise():
        run_id = str(UUID(int=1))
        journal = BacktestMemoryJournal(run_id=run_id)
        profile = PortfolioAccountProfile(
            "cash", "DU1", "backtest", "simulated",
            PortfolioPolicy(maximum_position_fraction=1.,
                            maximum_ticker_fraction=1.,
                            maximum_planned_risk_fraction=.5,
                            maximum_open_risk_fraction=.5,
                            entry_fee_buffer_bps=0.,
                            allow_outside_rth=True))
        portfolio = PortfolioManagementEngine(
            [profile], journal=journal, run_id=run_id,
            strategy_id="early-squeeze-strategy", strategy_revision=1,
            event_clock=lambda: at)
        portfolio.synchronize_snapshot(
            "DU1", summary=AccountSummary(
                account_id="DU1", netliquidation=9000, totalcashvalue=9000,
                buyingpower=9000, grosspositionvalue=0, availablefunds=9000,
                excessliquidity=9000, timestamp=at),
            ledger=AccountLedger(
                acctId="DU1", cashbalance=9000, settledcash=9000,
                stockmarketvalue=0, netliquidationvalue=9000,
                realizedpnl=0, unrealizedpnl=0, timestamp=at),
            positions=[])
        proposal = StrategyOneEntryProposal(
            "assignment-1", "DU1", "AAA", 31_000, 30_000,
            10.01, 9.89, 12., "R4", .5, 30_000, "S1")
        intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
        assert intent.metadata == {}
        decision, approved = await portfolio.approve(
            intent, account_id="DU1", assignment_id=proposal.assignment_id)
        return decision, approved, journal.records(journal.run_id)

    decision, approved, records = asyncio.run(exercise())
    assert approved is not None, decision.reasons
    assert approved.quantity > 0
    assert approved.metadata["assignment_id"] == "assignment-1"
    assert records
    assert decision.decided_at == at
    assert all(record.event_time == at for record in records)
    assert all(record.category == "portfolio_management" for record in records)
    assert {record.entity_type for record in records} == {
        "portfolio_decision", "portfolio_reservation",
    }
    reservation = next(record for record in records
                       if record.entity_type == "portfolio_reservation")
    assert reservation.payload["assignment_id"] == "assignment-1"
    for index, record in enumerate(records, start=1):
        projected = project_journal_record(
            record, run_month=date(2026, 8, 1), attempt_id=str(UUID(int=2)),
            batch_id=str(UUID(int=index + 2)),
            prior_batch_id=str(UUID(int=index + 1)) if index > 1 else str(UUID(int=0)),
            source_cursor="2026-08-18:30000", expected_mode="backtest",
        )
        assert len(projected.events) == 1
        assert projected.events[0]["event_month"] == "2026-08-01"


def test_strategy_one_approved_intent_reaches_causal_oms_without_sqlite():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)

    async def exercise():
        run_id = str(UUID(int=11))
        journal = BacktestMemoryJournal(run_id=run_id)
        broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST,
                                        initial_time=at)
        await broker.initialize()
        risk = RiskAuthority()
        await risk.prime(broker, ["DU1"])
        policy = PortfolioPolicy(maximum_position_fraction=1.,
                                 maximum_ticker_fraction=1.,
                                 maximum_planned_risk_fraction=.5,
                                 maximum_open_risk_fraction=.5,
                                 allow_outside_rth=True)
        portfolio = PortfolioManagementEngine(
            [PortfolioAccountProfile("cash", "DU1", "backtest", "simulated", policy)],
            journal=journal, run_id=run_id, strategy_id="strategy-1",
            strategy_revision=1, event_clock=lambda: at)
        portfolio.synchronize_snapshot(
            "DU1", summary=AccountSummary(
                account_id="DU1", netliquidation=9000, totalcashvalue=9000,
                buyingpower=9000, grosspositionvalue=0, availablefunds=9000,
                excessliquidity=9000, timestamp=at),
            ledger=AccountLedger(
                acctId="DU1", cashbalance=9000, settledcash=9000,
                stockmarketvalue=0, netliquidationvalue=9000,
                realizedpnl=0, unrealizedpnl=0, timestamp=at), positions=[])
        proposal = StrategyOneEntryProposal(
            "assignment-1", "DU1", "AAA", 31_000, 30_000,
            10.01, 9.89, 12., "R4", .5, 30_000, "S1")
        intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
        _, approved = await portfolio.approve(
            intent, account_id="DU1", assignment_id=proposal.assignment_id)
        assert approved is not None
        planner = IbkrStrategyOrderPlanner()
        instrument = InstrumentContract("AAA", 123, "AAA", "STK", "USD")
        manager = OrderManagementEngine(
            broker=broker,
            planner=lambda item, account_id, _event: planner.plan(
                account_id=account_id, instrument=instrument, intent=item,
                strategy_id="strategy-1", strategy_revision=1),
            risk=risk, journal=journal, run_id=run_id,
            strategy_id="strategy-1", strategy_revision=1,
            policy=BrokerCommunicationPolicy(), causal_execution_clock=True)
        manager.on_market_snapshot(ExecutionMarketSnapshot(
            "AAA", 9.99, 10.01, .01, at, "qmd-history"))
        try:
            group = await asyncio.wait_for(manager.submit_intent(
                approved, account_id="DU1", event=None), 5)
            records = journal.records(run_id)
            frozen = tuple(journal.oms_group_for_record(record.record_id)
                           for record in records
                           if record.entity_type == "order_group_state")
            admissions = tuple(journal.oms_admission_for_record(record.record_id)
                               for record in records
                               if record.entity_type == "order_group_state")
            assert admissions and all(row is not None and
                                      row["assignment_id"] == "assignment-1"
                                      for row in admissions)
            source = strategy_intent_batch(
                intent, run_id=run_id, run_month=date(2026, 8, 1),
                account_id="DU1", attempt_id=str(UUID(int=14)),
                batch_id=str(UUID(int=15)), prior_batch_id=str(UUID(int=0)),
                sequence=1, source_cursor="intent", run_status="running",
                recorded_at=at)
            transitions = tuple(record for record in records
                                if record.entity_type == "order_group_state")
            for transition, captured, admission in zip(
                    transitions, frozen, admissions, strict=True):
                projected = oms_group_state_batch(
                    captured, run_id=run_id, run_month=date(2026, 8, 1),
                    attempt_id=str(UUID(int=14)),
                    batch_id=str(UUID(int=16 + transition.sequence)),
                    prior_batch_id=str(UUID(int=15)), sequence=transition.sequence,
                    source_cursor="oms", run_status="running",
                    strategy_id="strategy-1", strategy_revision=1,
                    recorded_at=transition.recorded_at,
                    published_intent_batch=source,
                    committed_intent_batch_id=source.batch_id,
                    admission_source_intent=intent,
                    admission_reservation=admission,
                    journal_record_id=transition.record_id)
                assert projected.events[0]["record_id"] == transition.record_id
            latest = frozen[-1]
            assert latest is not None
            manager._groups[group.group_id].broker_order_ids.append("later-mutation")
            assert "later-mutation" not in latest.broker_order_ids
            journal.mark_fenced(records[-1].sequence)
            assert all(journal.oms_group_for_record(record.record_id) is None
                       for record in records)
            assert all(journal.oms_admission_for_record(record.record_id) is None
                       for record in records)
            return group, records, frozen
        finally:
            await manager.close()
            journal.close()

    group, records, frozen = asyncio.run(exercise())
    assert {(record.category, record.entity_type) for record in records} == {
        ("broker", "order_acknowledgement"),
        ("command", "order"),
        ("order_management", "order_group_state"),
        ("portfolio_management", "portfolio_decision"),
        ("portfolio_management", "portfolio_reservation"),
        ("protection", "protection_change"),
    }
    assert group.assignment_id == "assignment-1"
    assert group.broker_order_ids, group
    acknowledgements = [record for record in records
                        if (record.category, record.entity_type)
                        == ("broker", "order_acknowledgement")]
    assert acknowledgements
    for record in acknowledgements:
        projected_ack = project_broker_acknowledgement_v4(
            record, attempt_id=str(UUID(int=15)), batch_id=str(UUID(int=14)))
        assert projected_ack.detail["broker_order_id"] in group.broker_order_ids
    assert frozen and all(item is not None and item.group_id == group.group_id
                          for item in frozen)
    assert any(record.category == "command" and record.entity_type == "order"
               for record in records)
    group_transition = next(record for record in records
                            if record.category == "order_management"
                            and record.entity_type == "order_group_state")
    # The direct OMS path is real, but a flattened transition is insufficient
    # to recover its approved quantity, assignment, and keyed order state.
    # Keep publication gated until a normalized OMS admission revision exists.
    with pytest.raises(ValueError, match="lacks a typed projection"):
        project_journal_record(
            group_transition, run_month=date(2026, 8, 1),
            attempt_id=str(UUID(int=12)), batch_id=str(UUID(int=13)),
            prior_batch_id=str(UUID(int=0)), source_cursor="2026-08-18:31000",
            expected_mode="backtest",
            expected_config={"strategy_id": "strategy-1", "strategy_revision": 1},
        )
