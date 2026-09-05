"""Revision 42 candle-confirmed reconciliation against a changing producer book."""
import unittest
from datetime import timedelta
from dataclasses import replace

from src.trading_runtime import strategy_engine as strategy
from tests.test_long_momentum_contract import level, parameters, observation
from tests.test_long_momentum_strategy import NOW, assignment


class DynamicTargetsTests(unittest.TestCase):
    def run_close(self, *, close=102.5, opened=100, rows=None, state=None,
                  seconds=0, events=("bar_close",), supports=()):
        state = state if state is not None else {
            "previous_target_close": 100, "structural_profit_targets": [103],
            "structural_profit_target_frontier": [level(101, "r1"), level(102), level(103)]}
        rows = rows if rows is not None else tuple(level(p) for p in range(101, 107))
        obs = replace(observation(close, bar_open=opened, position_quantity=50,
            structural_resistance_levels=rows, structural_support_levels=supports),
            observed_at=NOW+timedelta(seconds=seconds), source_timeframe="1s", evaluation_events=events)
        result = strategy.LongMomentumStrategyEngine(revision=42)._moving_target_result(
            assignment(strategy_revision=42, parameters=parameters(), status=strategy.AssignmentStatus.MANAGING),
            obs, parameters(), state, side="long", stop=95)
        return result, state

    def test_cross_two_uses_highest_crossing_and_third_above_it(self):
        result, state = self.run_close()
        intent = result.evaluation.intents[0]
        self.assertEqual(state["structural_profit_targets"], [105])
        self.assertEqual(intent.metadata["ratchet_acceptance"]["level"]["price"], 102)
        self.assertEqual(len(intent.metadata["ratchet_acceptance"]["crossed_levels"]), 2)
        self.assertIsNone(self.run_close(state=state)[0])

    def test_red_wick_and_intrabar_cannot_reconcile(self):
        for kwargs in ({"opened":103}, {"close":101}, {"events":("market_data_update",)}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.run_close(**kwargs)[0])
        self.assertIsNotNone(self.run_close(opened=102.5)[0])  # Doji is not red.

    def test_reordered_or_retired_old_first_does_not_hide_current_crossing(self):
        for rows in (tuple(level(p) for p in range(102,107)),
                     (level(104,"r1"),level(102),level(103),level(105),level(106))):
            result, state = self.run_close(rows=rows)
            self.assertEqual(state["structural_profit_targets"], [105])
            self.assertEqual(result.evaluation.intents[0].metadata["ratchet_acceptance"]["level"]["price"],102)

    def test_new_level_and_current_quality_are_used(self):
        rows = tuple(level(p) for p in (101,102,102.25,103,104,105,106))
        result, _ = self.run_close(rows=rows)
        self.assertEqual(result.evaluation.intents[0].metadata["ratchet_acceptance"]["level"]["price"],102.25)
        bad = tuple(level(p,quality=0) for p in (101,102)) + tuple(level(p) for p in (103,104,105))
        self.assertIsNone(self.run_close(rows=bad)[0])

    def test_role_flip_uses_current_price_and_future_rows_are_excluded(self):
        rows = tuple(level(p) for p in (102,103,104))
        result,_ = self.run_close(close=101.5, rows=rows, supports=(level(101,"r1"),))
        self.assertIsNotNone(result)
        self.assertIsNone(self.run_close(close=101.5,rows=rows,supports=(level(102,"r1"),))[0])
        future = tuple(level(p,confirmed_at=NOW+timedelta(seconds=1)) for p in (101,102)) + rows
        self.assertIsNone(self.run_close(close=101.5,rows=future)[0])

    def test_pending_retry_requires_fresh_non_red_current_confirmation(self):
        for close, opened, retired, expected in ((100.5,100,False,False),
                (101.5,102,False,False),(101.5,101,True,False),(101.5,101,False,True)):
            _, state = self.run_close(close=101.5,rows=(level(101,"r1"),level(102)))
            self.assertIn("pending_profit_target_advance",state)
            rows=tuple(level(p) for p in (102,103,104))
            if not retired:
                rows=(level(101,"r1"),)+rows
            result,_=self.run_close(close=close,opened=opened,rows=rows,state=state,seconds=1)
            self.assertEqual(result is not None,expected)
            if expected:
                accepted=result.evaluation.intents[0].metadata["ratchet_acceptance"]
                self.assertEqual(accepted["observed_at"],(NOW+timedelta(seconds=1)).isoformat())
                self.assertEqual(accepted["price"],close)

    def test_engine_emits_stop_and_target_on_green_but_only_stop_on_red(self):
        for opened, expected in ((100, ["replace_protective_stop", "replace_profit_target"]),
                                 (103, ["replace_protective_stop"])):
            state = {"entry_at": (NOW-timedelta(seconds=10)).isoformat(),
                     "entry_reference_price": 100, "active_stop": 90, "initial_stop": 90,
                     "previous_target_close": 100, "last_price": 100,
                     "structural_profit_targets": [103],
                     "structural_profit_target_frontier": [level(101)]}
            obs = replace(observation(102.5, bar_open=opened, position_quantity=50,
                structural_support_levels=(level(99),level(95)),
                structural_resistance_levels=tuple(level(p) for p in range(101,107))),
                evaluation_events=("bar_close",),source_timeframe="1s")
            result = strategy.LongMomentumStrategyEngine(revision=42).evaluate(
                assignment(strategy_revision=42,parameters=parameters(),
                    status=strategy.AssignmentStatus.MANAGING,state=state),obs)
            self.assertEqual([intent.action for intent in result.evaluation.intents],expected)

    def test_red_candle_still_executes_breached_stop(self):
        obs = replace(observation(94,bar_open=100,position_quantity=50),
            evaluation_events=("bar_close",),source_timeframe="1s")
        result = strategy.LongMomentumStrategyEngine(revision=42).evaluate(
            assignment(strategy_revision=42,parameters=parameters(),
                status=strategy.AssignmentStatus.MANAGING,
                state={"entry_at":NOW.isoformat(),"entry_reference_price":100,
                       "active_stop":95,"initial_stop":95}),obs)
        self.assertEqual(result.status,strategy.AssignmentStatus.EXIT_PENDING)
        self.assertEqual(result.evaluation.intents[0].action,"exit")

    def test_deferred_role_flip_keeps_identity_but_revalidates_current_support(self):
        _, state = self.run_close(close=101.5,rows=(level(101,"r1"),level(102)))
        self.assertIsNone(self.run_close(close=101.6,opened=101.5,state=state,seconds=1,
            rows=(level(102),),supports=(level(101,"r1"),))[0])
        result,_ = self.run_close(close=101.7,opened=101.6,state=state,seconds=2,
            rows=tuple(level(p) for p in (102,103,104)),supports=(level(101,"r1"),))
        self.assertEqual(result.evaluation.intents[0].profit_target_price,104)


if __name__ == "__main__":
    unittest.main()
