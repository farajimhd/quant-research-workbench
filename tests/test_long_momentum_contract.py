"""Revision 37 contract checks. Synthetic observations only; no historical run."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.trading_runtime import strategy_engine as strategy
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy
from src.trading_runtime.order_management import OrderManagementState
from tests.test_long_momentum_strategy import NOW, assignment, confirmed_observation
from tests.test_portfolio_management import intent, ledger, summary
from tests import test_order_management as oms_helpers


def level(price, identity=None, *, confirmed_at=NOW, quality=0.8):
    return {"unified_level_id": identity or str(price), "price": price,
            "lower": price, "upper": price, "salience": 1.0, "confidence": 1.0,
            "hold_observation_count": 2, "ticker_relative_quality_score": quality,
            "ticker_relative_quality_status": "available",
            "confirmed_at_ms": confirmed_at.timestamp() * 1000}


def parameters(mode="qualified_support"):
    result = strategy.resolve_long_momentum_parameters({"protection": {"trailing": {"mode": mode}}})
    result["protection"]["luld_profit_target"]["enabled"] = False
    return result


def observation(price=101, **kwargs):
    return confirmed_observation(price=price, evaluation_events=("market_data_update",), **kwargs)


class StrategyContractTests(unittest.TestCase):
    def test_both_trailing_modes_are_versioned_and_selectable(self):
        self.assertEqual(strategy.STRATEGY_REVISION, 37)
        self.assertEqual(parameters()["protection"]["trailing"]["mode"], "qualified_support")
        self.assertEqual(parameters("support_distance")["protection"]["trailing"]["mode"], "support_distance")
        with self.assertRaises(ValueError):
            parameters("invented")

    def test_stale_add_and_replenishment_configuration_cannot_issue_adds(self):
        result = strategy.resolve_long_momentum_parameters({
            "add": {"enabled": True}, "structural_entry": {"entry_tranche_count": 3},
            "phase_policy": {"initial_entry": {"add_steps": [{"enabled": True}],
                                                "order_intent": {"execution_policy": "adaptive_urgent", "partial_fill_policy": "complete_remainder"}}},
            "reentry": {"target_replenishment": {"enabled": True}},
        })
        self.assertFalse(result["add"]["enabled"])
        self.assertEqual(result["structural_entry"]["entry_tranche_count"], 1)
        self.assertEqual(result["phase_policy"]["initial_entry"]["add_steps"], [])
        self.assertFalse(result["reentry"]["target_replenishment"]["enabled"])

    def test_initial_second_support_has_no_percentage_substitute(self):
        evidence = {}
        obs = observation(structural_support_levels=(level(95), level(70)))
        self.assertEqual(strategy._initial_stop(obs, parameters(), None, side="long", selection_evidence=evidence), 70)
        self.assertEqual(evidence["ticker_relative_unavailable_policy"], "fail_closed")
        self.assertEqual(strategy._initial_stop(replace(obs, structural_support_levels=(level(95),)), parameters(), None, side="long"), 0)

    def test_support_stop_requires_newer_support_and_never_loosens(self):
        state = {"active_stop": 90, "initial_stop": 90, "entry_reference_price": 100,
                 "entry_at": NOW.isoformat(), "high_water_price": 150}
        obs = observation(110, observed_at=NOW + timedelta(seconds=3),
                          structural_support_levels=(level(105), level(100)))
        self.assertEqual(strategy._ratcheted_stop(obs, parameters(), state, side="long"), 90)
        newer = tuple(level(value, confirmed_at=NOW + timedelta(seconds=1)) for value in (105, 100))
        self.assertEqual(strategy._ratcheted_stop(replace(obs, structural_support_levels=newer), parameters(), state, side="long"), 100)
        state["active_stop"] = 100
        self.assertEqual(strategy._ratcheted_stop(replace(obs, structural_support_levels=(level(99), level(95))), parameters(), state, side="long"), 100)
        future = tuple(level(value, confirmed_at=NOW + timedelta(seconds=4)) for value in (109, 108))
        self.assertEqual(strategy._ratcheted_stop(replace(obs, structural_support_levels=future), parameters(), state, side="long"), 100)

    def test_optional_distance_tracks_high_water_from_initial_support(self):
        obs = observation(110, structural_support_levels=(level(99), level(95)))
        self.assertIsNone(strategy._trailing_amount(obs, parameters(), stop=95))
        state = {"active_stop": 95, "entry_reference_price": 100,
                 "entry_at": NOW.isoformat(), "trailing_amount": 5, "high_water_price": 112}
        self.assertEqual(strategy._ratcheted_stop(obs, parameters("support_distance"), state, side="long"), 107)

    def target_result(self, previous=100, price=101, *, first=101, events=("market_data_update",), state=None):
        state = state if state is not None else {"previous_observed_price": previous,
                 "structural_profit_targets": [103],
                 "structural_profit_target_frontier": [level(101, "r1"), level(102), level(103)]}
        obs = observation(price, position_quantity=50,
                          structural_resistance_levels=(level(first, "r1"), level(102), level(103), level(104), level(105)))
        obs = replace(obs, evaluation_events=events)
        return strategy.LongMomentumStrategyEngine()._moving_target_result(
            assignment(strategy_revision=37, parameters=parameters(), status=strategy.AssignmentStatus.MANAGING),
            obs, parameters(), state, side="long", stop=95), state

    def test_first_resistance_touch_moves_r3_one_level_without_bar_close(self):
        result, state = self.target_result()
        self.assertEqual(result.evaluation.intents[0].action, "replace_profit_target")
        self.assertEqual(state["structural_profit_targets"], [104])
        state["previous_observed_price"] = 101
        self.assertIsNone(self.target_result(previous=101, price=101.1, state=state)[0])

    def test_no_target_move_without_actual_hit_or_when_level_moved_up(self):
        self.assertIsNone(self.target_result(previous=101.1, price=101.2)[0])
        self.assertIsNone(self.target_result(price=101.1, first=101.5)[0])
        self.assertIsNone(self.target_result(events=("bar_close",))[0])

    def test_removed_resistance_cannot_trigger_stale_advancement(self):
        state = {"previous_observed_price": 100, "structural_profit_targets": [103],
                 "structural_profit_target_frontier": [level(101, "removed")]}
        self.assertIsNone(self.target_result(state=state)[0])

    def test_zero_fill_pending_entry_exits_on_breached_support(self):
        current = assignment(strategy_revision=37, parameters=parameters(), status=strategy.AssignmentStatus.ENTRY_PENDING,
                             state={"entry_at": NOW.isoformat(), "entry_reference_price": 100,
                                    "initial_stop": 95, "active_stop": 95})
        result = strategy.LongMomentumStrategyEngine().evaluate(current, observation(94, position_quantity=0))
        self.assertEqual(result.status, strategy.AssignmentStatus.EXIT_PENDING)
        request = result.evaluation.intents[0]
        self.assertEqual(request.quantity, 0)
        self.assertTrue(request.metadata["cancel_entry_acquisition"])

    def test_entry_keeps_causal_resistance_cross_that_producer_flips_to_support(self):
        policy = parameters()["structural_entry"]
        policy["minimum_reaction_probability"] = 0
        state = {"previous_observed_price": 99}
        before = observation(100, structural_session_high=102,
                             structural_resistance_levels=({**level(101, "pivot"), "side": -1},))
        strategy._event_price_top_n_resistance_trigger(before, policy, state)
        state["previous_observed_price"] = 100
        after = observation(102, structural_session_high=102,
                            structural_support_levels=({**level(101, "pivot"), "side": 1},))
        result = strategy._event_price_top_n_resistance_trigger(after, policy, state)
        self.assertTrue(result["passed"])
        self.assertTrue(result["level"]["crossing_role_flip"])
        self.assertEqual(result["current_snapshot"]["levels"], [])

    def test_stop_update_does_not_swallow_same_event_target_hit(self):
        state = {"entry_at": (NOW - timedelta(seconds=10)).isoformat(), "entry_reference_price": 100,
                 "active_stop": 90, "initial_stop": 90, "last_price": 100,
                 "structural_profit_targets": [103], "structural_profit_target_frontier": [level(101, "r1")]}
        obs = observation(101, position_quantity=50, structural_support_levels=(level(99), level(95)),
                          structural_resistance_levels=(level(101, "r1"), level(102), level(103), level(104)))
        result = strategy.LongMomentumStrategyEngine().evaluate(
            assignment(strategy_revision=37, parameters=parameters(), status=strategy.AssignmentStatus.MANAGING, state=state), obs)
        self.assertEqual([row.action for row in result.evaluation.intents], ["replace_protective_stop", "replace_profit_target"])


class AssignmentExitRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_and_late_buy_cannot_unlatch_exit(self):
        current = assignment(strategy_revision=37, parameters=parameters(), status=strategy.AssignmentStatus.EXIT_PENDING,
                             state={"entry_acquisition_exit_latched": True})
        executor = strategy.AssignedLongMomentumStrategy([current])
        snapshot = SimpleNamespace(assignment_id=current.assignment_id, state="cancelled", action="enter_long",
                                   filled_quantity=5, fill_incremental_quantity=0, updated_at=NOW)
        await executor.on_order_group_update(snapshot, aggregate_position_quantity=5)
        self.assertEqual(executor.assignments()[0].status, strategy.AssignmentStatus.EXIT_PENDING)
        snapshot.state = "filled"
        snapshot.fill_incremental_quantity = 2
        await executor.on_order_group_update(snapshot, aggregate_position_quantity=7)
        self.assertEqual(executor.assignments()[0].status, strategy.AssignmentStatus.EXIT_PENDING)


class PortfolioContractTests(unittest.IsolatedAsyncioTestCase):
    def make_portfolio(self, mode="backtest", fraction=1.0):
        journal = TradingJournal(Path(":memory:"))
        self.addCleanup(journal.close)
        policy = PortfolioPolicy(maximum_position_fraction=1, maximum_ticker_fraction=1,
                                 maximum_strategy_fraction=1, maximum_planned_risk_fraction=1,
                                 maximum_open_risk_fraction=1, maximum_buying_power_utilization=1,
                                 minimum_cash_reserve=0)
        profiles = [PortfolioAccountProfile(account, account, mode, "cash", policy,
                                           strategy_allocations={"default": fraction}) for account in ("A", "B")]
        engine = PortfolioManagementEngine(profiles, journal=journal, run_id="contract", strategy_id="long", strategy_revision=37)
        for account in ("A", "B"):
            engine.synchronize_snapshot(account, summary=summary(account, equity=20000, available=10000), ledger=ledger(account, cash=10000), positions=[])
        return engine

    async def test_live_cash_percentage_is_per_account_and_includes_fee_reserve(self):
        engine = self.make_portfolio("live", 0.25)
        request = intent("entry", quantity=1000)
        request = replace(request, metadata={**request.metadata, "entry_completion_quote": "bid"})
        for account in ("A", "B"):
            decision, approved = await engine.approve(request, account_id=account)
            self.assertIsNotNone(approved)
            self.assertEqual(decision.approved_quantity, 24)
        reservations = list(engine.reservations.values())
        self.assertEqual({row.account_id for row in reservations}, {"A", "B"})
        self.assertAlmostEqual(reservations[0].reserved_notional, 2412)

    async def test_backtest_can_use_cash_and_reprice_same_reservation(self):
        engine = self.make_portfolio()
        request = intent("entry", quantity=1000)
        request = replace(request, metadata={**request.metadata, "entry_completion_quote": "bid"})
        decision, approved = await engine.approve(request, account_id="A")
        self.assertEqual(decision.approved_quantity, 99)
        self.assertTrue(await engine.authorize_entry_reprice(approved, "A", 100.1, 99))
        self.assertEqual(len(engine.reservations), 1)
        self.assertFalse(await engine.authorize_entry_reprice(approved, "A", 103, 99))
        self.assertFalse(await engine.authorize_entry_reprice(approved, "B", 100, 99))
        self.assertFalse(await engine.authorize_entry_reprice(approved, "A", float("nan"), 99))

    async def test_repeated_partial_fills_release_reservation_proportionally(self):
        from src.trading_runtime.order_management import OrderGroupSnapshot
        engine = self.make_portfolio()
        request = intent("entry", quantity=100, price=50, invalidation=49)
        request = replace(request, metadata={**request.metadata, "entry_completion_quote": "bid"})
        decision, _ = await engine.approve(request, account_id="A")
        for filled in (20, 40):
            engine.on_order_group_update(OrderGroupSnapshot(
                group_id="g", intent_id="entry", account_id="A", ticker="AAPL", action="enter_long",
                state=OrderManagementState.PARTIALLY_FILLED, client_order_ids=("c",), broker_order_ids=("b",),
                submitted_at=request.event_time, updated_at=request.event_time, filled_quantity=filled,
                remaining_quantity=100-filled, warning_message_ids=(), rejection_reason="",
                decision_to_submit_ms=0, policy_version=1, reentry_after_fill=False, assignment_id="assignment-AAPL"))
        remaining = engine.reservations[decision.reservation_id]
        self.assertEqual(remaining.remaining_quantity, 60)
        self.assertAlmostEqual(remaining.reserved_planned_risk, 60)
        self.assertAlmostEqual(remaining.reserved_notional, 3015)


class StructureCursorClientTests(unittest.TestCase):
    def test_equal_timestamp_boundaries_require_matching_event_sequence(self):
        from src.backend.qmd_gateway_client import qmd_advance_historical_structure_timeline
        at = NOW.isoformat()
        payload = {"complete": True, "session_id": "session", "boundaries": [
            {"as_of": at, "as_of_sequence": sequence, "snapshot": {}} for sequence in (10, 11)]}
        with patch("src.backend.qmd_gateway_client.qmd_history_post_json", return_value=payload):
            self.assertEqual(qmd_advance_historical_structure_timeline(session_id="session", as_ofs=[at, at], as_of_sequences=[10, 11]), payload)
            payload["boundaries"][0]["as_of_sequence"] = 11
            with self.assertRaisesRegex(RuntimeError, "exact event"):
                qmd_advance_historical_structure_timeline(session_id="session", as_ofs=[at, at], as_of_sequences=[10, 11])


class StructureBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefetch_retains_exact_snapshot_for_each_equal_time_event(self):
        from src.backend.replay_run_service import ReplayRunController
        from src.market_engine.events import TradeEvent
        controller = ReplayRunController.__new__(ReplayRunController)
        controller._event_structure_sessions = {}
        events = [TradeEvent((), str(seq), 1, NOW, None, 100, raw={"arrival_sequence": seq},
                             sequence=seq, ticker="TEST", ts=NOW) for seq in (10, 11)]
        controller._event_structure_batch = events
        payload = {"boundaries": [{"snapshot": {"session_high": value}} for value in (100, 101)]}
        with patch("src.backend.replay_run_service.qmd_historical_structure_snapshot", return_value={"session_id": "s"}), \
             patch("src.backend.replay_run_service.qmd_advance_historical_structure_timeline", return_value=payload) as advance:
            first = await controller._event_structure_context(events[0])
            second = await controller._event_structure_context(events[1])
        self.assertEqual(first["qmd_structure_session_high"], 100)
        self.assertEqual(second["qmd_structure_session_high"], 101)
        advance.assert_called_once()
        self.assertEqual(advance.call_args.kwargs["as_of_sequences"], [10, 11])


class OmsContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from src.trading_runtime.order_management import OrderManagementEngine
        from src.trading_runtime.risk import RiskAuthority
        self.journal = TradingJournal(Path(":memory:"))
        self.addCleanup(self.journal.close)
        self.broker = oms_helpers.RecordingBroker()
        await self.broker.initialize()
        risk = RiskAuthority()
        await risk.prime(self.broker, ["DU1"])
        self.authorize = AsyncMock(return_value=True)
        self.manager = OrderManagementEngine(broker=self.broker, planner=oms_helpers.planner,
                                            risk=risk, journal=self.journal, run_id="contract", strategy_id="long",
                                            strategy_revision=37, causal_execution_clock=True,
                                            reprice_authorizer=self.authorize)
        self.addAsyncCleanup(self.manager.close)

    async def submit(self):
        request = oms_helpers.intent()
        request = replace(request, event_time=NOW,
                          metadata={**request.metadata, "quote_observed_at": NOW.isoformat(), "entry_completion_quote": "bid", "assignment_id": "test"})
        return await self.manager.submit_intent(oms_helpers.portfolio_approved(self.journal, request), account_id="DU1", event=None)

    async def test_zero_fill_exit_cancels_parent_and_cannot_reprice_it(self):
        submitted = await self.submit()
        request = replace(oms_helpers.intent(action="exit"), quantity=0,
                          metadata={"assignment_id": "test", "cancel_entry_acquisition": True})
        await self.manager.cancel_entry_acquisition(request, account_id="DU1")
        group = self.manager._groups[submitted.group_id]
        self.assertFalse(await self.manager._attempt_reprice(group, record_time=NOW))
        orders = await self.broker.live_orders()
        self.assertTrue(all(str(row.order_status).lower() == "cancelled" for row in orders))

    async def test_partial_reprice_changes_bid_not_total_quantity(self):
        from src.market_engine.events import QuoteEvent
        from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
        submitted = await self.submit()
        at = NOW + timedelta(milliseconds=100)
        quote = QuoteEvent(1, 9.99, 80, 1, 9.98, 80, (), (), at, raw={"conid": 123},
                           sequence=1, ticker="TEST", ts=at)
        await self.broker.on_market_event(quote)
        await self.manager.reconcile()
        group = self.manager._groups[submitted.group_id]
        self.assertGreater(group.filled_quantity, 0)
        self.assertLess(group.filled_quantity, 100)
        at += timedelta(milliseconds=100)
        self.manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", 10.03, 10.05, 0.01, at, "test"))
        self.assertTrue(await self.manager._attempt_reprice(group, record_time=at))
        self.assertEqual(self.broker.modifications[-1][1].price, 10.03)
        self.assertEqual(self.broker.modifications[-1][1].quantity, 100)
        self.assertEqual(self.authorize.call_args.args[-1], 100 - group.filled_quantity)
        self.authorize.return_value = False
        at += timedelta(milliseconds=100)
        self.manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", 10.04, 10.06, 0.01, at, "test"))
        count = len(self.broker.modifications)
        self.assertFalse(await self.manager._attempt_reprice(group, record_time=at))
        self.assertEqual(len(self.broker.modifications), count)

    async def test_stop_and_target_amendments_preserve_oca_and_repair_contract(self):
        from src.market_engine.events import QuoteEvent
        from src.trading_runtime.execution_policies import ProtectionProfile, ProtectionSlice, StopRule, StopRuleType
        from src.trading_runtime.domain import InstrumentContract
        from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
        planner = IbkrStrategyOrderPlanner()
        self.manager.planner = lambda request, account, event: planner.plan(
            account_id=account, instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
            intent=request, strategy_id="long", strategy_revision=37)
        profile = ProtectionProfile("support", 1, (ProtectionSlice("position", 1.0, StopRule(StopRuleType.FIXED_PRICE, price=9.8), profit_target_price=11),))
        request = replace(oms_helpers.intent(), event_time=NOW, protection_profile=profile,
                          profit_target_price=11, metadata={**oms_helpers.intent().metadata, "quote_observed_at": NOW.isoformat(), "assignment_id": "test"})
        submitted = await self.manager.submit_intent(oms_helpers.portfolio_approved(self.journal, request), account_id="DU1", event=None)
        at = NOW + timedelta(milliseconds=100)
        quote = QuoteEvent(1, 9.99, 4000, 1, 9.98, 4000, (), (), at, raw={"conid": 123},
                           sequence=1, ticker="TEST", ts=at)
        await self.broker.on_market_event(quote)
        await self.manager.reconcile()
        group = self.manager._groups[submitted.group_id]
        self.assertEqual(group.filled_quantity, 100)
        stop = replace(request, intent_id="support-step", action="replace_protective_stop", invalidation_price=9.9)
        await self.manager.submit_intent(oms_helpers.portfolio_approved(self.journal, stop), account_id="DU1", event=None)
        target = replace(request, intent_id="target-step", action="replace_profit_target", profit_target_price=11.2)
        await self.manager.submit_intent(oms_helpers.portfolio_approved(self.journal, target), account_id="DU1", event=None)
        changed = [row for _, row in self.broker.modifications]
        self.assertEqual({row.orderType for row in changed}, {"STP", "LMT"})
        self.assertTrue(all(row.quantity == 100 and row.isSingleGroup for row in changed))
        self.assertEqual(group.intent.resolved_protection_profile().slices[0].stop.price, 9.9)
        self.assertEqual(group.intent.resolved_protection_profile().slices[0].profit_target_price, 11.2)
        # A lost acknowledgement leaves the local repair profile stale while
        # the broker already holds the higher stop. Retrying must recover it.
        group.intent = replace(group.intent, invalidation_price=9.8, protection_profile=profile)
        await self.manager._replace_protective_stop(stop, account_id="DU1")
        self.assertEqual(group.intent.resolved_protection_profile().slices[0].stop.price, 9.9)


class RuntimeExitContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_exit_cancels_zero_fill_without_fabricated_sell(self):
        from src.trading_runtime.runtime import TradingRuntime
        from src.trading_runtime.signals import StrategyEvaluation
        runtime = TradingRuntime.__new__(TradingRuntime)
        runtime.run_id = "test"
        runtime.config = SimpleNamespace(strategy_id="long", strategy_revision=37)
        runtime.journal = TradingJournal(Path(":memory:"))
        self.addCleanup(runtime.journal.close)
        runtime.intent_planner = object()
        runtime.order_manager = SimpleNamespace(cancel_entry_acquisition=AsyncMock(), reconcile=AsyncMock())
        runtime._refresh_portfolio_from_broker = AsyncMock()
        runtime.broker = SimpleNamespace(positions=AsyncMock(return_value=[]))
        runtime.portfolio = SimpleNamespace(approve=AsyncMock())
        runtime._assignment_for_intent = lambda request: None
        request = replace(oms_helpers.intent(action="exit"), quantity=0, metadata={"cancel_entry_acquisition": True})
        result = await runtime._execute_intents(StrategyEvaluation(intents=(request,)), "DU1", None)
        runtime.order_manager.cancel_entry_acquisition.assert_awaited_once()
        runtime.portfolio.approve.assert_not_awaited()
        self.assertEqual(result[0]["decision"]["held_quantity"], 0)

    async def test_working_exit_is_counted_before_any_duplicate_sell(self):
        from src.trading_runtime.runtime import TradingRuntime
        from src.trading_runtime.signals import StrategyEvaluation
        runtime = TradingRuntime.__new__(TradingRuntime)
        runtime.run_id = "test"
        runtime.config = SimpleNamespace(strategy_id="long", strategy_revision=37)
        runtime.journal = TradingJournal(Path(":memory:"))
        self.addCleanup(runtime.journal.close)
        runtime.intent_planner = object()
        runtime.order_manager = SimpleNamespace(cancel_entry_acquisition=AsyncMock(), pending_exit_quantity=AsyncMock(return_value=50))
        runtime._refresh_portfolio_from_broker = AsyncMock()
        runtime.broker = SimpleNamespace(positions=AsyncMock(return_value=[SimpleNamespace(position=50, contractDesc="TEST")]))
        runtime.portfolio = SimpleNamespace(approve=AsyncMock())
        runtime._assignment_for_intent = lambda request: None
        request = replace(oms_helpers.intent(action="exit"), quantity=50, metadata={"cancel_entry_acquisition": True})
        result = await runtime._execute_intents(StrategyEvaluation(intents=(request,)), "DU1", None)
        runtime.portfolio.approve.assert_not_awaited()
        self.assertEqual(result[0]["decision"]["status"], "exit_fill_pending")
