"""Revision 43 completed-bar R3 acceptance and actual engine entry checks."""
import unittest
from dataclasses import replace
from datetime import timedelta

from src.trading_runtime import strategy_engine as strategy
from tests.test_long_momentum_contract import level
from tests.test_long_momentum_strategy import NOW, assignment, confirmed_observation


def parameters():
    result = strategy.resolve_long_momentum_parameters(revision=43)
    result["protection"]["luld_profit_target"]["enabled"] = False
    result["structural_entry"]["minimum_reaction_probability"] = 0
    result["structural_entry"]["enabled"] = True
    return result


def bar(second=0, close=101.2, prices=(103, 102, 101), **kwargs):
    return confirmed_observation(
        observed_at=NOW + timedelta(seconds=second), price=close, bar_open=close - .1,
        source_timeframe="1s", structural_session_high=103,
        structural_resistance_levels=tuple({**level(p), "side": -1} for p in (*prices, 104, 105, 106)),
        structural_support_levels=(level(99), level(98)), **kwargs)


class R3AcceptanceTests(unittest.TestCase):
    def trigger(self, observation, state=None):
        return strategy._prior_completed_frame_resistance_trigger(
            observation, parameters()["structural_entry"], state if state is not None else {})

    def test_later_close_does_not_need_new_cross(self):
        state = {}
        for second, close in enumerate((101.2, 101.3, 102.2)):
            result = self.trigger(bar(second, close), state)
            self.assertTrue(result["passed"])
            self.assertEqual(result["reference_price"], 101)
            self.assertEqual(result["acceptance"]["accepted_at"], NOW.isoformat())
            self.assertEqual([r["price"] for r in result["current_snapshot"]["levels"]], [103, 102, 101])

    def test_equal_or_below_r3_invalidates_then_non_red_above_requalifies(self):
        state = {}
        self.trigger(bar(), state)
        for second, close in ((1, 101), (2, 100.9)):
            self.assertFalse(self.trigger(bar(second, close), state)["passed"])
            self.assertNotIn("accepted_entry_r3", state)
        self.assertTrue(self.trigger(bar(3), state)["passed"])

    def test_dynamic_r3_is_reselected_and_needs_its_own_confirmation(self):
        state = {}
        self.trigger(bar(), state)
        changed = bar(1, prices=(103, 102, 101.5))
        self.assertFalse(self.trigger(changed, state)["passed"])
        result = self.trigger(replace(changed, observed_at=NOW + timedelta(seconds=2), price=101.6), state)
        self.assertTrue(result["passed"])
        self.assertEqual(result["reference_price"], 101.5)
        self.assertEqual(result["acceptance"]["accepted_at"], (NOW + timedelta(seconds=2)).isoformat())

    def test_red_doji_intrabar_missing_open_and_missing_r3(self):
        self.assertFalse(self.trigger(replace(bar(), bar_open=101.3))["passed"])
        self.assertTrue(self.trigger(replace(bar(), bar_open=101.2))["passed"])
        self.assertFalse(self.trigger(replace(bar(), evaluation_events=("market_data_update",)))["passed"])
        self.assertFalse(self.trigger(replace(bar(), bar_open=None))["passed"])
        self.assertFalse(self.trigger(bar(prices=(103, 102)))["passed"])

    def test_unqualified_and_future_r3_cannot_confirm(self):
        for invalid in (level(101, quality=.19), level(101, confirmed_at=NOW + timedelta(seconds=1))):
            obs = replace(bar(), structural_resistance_levels=(
                {**level(103), "side": -1}, {**level(102), "side": -1}, {**invalid, "side": -1}))
            self.assertFalse(self.trigger(obs)["passed"])

    def test_actual_engine_waits_for_macd_then_enters_above_same_r3(self):
        engine = strategy.LongMomentumStrategyEngine(revision=43)
        current = assignment(strategy_revision=43, parameters=parameters())
        sources = {"indicator.flow_structure.score@100ms": {"value": .7},
                   "indicator.flow_structure.confidence@100ms": {"value": .8},
                   "indicator.macd.line@5s": {"value": .4},
                   "indicator.macd.signal@5s": {"value": .2},
                   "indicator.macd.histogram@5s": {"value": .2}}
        first = engine.evaluate(current, bar(macd_line=-.1, macd_signal=.2, macd_histogram=-.3,
                                             source_values=sources))
        self.assertEqual(first.evaluation.intents, ())
        self.assertEqual(first.evaluation.signals[0].reason, "entry_macd_not_positive_open")
        self.assertTrue(first.state["latest_structural_entry_trigger"]["passed"])
        later = engine.evaluate(replace(current, state=first.state, status=first.status),
                                bar(1, 101.3, source_values=sources))
        self.assertEqual([i.action for i in later.evaluation.intents], ["enter_long"])

    def test_historical_revision_keeps_fresh_cross_requirement(self):
        policy = strategy.resolve_long_momentum_parameters(revision=42)["structural_entry"]
        policy["minimum_reaction_probability"] = 0
        state = {}
        for second, close, expected in ((0, 100.9, False), (1, 101.2, True), (2, 101.3, False)):
            result = strategy._prior_completed_frame_resistance_trigger(bar(second, close), policy, state)
            self.assertEqual(result["passed"], expected)

    def test_revision_normalizes_stale_entry_overrides(self):
        policy = strategy.resolve_long_momentum_parameters({"structural_entry": {
            "persistent_r3_acceptance": False, "maximum_entry_levels": 1, "acceptance_buffer_bps": 100}},
            revision=43)["structural_entry"]
        self.assertTrue(policy["persistent_r3_acceptance"])
        self.assertEqual(policy["maximum_entry_levels"], 3)
        self.assertEqual(policy["acceptance_buffer_bps"], 0)


if __name__ == "__main__":
    unittest.main()
