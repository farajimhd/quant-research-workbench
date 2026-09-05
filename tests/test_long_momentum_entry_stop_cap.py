"""Revision 45 caps entry risk distance, never subsequent support updates."""
import unittest
from dataclasses import replace
from datetime import timedelta

from src.trading_runtime import strategy_engine as strategy
from tests.test_long_momentum_contract import level, observation
from tests.test_long_momentum_strategy import NOW, assignment, confirmed_observation


def parameters():
    return strategy.resolve_long_momentum_parameters({"protection": {"stop": {"maximum_risk_pct": 10}}}, revision=45)


class EntryStopCapTests(unittest.TestCase):
    def test_entry_uses_support_within_limit_or_cap_for_distant_support(self):
        for support, expected, capped in ((3.7, 3.7, False), (3.6, 3.6, False), (3.2, 3.6, True)):
            with self.subTest(support=support):
                evidence = {}
                obs = observation(4, structural_support_levels=(level(3.9), level(support)))
                stop = strategy._initial_stop(obs, parameters(), 4, side="long", selection_evidence=evidence)
                self.assertEqual(stop, expected)
                self.assertEqual(evidence["entry_distance_cap_applied"], capped)
                self.assertEqual(evidence["uncapped_structural_stop"], support)
                self.assertEqual(evidence["selected_support_level"]["price"], support)

    def test_support_update_is_not_capped_even_above_old_stop(self):
        state = {"active_stop": 3.6, "initial_stop": 3.6, "entry_reference_price": 4,
                 "entry_at": NOW.isoformat(), "high_water_price": 5}
        obs = observation(5, observed_at=NOW + timedelta(seconds=2),
            structural_support_levels=(level(4.8, confirmed_at=NOW + timedelta(seconds=1)),
                                       level(3.75, confirmed_at=NOW + timedelta(seconds=1))))
        # Reapplying a 10% cap would incorrectly return 4.50 instead of 3.75.
        self.assertEqual(strategy._ratcheted_stop(obs, parameters(), state, side="long"), 3.75)
        self.assertFalse(state["trailing_support_selection"]["entry_distance_cap_evaluated"])
        state["active_stop"] = 3.75
        self.assertEqual(strategy._ratcheted_stop(replace(obs, price=6), parameters(), state, side="long"), 3.75)
        low = replace(obs, structural_support_levels=(level(3.7, confirmed_at=obs.observed_at), level(3.5, confirmed_at=obs.observed_at)))
        self.assertEqual(strategy._ratcheted_stop(low, parameters(), state, side="long"), 3.75)

    def test_old_support_and_missing_support_cannot_raise_capped_stop(self):
        state = {"active_stop": 3.6, "initial_stop": 3.6, "entry_reference_price": 4,
                 "entry_at": NOW.isoformat(), "high_water_price": 8}
        obs = observation(8, observed_at=NOW + timedelta(seconds=2),
                          structural_support_levels=(level(3.9), level(3.75)))
        self.assertEqual(strategy._ratcheted_stop(obs, parameters(), state, side="long"), 3.6)
        self.assertEqual(strategy._ratcheted_stop(replace(obs, structural_support_levels=()), parameters(), state, side="long"), 3.6)
        self.assertEqual(strategy._initial_stop(replace(obs, structural_support_levels=()), parameters(), 8, side="long"), 0)

    def test_legacy_revision_keeps_uncapped_entry_support(self):
        legacy = strategy.resolve_long_momentum_parameters(revision=44)
        self.assertNotIn("cap_initial_stop_distance", legacy["protection"]["stop"])
        obs = observation(4, structural_support_levels=(level(3.9), level(3.2)))
        self.assertEqual(strategy._initial_stop(obs, legacy, 4, side="long"), 3.2)

    def test_actual_entry_intent_carries_capped_stop(self):
        p = parameters()
        p["structural_entry"]["enabled"] = False
        obs = confirmed_observation(price=101, bar_open=100,
            structural_support_levels=(level(95), level(70)),
            structural_resistance_levels=tuple({**level(price), "side": -1} for price in (102, 103, 104)))
        result = strategy.LongMomentumStrategyEngine(revision=45).evaluate(
            assignment(strategy_revision=45, parameters=p), obs)
        self.assertEqual(len(result.evaluation.intents), 1, result.evaluation.signals)
        entry = result.evaluation.intents[0]
        self.assertEqual(entry.action, "enter_long")
        self.assertEqual(entry.invalidation_price, 90.9)
        self.assertEqual(result.state["initial_stop"], 90.9)
        self.assertTrue(entry.metadata["protective_stop_selection"]["entry_distance_cap_applied"])
        self.assertEqual(entry.metadata["protective_stop_selection"]["uncapped_structural_stop"], 70)
