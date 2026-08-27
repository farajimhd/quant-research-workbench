from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from research.strategy_optimization.long_momentum_squeeze_v1.config import (
    SearchConfig,
    TrialSpec,
    generate_trials,
)
from research.strategy_optimization.long_momentum_squeeze_v1.mutations import (
    _configure_protection_profile,
    assert_hard_liquidity_contract,
)
from research.strategy_optimization.long_momentum_squeeze_v1.objectives import (
    TrialMetrics,
    count_causal_violations,
    realized_drawdown,
)
from research.strategy_optimization.long_momentum_squeeze_v1.optimize import (
    _eligible_validation_pairs,
)


class LongMomentumSqueezeOptimizerTests(unittest.TestCase):
    def test_search_is_deterministic_unique_and_includes_baseline(self) -> None:
        config = SearchConfig(tuning_trials=24)
        self.assertEqual(config.initial_cash, 10_000.0)

        first = generate_trials(config)
        second = generate_trials(config)

        self.assertEqual(first, second)
        self.assertEqual(first[0], TrialSpec())
        self.assertEqual(len({row.trial_id for row in first}), 24)
        self.assertTrue(all(row.profit_quantity_fraction == 1.0 for row in first))

    def test_realized_drawdown_includes_commissions_and_losing_round_trip(self) -> None:
        executions = [
            {"execution_id": "1", "trade_time": "1", "account": "A", "conid": 1,
             "side": "B", "size": 10, "price": 10, "commission": 1},
            {"execution_id": "2", "trade_time": "2", "account": "A", "conid": 1,
             "side": "S", "size": 10, "price": 12, "commission": 1},
            {"execution_id": "3", "trade_time": "3", "account": "A", "conid": 1,
             "side": "B", "size": 10, "price": 12, "commission": 1},
            {"execution_id": "4", "trade_time": "4", "account": "A", "conid": 1,
             "side": "S", "size": 10, "price": 11, "commission": 1},
        ]

        self.assertEqual(realized_drawdown(executions), 12.0)

    def test_objective_disqualifies_no_trade_and_liquidity_violation(self) -> None:
        base = dict(
            net_pnl=100.0,
            realized_pnl=100.0,
            commissions=2.0,
            maximum_realized_drawdown=10.0,
            execution_count=2,
            entry_intent_count=1,
            exit_intent_count=1,
            reentry_count=0,
            profit_take_count=0,
            liquidity_violations=0,
            causal_violations=0,
            squeeze_occurrence_count=5,
            watchlist_transition_count=2,
            final_position_count=0,
            final_absolute_position_quantity=0.0,
            final_open_order_count=0,
        )
        self.assertTrue(TrialMetrics(**base).admissible)
        self.assertFalse(TrialMetrics(**{**base, "execution_count": 0, "entry_intent_count": 0}).admissible)
        self.assertFalse(TrialMetrics(**{**base, "liquidity_violations": 1}).admissible)
        self.assertFalse(
            TrialMetrics(
                **{
                    **base,
                    "final_position_count": 1,
                    "final_absolute_position_quantity": 25.0,
                }
            ).admissible
        )
        self.assertFalse(
            TrialMetrics(**{**base, "final_open_order_count": 1}).admissible
        )

    def test_validation_eligibility_ignores_failed_rows_without_metrics(self) -> None:
        trial = {"trial_id": "candidate"}
        validation = [
            {
                "trial": trial,
                "simulation_profile": "baseline",
                "status": "completed",
                "metrics": {"admissible": True},
            },
            {
                "trial": trial,
                "simulation_profile": "stress",
                "status": "failed",
                "error": "execution-path failure",
            },
        ]

        self.assertEqual(_eligible_validation_pairs(validation), [])

    def test_causal_audit_uses_durable_journal_sequence(self) -> None:
        early = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
        late = datetime(2026, 8, 21, 8, 1, tzinfo=timezone.utc)
        records = [
            SimpleNamespace(sequence=2, event_time=late, category="strategy", entity_id="A", payload={}),
            SimpleNamespace(sequence=1, event_time=early, category="strategy", entity_id="A", payload={}),
        ]

        self.assertEqual(count_causal_violations(records), 0)

    def test_hard_liquidity_contract_cannot_be_weakened(self) -> None:
        conditions = [
            {"condition_id": "squeeze-session-dollar-volume", "value": 500_000.0},
            {"condition_id": "squeeze-trade-rate", "value": 1.0},
            {"condition_id": "squeeze-relative-liquidity", "value": 50.0},
            {"condition_id": "squeeze-volume-attraction", "value": 1.5},
            {"condition_id": "squeeze-spread-quality", "value": 50.0},
        ]
        configuration = {
            "configuration_model": {"market_discovery": {
                "watchlists": [{
                    "watchlist_id": "squeeze-tradable-candidates",
                    "inclusion_rule_sets": [
                        "strategy-squeeze-volume-spread-quality",
                        "watchlist-small-caps",
                    ],
                }],
                "rule_sets": [{
                    "rule_set_id": "watchlist-small-caps",
                    "conditions": [
                        {"condition_id": "small-cap-positive", "value": 0},
                        {"condition_id": "small-cap-maximum", "value": 2_000_000_000},
                    ],
                }],
            }},
            "payload": {"strategy": {"parameters": {"entry_rules": {"confirmation": {
                "rule_sets": [{
                    "rule_set_id": "strategy-squeeze-volume-spread-quality",
                    "conditions": conditions,
                }]
            }}}}}
        }
        assert_hard_liquidity_contract(configuration)
        conditions[0]["value"] = 1.0
        with self.assertRaisesRegex(ValueError, "weakened"):
            assert_hard_liquidity_contract(configuration)

    def test_trial_stop_method_changes_executable_protection_profile(self) -> None:
        for method, expected_type, expects_anchor in (
            ("structure", "swing_anchored", True),
            ("volatility", "volatility", False),
            ("hybrid", "hybrid", True),
        ):
            with self.subTest(method=method):
                profile = {
                    "profile_id": "hybrid-single",
                    "revision": 1,
                    "slices": [{"stop": {
                        "rule_type": "hybrid",
                        "anchor_source": "strategy_swing",
                        "anchor_ordinal": "most_recent",
                        "structural_timeframe": "strategy",
                    }}],
                }
                trial = TrialSpec(
                    stop_method=method,
                    stop_volatility_multiple=1.5,
                )

                _configure_protection_profile(profile, trial)

                stop = profile["slices"][0]["stop"]
                self.assertEqual(stop["rule_type"], expected_type)
                self.assertEqual(stop["volatility_multiple"], 1.5)
                self.assertEqual("anchor_source" in stop, expects_anchor)


if __name__ == "__main__":
    unittest.main()
