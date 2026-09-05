"""Current momentum contract checks. Synthetic observations only; no historical run."""
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
    def test_retired_first_resistance_reconciles_to_current_ladder(self):
        engine = strategy.LongMomentumStrategyEngine(revision=41)
        for close, expected in ((4.1, None), (4.18, 4.2555)):
            state = {"structural_profit_targets": [4.195],
                     "structural_profit_target_frontier": [level(4.071, "retired"), level(4.125)],
                     "previous_target_close": 4.07}
            obs = confirmed_observation(price=close, position_quantity=100, source_timeframe="1s",
                structural_resistance_levels=tuple(level(p) for p in (4.125, 4.195, 4.2275, 4.2555)))
            result = engine._moving_target_result(
                assignment(strategy_revision=41, parameters=parameters()), obs,
                parameters(), state, side="long", stop=4.0)
            self.assertEqual(result.evaluation.intents[0].profit_target_price if result else None, expected)

    def test_confirmed_non_red_breakout_requests_one_persistent_ask_entry(self):
        policy = parameters()
        policy["structural_entry"].update(enabled=True, minimum_reaction_probability=0,
                                           minimum_ticker_relative_quality_score=0.2)
        levels = tuple({**level(price), "side": -1} for price in (100.4, 100.5, 100.6, 101, 102, 103))
        obs = confirmed_observation(price=100.75, bar_open=100.3, structural_session_high=100.75,
                                    structural_resistance_levels=levels,
                                    structural_support_levels=(level(99), level(98)))
        state = {"qualified_entry_resistance_snapshot": {
            "selected_at": (NOW - timedelta(seconds=1)).isoformat(), "reference_close": 100.3,
            "levels": [{"unified_level_id": str(price), "price": price, "entry_boundary": price}
                       for price in (100.4, 100.5, 100.6)],
        }}
        result = strategy.LongMomentumStrategyEngine(revision=41).evaluate(
            assignment(strategy_revision=41, parameters=policy, state=state), obs)
        self.assertEqual(len(result.evaluation.intents), 1)
        entry = result.evaluation.intents[0]
        self.assertEqual(entry.action, "enter_long")
        self.assertEqual(entry.metadata["entry_completion_quote"], "ask")
        self.assertEqual(entry.profit_target_price, 103)
        self.assertTrue(entry.resolved_execution_policy().envelope.persist_until_cancelled)

    def test_current_engine_blocks_red_completed_entry(self):
        policy = parameters()
        policy["structural_entry"]["enabled"] = False
        result = strategy.LongMomentumStrategyEngine(revision=41).evaluate(
            assignment(strategy_revision=41, parameters=policy),
            confirmed_observation(price=100.75, bar_open=100.80),
        )
        self.assertEqual(result.evaluation.signals[0].reason, "entry_closed_candle_bearish")
        self.assertEqual(result.evaluation.intents, ())

    def test_closed_non_red_entry_is_mandatory_despite_stale_overrides(self):
        result = strategy.resolve_long_momentum_parameters({"entry_candle_confirmation": {
            "enabled": False, "require_closed_bar": False, "reject_bearish_close": False,
        }})
        self.assertTrue(all(result["entry_candle_confirmation"][key] for key in
                            ("enabled", "require_closed_bar", "reject_bearish_close")))
        self.assertEqual(result["structural_entry"]["selection_mode"], strategy._COMPLETED_FRAME_TOP_N_ENTRY_MODE)

    def test_completed_breakout_tracks_current_price_and_rejects_intrabar(self):
        policy = parameters()["structural_entry"]
        policy["minimum_reaction_probability"] = 0
        before = replace(observation(100, structural_session_high=103,
                         structural_resistance_levels=({**level(101, "r1"), "side": -1},)),
                         evaluation_events=("bar_close",), source_timeframe="1s")
        state = {}
        strategy._prior_completed_frame_resistance_trigger(before, policy, state)
        after = replace(before, observed_at=NOW + timedelta(seconds=1), price=101.2,
                        structural_resistance_levels=({**level(101.5, "r1"), "side": -1},))
        self.assertFalse(strategy._prior_completed_frame_resistance_trigger(after, policy, state)["passed"])
        after = replace(after, observed_at=NOW + timedelta(seconds=2), price=101.6)
        self.assertFalse(strategy._prior_completed_frame_resistance_trigger(
            replace(after, evaluation_events=("market_data_update",)), policy, state)["passed"])
        self.assertTrue(strategy._prior_completed_frame_resistance_trigger(after, policy, state)["passed"])

    def test_pending_exit_emits_only_for_uncovered_late_fill(self):
        current = assignment(strategy_revision=41, parameters=parameters(), status=strategy.AssignmentStatus.EXIT_PENDING,
                             state={"last_exit_reason": "protective_stop"})
        engine = strategy.LongMomentumStrategyEngine(revision=41)
        obs = observation(94, position_quantity=50, pending_exit_quantity=50)
        self.assertEqual(engine.evaluate(current, obs).evaluation.intents, ())
        self.assertEqual(len(engine.evaluate(current, replace(obs, position_quantity=55)).evaluation.intents), 1)

    def test_uncrossed_target_ladder_is_not_replaced_by_nearby_book(self):
        _, state = self.target_result(price=100.5)
        self.assertEqual(state["structural_profit_target_frontier"][0]["unified_level_id"], "r1")

    def test_both_trailing_modes_are_versioned_and_selectable(self):
        self.assertEqual(strategy.STRATEGY_REVISION, 42)
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

    def test_qualified_levels_survive_legacy_break_count_ceiling(self):
        resolved = strategy.resolve_long_momentum_parameters({
            "structural_entry": {"maximum_break_count": 100, "minimum_reaction_probability": 0,
                                 "minimum_ticker_relative_quality_score": 0.2, "maximum_break_probability": 1.0},
            "protection": {"profit_ladder": {"maximum_break_count": 100}},
        })
        row = {**level(3.55, quality=0.3578), "break_count": 140, "hold_count": 571,
               "hold_probability": 0.8, "hold_observation_count": 711}
        self.assertNotIn("maximum_break_count", resolved["structural_entry"])
        self.assertNotIn("maximum_break_count", resolved["protection"]["profit_ladder"])
        self.assertTrue(strategy._level_is_entry_quality(row, resolved["structural_entry"], observed_at=NOW))
        self.assertFalse(strategy._level_is_entry_quality(
            row, {**resolved["structural_entry"], "maximum_break_probability": 0}, observed_at=NOW))
        levels = tuple({**level(price), "break_count": 140, "side": -1} for price in (3.524, 3.55, 3.601))
        trigger = strategy._event_price_top_n_resistance_trigger(
            observation(3.53, structural_session_high=3.62, structural_resistance_levels=levels),
            resolved["structural_entry"], {"previous_observed_price": 3.52})
        self.assertTrue(trigger["passed"])
        self.assertEqual(trigger["reference_price"], 3.524)

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

    def target_result(self, previous=100, price=101.1, *, first=101, events=("bar_close",), state=None):
        state = state if state is not None else {"previous_observed_price": previous,
                 "structural_profit_targets": [103],
                 "structural_profit_target_frontier": [level(101, "r1"), level(102), level(103)]}
        obs = observation(price, position_quantity=50,
                          structural_resistance_levels=(level(first, "r1"), level(102), level(103), level(104), level(105)))
        obs = replace(obs, evaluation_events=events, source_timeframe="1s")
        return strategy.LongMomentumStrategyEngine(revision=41)._moving_target_result(
            assignment(strategy_revision=41, parameters=parameters(), status=strategy.AssignmentStatus.MANAGING),
            obs, parameters(), state, side="long", stop=95), state

    def test_first_resistance_close_moves_r3_once_per_completed_candle(self):
        result, state = self.target_result()
        self.assertEqual(result.evaluation.intents[0].action, "replace_profit_target")
        self.assertEqual(state["structural_profit_targets"], [104])
        self.assertEqual(result.evaluation.intents[0].metadata["ratchet_clock"], "completed_1s_bar")
        state["previous_observed_price"] = 101
        self.assertIsNone(self.target_result(previous=101, price=101.1, state=state)[0])

    def test_no_target_move_on_wick_touch_or_when_level_moved_up(self):
        self.assertIsNone(self.target_result(price=101)[0])
        self.assertIsNone(self.target_result(price=101.1, first=101.5)[0])
        self.assertIsNone(self.target_result(events=("market_data_update",))[0])

    def test_removed_resistance_cannot_trigger_stale_advancement(self):
        state = {"previous_observed_price": 100, "structural_profit_targets": [103],
                 "structural_profit_target_frontier": [level(101, "removed")]}
        self.assertIsNone(self.target_result(state=state)[0])

    def test_zero_fill_pending_entry_exits_on_breached_support(self):
        current = assignment(strategy_revision=41, parameters=parameters(), status=strategy.AssignmentStatus.ENTRY_PENDING,
                             state={"entry_at": NOW.isoformat(), "entry_reference_price": 100,
                                    "initial_stop": 95, "active_stop": 95})
        result = strategy.LongMomentumStrategyEngine(revision=41).evaluate(current, observation(94, position_quantity=0))
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
        obs = observation(101.1, position_quantity=50, structural_support_levels=(level(99), level(95)),
                          structural_resistance_levels=(level(101, "r1"), level(102), level(103), level(104)))
        obs = replace(obs, evaluation_events=("bar_close",), source_timeframe="1s")
        result = strategy.LongMomentumStrategyEngine(revision=41).evaluate(
            assignment(strategy_revision=41, parameters=parameters(), status=strategy.AssignmentStatus.MANAGING, state=state), obs)
        self.assertEqual([row.action for row in result.evaluation.intents], ["replace_protective_stop", "replace_profit_target"])


class AssignmentExitRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_deferred_capital_keeps_one_request_and_rechecks_current_candle(self):
        policy = parameters()
        policy["structural_entry"].update(enabled=True, minimum_reaction_probability=0)
        levels = tuple({**level(p), "side": -1} for p in (100.4, 100.5, 100.6, 101, 102, 103))
        state = {"qualified_entry_resistance_snapshot": {
            "selected_at": (NOW-timedelta(seconds=1)).isoformat(), "reference_close": 100.3,
            "levels": [{"unified_level_id": str(p), "price": p} for p in (100.4,100.5,100.6)]}}
        current = assignment(strategy_revision=41, parameters=policy, state=state)
        obs = confirmed_observation(price=100.75, bar_open=100.3, structural_session_high=100.75,
            structural_resistance_levels=levels, structural_support_levels=(level(99),level(98)))
        result = strategy.LongMomentumStrategyEngine(revision=41).evaluate(current, obs)
        request = result.evaluation.intents[0]
        current = replace(current, state=result.state, status=result.status)
        executor = strategy.AssignedLongMomentumStrategy([current], revision=current.strategy_revision)
        await executor.on_intent_deferred(request, reasons=("limited_by_available_funds",), event_time=NOW)
        waiting = executor.assignments()[0]
        resumed = replace(obs, observed_at=NOW+timedelta(seconds=1))
        retry = strategy.LongMomentumStrategyEngine(revision=41).evaluate(waiting, resumed)
        self.assertEqual(retry.evaluation.intents[0].intent_id, request.intent_id)
        self.assertEqual(retry.evaluation.signals[0].action, "hold")
        red = strategy.LongMomentumStrategyEngine(revision=41).evaluate(waiting, replace(resumed, bar_open=101))
        self.assertEqual(red.evaluation.intents, ())
        invalid = strategy.LongMomentumStrategyEngine(revision=41).evaluate(waiting, replace(resumed, price=97))
        self.assertEqual(invalid.evaluation.intents, ())
        self.assertNotIn("pending_capital_request", invalid.state)

    async def test_partial_stop_managed_remainder_preserves_reentry_origin(self):
        for origin, enabled, disabled, expected in (
            ("protective_stop", True, False, strategy.AssignmentStatus.REENTRY_COOLDOWN),
            ("profit_target", True, False, strategy.AssignmentStatus.REENTRY_COOLDOWN),
            ("protective_stop", False, False, strategy.AssignmentStatus.COMPLETED),
            ("protective_stop", True, True, strategy.AssignmentStatus.COMPLETED),
            ("managed_exit", True, False, strategy.AssignmentStatus.COMPLETED),
        ):
            with self.subTest(origin=origin, enabled=enabled, disabled=disabled):
                policy = parameters()
                policy["reentry"]["after_protective_exit"] = enabled
                current = assignment(strategy_revision=41, parameters=policy,
                    status=strategy.AssignmentStatus.MANAGING,
                    state={"entries": 1, "disable_after_exit": disabled})
                executor = strategy.AssignedLongMomentumStrategy([current], revision=current.strategy_revision)
                first = SimpleNamespace(assignment_id=current.assignment_id, action="exit",
                    state="partially_filled", fill_role=origin, fill_incremental_quantity=156,
                    updated_at=NOW, reentry_after_fill=False)
                await executor.on_order_group_update(first, aggregate_position_quantity=2713)
                self.assertEqual(executor.assignments()[0].state["liquidation_origin_fill_role"], origin)
                final = SimpleNamespace(assignment_id=current.assignment_id, action="exit",
                    state="filled", fill_role="managed_exit", fill_incremental_quantity=2713,
                    updated_at=NOW + timedelta(seconds=1), reentry_after_fill=False)
                await executor.on_order_group_update(final, aggregate_position_quantity=0)
                after = executor.assignments()[0]
                self.assertEqual(after.status, expected)
                self.assertEqual(after.state.get("reentries", 0), int(expected == strategy.AssignmentStatus.REENTRY_COOLDOWN))
                await executor.on_order_group_update(final, aggregate_position_quantity=0)
                self.assertEqual(executor.assignments()[0].state.get("reentries", 0), after.state.get("reentries", 0))

    async def test_rejected_add_preserves_existing_position_and_target_frontier(self):
        state = {"entry_reference_price": 3.65, "active_stop": 3.5, "initial_stop": 3.4,
                 "entry_at": NOW.isoformat(), "structural_profit_targets": [3.7845],
                 "structural_profit_target_frontier": [level(3.6745)], "entries": 1}
        current = assignment(strategy_revision=41, parameters=parameters(), status=strategy.AssignmentStatus.MANAGING, state=state)
        executor = strategy.AssignedLongMomentumStrategy([current], revision=current.strategy_revision)
        request = replace(oms_helpers.intent(), action="add_long", metadata={"assignment_id": current.assignment_id})
        await executor.on_intent_rejected(request, reasons=("limited_by_open_risk",), event_time=NOW)
        after = executor.assignments()[0]
        self.assertEqual(after.status, strategy.AssignmentStatus.MANAGING)
        for key, value in state.items():
            self.assertEqual(after.state[key], value)

    async def test_cancel_and_late_buy_cannot_unlatch_exit(self):
        current = assignment(strategy_revision=41, parameters=parameters(), status=strategy.AssignmentStatus.EXIT_PENDING,
                             state={"entry_acquisition_exit_latched": True})
        executor = strategy.AssignedLongMomentumStrategy([current], revision=current.strategy_revision)
        snapshot = SimpleNamespace(assignment_id=current.assignment_id, state="cancelled", action="enter_long",
                                   filled_quantity=5, fill_incremental_quantity=0, updated_at=NOW)
        await executor.on_order_group_update(snapshot, aggregate_position_quantity=5)
        self.assertEqual(executor.assignments()[0].status, strategy.AssignmentStatus.EXIT_PENDING)
        snapshot.state = "filled"
        snapshot.fill_incremental_quantity = 2
        await executor.on_order_group_update(snapshot, aggregate_position_quantity=7)
        self.assertEqual(executor.assignments()[0].status, strategy.AssignmentStatus.EXIT_PENDING)


class PortfolioContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_entry_request_cannot_reserve_cash_twice(self):
        import asyncio
        engine = self.make_portfolio()
        request = replace(intent("same", quantity=20, price=100, invalidation=99),
            metadata={"entry_completion_quote": "ask", "wait_for_capital": True})
        results = await asyncio.gather(*(engine.approve(request, account_id="A") for _ in range(2)))
        self.assertEqual(sum(approved is not None for _, approved in results), 1)
        self.assertEqual(len(engine.reservations), 1)
        self.assertIn("entry_request_already_allocated", results[1][0].reasons)

    async def test_competing_requests_share_cash_and_defer_until_capacity_returns(self):
        import asyncio
        engine = self.make_portfolio()
        requests = [replace(intent(name, quantity=60, price=100, invalidation=99), ticker=name,
                    metadata={"assignment_id": name, "entry_completion_quote": "ask", "wait_for_capital": True})
                    for name in ("AAA", "BBB", "CCC")]
        results = await asyncio.gather(*(engine.approve(r, account_id="A") for r in requests))
        self.assertEqual([d.approved_quantity for d, _ in results], [60, 39, 0])
        self.assertEqual(str(results[2][0].status), "deferred")
        self.assertIn(requests[2].intent_id, engine.states["A"].pending_entry_requests)
        self.assertLessEqual(sum(r.reserved_notional for r in engine.reservations.values()), 10000)
        engine.release_intent(requests[0].intent_id, reason="liquidated")
        decision, approved = await engine.approve(requests[2], account_id="A")
        self.assertEqual(decision.approved_quantity, 60)
        self.assertIsNotNone(approved)
        self.assertNotIn(requests[2].intent_id, engine.states["A"].pending_entry_requests)

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

    async def test_partial_acquisition_does_not_shrink_its_original_risk_budget(self):
        from tests.test_portfolio_management import position
        from src.trading_runtime.order_management import OrderGroupSnapshot
        engine = self.make_portfolio()
        state = engine.states["A"]
        state.profile = replace(state.profile, policy=replace(state.profile.policy,
            maximum_planned_risk_fraction=0.08, maximum_open_risk_fraction=0.08))
        request = replace(intent("entry", quantity=99, price=100, invalidation=92),
                          metadata={"assignment_id": "assignment-AAPL", "entry_completion_quote": "ask"})
        decision, approved = await engine.approve(request, account_id="A")
        self.assertEqual(decision.approved_quantity, 99)
        engine.on_order_group_update(OrderGroupSnapshot(
            group_id="g", intent_id="entry", account_id="A", ticker="AAPL", action="enter_long",
            state=OrderManagementState.PARTIALLY_FILLED, client_order_ids=("c",), broker_order_ids=("b",),
            submitted_at=request.event_time, updated_at=request.event_time, filled_quantity=40,
            remaining_quantity=59, warning_message_ids=(), rejection_reason="", decision_to_submit_ms=0,
            policy_version=1, reentry_after_fill=False, assignment_id="assignment-AAPL"))
        engine.synchronize_snapshot("A", summary=summary("A", equity=10000, available=6000),
                                    ledger=ledger("A", cash=6000), positions=[position("A", "AAPL", quantity=40)])
        self.assertTrue(await engine.authorize_entry_reprice(approved, "A", 100, 59))

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
        self.assertAlmostEqual(sum(row.planned_risk for row in engine.allocations.values()), 40)


class StructureCursorClientTests(unittest.TestCase):
    def test_equal_timestamp_boundaries_require_matching_event_sequence(self):
        from src.backend.qmd_gateway_client import qmd_advance_historical_structure_timeline
        at = NOW.isoformat()
        payload = {"complete": True, "session_id": "session", "source_revision_before": {"token": "execution-clock-v1:test"}, "boundaries": [
            {"as_of": at, "as_of_sequence": sequence, "snapshot": {}} for sequence in (10, 11)]}
        with patch("src.backend.qmd_gateway_client.qmd_history_post_json", return_value=payload):
            self.assertEqual(qmd_advance_historical_structure_timeline(session_id="session", as_ofs=[at, at], as_of_sequences=[10, 11]), payload)
            payload["boundaries"][0]["as_of_sequence"] = 11
            with self.assertRaisesRegex(RuntimeError, "exact event"):
                qmd_advance_historical_structure_timeline(session_id="session", as_ofs=[at, at], as_of_sequences=[10, 11])
            payload["source_revision_before"]["token"] = "structure-input-v1:archive-sip-condition"
            with self.assertRaisesRegex(RuntimeError, "execution clock"):
                qmd_advance_historical_structure_timeline(session_id="session", as_ofs=[at, at], as_of_sequences=[10, 11])


class StructureBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefetch_retains_exact_snapshot_for_each_equal_time_event(self):
        from src.backend.replay_run_service import ReplayRunController
        from src.market_engine.events import TradeEvent
        controller = ReplayRunController.__new__(ReplayRunController)
        controller._event_structure_sessions = {}
        events = [TradeEvent((), str(seq), 1, NOW, None, 100, raw={"arrival_sequence": seq},
                             sequence=seq, ticker="TEST", ts=NOW) for seq in (10, 11)]
        controller._event_structure_batch = [*events, replace(events[0], sequence=12,
            raw={"arrival_sequence": 12, "price_eligible": False})]
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

    async def test_ask_completion_fills_same_quantity_across_changing_quotes(self):
        from src.market_engine.events import QuoteEvent
        from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
        submitted = await self.submit()
        group = self.manager._groups[submitted.group_id]
        group.intent = replace(group.intent, metadata={**group.intent.metadata, "entry_completion_quote": "ask"})
        for index in range(1, 5):
            at = NOW + timedelta(milliseconds=100 * index)
            ask = 10.02 + index * 0.01
            self.manager.on_market_snapshot(ExecutionMarketSnapshot("TEST", ask - 0.01, ask, 0.01, at, "test"))
            await self.manager._attempt_reprice(group, record_time=at)
            self.assertAlmostEqual(self.broker.modifications[-1][1].price, ask)
            await self.broker.on_market_event(QuoteEvent(1, ask, 100, 1, ask - 0.01, 100,
                (), (), at, raw={"conid": 123}, sequence=index, ticker="TEST", ts=at))
            await self.manager.reconcile()
        self.assertEqual(group.filled_quantity, 100)
        self.assertEqual(group.remaining_quantity, 0)
        self.assertTrue(all(request.quantity == 100 for _, request in self.broker.modifications
                            if request.side == "BUY"))

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
        stop = replace(request, intent_id="support-step", action="replace_protective_stop", invalidation_price=10.2, reference_price=10.5)
        await self.manager.submit_intent(oms_helpers.portfolio_approved(self.journal, stop), account_id="DU1", event=None)
        target = replace(request, intent_id="target-step", action="replace_profit_target", profit_target_price=11.2)
        await self.manager.submit_intent(oms_helpers.portfolio_approved(self.journal, target), account_id="DU1", event=None)
        changed = [row for _, row in self.broker.modifications]
        self.assertEqual({row.orderType for row in changed}, {"STP", "LMT"})
        self.assertTrue(all(row.quantity == 100 and row.isSingleGroup for row in changed))
        self.assertEqual(group.intent.resolved_protection_profile().slices[0].stop.price, 10.2)
        self.assertEqual(group.intent.resolved_protection_profile().slices[0].profit_target_price, 11.2)
        # A lost acknowledgement leaves the local repair profile stale while
        # the broker already holds the higher stop. Retrying must recover it.
        group.intent = replace(group.intent, invalidation_price=9.8, protection_profile=profile)
        await self.manager._replace_protective_stop(stop, account_id="DU1")
        self.assertEqual(group.intent.resolved_protection_profile().slices[0].stop.price, 10.2)

        # Restoring quantity after a partial fill must preserve a valid stop
        # above the original entry, without validating it as a new entry stop.
        stops = [row for row in await self.broker.live_orders() if row.orderType == "STP"]
        for row in stops:
            await self.broker.cancel_order("DU1", str(row.orderId))
        await self.manager.reconcile_protection(group)
        repaired = [row for row in await self.broker.live_orders()
                    if row.orderType == "STP" and row.remainingQuantity > 0
                    and row.order_status in {"Submitted", "PreSubmitted"}]
        self.assertTrue(repaired)
        self.assertTrue(all(row.auxPrice == 10.2 for row in repaired))


class RuntimeExitContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_is_published_before_terminal_cleanup(self):
        from src.backend.replay_run_service import ReplayRunController
        controller = ReplayRunController.__new__(ReplayRunController)
        controller.status = "running"
        controller._historical_structure_prefetch_task = None
        controller._flush_passive_market_events = lambda: None
        controller._runtime_finished = False
        controller._runtime = SimpleNamespace(finish=AsyncMock(side_effect=RuntimeError("cleanup failed")))
        published = []
        async def publish(**kwargs):
            published.append(controller.status)
        controller._publish = publish
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            await controller._finish("failed")
        self.assertEqual(published, ["failed"])

    async def test_ineligible_trade_cannot_change_simulator_mark_or_fill(self):
        from src.market_engine.events import TradeEvent
        broker = oms_helpers.RecordingBroker()
        await broker.initialize()
        at = NOW
        event = TradeEvent(conditions=(14, 12, 37, 41), event_id="excluded", exchange=1,
                           ingest_ts=at, participant_ts=at, price=4.15, size=100,
                           ticker="TEST", ts=at, raw={"conid": 123, "price_eligible": False})
        self.assertEqual(await broker.on_market_event(event), [])
        self.assertNotIn("TEST", broker._trades_by_ticker)

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
