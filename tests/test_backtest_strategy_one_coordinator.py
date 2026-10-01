"""The numbered proposal lane keeps broker, activation, and decision order."""
import asyncio
from dataclasses import replace
import numpy as np
import pytest

from src.backend.backtest_strategy_one_activation import StrategyOneActivation
from src.backend.backtest_strategy_one_coordinator import run_strategy_one_proposals
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
from src.backend.backtest_strategy_one_static_gate import (
    MISSING_INITIAL_PROTECTION, StrategyOneStaticGate,
)
from test_strategy_one_stateful import _facts
from src.trading_runtime.strategy_one_stateful import StrategyOneReentryWitness


@pytest.mark.parametrize('pending_entry', [False, True])
def test_staged_twenty_coordinator_keeps_source_evidence_and_financial_rejections(monkeypatch, pending_entry):
    from test_backtest_strategy_first_price_source import authority, Bars
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
    from src.trading_runtime import numbered_fixed_strategy as contracts
    original_contract = contracts.numbered_fixed_strategy
    # Exercise staged routing with inherited execution policy. This does not
    # register or certify Strategy20, and is not a portfolio backtest.
    monkeypatch.setattr(contracts, 'numbered_fixed_strategy',
        lambda number: original_contract(19 if number == 20 else number))
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    candidate, _, _, financial = _facts()
    financial = replace(financial, pending_entry=pending_entry)
    proposals = []
    actions = []
    async def noop(*_args):
        pass
    async def broker(work):
        actions.append(('broker', work.boundary_ms))
    async def views(*_args):
        actions.append(('financial', 31000))
        return (financial,)
    async def proposed(value):
        proposals.append(value)
    scheduler = StrategyOneBoundaryScheduler(session_date='2026-08-18',
        candidate_rows=iter((candidate,)), active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(scheduler, parent.entry,
        process_broker_boundary=broker, financial_views=views,
        on_entry_proposal=proposed, on_management=noop,
        position_source_owned=lambda _view: False, financially_active_tickers=lambda: (),
        finish_boundary=noop, observe_activation=noop, observe_completed_seconds=noop,
        strategy_number=20, momentum_plan=parent.momentum, initial_momentum_plan=plan))
    assert actions == [('broker', 31000), ('financial', 31000)]
    assert counts.candidate_decisions == 1
    assert counts.entry_proposals == (0 if pending_entry else 1)
    if proposals:
        proposal = proposals[0]
        assert proposal.strategy_number == 20
        assert proposal.first_price == plan.price_witness('AAA', 31000)
        assert proposal.initial_momentum == plan.selection_witness('AAA', 31000)
        assert proposal.price_source_token == plan.source.token


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
        position_source_owned=lambda _view: False,
        financially_active_tickers=lambda: (), finish_boundary=finished,
        observe_activation=activated, observe_completed_seconds=seconds))
    assert (counts.completed_boundaries, counts.candidate_decisions,
            counts.entry_proposals, counts.management_evaluations) == (2, 1, 1, 0)
    assert actions == [("activation", "AAA", 30_000),
                       ("broker", "AAA", 31_000),
                       ("proposal", "AAA", 31_000)]


def test_reentry_proposal_consumes_only_supplied_completed_bar_witness():
    candidate, fact, activation, financial = _facts()
    financial = replace(
        financial, completed_entries=1,
        permissions=replace(financial.permissions, reenter=True))
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    proposals = []

    async def noop(*_args):
        pass

    async def view(_ticker, _boundary):
        return (financial,)

    async def witness(_financial, _candidate):
        return StrategyOneReentryWitness(
            30_500, "R3", 100_000, 99_900, 100_200)

    async def proposed(value):
        proposals.append(value)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop,
        financial_views=view, on_entry_proposal=proposed,
        on_management=noop, position_source_owned=lambda _view: False,
        financially_active_tickers=lambda: (), finish_boundary=noop,
        observe_activation=noop, observe_completed_seconds=noop,
        reentry_witness=witness))
    assert counts.entry_proposals == 1
    assert len(proposals) == 1


def test_vectorized_rejection_prevents_stateful_candidate_decision():
    candidate, fact, activation, financial = _facts()
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    gate = StrategyOneStaticGate(
        (fact,), np.array([MISSING_INITIAL_PROTECTION], dtype=np.uint8),
        np.array([], dtype=np.int64))
    calls = []

    async def noop(*_args):
        pass

    async def views(*_args):
        calls.append("financial")
        return (financial,)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop,
        financial_views=views, on_entry_proposal=noop, on_management=noop,
        position_source_owned=lambda _view: False,
        financially_active_tickers=lambda: (), finish_boundary=noop,
        observe_activation=noop, observe_completed_seconds=noop,
        static_gate=gate))
    assert counts.candidate_decisions == counts.entry_proposals == 0
    assert calls == []


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
        position_source_owned=lambda _view: False,
        financially_active_tickers=lambda: tuple(sorted(active)),
        finish_boundary=noop, observe_activation=noop,
        observe_completed_seconds=noop))
    assert counts.entry_proposals == 0
    assert counts.management_evaluations == 1
    assert actions == ["management"]


def test_broker_flattened_active_ticker_still_clears_position_management():
    candidate, fact, activation, financial = _facts()
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    active = {"AAA"}
    managed = []

    async def broker(work):
        if work.boundary_ms == 31_000:
            active.clear()  # A protective broker fill flattened the position.

    async def noop(*_args):
        pass

    async def view(_ticker, _boundary):
        # This state could otherwise admit a reentry on the very bucket that
        # just filled the old protective exit.
        return (financial,)

    async def management(current, _rows, boundary):
        managed.append((current.position_quantity, boundary))

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=broker,
        financial_views=view, on_entry_proposal=noop,
        on_management=management,
        position_source_owned=lambda _view: True,
        financially_active_tickers=lambda: tuple(sorted(active)),
        finish_boundary=noop, observe_activation=noop,
        observe_completed_seconds=noop))
    assert managed == [(0., 31_000)]
    assert counts.entry_proposals == 0


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
        on_management=noop, position_source_owned=lambda _view: False,
        financially_active_tickers=lambda: (),
        finish_boundary=noop, observe_activation=noop,
        observe_completed_seconds=noop))
    assert counts.candidate_decisions == counts.entry_proposals == 2
    assert submitted == [("DU1", "assignment-1"),
                         ("DU2", "assignment-2")]


def test_second_assignment_sees_post_submission_financial_state():
    candidate, fact, activation, first = _facts()
    second = replace(first, assignment_id="assignment-2")
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    submitted = []
    snapshots = []

    async def noop(*_args):
        pass

    async def views(_ticker, _boundary):
        snapshots.append(len(submitted))
        if submitted:
            return (replace(second, pending_entry=True), first)
        return (second, first)

    async def proposal(value):
        submitted.append(value.assignment_id)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda _ticker, _after: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop,
        financial_views=views, on_entry_proposal=proposal,
        on_management=noop, position_source_owned=lambda _view: False,
        financially_active_tickers=lambda: (),
        finish_boundary=noop, observe_activation=noop,
        observe_completed_seconds=noop))
    assert submitted == ["assignment-1"]
    assert snapshots == [0, 1]
    assert counts.candidate_decisions == 2

@pytest.mark.parametrize("boundary,bos,expected", [
    (31_000, 1_000, 1), (31_100, 1_000, 0), (31_000, None, 0),
])
def test_strategy_twelve_coordinator_threads_number_into_real_admission(boundary, bos, expected):
    candidate, fact, activation, financial = _facts()
    candidate = replace(
        candidate, market_row={**candidate.market_row, "boundary_ms": boundary},
        evidence=replace(candidate.evidence, boundary_ms=boundary))
    fact = replace(fact, boundary_ms=boundary, bos_break_boundary_ms=bos)
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (activation,), (fact,), "e" * 64)
    proposals = []

    async def noop(*args):
        pass

    async def views(*args):
        return (financial,)

    async def proposal(value):
        proposals.append(value)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda *args: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop, financial_views=views,
        on_entry_proposal=proposal, on_management=noop,
        position_source_owned=lambda view: False,
        financially_active_tickers=lambda: (), finish_boundary=noop,
        observe_activation=noop, observe_completed_seconds=noop,
        strategy_number=12))
    assert counts.entry_proposals == expected
    assert len(proposals) == expected
    assert all(value.strategy_number == 12 for value in proposals)
