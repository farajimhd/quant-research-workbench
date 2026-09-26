"""The numbered proposal lane keeps broker, activation, and decision order."""
import asyncio
from dataclasses import replace

from src.backend.backtest_strategy_one_activation import StrategyOneActivation
from src.backend.backtest_strategy_one_coordinator import run_strategy_one_proposals
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
from test_strategy_one_stateful import _facts


def test_proposal_lane_uses_broker_before_financial_entry_without_order():
    candidate, fact, activation, financial = _facts()
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    actions = []

    async def broker(work):
        for ticker, _rows in work.broker_rows:
            actions.append(("broker", ticker, work.boundary_ms))

    async def activated(row):
        actions.append(("activation", row.ticker, row.boundary_ms))

    async def seconds(_work):
        pass

    async def proposal(value):
        actions.append(("proposal", value.ticker, value.boundary_ms))

    async def management(view, _rows, boundary):
        actions.append(("management", view.ticker, boundary))

    async def finished(_work):
        pass

    async def view(_ticker, _boundary):
        return (financial,)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=broker,
        financial_views=view,
        on_entry_proposal=proposal, on_management=management,
        financially_active_tickers=lambda: (), finish_boundary=finished,
        observe_activation=activated, observe_completed_seconds=seconds))
    assert (counts.completed_boundaries, counts.candidate_decisions,
            counts.entry_proposals, counts.management_evaluations) == (2, 1, 1, 0)
    assert actions == [("activation", "AAA", 30_000),
                       ("broker", "AAA", 31_000),
                       ("proposal", "AAA", 31_000)]


def test_held_candidate_routes_to_management_not_another_entry():
    candidate, fact, activation, financial = _facts()
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    actions = []
    active = {"AAA"}

    async def noop(*_args):
        pass

    async def proposal(_value):
        actions.append("proposal")

    async def management(_ticker, _rows, _boundary):
        actions.append("management")
        active.clear()

    async def view(_ticker, _boundary):
        return (replace(financial, position_quantity=10.),)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop,
        financial_views=view,
        on_entry_proposal=proposal, on_management=management,
        financially_active_tickers=lambda: tuple(sorted(active)),
        finish_boundary=noop, observe_activation=noop,
        observe_completed_seconds=noop))
    assert counts.entry_proposals == 0
    assert counts.management_evaluations == 1
    assert actions == ["management"]


def test_one_ticker_evaluates_all_accounts_in_stable_financial_order():
    candidate, fact, activation, first = _facts()
    second = replace(first, account_id="DU2", assignment_id="assignment-2")
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    submitted = []

    async def noop(*_args):
        pass

    async def views(_ticker, _boundary):
        return (second, first)

    async def proposal(value):
        submitted.append((value.account_id, value.assignment_id))

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop,
        financial_views=views, on_entry_proposal=proposal,
        on_management=noop, financially_active_tickers=lambda: (),
        finish_boundary=noop, observe_activation=noop,
        observe_completed_seconds=noop))
    assert counts.candidate_decisions == counts.entry_proposals == 2
    assert submitted == [("DU1", "assignment-1"),
                         ("DU2", "assignment-2")]
