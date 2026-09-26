"""The sparse Strategy 1 adapter binds real financial ownership, not frames."""
import asyncio
from datetime import date
from types import SimpleNamespace
import numpy as np

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_execution import run_strategy_one_fixed_session
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
from src.trading_runtime.runtime import RunMode
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
        self.order_manager = SimpleNamespace(snapshots=lambda: [])
        self.actions = actions

    async def submit_strategy_one_proposal(self, proposal):
        return [{"order_group": "G1", "decision": {"status": "approved"}}]

    async def submit_strategy_one_protection(self, *_args, **_kwargs):
        raise AssertionError("No later held boundary may need protection")

    async def process_liquidity_boundary(self, rows, *, at):
        boundary = rows[0]["boundary_ms"]
        self.actions.append(("broker", boundary))
        if boundary == 31_100:
            self.broker.held = False


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

    async def run():
        await manager.on_entry_proposal(proposal)
        counts = await run_strategy_one_fixed_session(
            scheduler, entry, evidence, manager, runtime=runtime,
            static_gate=StrategyOneStaticGate(
                (), np.array([], dtype=np.uint8),
                np.array([], dtype=np.int64)),
            assignments=(assignment,), finish_boundary=finish)
        assert (counts.completed_boundaries, counts.management_evaluations) == (2, 2)

    asyncio.run(run())
    assert actions == [
        ("broker", 31_000), ("seconds", 31_000), ("finish", 31_000),
        ("broker", 31_100), ("seconds", 31_100), ("finish", 31_100)]
    assert manager._submitted == {}
    assert manager._positions == {}
