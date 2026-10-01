"""The sparse Strategy 1 adapter binds real financial ownership, not frames."""
import asyncio
from datetime import date
from types import SimpleNamespace
import threading
import numpy as np
import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_entry_product import CandidateFact
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_execution import run_strategy_one_fixed_session
from src.backend import backtest_strategy_one_execution as execution
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


class _Evidence(StrategyOneCausalEvidence):
    def __init__(self, actions):
        self.session = date(2026, 8, 18)
        self.actions = actions

    async def observe_completed_seconds(self, work):
        self.actions.append(("seconds", work.boundary_ms))

    async def observe_activation(self, activation):
        self.actions.append(("activation", activation.boundary_ms))


class _Broker:
    def __init__(self):
        self.held = True

    def financially_active_tickers(self):
        return ("AAA",) if self.held else ()

    async def positions(self, account_id):
        assert account_id == "DU1"
        return ([SimpleNamespace(conid=123, contractDesc="AAA", position=1.)]
                if self.held else [])


class _Runtime:
    def __init__(self, actions):
        self.config = SimpleNamespace(mode=RunMode.BACKTEST,
                                      strategy_id="early-squeeze-strategy",
                                      strategy_revision=1)
        self.journal = BacktestMemoryJournal(run_id="test")
        self.broker = _Broker()
        self.order_manager = SimpleNamespace(snapshots=lambda: [
            SimpleNamespace(
                group_id="G1", account_id="DU1", assignment_id="A1",
                ticker="AAA", action="enter_long",
                state=OrderManagementState.WORKING,
                filled_quantity=1., entry_submission_closed=True)
        ])
        self.actions = actions

    async def submit_strategy_one_proposal(self, proposal):
        return [{"order_group": "G1", "decision": {"status": "approved"}}]

    async def submit_strategy_one_add(self, proposal):
        raise AssertionError("No completed resistance add is present")

    async def submit_strategy_one_protection(self, *_args, **_kwargs):
        raise AssertionError("No later held boundary may need protection")

    async def process_liquidity_boundary(self, rows, *, at):
        boundary = rows[0]["boundary_ms"]
        self.actions.append(("broker", boundary))
        if boundary == 31_100:
            self.broker.held = False


@pytest.mark.parametrize('strategy_number', [20, 21, 22, 23, 24, 25])
def test_native_numbered_wrapper_advances_cutoff_and_rejects_residual_exposure(monkeypatch, strategy_number):
    """Exercise shared session callbacks; native proposals are covered separately."""
    actions = []
    runtime = _Runtime(actions)
    runtime.config.strategy_revision = strategy_number
    evidence = _Evidence(actions)
    manager = StrategyOneManagementRunner(runtime=runtime, evidence=evidence,
                                         tick_for_ticker=lambda _: .01)
    assignment = StrategyAssignment('A1', 'early-squeeze-strategy', strategy_number,
        'DU1', 'AAA', 123, AssignmentStatus.MANAGING, StrategyPermissions(enter=True), {})
    scheduler = StrategyOneBoundaryScheduler(session_date='2026-08-18',
        candidate_rows=iter(()), active_source=lambda *_: iter(()))
    entry = CertifiedEntryEvidencePlan('b' * 16, '2026-08-18', (), (), (), 'e' * 64)
    async def advance(boundary):
        actions.append(('clock', boundary))
    runtime.advance_numbered_session_clock = advance
    async def proposals(_scheduler, _entry, **callbacks):
        assert callbacks['strategy_number'] == strategy_number
        cutoff = SimpleNamespace(boundary_ms=19_500_000)
        terminal = SimpleNamespace(boundary_ms=19_800_000)
        await callbacks['observe_completed_seconds'](cutoff)
        with pytest.raises(RuntimeError, match='residual exposure'):
            await callbacks['finish_boundary'](terminal)
        runtime.broker.held = False
        await callbacks['finish_boundary'](terminal)
        return SimpleNamespace(completed_boundaries=2)
    async def finish(work):
        actions.append(('finish', work.boundary_ms))
    monkeypatch.setattr(execution, 'run_strategy_one_proposals', proposals)
    asyncio.run(run_strategy_one_fixed_session(scheduler, entry, evidence, manager,
        runtime=runtime, static_gate=StrategyOneStaticGate((),
            np.array([], dtype=np.uint8), np.array([], dtype=np.int64)),
        assignments=(assignment,), before_boundary=lambda _: asyncio.sleep(0),
        finish_boundary=finish))
    assert actions == [('clock', 19_500_000), ('seconds', 19_500_000),
                       ('finish', 19_800_000), ('finish', 19_800_000)]


def test_fixed_adapter_clears_position_after_broker_exit_on_same_boundary():
    actions = []
    runtime = _Runtime(actions)
    evidence = _Evidence(actions)
    manager = StrategyOneManagementRunner(
        runtime=runtime, evidence=evidence, tick_for_ticker=lambda _: .01)
    assignment = StrategyAssignment(
        "A1", "early-squeeze-strategy", 1, "DU1", "AAA", 123,
        AssignmentStatus.MANAGING, StrategyPermissions(enter=True), {})
    proposal = StrategyOneEntryProposal(
        "A1", "DU1", "AAA", 30_100, 30_000, 10.01, 9.69, 10.3,
        "R3", .5, 30_000, "S1")

    def source(ticker, after):
        assert ticker == "AAA" and after == 0
        for boundary in (31_000, 31_100):
            yield boundary, {100: {
                "session_date": "2026-08-18", "ticker": "AAA",
                "boundary_ms": boundary, "resolution_ms": 100}}

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter(()),
        active_source=source)
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (), (), "e" * 64)

    async def finish(work):
        actions.append(("finish", work.boundary_ms))

    async def before(work):
        actions.append(("before", work.boundary_ms))

    async def run():
        await manager.on_entry_proposal(proposal)
        counts = await run_strategy_one_fixed_session(
            scheduler, entry, evidence, manager, runtime=runtime,
            static_gate=StrategyOneStaticGate(
                (), np.array([], dtype=np.uint8),
                np.array([], dtype=np.int64)),
            assignments=(assignment,), before_boundary=before,
            finish_boundary=finish)
        assert (counts.completed_boundaries, counts.management_evaluations) == (2, 2)

    asyncio.run(run())
    assert actions == [
        ("before", 31_000), ("broker", 31_000),
        ("seconds", 31_000), ("finish", 31_000),
        ("before", 31_100), ("broker", 31_100),
        ("seconds", 31_100), ("finish", 31_100)]
    assert manager._submitted == {}
    assert manager._positions == {}


def test_reentry_prior_close_reuses_one_readonly_client_and_closes_it(monkeypatch):
    actions = []
    runtime = _Runtime(actions)
    evidence = _Evidence(actions)
    manager = StrategyOneManagementRunner(
        runtime=runtime, evidence=evidence, tick_for_ticker=lambda _: .01)
    assignment = StrategyAssignment(
        "A1", "early-squeeze-strategy", 1, "DU1", "AAA", 123,
        AssignmentStatus.MANAGING, StrategyPermissions(enter=True), {})
    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter(()),
        active_source=lambda *_: iter(()))
    facts = tuple(CandidateFact(
        "AAA", boundary, 30_000, 30_000, "P1", 120_000,
        "pivot", "R1", "P1", True, 1.0, 2.0, "T1", 1,
    ) for boundary in (31_000, 31_100))
    entry = CertifiedEntryEvidencePlan(
        "b" * 16, "2026-08-18", (), (), facts, "e" * 64)
    prior = SimpleNamespace(closed_boundary_ms=30_000,
                            entry_resistance_id="R1", high_int=120_000)
    monkeypatch.setattr(manager, "last_closed_position", lambda _: prior)
    worker_ids = []
    clients = []

    class Client:
        def __init__(self):
            self.closed = False

        def close(self):
            worker_ids.append(threading.get_ident())
            self.closed = True

    def client_factory():
        worker_ids.append(threading.get_ident())
        client = Client()
        clients.append(client)
        return client

    def previous(_plan, *, ticker, boundaries_ms, client, **_kwargs):
        assert ticker == "AAA" and client is clients[0]
        assert boundaries_ms == (31_000, 31_100)
        worker_ids.append(threading.get_ident())
        return {boundary: 100_000 + boundary for boundary in boundaries_ms}

    async def proposals(_scheduler, _entry, **callbacks):
        financial = SimpleNamespace(ticker="AAA")
        for boundary in (31_000, 31_100):
            candidate = SimpleNamespace(
                market_row={"ticker": "AAA", "close_int": 120_000},
                evidence=SimpleNamespace(boundary_ms=boundary))
            witness = await callbacks["reentry_witness"](financial, candidate)
            assert witness is not None
        return SimpleNamespace(completed_boundaries=2)

    monkeypatch.setattr(execution, "load_previous_completed_100ms_closes_batch", previous)
    monkeypatch.setattr(execution, "run_strategy_one_proposals", proposals)
    asyncio.run(run_strategy_one_fixed_session(
        scheduler, entry, evidence, manager, runtime=runtime,
        static_gate=StrategyOneStaticGate(
            facts, np.zeros(2, dtype=np.uint8), np.arange(2, dtype=np.int64)),
        assignments=(assignment,), market_plan=object(),
        client_factory=client_factory,
        before_boundary=lambda _: asyncio.sleep(0),
        finish_boundary=lambda _: asyncio.sleep(0)))
    assert len(clients) == 1 and clients[0].closed
    assert len(worker_ids) == 3 and len(set(worker_ids)) == 1
