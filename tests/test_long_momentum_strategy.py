from __future__ import annotations

import tempfile
import unittest
import os
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import (
    AssignedLongMomentumStrategy,
    AssignmentStatus,
    LongMomentumStrategyEngine,
    STRATEGY_ID,
    STRATEGY_REVISION,
    StrategyAssignment,
    StrategyObservation,
    StrategyPermissions,
    default_long_momentum_parameters,
    evaluate_entry_decision_rules,
    long_momentum_strategy_definition,
    strategy_input_catalog,
    strategy_rule_timeframes,
)
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner, RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.backend import trading_runtime_service


NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def assignment(
    *,
    parameters: dict | None = None,
    status: AssignmentStatus = AssignmentStatus.WATCHING,
    permissions: StrategyPermissions | None = None,
    state: dict | None = None,
) -> StrategyAssignment:
    return StrategyAssignment(
        assignment_id="assignment-1",
        strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION,
        account_id="DU123",
        ticker="AAPL",
        conid=265598,
        status=status,
        permissions=permissions or StrategyPermissions(enter=True, add=True, reenter=True),
        parameters=parameters or default_long_momentum_parameters(),
        state=state or {},
        created_at=NOW,
        updated_at=NOW,
    )


def confirmed_observation(**overrides) -> StrategyObservation:
    payload = {
        "ticker": "AAPL",
        "observed_at": NOW,
        "price": 101.0,
        "bid": 100.99,
        "ask": 101.01,
        "previous_close": 100.0,
        "previous_high": 100.5,
        "swing_high": 100.5,
        "swing_low": 99.5,
        "vwap": 100.2,
        "vwap_slope_bps_per_second": 1.0,
        "macd_line": 0.4,
        "macd_signal": 0.2,
        "macd_histogram": 0.2,
        "qmd_score": 0.6,
        "qmd_confidence": 0.8,
        "qmd_bias": "bullish",
        "volatility": 0.4,
        "upper_luld_price": 110.0,
    }
    payload.update(overrides)
    return StrategyObservation(**payload)


class LongMomentumStrategyTests(unittest.TestCase):
    def test_entry_requires_macd_line_and_signal_above_zero(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(
                macd_line=-0.10,
                macd_signal=-0.20,
                macd_histogram=0.10,
            ),
        )

        self.assertEqual(result.evaluation.signals[0].action, "wait")
        self.assertEqual(result.evaluation.signals[0].reason, "entry_confirmation_incomplete")
        failures = result.evaluation.signals[0].metadata["reason_detail"]
        self.assertIn("indicator.macd.line (5s) is -0.1; requires greater than 0", failures)
        self.assertIn("indicator.macd.signal (5s) is -0.2; requires greater than 0", failures)

    def test_entry_uses_unified_support_and_builds_causal_five_target_ladder(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(
                price=3.55,
                bid=3.54,
                ask=3.55,
                previous_close=3.20,
                previous_high=3.50,
                swing_high=3.50,
                swing_low=3.48,
                structural_support_lower=3.35,
                structural_support_strength=0.9,
                structural_support_confidence=0.9,
                structural_resistance_lower=4.27,
                structural_resistance_strength=0.9,
                structural_resistance_confidence=0.9,
                vwap=3.45,
                volatility=0.05,
                upper_luld_price=None,
            ),
        )

        signal = result.evaluation.signals[0]
        self.assertEqual(signal.action, "enter_long")
        self.assertAlmostEqual(signal.invalidation_price, 3.3473, places=4)
        self.assertEqual(len(signal.metadata["profit_targets"]), 5)
        self.assertIn(4.27, signal.metadata["profit_targets"])
        self.assertEqual(result.state["structural_profit_targets"], signal.metadata["profit_targets"])

    def test_entry_prioritizes_five_causal_level_book_zones_before_fibonacci_fallbacks(self) -> None:
        levels = tuple(
            {
                "price": price,
                "lower": lower,
                "strength": 0.9,
                "confidence": 0.8,
            }
            for price, lower in (
                (3.69, 3.68),
                (3.73, 3.72),
                (3.85, 3.83),
                (3.91, 3.89),
                (3.96, 3.94),
            )
        )
        result = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(
                price=3.67,
                bid=3.66,
                ask=3.67,
                previous_close=3.20,
                previous_high=3.62,
                swing_high=3.62,
                swing_low=3.60,
                structural_support_lower=3.60,
                structural_support_strength=1.0,
                structural_support_confidence=0.77,
                structural_resistance_levels=levels,
                vwap=3.47,
                volatility=0.025,
                upper_luld_price=None,
            ),
        )

        self.assertEqual(
            result.evaluation.signals[0].metadata["profit_targets"],
            [3.68, 3.72, 3.83, 3.89, 3.94],
        )

    def test_background_evaluation_emits_autonomous_causal_lineage(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(source_signal_ids=("qmd-signal-41",)),
        )

        signal = result.evaluation.signals[0]
        self.assertEqual(signal.metadata["correlation_id"], "run:assignment-1")
        self.assertEqual(signal.metadata["causation_id"], "event:qmd-signal-41")
        self.assertEqual(result.evaluation_payload["correlation_id"], "run:assignment-1")
        self.assertEqual(result.evaluation_payload["causation_id"], "event:qmd-signal-41")
        if result.evaluation.intents:
            intent_metadata = result.evaluation.intents[0].metadata
            self.assertEqual(intent_metadata["correlation_id"], "run:assignment-1")
            self.assertEqual(intent_metadata["causation_id"], "event:qmd-signal-41")

    def test_definition_is_long_only_timeframe_aware_and_searchable(self) -> None:
        definition = long_momentum_strategy_definition()
        config = definition["config"]
        self.assertEqual(config["direction"], "single_side")
        self.assertEqual(config["supported_sides"], ["long", "short"])
        self.assertEqual(
            config["parameters"]["entry_rules"]["trigger"]["operator"],
            "any",
        )
        self.assertEqual(config["taxonomy"]["indicators"][0]["timeframe"], "100ms")
        self.assertNotIn("evaluation_triggers", config["taxonomy"])
        self.assertIn("signal.vwap_transition.score", {
            row["source_id"] for row in config["input_catalog"]
        })
        self.assertIn("signal.sec_filing.score", {
            row["source_id"] for row in config["input_catalog"]
        })
        catalog = {row["source_id"]: row for row in strategy_input_catalog()}
        self.assertEqual(catalog["signal.news_labeled"]["runtime_field"], "news_labeled")
        self.assertEqual(catalog["signal.news_labeled"]["value_type"], "boolean")
        self.assertEqual(catalog["signal.sec_labeled"]["runtime_field"], "sec_labeled")
        self.assertFalse(confirmed_observation().news_labeled)
        self.assertFalse(confirmed_observation().sec_labeled)

    def test_flat_campaign_evaluates_only_updates_referenced_by_entry_rules(self) -> None:
        parameters = default_long_momentum_parameters()
        engine = LongMomentumStrategyEngine()

        blocked = engine.evaluate(
            assignment(parameters=parameters),
            confirmed_observation(
                changed_source_ids=("signal.sec_filing.score",),
                evaluation_events=("signal_event",),
            ),
        )
        self.assertEqual(
            blocked.evaluation.signals[0].reason,
            "no_active_rule_source_updated",
        )

        admitted = engine.evaluate(
            assignment(parameters=parameters),
            confirmed_observation(
                changed_source_ids=("signal.company_news.score",),
                evaluation_events=("signal_event",),
                news_score=0.9,
            ),
        )
        self.assertNotEqual(
            admitted.evaluation.signals[0].reason,
            "no_active_rule_source_updated",
        )

    def test_source_timeframe_must_match_rule_dependency(self) -> None:
        blocked = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(
                changed_source_ids=("indicator.macd.line@1s",),
            ),
        )
        admitted = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(
                changed_source_ids=("indicator.macd.line@5s",),
            ),
        )

        self.assertEqual(
            blocked.evaluation.signals[0].reason,
            "no_active_rule_source_updated",
        )
        self.assertNotEqual(
            admitted.evaluation.signals[0].reason,
            "no_active_rule_source_updated",
        )

    def test_replay_timeframes_include_entry_add_reentry_and_exit_rules(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {
            "initial_entry": {
                "add_steps": [{
                    "enabled": True,
                    "rules": {"groups": [{
                        "enabled": True,
                        "conditions": [{"left_timeframe": "10s", "right_timeframe": ""}],
                    }]},
                }],
            },
            "reentry": {
                "rules": {"trigger": {"groups": [{
                    "enabled": True,
                    "conditions": [{"left_timeframe": "30s", "right_timeframe": ""}],
                }]}},
            },
            "exit": {
                "rule_sets": [{
                    "enabled": True,
                    "rules": {"groups": [{
                        "enabled": True,
                        "conditions": [{"left_timeframe": "1m", "right_timeframe": ""}],
                    }]},
                }],
            },
        }

        timeframes = strategy_rule_timeframes(parameters)

        self.assertTrue({"10s", "30s", "1m"} <= timeframes)

    def test_modern_rule_sets_and_structured_intervals_drive_dependencies(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["entry_rules"] = {
            "trigger": {
                "rule_sets": [{
                    "rule_set_id": "modern-price-volume",
                    "enabled": True,
                    "operator": "all",
                    "conditions": [{
                        "condition_id": "modern-price-volume-condition",
                        "left_source_id": "signal.price_volume_expansion.score",
                        "left_interval": {"value": 10, "unit": "seconds"},
                        "comparator": "greater_or_equal",
                        "value": 0.5,
                    }],
                }],
            },
            "confirmation": {
                "rule_sets": [{
                    "rule_set_id": "modern-macd",
                    "enabled": True,
                    "operator": "all",
                    "conditions": [{
                        "condition_id": "modern-macd-condition",
                        "left_source_id": "indicator.macd.line",
                        "left_interval": {"value": 30, "unit": "seconds"},
                        "comparator": "greater_than",
                        "value": 0,
                    }],
                }],
            },
            "veto": {"rule_sets": []},
        }

        self.assertTrue({"10s", "30s"} <= strategy_rule_timeframes(parameters))
        blocked = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(changed_source_ids=("indicator.macd.line@5s",)),
        )
        admitted = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(changed_source_ids=("indicator.macd.line@30s",)),
        )
        self.assertEqual(blocked.evaluation.signals[0].reason, "no_active_rule_source_updated")
        self.assertNotEqual(admitted.evaluation.signals[0].reason, "no_active_rule_source_updated")

    def test_order_and_position_events_bypass_source_relevance_routing(self) -> None:
        parameters = default_long_momentum_parameters()

        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(
                changed_source_ids=("unrelated.source",),
                evaluation_events=("order_event",),
            ),
        )

        self.assertNotEqual(
            result.evaluation.signals[0].reason,
            "no_active_rule_source_updated",
        )

    def test_entry_rules_support_or_logic_across_explicit_sources(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["entry_rules"]["trigger"]["groups"] = [
            next(
                group for group in parameters["entry_rules"]["trigger"]["groups"]
                if group["group_id"] == "break-vwap"
            )
        ]
        configured = assignment()
        configured = StrategyAssignment(
            **{
                **configured.payload(),
                "status": AssignmentStatus.WATCHING,
                "permissions": configured.permissions,
                "parameters": parameters,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        result = LongMomentumStrategyEngine().evaluate(
            configured,
            confirmed_observation(price=100.3, swing_high=101.0),
        )
        self.assertEqual(result.evaluation.signals[0].action, "enter_long")
        self.assertIn(
            "break-vwap",
            result.evaluation.signals[0].metadata["entry_rules"]["trigger"]["matched_groups"],
        )

    def test_rule_sources_resolve_their_configured_timeframes(self) -> None:
        rules = default_long_momentum_parameters()["entry_rules"]
        observation = confirmed_observation(
            source_timeframe="1s",
            source_values={
                "indicator.flow_structure.score@100ms": {"value": 0.7},
                "indicator.flow_structure.confidence@100ms": {"value": 0.8},
                "market.last_price@5s": {"value": 101.0},
                "indicator.vwap.value@5s": {"value": 100.2},
                "indicator.vwap.slope@5s": {"value": 0.5},
                "indicator.macd.line@5s": {"value": 0.4},
                "indicator.macd.signal@5s": {"value": 0.2},
                "indicator.macd.histogram@5s": {"value": 0.2},
            },
        )
        result = evaluate_entry_decision_rules(rules, observation)
        self.assertTrue(result["confirmation"]["passed"])
        self.assertEqual(
            set(result["confirmation"]["matched_groups"]),
            {"qmd-alignment", "vwap-confirmation", "macd-confirmation"},
        )

    def test_required_score_is_local_to_each_rule_set(self) -> None:
        rules = default_long_momentum_parameters()["entry_rules"]
        qmd_group = rules["confirmation"]["groups"][0]
        qmd_group["operator"] = "score"
        qmd_group["required_score"] = 0.5
        partial = evaluate_entry_decision_rules(
            rules, confirmed_observation(qmd_confidence=0.0)
        )
        self.assertTrue(partial["confirmation"]["groups"]["qmd-alignment"])
        self.assertEqual(
            partial["confirmation"]["group_scores"]["qmd-alignment"], 0.5
        )

        qmd_group["required_score"] = 1.0
        strict = evaluate_entry_decision_rules(
            rules, confirmed_observation(qmd_confidence=0.0)
        )
        self.assertFalse(strict["confirmation"]["groups"]["qmd-alignment"])
        self.assertFalse(strict["confirmation"]["passed"])

    def test_nested_rule_expression_supports_mixed_and_or_logic(self) -> None:
        legacy = default_long_momentum_parameters()["entry_rules"]
        rules = deepcopy(legacy)
        confirmation_sets = []
        for group in rules["confirmation"].pop("groups"):
            confirmation_sets.append({
                **group,
                "rule_set_id": group.pop("group_id"),
            })
        rules["confirmation"] = {
            "rule_sets": confirmation_sets,
            "expression": {
                "kind": "operator",
                "operator": "and",
                "children": [
                    {"kind": "rule_set", "rule_set_id": confirmation_sets[0]["rule_set_id"]},
                    {
                        "kind": "operator",
                        "operator": "or",
                        "children": [
                            {"kind": "rule_set", "rule_set_id": confirmation_sets[1]["rule_set_id"]},
                            {"kind": "rule_set", "rule_set_id": confirmation_sets[2]["rule_set_id"]},
                        ],
                    },
                ],
            },
        }
        result = evaluate_entry_decision_rules(
            rules,
            confirmed_observation(macd_histogram=-0.2),
        )
        self.assertTrue(result["confirmation"]["groups"]["qmd-alignment"])
        self.assertTrue(result["confirmation"]["groups"]["vwap-confirmation"])
        self.assertFalse(result["confirmation"]["groups"]["macd-confirmation"])
        self.assertTrue(result["confirmation"]["passed"])

    def test_confirmed_swing_break_enters_with_semantic_protection(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(assignment(), confirmed_observation())
        self.assertEqual(result.status, AssignmentStatus.ENTRY_PENDING)
        self.assertEqual(result.evaluation.signals[0].action, "enter_long")
        intent = result.evaluation.intents[0]
        self.assertLess(intent.invalidation_price or 0, intent.reference_price)
        self.assertGreater(intent.profit_target_price or 0, intent.reference_price)
        self.assertGreater(intent.trailing_amount or 0, 0)

    def test_materialized_swing_rule_waits_below_then_enters_above_threshold(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["entry_rules"] = {
            "trigger": {
                "expression": {
                    "kind": "rule_set",
                    "rule_set_id": "swing-break",
                },
                "rule_sets": [{
                    "rule_set_id": "swing-break",
                    "enabled": True,
                    "operator": "all",
                    "conditions": [{
                        "condition_id": "price-over-swing",
                        "left_source_id": "market.last_price",
                        "left_field_ref": "market.last_price@1s",
                        "left_interval": "1s",
                        "comparator": "above_by_bps",
                        "right_source_id": "indicator.structure.swing_high",
                        "right_field_ref": "indicator.structure.swing_high@1s",
                        "right_interval": "1s",
                        "value": 5.0,
                        "enabled": True,
                    }],
                }],
            },
            "confirmation": {
                "expression": {
                    "kind": "rule_set",
                    "rule_set_id": "liquidity",
                },
                "rule_sets": [{
                    "rule_set_id": "liquidity",
                    "enabled": True,
                    "operator": "all",
                    "conditions": [{
                        "condition_id": "dollar-volume",
                        "left_source_id": "market.session_dollar_volume",
                        "left_field_ref": "market.session_dollar_volume",
                        "comparator": "greater_or_equal",
                        "value": 100_000.0,
                        "enabled": True,
                    }],
                }],
            },
            "veto": {"expression": {}, "rule_sets": []},
        }
        below = confirmed_observation(
            price=13.00,
            swing_high=13.03,
            source_values={
                "market.last_price@1s": {"value": 13.00},
                "indicator.structure.swing_high@1s": {"value": 13.03},
                "market.session_dollar_volume": {"value": 500_000.0},
            },
        )
        waiting = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters), below
        )
        wait_signal = waiting.evaluation.signals[0]
        self.assertEqual(wait_signal.reason, "waiting_for_swing_high_cross")
        self.assertAlmostEqual(
            wait_signal.metadata["trigger_threshold_price"],
            13.03 * 1.0005,
        )

        above = replace(
            below,
            observed_at=NOW + timedelta(seconds=1),
            price=13.05,
            source_values={
                "market.last_price@1s": {"value": 13.05},
                "indicator.structure.swing_high@1s": {"value": 13.03},
                "market.session_dollar_volume": {"value": 500_000.0},
            },
        )
        entered = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters), above
        )
        self.assertEqual(entered.evaluation.signals[0].action, "enter_long")
        self.assertTrue(
            entered.evaluation.signals[0].metadata[
                "trigger_reference_name"
            ].startswith("indicator.structure.swing_high"),
        )

    def test_configured_execution_and_multi_swing_protection_reach_entry_intent(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {
            "initial_entry": {
                "capital_request": {
                    "mode": "mandate_fraction",
                    "value": 0.2,
                    "priority": 70,
                    "allow_replacement": True,
                },
                "order_intent": {
                    "execution_policy": "fast-entry@2",
                    "protection_profile": "layered-swings@3",
                    "partial_fill_policy": "complete_remainder",
                    "deadline_ms": 180,
                },
                "add_steps": [],
            }
        }
        parameters["execution_policy_catalog"] = {
            "fast-entry@2": {
                "policy_id": "fast-entry",
                "revision": 2,
                "name": "adaptive_urgent",
                "quote_source": "qmd",
                "partial_fill_policy": "accept_partial",
                "envelope": {
                    "deadline_ms": 500,
                    "maximum_reprices": 7,
                    "minimum_reprice_interval_ms": 25,
                },
            }
        }
        parameters["protection_profile_catalog"] = {
            "layered-swings@3": {
                "profile_id": "layered-swings",
                "revision": 3,
                "add_policy": "tighten_only",
                "profit_pocket_transition": "start_swing_trail",
                "mandatory_catastrophic_backstop": True,
                "emergency_repair_deadline_ms": 250,
                "slices": [
                    {
                        "slice_id": "near",
                        "quantity_fraction": 0.5,
                        "use_strategy_profit_target": True,
                        "stop": {
                            "rule_type": "swing_anchored",
                            "order_type": "STP",
                            "anchor_source": "strategy_swing",
                            "anchor_ordinal": "most_recent",
                            "buffer_bps": 5,
                        },
                        "trailing": {"rule_type": "none"},
                    },
                    {
                        "slice_id": "deep",
                        "quantity_fraction": 0.5,
                        "stop": {
                            "rule_type": "swing_anchored",
                            "order_type": "STP",
                            "anchor_source": "strategy_swing",
                            "anchor_ordinal": "second_recent",
                            "buffer_bps": 5,
                        },
                        "trailing": {
                            "rule_type": "swing_trail",
                            "structural_timeframe": "strategy",
                        },
                    },
                ],
            }
        }
        prior = {
            "structural_anchors": {
                "long": [{
                    "observation_id": "prior-swing",
                    "price": 98.8,
                    "confirmed_at": (NOW - timedelta(minutes=2)).isoformat(),
                    "timeframe": "strategy",
                }]
            }
        }

        blocked = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(),
        )
        self.assertEqual(blocked.evaluation.signals[0].action, "wait")
        self.assertEqual(
            blocked.evaluation.signals[0].reason,
            "protection_anchor_unavailable",
        )
        self.assertFalse(blocked.evaluation.intents)

        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters, state=prior),
            confirmed_observation(),
        )
        intent = result.evaluation.intents[0]

        self.assertEqual(intent.execution_policy.identity, "fast-entry@2")
        self.assertEqual(intent.execution_policy.envelope.deadline_ms, 180)
        self.assertEqual(intent.execution_policy.envelope.maximum_reprices, 7)
        self.assertEqual(intent.execution_policy.partial_fill_policy.value, "complete_remainder")
        self.assertEqual(intent.protection_profile.identity, "layered-swings@3")
        self.assertEqual(
            [item.stop.anchor.price for item in intent.protection_profile.slices],
            [99.5, 98.8],
        )
        self.assertEqual(intent.protection_profile.add_policy.value, "tighten_only")
        self.assertEqual(
            intent.protection_profile.profit_pocket_transition.value,
            "start_swing_trail",
        )

    def test_hybrid_protection_carries_volatility_into_order_planning(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"initial_entry": {
            "capital_request": {
                "mode": "mandate_fraction",
                "value": 0.1,
                "allow_replacement": False,
            },
            "order_intent": {
                "execution_policy": "adaptive_urgent",
                "protection_profile": "hybrid-single",
                "partial_fill_policy": "complete_remainder",
                "deadline_ms": 500,
            },
            "add_steps": [],
        }}
        parameters["protection_profile_catalog"] = {"hybrid-single": {
            "profile_id": "hybrid-single",
            "revision": 1,
            "slices": [{
                "slice_id": "position",
                "quantity_fraction": 1.0,
                "stop": {
                    "rule_type": "hybrid",
                    "order_type": "STP",
                    "anchor_source": "strategy_swing",
                    "anchor_ordinal": "most_recent",
                    "buffer_bps": 8.0,
                    "volatility_multiple": 1.25,
                },
                "trailing": {
                    "rule_type": "volatility_trail",
                    "volatility_multiple": 1.0,
                    "activation_gain_percent": 0.5,
                },
            }],
        }}

        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(),
        )
        intent = replace(result.evaluation.intents[0], quantity=100)
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD"),
            intent=intent,
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        self.assertEqual(intent.metadata["volatility"], 0.4)
        self.assertEqual([order.orderType for order in plan.orders], ["LMT", "LMT", "STP"])

    def test_hybrid_protection_uses_volatility_when_no_swing_exists(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"initial_entry": {
            "capital_request": {"mode": "mandate_fraction", "value": 0.1},
            "order_intent": {
                "execution_policy": "adaptive_urgent",
                "protection_profile": "hybrid-single",
                "partial_fill_policy": "complete_remainder",
                "deadline_ms": 500,
            },
            "add_steps": [],
        }}
        parameters["protection_profile_catalog"] = {"hybrid-single": {
            "profile_id": "hybrid-single",
            "revision": 1,
            "slices": [{
                "slice_id": "position",
                "quantity_fraction": 1.0,
                "stop": {
                    "rule_type": "hybrid",
                    "order_type": "STP",
                    "anchor_source": "strategy_swing",
                    "anchor_ordinal": "most_recent",
                    "volatility_multiple": 1.25,
                },
                "trailing": {"rule_type": "none"},
            }],
        }}
        observation = confirmed_observation(swing_low=None)

        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters), observation
        )

        self.assertEqual(result.evaluation.signals[0].action, "enter_long")
        stop = result.evaluation.intents[0].protection_profile.slices[0].stop
        self.assertIsNone(stop.anchor)
        self.assertEqual(stop.rule_type.value, "hybrid")

    def test_short_profile_emits_relative_sell_intent_with_phase_order_policy(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["strategy_behavior"] = {
            "side": "short",
            "eligible_sessions": ["premarket", "regular"],
        }
        parameters["phase_policy"] = {
            "initial_entry": {
                "capital_request": {
                    "mode": "mandate_fraction",
                    "value": 0.2,
                    "priority": 70,
                    "allow_replacement": True,
                },
                "order_intent": {
                    "execution_policy": "adaptive_urgent",
                    "partial_fill_policy": "complete_remainder",
                    "deadline_ms": 500,
                },
                "add_steps": [],
            }
        }

        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(),
        )

        self.assertEqual(result.evaluation.signals[0].action, "enter_short")
        intent = result.evaluation.intents[0]
        self.assertEqual(intent.quantity, 0)
        self.assertEqual(intent.capital_request.mode, "mandate_fraction")
        self.assertEqual(intent.capital_request.value, 0.2)
        self.assertIsNone(intent.capital_request.maximum_quantity)
        self.assertEqual(intent.execution_policy.name.value, "adaptive_urgent")
        self.assertEqual(intent.time_in_force, "")
        self.assertFalse(intent.outside_rth)
        self.assertEqual(intent.metadata["session_routing"], "smart")
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract(
                "ibkr:265598", 265598, "AAPL", "STK", "USD"
            ),
            intent=replace(intent, quantity=100),
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        self.assertTrue(all(order.tif == "DAY" for order in plan.orders))
        self.assertTrue(all(order.outsideRTH for order in plan.orders))
        self.assertGreater(intent.invalidation_price or 0, intent.reference_price)

    def test_premarket_entry_intent_requests_outside_rth_routing(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(
            assignment(),
            confirmed_observation(
                observed_at=datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
            ),
        )

        self.assertEqual(result.evaluation.signals[0].action, "enter_long")
        self.assertTrue(result.evaluation.intents[0].outside_rth)

    def test_campaign_initial_entry_authority_requires_operator_confirmation(self) -> None:
        waiting = assignment(
            state={
                "campaign_policy": {
                    "initial_entry_authority": "confirm",
                    "reentry_authority": "confirm",
                    "exit_authority": "automatic",
                }
            }
        )
        result = LongMomentumStrategyEngine().evaluate(
            waiting, confirmed_observation()
        )
        self.assertEqual(
            result.evaluation.signals[0].reason,
            "initial_entry_confirmation_required",
        )
        confirmed = LongMomentumStrategyEngine().evaluate(
            waiting,
            confirmed_observation(manual_entry_request=True),
        )
        self.assertEqual(confirmed.evaluation.signals[0].action, "enter_long")

    def test_confirm_authority_proposes_only_after_strategy_rules_pass(self) -> None:
        waiting = assignment(state={"campaign_policy": {"initial_entry_authority": "confirm"}})
        result = LongMomentumStrategyEngine().evaluate(
            waiting,
            confirmed_observation(price=99.0, swing_high=101.0, vwap=100.0),
        )
        self.assertNotEqual(
            result.evaluation.signals[0].reason,
            "initial_entry_confirmation_required",
        )

    def test_veto_blocks_entry_even_when_breakout_is_confirmed(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(
            assignment(), confirmed_observation(liquidity_dislocation_score=0.9)
        )
        self.assertEqual(result.evaluation.signals[0].action, "wait")
        self.assertEqual(result.evaluation.signals[0].reason, "entry_vetoed")
        self.assertFalse(result.evaluation.intents)

    def test_manual_position_is_managed_without_entry_permission(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            permissions=StrategyPermissions(enter=False, add=False, reduce=True, exit=True),
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(price=100.0, position_quantity=100, average_price=101.0),
        )
        self.assertEqual(result.evaluation.signals[0].action, "exit")
        self.assertEqual(result.evaluation.signals[0].reason, "failed_breakout")

    def test_configured_exit_route_priority_selects_the_authoritative_reason(self) -> None:
        parameters = default_long_momentum_parameters()
        for route in parameters["exit_routes"]:
            if route["route_id"] == "failed-breakout":
                route["priority"] = 60
            if route["route_id"] == "bearish-momentum":
                route["priority"] = 90
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 90.0,
                "initial_stop": 90.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )
        managed = StrategyAssignment(
            **{
                **managed.payload(),
                "status": AssignmentStatus.MANAGING,
                "permissions": managed.permissions,
                "parameters": parameters,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=100.0,
                position_quantity=100,
                average_price=101.0,
                qmd_score=-0.8,
                qmd_confidence=0.9,
                macd_line=-0.4,
                macd_signal=-0.2,
                macd_histogram=-0.2,
            ),
        )
        signal = result.evaluation.signals[0]
        self.assertEqual(signal.reason, "bearish_qmd_macd")
        self.assertEqual(signal.metadata["exit_route_id"], "bearish-momentum")

    def test_below_entry_loss_guard_exits_on_first_causal_trigger(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
        parameters["profit_pocket"]["enabled"] = False
        parameters["protection"]["trailing"]["enabled"] = False
        parameters["momentum_management"]["downside_loss_guard"]["enabled"] = True
        managed = assignment(
            parameters=parameters,
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 95.0,
                "initial_stop": 95.0,
                "breakout_level": 90.0,
                "entry_at": (NOW - timedelta(seconds=10)).isoformat(),
                "entry_reference_price": 101.0,
                "high_water_price": 102.0,
            },
        )
        cases = (
            (
                "downside_bearish_choch",
                dict(structure_event="choch", structure_direction="bearish", vwap=99.0),
            ),
            (
                "downside_macd_closed",
                dict(macd_line=-0.2, macd_signal=-0.1, macd_histogram=-0.1, vwap=99.0),
            ),
            (
                "downside_vwap_lost",
                dict(vwap=100.5),
            ),
        )
        for reason, overrides in cases:
            with self.subTest(reason=reason):
                result = LongMomentumStrategyEngine().evaluate(
                    managed,
                    confirmed_observation(
                        price=100.0,
                        average_price=101.0,
                        position_quantity=100,
                        source_timeframe="1s",
                        **overrides,
                    ),
                )
                self.assertEqual(result.evaluation.signals[0].action, "exit")
                self.assertEqual(result.evaluation.signals[0].reason, reason)

    def test_downside_loss_guard_does_not_apply_above_entry(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
        parameters["profit_pocket"]["enabled"] = False
        parameters["protection"]["trailing"]["enabled"] = False
        parameters["momentum_management"]["downside_loss_guard"]["enabled"] = True
        managed = assignment(
            parameters=parameters,
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 95.0,
                "initial_stop": 95.0,
                "breakout_level": 90.0,
                "entry_at": (NOW - timedelta(seconds=10)).isoformat(),
                "entry_reference_price": 101.0,
                "high_water_price": 102.0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=101.5,
                average_price=101.0,
                position_quantity=100,
                source_timeframe="1s",
                structure_event="choch",
                structure_direction="bearish",
                macd_line=-0.2,
                macd_signal=-0.1,
                macd_histogram=-0.1,
                vwap=102.0,
            ),
        )
        self.assertEqual(result.evaluation.signals[0].action, "hold")

    def test_protective_exit_cannot_be_disabled_by_campaign_permissions(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            permissions=StrategyPermissions(
                enter=False,
                add=False,
                reduce=False,
                exit=False,
                reenter=False,
            ),
            state={
                "active_stop": 100.0,
                "initial_stop": 100.0,
                "breakout_level": 101.0,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
                "campaign_policy": {"exit_authority": "manual"},
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=99.0,
                position_quantity=100,
                average_price=101.0,
            ),
        )
        self.assertEqual(result.evaluation.signals[0].reason, "protective_stop")
        self.assertEqual(result.evaluation.signals[0].action, "exit")

    def test_manual_initial_entry_mode_skips_entry_rule_evaluation(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"initial_entry": {
            "mode": "manual",
            "capital_request": {"mode": "fixed_quantity", "value": 100, "allow_replacement": False},
            "order_intent": {"execution_policy": "adaptive_urgent", "protection_profile": "hybrid-single", "partial_fill_policy": "complete_remainder", "deadline_ms": 750},
        }}

        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(),
        )

        self.assertEqual(result.evaluation.signals[0].action, "wait")
        self.assertEqual(result.evaluation.signals[0].reason, "initial_entry_manual_mode")
        self.assertFalse(result.evaluation.intents)

    def test_manual_reentry_mode_skips_reentry_rule_evaluation(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"reentry": {
            "mode": "manual",
            "capital_request": {"mode": "fixed_quantity", "value": 100, "allow_replacement": False},
            "order_intent": {"execution_policy": "adaptive_urgent", "protection_profile": "hybrid-single", "partial_fill_policy": "complete_remainder", "deadline_ms": 750},
        }}
        waiting = assignment(
            parameters=parameters,
            status=AssignmentStatus.REENTRY_COOLDOWN,
            state={"reentries": 1, "last_exit_at": (NOW - timedelta(seconds=5)).isoformat()},
        )

        result = LongMomentumStrategyEngine().evaluate(waiting, confirmed_observation())

        self.assertEqual(result.evaluation.signals[0].action, "wait")
        self.assertEqual(result.evaluation.signals[0].reason, "reentry_manual_mode")
        self.assertFalse(result.evaluation.intents)

    def test_manual_manage_and_exit_modes_preserve_protective_stop(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {
            "manage": {"mode": "manual"},
            "exit": {"mode": "manual"},
        }
        managed = assignment(
            parameters=parameters,
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 100.0,
                "initial_stop": 100.0,
                "breakout_level": 101.0,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )

        held = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(price=100.5, position_quantity=100, average_price=101.0),
        )
        protected = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(price=99.0, position_quantity=100, average_price=101.0),
        )

        self.assertEqual(held.evaluation.signals[0].reason, "position_management_manual_mode")
        self.assertEqual(held.evaluation.signals[0].action, "hold")
        self.assertEqual(held.state["active_stop"], 100.0)
        self.assertEqual(protected.evaluation.signals[0].reason, "protective_stop")
        self.assertEqual(protected.evaluation.signals[0].action, "exit")
        self.assertEqual(protected.status, AssignmentStatus.EXIT_PENDING)
        self.assertFalse(protected.evaluation.intents[0].metadata["buy_back"])

    def test_bullish_choch_adds_only_with_confirmation(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.2,
                "adds": 0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=101.3,
                position_quantity=100,
                average_price=101.0,
                structure_event="choch",
                structure_direction="bullish",
            ),
        )
        self.assertEqual(result.evaluation.signals[0].action, "add_long")
        self.assertEqual(result.evaluation.intents[0].quantity, 50)

    def test_profit_pocket_closes_the_campaign_episode_before_reentry(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 102.0,
                "last_acceleration": 0.3,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=102.0,
                position_quantity=100,
                average_price=101.0,
                acceleration=0.1,
            ),
        )
        self.assertEqual(result.evaluation.signals[0].action, "exit")
        self.assertEqual(result.evaluation.signals[0].reason, "profit_pocket")
        self.assertEqual(result.evaluation.intents[0].quantity, 100)
        self.assertEqual(result.status, AssignmentStatus.EXIT_PENDING)
        self.assertTrue(result.evaluation.intents[0].metadata["buy_back"])

        pending = assignment(
            status=AssignmentStatus.EXIT_PENDING,
            state=dict(result.state),
        )
        duplicate = LongMomentumStrategyEngine().evaluate(
            pending,
            confirmed_observation(
                price=101.8,
                position_quantity=40,
                average_price=101.0,
                acceleration=-0.2,
            ),
        )
        self.assertEqual(duplicate.evaluation.signals[0].action, "hold")
        self.assertEqual(duplicate.evaluation.signals[0].reason, "exit_fill_pending")
        self.assertFalse(duplicate.evaluation.intents)

    def test_reentry_cooldown_is_deterministic(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["reentry"]["cooldown_ms"] = 5000
        waiting = assignment(
            status=AssignmentStatus.REENTRY_COOLDOWN,
            state={"reentries": 1, "last_exit_at": NOW.isoformat()},
        )
        waiting = StrategyAssignment(**{**waiting.payload(), "status": AssignmentStatus.REENTRY_COOLDOWN, "permissions": waiting.permissions, "parameters": parameters, "created_at": NOW, "updated_at": NOW})
        result = LongMomentumStrategyEngine().evaluate(
            waiting, confirmed_observation(observed_at=NOW + timedelta(seconds=1))
        )
        self.assertEqual(result.evaluation.signals[0].reason, "reentry_cooldown")

    def test_reentry_requires_a_renewed_early_squeeze_after_exit(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["reentry"].update({
            "cooldown_ms": 5_000,
            "maximum_attempts": 1,
            "require_new_signal_stream_id": "price-squeeze-early",
        })
        parameters["phase_policy"] = {
            "reentry": {
                "mode": "automatic",
                "rules": deepcopy(parameters["entry_rules"]),
                "capital_request": {
                    "mode": "all_available",
                    "value": 1.0,
                    "allow_replacement": False,
                },
                "order_intent": {
                    "execution_policy": "adaptive_urgent",
                    "partial_fill_policy": "complete_remainder",
                    "deadline_ms": 750,
                },
            }
        }
        waiting = assignment(
            parameters=parameters,
            status=AssignmentStatus.REENTRY_COOLDOWN,
            state={"reentries": 1, "last_exit_at": NOW.isoformat()},
        )
        stale = LongMomentumStrategyEngine().evaluate(
            waiting,
            confirmed_observation(
                observed_at=NOW + timedelta(seconds=6),
                source_values={
                    "signal.activation.price-squeeze-early": {
                        "value": True,
                        "observed_at": (NOW - timedelta(seconds=1)).isoformat(),
                    }
                },
            ),
        )
        renewed = LongMomentumStrategyEngine().evaluate(
            waiting,
            confirmed_observation(
                observed_at=NOW + timedelta(seconds=6),
                source_values={
                    "signal.activation.price-squeeze-early": {
                        "value": True,
                        "observed_at": (NOW + timedelta(seconds=5)).isoformat(),
                    }
                },
            ),
        )

        self.assertEqual(stale.evaluation.signals[0].reason, "waiting_for_renewed_early_squeeze")
        self.assertIn("new Early Squeeze Move", stale.evaluation.signals[0].metadata["reason_detail"])
        self.assertEqual(renewed.evaluation.signals[0].action, "enter_long")

    def test_failure_to_extend_reduces_half_and_remains_fill_gated(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
        parameters["profit_pocket"]["enabled"] = False
        parameters["protection"]["trailing"]["enabled"] = False
        parameters["momentum_management"]["failure_to_extend"]["enabled"] = True
        managed = assignment(
            parameters=parameters,
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 95.0,
                "initial_stop": 95.0,
                "entry_at": (NOW - timedelta(seconds=10)).isoformat(),
                "entry_reference_price": 100.0,
                "high_water_price": 102.0,
                "last_extension_at": (NOW - timedelta(seconds=4)).isoformat(),
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                price=101.0,
                position_quantity=100,
                average_price=100.0,
                qmd_score=0.1,
                flow_price_divergence_score=0.6,
                source_timeframe="100ms",
            ),
        )

        self.assertEqual(result.evaluation.signals[0].action, "reduce_long")
        self.assertEqual(result.evaluation.signals[0].reason, "failure_to_extend_partial")
        self.assertEqual(result.evaluation.intents[0].quantity, 50)
        self.assertEqual(result.status, AssignmentStatus.EXIT_PENDING)
        self.assertIn("stopped extending", result.evaluation.signals[0].metadata["reason_detail"])

    def test_qmd_exhaustion_and_higher_low_loss_are_full_exit_routes(self) -> None:
        base = default_long_momentum_parameters()
        base["phase_policy"] = {"exit": {"mode": "automatic", "rule_sets": []}}
        base["profit_pocket"]["enabled"] = False
        base["protection"]["trailing"]["enabled"] = False
        common_state = {
            "active_stop": 90.0,
            "initial_stop": 90.0,
            "entry_at": (NOW - timedelta(seconds=10)).isoformat(),
            "entry_reference_price": 100.0,
            "high_water_price": 102.0,
        }

        qmd_parameters = deepcopy(base)
        qmd_parameters["momentum_management"]["qmd_exhaustion"]["enabled"] = True
        qmd = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=qmd_parameters, status=AssignmentStatus.MANAGING, state=common_state),
            confirmed_observation(
                price=101.0,
                position_quantity=100,
                qmd_score=-0.2,
                qmd_confidence=0.8,
                flow_price_divergence_score=0.7,
                source_timeframe="100ms",
            ),
        )
        self.assertEqual(qmd.evaluation.signals[0].reason, "qmd_flow_geometry_exhaustion")
        self.assertEqual(qmd.evaluation.signals[0].action, "exit")

        structure_parameters = deepcopy(base)
        structure_parameters["momentum_management"]["structure_failure"]["enabled"] = True
        structure_state = {
            **common_state,
            "latest_post_entry_swing_low": 100.0,
            "higher_low_confirmed": True,
        }
        structure = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=structure_parameters, status=AssignmentStatus.MANAGING, state=structure_state),
            confirmed_observation(
                price=99.9,
                position_quantity=100,
                source_timeframe="100ms",
            ),
        )
        self.assertEqual(structure.evaluation.signals[0].reason, "loss_of_confirmed_higher_low")
        self.assertEqual(structure.evaluation.signals[0].action, "exit")

    def test_reentry_cooldown_sensitivity_and_fresh_confirmation(self) -> None:
        for cooldown_seconds in (1, 3, 5, 10):
            with self.subTest(cooldown_seconds=cooldown_seconds):
                parameters = default_long_momentum_parameters()
                parameters["reentry"]["cooldown_ms"] = cooldown_seconds * 1000
                parameters["phase_policy"] = {"reentry": {
                    "mode": "automatic",
                    "rules": deepcopy(parameters["entry_rules"]),
                    "capital_request": {
                        "mode": "fixed_quantity",
                        "value": 100,
                        "allow_replacement": False,
                    },
                    "order_intent": {
                        "execution_policy": "adaptive_urgent",
                        "partial_fill_policy": "complete_remainder",
                        "deadline_ms": 500,
                    },
                }}
                waiting = assignment(
                    parameters=parameters,
                    status=AssignmentStatus.REENTRY_COOLDOWN,
                    state={
                        "reentries": 1,
                        "last_exit_at": NOW.isoformat(),
                        "entry_at": (NOW - timedelta(seconds=30)).isoformat(),
                    },
                )
                before = LongMomentumStrategyEngine().evaluate(
                    waiting,
                    confirmed_observation(
                        observed_at=NOW + timedelta(
                            seconds=cooldown_seconds,
                            milliseconds=-1,
                        )
                    ),
                )
                ready = LongMomentumStrategyEngine().evaluate(
                    waiting,
                    confirmed_observation(
                        observed_at=NOW + timedelta(seconds=cooldown_seconds)
                    ),
                )

                self.assertEqual(before.evaluation.signals[0].reason, "reentry_cooldown")
                self.assertEqual(ready.evaluation.signals[0].action, "enter_long")

        stale = assignment(
            status=AssignmentStatus.REENTRY_COOLDOWN,
            state={
                "reentries": 1,
                "last_exit_at": NOW.isoformat(),
                "entry_at": (NOW - timedelta(seconds=30)).isoformat(),
            },
        )
        stale_result = LongMomentumStrategyEngine().evaluate(
            stale,
            confirmed_observation(observed_at=NOW),
        )
        self.assertEqual(
            stale_result.evaluation.signals[0].reason,
            "reentry_confirmation_not_fresh",
        )

    def test_session_entry_cutoff_blocks_new_exposure(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["strategy_behavior"] = {
            "entry_cutoff_time": "15:45:00",
            "flatten_time": "15:55:00",
        }
        result = LongMomentumStrategyEngine().evaluate(
            assignment(parameters=parameters),
            confirmed_observation(observed_at=datetime(2026, 7, 24, 19, 45, tzinfo=timezone.utc)),
        )

        self.assertEqual(result.evaluation.signals[0].reason, "entry_cutoff_reached")
        self.assertEqual(result.evaluation.signals[0].action, "wait")
        self.assertEqual(result.status, AssignmentStatus.COMPLETED)

    def test_session_flatten_forces_full_exit_without_reentry(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["strategy_behavior"] = {
            "entry_cutoff_time": "15:45:00",
            "flatten_time": "15:55:00",
        }
        managed = assignment(
            parameters=parameters,
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(
                observed_at=datetime(2026, 7, 24, 19, 55, tzinfo=timezone.utc),
                position_quantity=100,
            ),
        )

        self.assertEqual(result.evaluation.signals[0].reason, "session_flatten")
        self.assertEqual(result.evaluation.signals[0].action, "exit")
        self.assertEqual(result.evaluation.intents[0].quantity, 100)
        self.assertEqual(result.status, AssignmentStatus.EXIT_PENDING)
        self.assertFalse(result.evaluation.intents[0].metadata["buy_back"])

    def test_ibkr_entry_plan_has_parent_target_hard_stop_and_trailing_stop(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(assignment(), confirmed_observation())
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD"),
            intent=result.evaluation.intents[0],
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        self.assertEqual([order.orderType for order in plan.orders], ["LMT", "LMT", "STP", "TRAIL"])
        self.assertTrue(all(order.parentId == plan.orders[0].cOID for order in plan.orders[1:]))
        self.assertTrue(all(order.isSingleGroup for order in plan.orders))
        self.assertTrue(all(not order.cOID for order in plan.orders[1:]))
        self.assertTrue(all(order.tif == "DAY" for order in plan.orders))
        self.assertTrue(all(not order.outsideRTH for order in plan.orders))
        self.assertTrue(all("strategy" not in order.to_cpapi() for order in plan.orders))
        self.assertTrue(all("strategy_intent_id" not in order.to_cpapi() for order in plan.orders))

    def test_stock_order_plan_floors_theoretical_fractional_quantity(self) -> None:
        result = LongMomentumStrategyEngine().evaluate(assignment(), confirmed_observation())
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD"),
            intent=replace(result.evaluation.intents[0], quantity=143.51),
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )

        self.assertTrue(plan.orders)
        self.assertTrue(all(order.quantity == 143 for order in plan.orders))

    def test_full_exit_replaces_existing_protection_with_one_oca_group(self) -> None:
        managed = assignment(
            status=AssignmentStatus.MANAGING,
            state={
                "active_stop": 99.0,
                "initial_stop": 99.0,
                "breakout_level": 100.5,
                "entry_reference_price": 101.0,
                "high_water_price": 101.0,
            },
        )
        result = LongMomentumStrategyEngine().evaluate(
            managed,
            confirmed_observation(price=100.0, position_quantity=100, average_price=101.0),
        )
        plan = IbkrStrategyOrderPlanner().plan(
            account_id="DU123",
            instrument=InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD"),
            intent=result.evaluation.intents[0],
            strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_REVISION,
        )
        self.assertEqual([order.orderType for order in plan.orders], ["LMT", "STP", "TRAIL"])
        self.assertTrue(plan.cancel_strategy_protection)
        self.assertTrue(all(order.isSingleGroup for order in plan.orders))
        self.assertTrue(all(order.parentId is None for order in plan.orders))
        self.assertTrue(all(order.cOID for order in plan.orders))

    def test_assignments_and_decisions_are_durable_and_point_in_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            payload = assignment().payload()
            journal.save_strategy_assignment(payload)
            journal.append(
                run_id="assignment-1",
                category="strategy",
                entity_type="strategy_evaluation",
                entity_id="decision-1",
                event_time=NOW,
                payload={
                    "strategy_id": STRATEGY_ID,
                    "strategy_revision": STRATEGY_REVISION,
                    "ticker": "AAPL",
                    "action": "enter_long",
                },
            )
            self.assertEqual(journal.strategy_assignment("assignment-1")["ticker"], "AAPL")
            self.assertFalse(
                journal.strategy_records(
                    ticker="AAPL", strategy_id=STRATEGY_ID, as_of=NOW - timedelta(milliseconds=1)
                )
            )
            self.assertEqual(
                len(journal.strategy_records(ticker="AAPL", strategy_id=STRATEGY_ID, as_of=NOW)),
                1,
            )
            journal.close()


class LongMomentumRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_profit_target_slice_keeps_campaign_managing_until_position_is_flat(self) -> None:
        parameters = default_long_momentum_parameters()
        parameters["reentry"]["after_protective_exit"] = True
        assigned = assignment(
            parameters=parameters,
            status=AssignmentStatus.MANAGING,
        )
        strategy = AssignedLongMomentumStrategy([assigned])
        snapshot = SimpleNamespace(
            action="exit",
            assignment_id=assigned.assignment_id,
            fill_role="profit_target",
            reentry_after_fill=False,
            state="filled",
            updated_at=NOW,
        )

        await strategy.on_order_group_update(
            snapshot,
            aggregate_position_quantity=276,
        )
        self.assertEqual(strategy.assignments()[0].status, AssignmentStatus.MANAGING)

        await strategy.on_order_group_update(
            snapshot,
            aggregate_position_quantity=0,
        )
        updated = strategy.assignments()[0]
        self.assertEqual(updated.status, AssignmentStatus.REENTRY_COOLDOWN)
        self.assertEqual(updated.state["reentries"], 1)
        self.assertEqual(updated.state["last_exit_at"], NOW.isoformat())

    async def test_portfolio_rejection_clears_phantom_entry_pending_state(self) -> None:
        assigned = assignment(
            status=AssignmentStatus.ENTRY_PENDING,
            state={
                "entries": 1,
                "entry_at": NOW.isoformat(),
                "entry_reference_price": 101.0,
                "initial_stop": 99.0,
                "active_stop": 99.0,
                "structural_profit_targets": [103.0],
            },
        )
        strategy = AssignedLongMomentumStrategy([assigned])
        intent = LongMomentumStrategyEngine().evaluate(
            assignment(), confirmed_observation()
        ).evaluation.intents[0]

        await strategy.on_intent_rejected(
            intent,
            reasons=("too_many_protection_slices",),
            event_time=NOW,
        )

        updated = strategy.assignments()[0]
        self.assertEqual(updated.status, AssignmentStatus.WATCHING)
        self.assertEqual(updated.state["entries"], 0)
        self.assertNotIn("entry_at", updated.state)
        self.assertNotIn("active_stop", updated.state)
        self.assertEqual(
            updated.state["last_intent_rejection"]["reasons"],
            ["too_many_protection_slices"],
        )

    async def test_shared_runtime_records_intent_and_submits_protected_order_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            assigned = assignment()
            strategy = AssignedLongMomentumStrategy([assigned])
            instrument = InstrumentContract("ibkr:265598", 265598, "AAPL", "STK", "USD")
            runtime = TradingRuntime(
                RunConfig(
                    mode=RunMode.PAPER,
                    strategy_id=STRATEGY_ID,
                    strategy_revision=STRATEGY_REVISION,
                    account_ids=("DU123",),
                    anchor_date=date(2026, 7, 24),
                    run_id="runtime-1",
                ),
                SimulatedBrokerAdapter(["DU123"], mode=TradingMode.PAPER),
                strategy,
                journal,
                intent_planner=RuntimeIbkrStrategyOrderPlanner(
                    {"AAPL": instrument},
                    strategy_id=STRATEGY_ID,
                    strategy_revision=STRATEGY_REVISION,
                ),
            )
            await runtime.initialize()
            await runtime.process_strategy_observation(confirmed_observation())
            self.assertEqual(len(await runtime.broker.live_orders()), 4)
            records = journal.records("runtime-1")
            self.assertTrue(any(row.entity_type == "strategy_intent" for row in records))
            self.assertTrue(any(row.entity_type == "strategy_assignment_state" for row in records))
            self.assertEqual(journal.strategy_assignment("assignment-1")["status"], "entry_pending")
            journal.close()


class LongMomentumServiceTests(unittest.TestCase):
    def test_assignment_api_service_persists_definition_evaluation_and_canvas_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("TRADING_JOURNAL_PATH")
            os.environ["TRADING_JOURNAL_PATH"] = str(Path(directory) / "service.sqlite3")
            trading_runtime_service.close_trading_journal()
            try:
                rows = trading_runtime_service.list_strategy_definitions()
                self.assertEqual([row["strategy_id"] for row in rows], [STRATEGY_ID])
                created = trading_runtime_service.create_strategy_assignment(
                    {
                        "account_id": "DU123",
                        "ticker": "AAPL",
                        "conid": 265598,
                        "strategy_id": STRATEGY_ID,
                        "strategy_revision": STRATEGY_REVISION,
                        "permissions": {"enter": True, "add": True, "reenter": True},
                    }
                )
                with self.assertRaisesRegex(
                    ValueError, "already has an active campaign leg"
                ):
                    trading_runtime_service.create_strategy_assignment(
                        {
                            "account_id": "DU123",
                            "ticker": "AAPL",
                            "conid": 265598,
                            "strategy_id": STRATEGY_ID,
                            "strategy_revision": STRATEGY_REVISION,
                            "permissions": {"enter": True},
                        }
                    )
                observation = confirmed_observation().payload()
                evaluated = trading_runtime_service.evaluate_strategy_assignment(
                    created["assignment_id"], observation
                )
                self.assertEqual(evaluated["evaluation"]["action"], "enter_long")
                self.assertEqual(len(evaluated["order_plan"]), 4)
                self.assertFalse(evaluated["orders_submitted"])
                canvas = trading_runtime_service.strategy_canvas_payload(as_of=NOW, ticker="AAPL")
                self.assertEqual(canvas["historical_source"], "saved_strategy_journal_only")
                self.assertEqual(len(canvas["signals"]), 1)
                self.assertEqual(canvas["order_management"], [])
                activity = trading_runtime_service.strategy_activity_payload(
                    as_of=NOW,
                    strategy_id=STRATEGY_ID,
                    ticker="AAPL",
                )
                self.assertEqual(activity["source"], "trading_journal")
                self.assertTrue(any(row["event_type"] == "decision" for row in activity["rows"]))
                self.assertTrue(any(row["action"] == "enter_long" for row in activity["rows"]))
                self.assertEqual(activity["catalog"]["tickers"], ["AAPL"])
            finally:
                trading_runtime_service.close_trading_journal()
                if previous is None:
                    os.environ.pop("TRADING_JOURNAL_PATH", None)
                else:
                    os.environ["TRADING_JOURNAL_PATH"] = previous


if __name__ == "__main__":
    unittest.main()
