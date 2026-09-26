"""Strategy 1 uses the shared Portfolio with a disk-free Backtest journal."""
import asyncio
from datetime import date, datetime, timezone
from uuid import UUID

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


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
