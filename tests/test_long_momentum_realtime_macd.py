"""Revision 44: completed R3 authority and symmetric event-native MACD gaps."""
import unittest
from dataclasses import replace
from datetime import timedelta

from src.trading_runtime import strategy_engine as strategy
from src.backend.replay_run_service import _ProvisionalMacdState
from tests.test_long_momentum_r3_acceptance import bar, parameters
from tests.test_long_momentum_strategy import NOW, assignment


def current_parameters():
    return strategy.resolve_long_momentum_parameters(parameters(), revision=44)


class RealtimeMacdTests(unittest.TestCase):
    def test_entry_arms_at_close_then_enters_at_exact_gap_intrabar(self):
        engine = strategy.LongMomentumStrategyEngine(revision=44)
        current = assignment(strategy_revision=44, parameters=current_parameters())
        sources = {"indicator.flow_structure.score@100ms": {"value": .7},
                   "indicator.flow_structure.confidence@100ms": {"value": .8},
                   "indicator.macd.line@5s": {"value": .4},
                   "indicator.macd.signal@5s": {"value": .2},
                   "indicator.macd.histogram@5s": {"value": .2}}
        first = engine.evaluate(current, bar(macd_line=-.1, macd_signal=.2,
                                             macd_histogram=-.3, source_values=sources))
        self.assertEqual(first.evaluation.intents, ())
        self.assertIn("accepted_entry_r3", first.state)
        current = replace(current, state=first.state, status=first.status)
        for gap, expected in ((.49, False), (.5, True), (.51, True)):
            with self.subTest(gap=gap):
                obs = replace(bar(close=101.3, source_values=sources),
                              observed_at=NOW + timedelta(milliseconds=250),
                              source_timeframe="", evaluation_events=("market_data_update",),
                              macd_line=.2 + 101.3 * gap / 10000, macd_signal=.2)
                result = engine.evaluate(current, obs)
                self.assertEqual(bool(result.evaluation.intents), expected, result.evaluation.signals)
                if expected:
                    self.assertEqual(result.evaluation.intents[0].action, "enter_long")

    def test_wick_cannot_arm_and_changed_or_lost_r3_requires_new_close(self):
        policy = current_parameters()["structural_entry"]
        trigger = strategy._prior_completed_frame_resistance_trigger
        tick = replace(bar(), observed_at=NOW + timedelta(milliseconds=250),
                       evaluation_events=("market_data_update",), source_timeframe="")
        state = {}
        self.assertFalse(trigger(tick, policy, state)["passed"])
        trigger(bar(), policy, state)
        self.assertTrue(trigger(tick, policy, state)["passed"])
        self.assertFalse(trigger(replace(tick, source_timeframe="5s", evaluation_events=("bar_close",)),
                                 policy, state)["passed"])
        self.assertFalse(trigger(replace(tick, bar_open=102), policy, state)["passed"])
        changed = replace(bar(close=101.7, prices=(103, 102, 101.5)),
                          observed_at=tick.observed_at, evaluation_events=tick.evaluation_events)
        self.assertFalse(trigger(changed, policy, state)["passed"])
        self.assertNotIn("accepted_entry_r3", state)
        self.assertTrue(trigger(replace(changed, evaluation_events=("bar_close",)), policy, state)["passed"])
        self.assertFalse(trigger(replace(changed, price=101.5), policy, state)["passed"])

    def test_both_exit_routes_require_gap_on_trade_without_delay(self):
        for loss_guard in (False, True):
            p = current_parameters()
            p["momentum_management"]["downside_loss_guard"].update(enabled=loss_guard, below_vwap=False)
            for gap in (.49, .5, .51):
                with self.subTest(loss_guard=loss_guard, gap=gap):
                    obs = replace(bar(close=100), observed_at=NOW + timedelta(milliseconds=250),
                                  macd_line=.2, macd_signal=.2 + 100 * gap / 10000,
                                  source_timeframe="", evaluation_events=("market_data_update",))
                    route = strategy._matching_momentum_management_route(
                        p, obs, {"entry_at": NOW.isoformat()}, gain_pct=-1 if loss_guard else 1, side="long")
                    self.assertEqual(route is not None, gap >= .5)
                    if route:
                        self.assertAlmostEqual(route["evidence"]["histogram_bps"], -gap)
                        self.assertEqual(route["evidence"]["minimum_exit_gap_bps"], .5)

    def test_vwap_exit_remains_independent_of_macd_gap(self):
        p = current_parameters()
        p["momentum_management"]["downside_loss_guard"].update(enabled=True, below_vwap=True)
        route = strategy._matching_momentum_management_route(
            p, replace(bar(close=100), vwap=101, macd_line=.2, macd_signal=.2),
            {"entry_at": NOW.isoformat()}, gain_pct=-1, side="long")
        self.assertEqual(route["mechanism"], "downside_vwap_lost")

    def test_engine_cancels_pending_acquisition_at_exit_gap_even_before_fills(self):
        engine = strategy.LongMomentumStrategyEngine(revision=44)
        p = current_parameters()
        p["protection"]["trailing"]["enabled"] = False
        p["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
        for quantity in (0, 30):
            current = assignment(strategy_revision=44, parameters=p,
                status=strategy.AssignmentStatus.ENTRY_PENDING,
                state={"active_stop": 90, "initial_stop": 90, "entry_at": NOW.isoformat(),
                       "entry_reference_price": 101, "high_water_price": 101})
            obs = replace(bar(close=100), observed_at=NOW + timedelta(milliseconds=250),
                          average_price=101, position_quantity=quantity, vwap=99,
                          macd_line=.2, macd_signal=.205, source_timeframe="",
                          evaluation_events=("market_data_update",))
            result = engine.evaluate(current, obs)
            self.assertEqual(result.evaluation.intents[0].action, "exit")
            self.assertTrue(result.evaluation.intents[0].metadata["cancel_entry_acquisition"])

    def test_preview_does_not_commit_ema_on_ticks(self):
        state = _ProvisionalMacdState(ema_fast=10, ema_slow=9.9, signal=.05,
                                     committed_at=NOW, sample_count=26)
        before = state.checkpoint()
        first = state.preview(10.1)
        state.preview(10.2)
        self.assertEqual(state.preview(10.1), first)
        self.assertEqual(state.checkpoint(), before)

    def test_old_revision_keeps_old_clock_and_gap_contract(self):
        p = strategy.resolve_long_momentum_parameters(revision=43)
        self.assertNotIn("evaluate_macd_intrabar", p["entry_candle_confirmation"])
        self.assertNotIn("minimum_macd_exit_gap_bps", p["momentum_management"])
        for bad in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                strategy.resolve_long_momentum_parameters({"momentum_management": {
                    "minimum_macd_exit_gap_bps": bad}})
