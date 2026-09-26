"""Strategy 1 held-position management uses causal evidence and OMS receipts."""
import asyncio
from dataclasses import replace

import pytest

from src.backend.backtest_strategy_one_evidence import (
    StrategyOneCausalEvidence, StrategyOneManagementEvidence,
)
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_position import (
    ResistanceBreak, confirm_protection_transition,
)
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryProposal, StrategyOneFinancialView,
)


def _level(identity, center):
    return {"unified_level_id": identity, "lower": center - .01,
            "upper": center + .01, "side": "resistance", "role": "resistance"}


def _proposal():
    return StrategyOneEntryProposal(
        "A1", "DU1", "AAA", 30_100, 30_000, 10.01, 9.69, 10.3,
        "R3", .5, 30_000, "S1")


def _financial(*, held=1., pending_exit=False):
    return StrategyOneFinancialView(
        "A1", "DU1", "AAA", AssignmentStatus.MANAGING,
        StrategyPermissions(observe=True, enter=True), held,
        False, pending_exit, False, 1)


def _evidence(boundary, *, quote=True, breaks=()):
    return StrategyOneManagementEvidence(
        "AAA", boundary, 10. if quote else None,
        10.01 if quote else None, True, None, None, tuple(breaks),
        tuple(_level(f"R{i}", 10 + i * .1) for i in range(1, 8)))


class _Evidence(StrategyOneCausalEvidence):
    def __init__(self):
        self.rows = {}

    async def management_evidence(self, ticker, resolutions, *, boundary_ms):
        assert ticker == "AAA"
        return self.rows[boundary_ms]


class _Runtime:
    def __init__(self):
        self.fail_protection = False
        self.calls = []

    async def submit_strategy_one_proposal(self, proposal):
        self.calls.append(("entry", proposal.boundary_ms))
        return ({"order_group": "G1", "decision": {"status": "approved"}},)

    async def submit_strategy_one_protection(self, previous, transition,
                                             financial, *, bid, ask):
        self.calls.append(("protection", transition.state.boundary_ms))
        if self.fail_protection:
            raise RuntimeError("OMS failed")
        return confirm_protection_transition(
            previous, transition,
            target_confirmed=transition.target_amendment is not None,
            stop_confirmed=transition.stop_amendment is not None)


def test_breaks_wait_for_fresh_quote_then_commit_only_confirmed_oms_state():
    async def run():
        source, runtime = _Evidence(), _Runtime()
        manager = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        await manager.on_entry_proposal(_proposal())
        held = _financial()
        await manager.on_management(held, {}, 30_100)
        key = ("DU1", "A1", "AAA")
        assert manager._positions[key].stop == 9.69
        breaks = tuple(ResistanceBreak(31_000, _level(f"B{i}", center))
                       for i, center in enumerate((9.8, 9.9, 10.1), 1))
        source.rows[31_000] = _evidence(31_000, quote=False, breaks=breaks)
        await manager.on_management(held, {}, 31_000)
        assert manager._positions[key].boundary_ms == 30_100
        assert len(manager._pending_breaks[key]) == 3
        source.rows[31_100] = _evidence(31_100)
        runtime.fail_protection = True
        with pytest.raises(RuntimeError, match="OMS failed"):
            await manager.on_management(held, {}, 31_100)
        assert manager._positions[key].boundary_ms == 30_100
        assert len(manager._pending_breaks[key]) == 3
        runtime.fail_protection = False
        await manager.on_management(held, {}, 31_100)
        assert manager._positions[key].boundary_ms == 31_100
        assert manager._positions[key].stop == 9.78
        assert manager._pending_breaks[key] == []
        await manager.on_management(replace(held, position_quantity=0.), {}, 31_200)
        assert key not in manager._positions
        assert key not in manager._submitted

    asyncio.run(run())


def test_first_held_boundary_cannot_order_same_bucket_break_after_fill():
    async def run():
        source, runtime = _Evidence(), _Runtime()
        manager = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        await manager.on_entry_proposal(_proposal())
        source.rows[31_000] = _evidence(31_000, breaks=(
            ResistanceBreak(31_000, _level("B1", 9.8)),))
        await manager.on_management(_financial(), {}, 31_000)
        assert runtime.calls == [("entry", 30_100)]
        assert manager._pending_breaks == {}

    asyncio.run(run())
