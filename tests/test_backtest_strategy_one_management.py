"""Strategy 1 held-position management uses causal evidence and OMS receipts."""
import asyncio
from dataclasses import replace

import pytest

from src.backend.backtest_strategy_one_evidence import (
    StrategyOneManagementEvidence,
)
from src.backend.backtest_strategy_one_management import (
    StrategyOneManagementRunner, StrategyOneManagementState,
)
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_position import (
    ProtectionState, ResistanceBreak, confirm_protection_transition,
)
from src.trading_runtime.strategy_one_add import propose_strategy_one_add
from src.trading_runtime.strategy_one_intent import strategy_one_add_intent
from datetime import date
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


def _add_rows(boundary=31_000):
    return {
        resolution: {
            "ticker": "AAA", "boundary_ms": boundary,
            "resolution_ms": resolution,
            "indicator_resolution_ms": resolution,
            "price_valid": 1, "macd_line": .2, "macd_signal": .1,
            "open_int": 99_000, "close_int": 101_000,
            "quote_valid": 1, "bid_int": 100_000,
            "ask_int": 100_100, "high_int": 101_000,
        } for resolution in (100, 1_000)
    }


def test_add_requires_distinct_completed_bars_bullish_macd_and_fresh_quote():
    financial = _financial()
    protection = ProtectionState(31_000, 9.69, 10.3,
                                 frozenset({"B1"}))
    resistance = ResistanceBreak(31_000, _level("B1", 10.))
    rows = _add_rows()
    proposal = propose_strategy_one_add(
        financial, protection, resistance, rows, boundary_ms=31_000,
        purchase_ordinal=2, fresh_bid=10., fresh_ask=10.01,
        prior_accepted_ids=frozenset())
    assert proposal is not None
    assert proposal.resistance_id == "B1"
    intent = strategy_one_add_intent(proposal, session_date=date(2026, 8, 18))
    assert intent.action == "add_long" and intent.metadata == {}
    assert intent.reason == "strategy_one_add"
    assert intent.capital_request.value == 1 / 3
    for resolution in (100, 1_000):
        blocked = {key: dict(value) for key, value in rows.items()}
        blocked[resolution]["macd_line"] = -.1
        assert propose_strategy_one_add(
            financial, protection, resistance, blocked,
            boundary_ms=31_000, purchase_ordinal=2,
            fresh_bid=10., fresh_ask=10.01,
            prior_accepted_ids=frozenset()) is None
    assert propose_strategy_one_add(
        financial, protection, resistance, rows, boundary_ms=31_000,
        purchase_ordinal=2, fresh_bid=None, fresh_ask=None,
        prior_accepted_ids=frozenset()) is None
    assert propose_strategy_one_add(
        replace(financial, current_purchase_groups=3), protection,
        resistance, rows, boundary_ms=31_000, purchase_ordinal=2,
        fresh_bid=10., fresh_ask=10.01,
        prior_accepted_ids=frozenset()) is None
    with pytest.raises(ValueError, match="new pinned resistance"):
        propose_strategy_one_add(
            financial, protection, resistance, rows,
            boundary_ms=31_000, purchase_ordinal=2,
            fresh_bid=10., fresh_ask=10.01,
            prior_accepted_ids=frozenset({"B1"}))


def test_manager_submits_at_most_two_distinct_add_groups_after_entry():
    async def run():
        source, runtime = _Evidence(), _Runtime()
        manager = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        await manager.on_entry_proposal(_proposal())
        await manager.on_management(_financial(), {}, 30_100)
        breaks = tuple(ResistanceBreak(31_000, _level(identity, center))
                       for identity, center in (("B1", 9.8), ("B2", 9.9)))
        source.rows[31_000] = _evidence(31_000, breaks=breaks)
        await manager.on_management(_financial(), _add_rows(), 31_000)
        assert [call for call in runtime.calls if call[0] == "add"] == [
            ("add", "B1"), ("add", "B2")]
        later = (ResistanceBreak(32_000, _level("B3", 10.)),)
        source.rows[32_000] = _evidence(32_000, breaks=later)
        rows = _add_rows(32_000)
        await manager.on_management(
            replace(_financial(), current_purchase_groups=3), rows, 32_000)
        assert len([call for call in runtime.calls if call[0] == "add"]) == 2

    asyncio.run(run())


class _Evidence:
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

    async def submit_strategy_one_add(self, proposal):
        self.calls.append(("add", proposal.resistance_id))
        return ({"order_group": proposal.resistance_id,
                 "decision": {"status": "approved"}},)

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
        assert not manager.owns_position_source(_financial(held=0.))
        await manager.on_entry_proposal(_proposal())
        held = _financial()
        assert manager.owns_position_source(held)
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
        assert not manager.owns_position_source(held)
        assert manager.last_closed_position(held).closed_boundary_ms == 31_200
        assert manager.last_closed_position(held).entry_resistance_id == "S1"

    asyncio.run(run())


def test_prior_position_high_uses_only_completed_bars_after_entry_bucket():
    async def run():
        source, runtime = _Evidence(), _Runtime()
        manager = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        await manager.on_entry_proposal(_proposal())
        held = _financial()
        await manager.on_management(
            held, {100: {"price_valid": 1, "high_int": 200_000}}, 30_100)
        assert manager.capture_state(boundary_ms=30_100).position_highs == (
            (("DU1", "A1", "AAA"), 100_100),)
        source.rows[30_200] = _evidence(30_200)
        await manager.on_management(
            held, {100: {"price_valid": 1, "high_int": 101_200}}, 30_200)
        captured = manager.capture_state(boundary_ms=30_200)
        restored = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        restored.restore_state(captured)
        assert restored.capture_state(boundary_ms=30_200) == captured
        await restored.on_management(
            replace(held, position_quantity=0.),
            {100: {"price_valid": 1, "high_int": 300_000}}, 30_300)
        prior = restored.last_closed_position(held)
        assert prior.high_int == 101_200
        assert prior.closed_boundary_ms == 30_300
        assert restored.capture_state(boundary_ms=30_300).closed_positions == (
            (("DU1", "A1", "AAA"), prior),)

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


def test_management_rejects_evidence_from_another_completed_boundary():
    async def run():
        source, runtime = _Evidence(), _Runtime()
        manager = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        await manager.on_entry_proposal(_proposal())
        await manager.on_management(_financial(), {}, 30_100)
        source.rows[31_000] = _evidence(31_100)
        with pytest.raises(ValueError, match="causal boundary"):
            await manager.on_management(_financial(), {}, 31_000)
        assert runtime.calls == [("entry", 30_100)]

    asyncio.run(run())


def test_typed_manager_capture_restores_pending_entry_position_and_breaks():
    async def run():
        source, runtime = _Evidence(), _Runtime()
        first = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        await first.on_entry_proposal(_proposal())
        await first.on_management(_financial(), {}, 30_100)
        source.rows[31_000] = _evidence(31_000, quote=False, breaks=(
            ResistanceBreak(31_000, _level("B1", 9.8)),))
        await first.on_management(_financial(), {}, 31_000)
        captured = first.capture_state(boundary_ms=31_000)
        assert isinstance(captured, StrategyOneManagementState)
        original_lower = captured.pending_breaks[0][1][0].level["lower"]
        first._pending_breaks[("DU1", "A1", "AAA")][0].level["lower"] = 1.0
        assert captured.pending_breaks[0][1][0].level["lower"] == original_lower
        first._pending_breaks[("DU1", "A1", "AAA")][0].level["lower"] = original_lower
        second = StrategyOneManagementRunner(
            runtime=runtime, evidence=source, tick_for_ticker=lambda _: .01)
        second.restore_state(captured)
        assert second.capture_state(boundary_ms=31_000) == captured
        source.rows[31_100] = _evidence(31_100)
        await first.on_management(_financial(), {}, 31_100)
        await second.on_management(_financial(), {}, 31_100)
        assert second.capture_state(boundary_ms=31_100) == first.capture_state(
            boundary_ms=31_100)
        with pytest.raises(RuntimeError, match="already active"):
            second.restore_state(captured)
        with pytest.raises(ValueError, match="entry source"):
            second._validate_capture(replace(captured, submitted=()),
                                     max_pending_breaks=256)

    asyncio.run(run())
