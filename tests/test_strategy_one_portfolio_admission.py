"""Strategy 1 uses the shared Portfolio with a disk-free Backtest journal."""
import asyncio
from datetime import date, datetime, timezone

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


def test_strategy_one_initial_admission_uses_no_sqlite_or_disk():
    async def exercise():
        at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
        journal = BacktestMemoryJournal(run_id="strategy-one-test")
        profile = PortfolioAccountProfile(
            "cash", "DU1", "backtest", "simulated",
            PortfolioPolicy(maximum_position_fraction=1.,
                            maximum_ticker_fraction=1.,
                            maximum_planned_risk_fraction=.5,
                            maximum_open_risk_fraction=.5,
                            entry_fee_buffer_bps=0.,
                            allow_outside_rth=True))
        portfolio = PortfolioManagementEngine(
            [profile], journal=journal, run_id="strategy-one-test",
            strategy_id="early-squeeze-strategy", strategy_revision=1)
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
        decision, approved = await portfolio.approve(intent, account_id="DU1")
        return decision, approved, journal.records(journal.run_id)

    decision, approved, records = asyncio.run(exercise())
    assert approved is not None, decision.reasons
    assert approved.quantity > 0
    assert records
    assert all(record.category == "portfolio_management" for record in records)
