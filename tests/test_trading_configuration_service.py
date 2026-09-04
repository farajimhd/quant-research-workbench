from __future__ import annotations

import tempfile
import unittest
import os
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import src.backend.trading_configuration_service as configuration_service

from src.backend.trading_configuration_service import (
    CONFIGURATION_SCHEMA_VERSION,
    _default_draft,
    _compiled_observation_dependencies,
    _compile_run_plans,
    _migrate_draft,
    _refresh_builtin_system_strategy_profiles,
    _normalize_rule_set_conditions,
    _profile_rule_set_ids,
    _qmd_family_capabilities,
    _qmd_runtime_capabilities,
    _resolved_source_account_id,
    _validate_draft,
    _validate_market_discovery,
    _validate_rule_set_definition,
    approved_canvas_profile,
    approved_configuration,
    approved_runtime_configuration_snapshot,
    backtest_configuration_snapshot,
    backtest_debug_configuration_snapshot,
    configuration_candidate,
    configuration_candidates,
    configuration_base,
    create_test_candidate,
    candidate_runtime_configuration_snapshot,
    effective_configuration_snapshot,
    market_discovery_runtime_configuration,
    market_discovery_presentation_configuration,
    materialize_market_discovery,
    merged_assignment_parameters,
    publish_configuration,
    public_configuration_revision,
    replay_configuration_snapshot,
    resolve_runtime_configuration,
    resolve_runtime_configurations,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import (
    STRATEGY_REVISION,
    long_momentum_strategy_definition,
)


class TradingConfigurationServiceTests(unittest.TestCase):
    def test_strategy_protection_is_not_overridden_by_oms_defaults(self) -> None:
        configuration = {
            "strategy": {
                "strategy_id": "long-momentum-campaign",
                "revision": 30,
                "parameters": {
                    "protection": {
                        "stop": {
                            "method": "ordinal_qualified_support",
                            "maximum_risk_pct": 15.0,
                            "minimum_hold_quality_score": 0.70,
                        },
                        "trailing": {"enabled": True},
                    }
                },
            },
            "campaign_policy": {},
            "oms": {
                "settings": {
                    "entry_urgency": "urgent",
                    "exit_urgency": "urgent",
                    "limit_offset_bps": 0.0,
                    "tick_size": 0.01,
                    "protection": {
                        "stop_method": "hybrid",
                        "structure_buffer_bps": 10.0,
                        "volatility_multiple": 1.0,
                        "maximum_risk_pct": 6.0,
                        "trailing_enabled": False,
                    },
                },
                "execution_policies": [],
                "protection_profiles": [],
            },
        }

        resolved = merged_assignment_parameters(configuration, {"parameters": {}})

        self.assertEqual(resolved["protection"]["stop"]["method"], "ordinal_qualified_support")
        self.assertEqual(resolved["protection"]["stop"]["maximum_risk_pct"], 15.0)
        self.assertEqual(resolved["protection"]["stop"]["minimum_hold_quality_score"], 0.70)
        self.assertTrue(resolved["protection"]["trailing"]["enabled"])

    def test_schema_v42_migrates_current_protected_squeeze_contract(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            legacy = _default_draft()
        legacy["schema_version"] = 42
        profile = legacy["strategy"]["profiles"][0]
        profile["definition_revision"] = 28
        profile["parameters"]["protection"]["stop"].update({
            "method": "hybrid",
            "maximum_risk_pct": 6.0,
        })
        profile["parameters"]["protection"]["trailing"]["enabled"] = False
        profile["parameters"]["momentum_management"]["downside_loss_guard"][
            "bearish_choch"
        ] = True
        protection = next(
            row
            for row in legacy["oms"]["protection_profiles"]
            if row["profile_id"] == "structural-single-target"
        )
        protection["slices"][0]["trailing"] = {"rule_type": "none"}

        migrated = _migrate_draft(legacy)

        migrated_profile = migrated["strategy"]["profiles"][0]
        migrated_stop = migrated_profile["parameters"]["protection"]["stop"]
        self.assertEqual(migrated_profile["definition_revision"], STRATEGY_REVISION)
        self.assertEqual(migrated_stop["method"], "ordinal_qualified_support")
        self.assertEqual(migrated_stop["maximum_risk_pct"], 15.0)
        self.assertEqual(migrated_stop["support_level_ordinal"], 2)
        self.assertTrue(
            migrated_profile["parameters"]["protection"]["trailing"]["enabled"]
        )
        self.assertNotIn(
            "bearish_choch",
            migrated_profile["parameters"]["momentum_management"]["downside_loss_guard"],
        )
        self.assertEqual(
            migrated_profile["parameters"]["structural_entry"]["entry_tranche_count"],
            3,
        )
        self.assertEqual(
            migrated_profile["lifecycle"]["initial_entry"]["capital_request"]["mode"],
            "all_available",
        )
        self.assertEqual(
            migrated_profile["lifecycle"]["initial_entry"]["capital_request"]["maximum_quantity"],
            10_000,
        )
        self.assertEqual(
            migrated_profile["parameters"]["protection"]["profit_ladder"][
                "incomplete_target_exit"
            ]["extended_hours_execution_policy"],
            "adaptive_urgent",
        )
        migrated_protection = next(
            row
            for row in migrated["oms"]["protection_profiles"]
            if row["profile_id"] == "structural-single-target"
        )
        self.assertEqual(
            migrated_protection["slices"][0]["trailing"]["rule_type"],
            "broker_amount",
        )
        migrated_policies = {
            row["policy_id"]: row for row in migrated["portfolio"]["policies"]
        }
        self.assertEqual(
            migrated_policies["default"]["maximum_planned_risk_fraction"],
            0.08,
        )
        self.assertEqual(
            migrated_policies["long-momentum-real-80"]["maximum_open_risk_fraction"],
            0.08,
        )
        migrated_mandates = {
            row["mandate_id"]: row for row in migrated["portfolio"]["mandates"]
        }
        self.assertTrue(
            all(
                row["maximum_planned_risk_fraction"] == 0.08
                for mandate_id, row in migrated_mandates.items()
                if mandate_id.startswith("balanced-")
                or mandate_id.startswith("long-momentum-squeeze-")
            )
        )
        _validate_draft(migrated, require_runtime_ready=False)

    def test_new_release_refreshes_only_builtin_system_strategy_profiles(self) -> None:
        draft = _default_draft()
        system_profile = draft["strategy"]["profiles"][0]
        system_profile["definition_revision"] = 1
        system_profile["name"] = "Stale built-in projection"
        system_profile["protected"] = False
        system_profile["parameters"]["liquidity_admission"][
            "maximum_vwap_extension_bps"
        ] = 500.0
        user_profile = deepcopy(system_profile)
        user_profile.update(
            profile_id="user-published-revision-one",
            name="User historical profile",
            origin="user",
            protected=False,
            publication_status="published",
        )
        draft["strategy"]["profiles"].append(user_profile)

        _refresh_builtin_system_strategy_profiles(draft)

        refreshed_system = next(
            row
            for row in draft["strategy"]["profiles"]
            if row["profile_id"] == system_profile["profile_id"]
        )
        preserved_user = next(
            row
            for row in draft["strategy"]["profiles"]
            if row["profile_id"] == user_profile["profile_id"]
        )
        self.assertEqual(
            refreshed_system["definition_revision"],
            long_momentum_strategy_definition()["revision"],
        )
        self.assertNotEqual(refreshed_system["name"], "Stale built-in projection")
        self.assertNotIn(
            "maximum_vwap_extension_bps",
            refreshed_system["parameters"]["liquidity_admission"],
        )
        self.assertEqual(preserved_user["definition_revision"], 1)
        self.assertEqual(preserved_user["name"], "User historical profile")

    def test_candidate_runtime_snapshot_reuses_immutable_projection(self) -> None:
        candidate = {
            "candidate_id": "candidate-cache-test",
            "candidate_revision": 1,
            "content_hash": "candidate-cache-hash",
            "created_at": "2026-08-27T12:00:00+00:00",
        }
        projected = {
            "revision_id": candidate["candidate_id"],
            "run_plan_id": "balanced-replay",
        }
        configuration_service._RUNTIME_SNAPSHOT_CACHE.clear()
        try:
            with patch(
                "src.backend.trading_configuration_service.configuration_candidate",
                return_value=candidate,
            ), patch(
                "src.backend.trading_configuration_service._runtime_configuration_snapshot",
                return_value=projected,
            ) as resolve:
                first = candidate_runtime_configuration_snapshot(
                    "replay",
                    candidate_id=candidate["candidate_id"],
                    run_plan_id="",
                )
                second = candidate_runtime_configuration_snapshot(
                    "replay",
                    candidate_id=candidate["candidate_id"],
                    run_plan_id="balanced-replay",
                )

            self.assertEqual(first, projected)
            self.assertEqual(second, projected)
            self.assertIsNot(first, projected)
            self.assertIsNot(second, projected)
            resolve.assert_called_once()
        finally:
            configuration_service._RUNTIME_SNAPSHOT_CACHE.clear()

    def test_atomic_rule_catalog_has_complete_executable_conditions(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            draft = _default_draft()

        rule_sets = draft["market_discovery"]["rule_sets"]
        self.assertGreater(len(rule_sets), 30)
        _validate_draft(draft, require_runtime_ready=False)
        for rule_set in rule_sets:
            with self.subTest(rule_set=rule_set["rule_set_id"]):
                _validate_rule_set_definition(rule_set, rule_set["name"])
                self.assertNotIn("registered condition(s)", rule_set["description"])

        bullish_choch = next(
            row
            for row in rule_sets
            if row["rule_set_id"] == "initial-entry-opportunity-bullish-choch"
        )
        self.assertEqual(bullish_choch["conditions"][0]["comparator"], "is_true")
        self.assertIsNone(bullish_choch["conditions"][0]["value"])

        squeeze_rules = [
            row for row in rule_sets
            if row["rule_set_id"].startswith("watchlist-squeeze-")
        ]
        self.assertEqual(len(squeeze_rules), 4)
        self.assertTrue(all(row["enabled"] for row in squeeze_rules))
        self.assertTrue(all(row["protected"] for row in squeeze_rules))

        halt_rule = next(
            row for row in rule_sets if row["rule_set_id"] == "signal-market-halt"
        )
        self.assertEqual(halt_rule["conditions"][0]["left_source_id"], "market.is_halted")
        self.assertEqual(halt_rule["conditions"][0]["comparator"], "is_true")

        halt_stream = next(
            row
            for row in draft["market_discovery"]["signal_streams"]
            if row["signal_stream_id"] == "market-halts"
        )
        self.assertEqual(halt_stream["source_type"], "core_scan")
        self.assertEqual(halt_stream["inclusion_rule_sets"], ["signal-market-halt"])
        self.assertNotIn("market_is_halted", halt_stream["columns"])
        self.assertIn("halt_category", halt_stream["columns"])
        self.assertIn("halt_direction", halt_stream["columns"])
        five_minute_column = next(
            row["column_id"]
            for row in draft["market_discovery"]["column_catalog"]
            if row.get("source_id") == "price_change_5_bar_pct"
        )
        self.assertIn(five_minute_column, halt_stream["columns"])
        self.assertEqual(halt_stream["column_labels"][five_minute_column], "Last 5 min")
        self.assertEqual(
            halt_stream["column_intervals"][five_minute_column],
            {"value": 1, "unit": "minutes"},
        )
        self.assertEqual(halt_stream["trigger_policy"], "false_to_true")
        self.assertEqual(halt_stream["rearm_policy"], "after_false")

        signal_context_columns = {
            "float_category",
            "short_pressure",
            "short_interest",
            "short_interest_pct",
            "days_to_cover",
            "short_volume",
            "short_volume_pct",
            "liquidity_rank",
            "liquidity_score",
        }
        for stream in draft["market_discovery"]["signal_streams"]:
            with self.subTest(signal_stream=stream["signal_stream_id"]):
                self.assertTrue(signal_context_columns.issubset(stream["columns"]))

    def test_rule_definition_rejects_incomplete_operands(self) -> None:
        base = {
            "name": "Malformed",
            "description": "Malformed test rule.",
            "enabled": True,
            "operator": "all",
            "required_score": 1,
            "conditions": [{
                "condition_id": "condition-1",
                "enabled": True,
                "left_source_id": "market.last_price",
                "left_timeframe": "1s",
                "comparator": "greater_than",
                "right_source_id": "",
                "right_timeframe": "",
                "value": None,
            }],
        }
        with self.assertRaisesRegex(ValueError, "requires a comparison value or target source"):
            _validate_rule_set_definition(base, "Malformed")

        basis_points = deepcopy(base)
        basis_points["conditions"][0].update({"comparator": "above_by_bps", "value": 5})
        with self.assertRaisesRegex(ValueError, "requires a target source"):
            _validate_rule_set_definition(basis_points, "Malformed")

    def test_legacy_rule_comparator_aliases_migrate_to_runtime_contract(self) -> None:
        rule_set = {
            "conditions": [
                {"comparator": "equal"},
                {"comparator": "greater_than_or_equal"},
                {"comparator": "less_than_or_equal"},
            ]
        }
        _normalize_rule_set_conditions(rule_set)
        self.assertEqual(
            [row["comparator"] for row in rule_set["conditions"]],
            ["equals", "greater_or_equal", "less_or_equal"],
        )

    def test_historical_snapshot_selects_one_exact_run_plan_and_strategy(self) -> None:
        approved = {
            "revision_id": "release-1",
            "revision": 4,
            "label": "Two strategies",
            "content_hash": "hash-1",
            "approved_at": "2026-08-13T12:00:00+00:00",
            "payload": {"canvas": {"revision": "canvas-1", "profile": {"workspaceStates": {}}}},
        }
        runtimes = [
            {
                "run_plan": {"run_plan_id": "plan-a", "name": "Plan A"},
                "strategy": {"strategy_id": "strategy-a", "revision": 1, "profile_id": "profile-a"},
            },
            {
                "run_plan": {"run_plan_id": "plan-b", "name": "Plan B"},
                "strategy": {"strategy_id": "strategy-b", "revision": 7, "profile_id": "profile-b"},
            },
        ]
        with patch(
            "src.backend.trading_configuration_service.approved_configuration",
            return_value=approved,
        ), patch(
            "src.backend.trading_configuration_service._migrate_draft",
            return_value=approved["payload"],
        ), patch(
            "src.backend.trading_configuration_service._validate_draft",
        ), patch(
            "src.backend.trading_configuration_service.resolve_runtime_configurations",
            return_value=runtimes,
        ):
            snapshot = approved_runtime_configuration_snapshot("backtest", run_plan_id="plan-b")

        self.assertEqual(snapshot["run_plan_id"], "plan-b")
        self.assertEqual(snapshot["payload"]["strategy"]["strategy_id"], "strategy-b")
        self.assertEqual(
            [row["run_plan_id"] for row in snapshot["available_run_plans"]],
            ["plan-a", "plan-b"],
        )

    def test_historical_snapshot_entrypoints_preserve_runtime_mode(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.approved_runtime_configuration_snapshot",
            side_effect=lambda mode, **_kwargs: {"mode": mode},
        ) as snapshot, patch(
            "src.backend.trading_configuration_service.approved_configuration",
            return_value={"revision_id": "approved"},
        ), patch(
            "src.backend.trading_configuration_service.configuration_candidate",
            return_value=None,
        ):
            replay = replay_configuration_snapshot()
            backtest = backtest_configuration_snapshot()
            debug = backtest_debug_configuration_snapshot()

        self.assertEqual(replay["mode"], "replay")
        self.assertEqual(backtest["mode"], "backtest")
        self.assertEqual(debug["mode"], "backtest_debug")
        self.assertEqual(
            [call.args[0] for call in snapshot.call_args_list],
            ["replay", "backtest", "backtest_debug"],
        )

    @patch("src.backend.trading_configuration_service.qmd_catalogs")
    def test_market_discovery_projects_qmd_runtime_catalog_authority(self, catalogs) -> None:
        catalogs.return_value = {
            "capability_catalog": [{
                "key": "momentum_core",
                "label": "Core Momentum Oscillators",
                "producer": "qmd",
                "kind": "indicator_family",
                "execution_scope": "watchlist",
                "allowed_scopes": ["watchlist", "strategy_run", "request", "offline"],
                "configuration_policy": "configurable",
                "implementation_status": "implemented",
                "operational_status": "ready",
                "cost_class": "medium",
                "stateful": True,
                "implementation_version": 4,
                "cadence": "bar_close",
                "warm_up_bars": 50,
                "persistence_policy": "if_signal_uses",
                "inputs": ["bars"],
                "outputs": ["rsi_14"],
            }],
            "indicator_catalog": [{
                "key": "momentum_core",
                "category": "momentum",
                "priority": "p1",
                "typical_timeframes": ["1m", "5m"],
                "rationale": "Closed-bar momentum calculations.",
            }],
            "signal_catalog": [],
        }
        with patch(
            "src.backend.trading_configuration_service._QMD_RUNTIME_CATALOG_CACHE",
            (0.0, []),
        ):
            rows = _qmd_runtime_capabilities()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["capability_id"], "qmd.family.momentum_core")
        self.assertEqual(row["capability_key"], "momentum_core")
        self.assertEqual(row["execution_scope"], "watchlist")
        self.assertEqual(row["timeframes"], ["1m", "5m"])
        self.assertEqual(row["catalog_authority"], "qmd_runtime_catalog")
        self.assertEqual(row["owner"], "qmd")
        self.assertEqual(row["implementation_version"], 4)
        self.assertEqual(row["cadence"], "bar_close")
        self.assertEqual(row["warm_up_bars"], 50)
        self.assertEqual(row["persistence_policy"], "if_signal_uses")
        self.assertEqual(row["consumers"], ["watchlist", "strategy_run", "request", "offline"])

    @patch(
        "src.backend.trading_configuration_service.get_strategy_definition",
        return_value=long_momentum_strategy_definition(),
    )
    def test_compiled_qmd_dependencies_pin_catalog_warm_up_and_revision(
        self, _definition
    ) -> None:
        profile = self._draft()["strategy"]["profiles"][0]

        dependencies = _compiled_observation_dependencies(
            profile,
            [{
                "capability_key": "momentum_core",
                "implementation_version": 7,
                "warm_up_bars": 50,
            }],
        )

        by_key = {row["capability_key"]: row for row in dependencies}
        self.assertEqual(
            by_key["momentum_core"]["warm_up"],
            {"bars": 50, "status": "required"},
        )
        self.assertEqual(by_key["momentum_core"]["capability_revision"], 7)
        self.assertEqual(
            by_key["vwap_transition"]["warm_up"],
            {"bars": None, "status": "catalog_unavailable"},
        )

    @patch("src.backend.trading_configuration_service.qmd_catalogs")
    def test_market_discovery_does_not_invent_qmd_rows_during_outage(self, catalogs) -> None:
        catalogs.side_effect = RuntimeError("QMD unavailable")
        with patch(
            "src.backend.trading_configuration_service._QMD_RUNTIME_CATALOG_CACHE",
            (0.0, []),
        ):
            self.assertEqual(_qmd_family_capabilities(), [])

    @patch("src.backend.trading_configuration_service.market_discovery_runtime_configuration")
    def test_market_discovery_presentation_projects_only_canvas_metadata(self, runtime) -> None:
        runtime.return_value = {
            "schema_version": 31,
            "market_discovery": {
                "atomic_fields": [{"field_id": "atomic.trade.price"}],
                "data_fields": [{"field_id": "field.price.change"}],
                "data_field_plan": {"nodes": ["large executable plan"]},
                "column_catalog": [{
                    "column_id": "last_price",
                    "name": "Last price",
                    "description": "Latest eligible trade price.",
                    "source_id": "field.market.last_price",
                    "source_kind": "data_field",
                    "semantic_type": "price",
                    "value_type": "number",
                    "presentation_value_type": "price",
                    "unit": "USD",
                    "provenance": {"authority": "qmd"},
                    "query_expression": "must not be sent to Canvas",
                }],
                "core_scan": {
                    "scan_id": "core",
                    "name": "Core scan",
                    "description": "All eligible symbols.",
                    "columns": ["last_price"],
                    "calculations": [{
                        "capability_id": "qmd.family.core_bars",
                        "enabled": True,
                        "scanner_columns": ["last_price"],
                        "query_expression": "must not be sent to Canvas",
                    }],
                },
                "watchlists": [{"watchlist_id": "leaders"}],
                "signal_streams": [{"signal_stream_id": "squeezes"}],
            },
            "run_plans": {
                "plans": [{"run_plan_id": "paper"}],
                "universes": [{"universe_id": "all"}],
                "strategy_definitions": [{"large": "unused"}],
            },
        }

        projected = market_discovery_presentation_configuration()

        discovery = projected["market_discovery"]
        self.assertEqual(projected["schema_version"], 31)
        self.assertNotIn("atomic_fields", discovery)
        self.assertNotIn("data_fields", discovery)
        self.assertNotIn("data_field_plan", discovery)
        self.assertNotIn("query_expression", discovery["column_catalog"][0])
        self.assertEqual(discovery["column_catalog"][0]["presentation_value_type"], "price")
        self.assertNotIn("query_expression", discovery["core_scan"]["calculations"][0])
        self.assertEqual(discovery["watchlists"], [{"watchlist_id": "leaders"}])
        self.assertEqual(discovery["signal_streams"], [{"signal_stream_id": "squeezes"}])
        self.assertEqual(projected["run_plans"], {
            "plans": [{"run_plan_id": "paper"}],
            "universes": [{"universe_id": "all"}],
        })

    def test_saved_qmd_capability_remains_reviewable_during_outage(self) -> None:
        draft = self._draft()
        saved = {
            "capability_id": "qmd.family.saved_only",
            "name": "Saved capability",
            "provider": "QMD",
            "execution_scope": "watchlist",
            "allowed_scopes": ["watchlist"],
            "configuration_policy": "configurable",
            "availability": "implemented",
            "implementation_status": "implemented",
            "enabled": True,
            "timeframes": ["1m"],
            "selected_timeframes": ["1m"],
        }
        draft["market_discovery"]["calculation_catalog"].append(saved)
        with patch(
            "src.backend.trading_configuration_service.qmd_catalogs",
            side_effect=RuntimeError("QMD unavailable"),
        ), patch(
            "src.backend.trading_configuration_service._QMD_RUNTIME_CATALOG_CACHE",
            (0.0, []),
        ):
            migrated = _migrate_draft(draft)

        rows = {
            row["capability_id"]: row
            for row in migrated["market_discovery"]["calculation_catalog"]
        }
        self.assertIn("qmd.family.saved_only", rows)
        self.assertTrue(rows["qmd.family.saved_only"]["enabled"])

    def test_system_ipo_template_migrates_from_pending_to_registered_path(self) -> None:
        draft = self._draft()
        ipo = next(
            row
            for row in draft["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "past-upcoming-ipos"
        )
        ipo.update({"availability": "integration_pending", "enabled": False})
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            migrated = _migrate_draft(draft)

        migrated_ipo = next(
            row
            for row in migrated["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "past-upcoming-ipos"
        )
        self.assertEqual(migrated_ipo["availability"], "available")
        self.assertTrue(migrated_ipo["enabled"])

    def test_system_discovery_presentation_migrates_to_current_contract(self) -> None:
        draft = self._draft()
        draft["market_discovery"]["core_scan"]["description"] = (
            "Legacy Data Definitions description."
        )
        squeeze = next(
            row
            for row in draft["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "price-or-volume-squeeze"
        )
        squeeze["name"] = "Price or Volume Squeeze"
        squeeze["description"] = "Legacy one-second squeeze description."

        migrated = _migrate_draft(draft)

        self.assertIn(
            "registered Data Fields",
            migrated["market_discovery"]["core_scan"]["description"],
        )
        migrated_squeeze = next(
            row
            for row in migrated["market_discovery"]["watchlists"]
            if row["watchlist_id"] == "price-or-volume-squeeze"
        )
        self.assertEqual(migrated_squeeze["name"], "Session Price or Volume Expansion")
        self.assertIn("20-session relative volume", migrated_squeeze["description"])

    def test_schema_v38_adds_liquidity_columns_rank_direction_and_early_stream(self) -> None:
        legacy = self._draft()
        legacy["schema_version"] = 38
        discovery = legacy["market_discovery"]
        discovery["core_scan"]["columns"] = ["symbol", "last_price"]
        discovery["core_scan"]["ranking_direction"] = "descending"
        for watchlist in discovery["watchlists"]:
            watchlist["columns"] = ["symbol", "last_price"]
            if watchlist["ranking_field"] == "market.liquidity_rank":
                watchlist["ranking_direction"] = "descending"
        discovery["signal_streams"] = [
            row for row in discovery["signal_streams"]
            if row["signal_stream_id"] != "price-squeeze-early"
        ]

        migrated = _migrate_draft(legacy)
        discovery = migrated["market_discovery"]
        self.assertEqual(discovery["core_scan"]["ranking_direction"], "ascending")
        self.assertIn("liquidity_score", discovery["core_scan"]["columns"])
        self.assertIn("liquidity_rank", discovery["core_scan"]["columns"])
        self.assertTrue(all("liquidity_score" in row["columns"] for row in discovery["watchlists"]))
        self.assertTrue(all("liquidity_rank" in row["columns"] for row in discovery["watchlists"]))
        self.assertIn(
            "price-squeeze-early",
            {row["signal_stream_id"] for row in discovery["signal_streams"]},
        )

    def test_default_discovery_adds_guarded_synthesis_deepfm_stream(self) -> None:
        discovery = self._draft()["market_discovery"]
        rule = next(
            row
            for row in discovery["rule_sets"]
            if row["rule_set_id"] == "signal-news-synthesis-deepfm-bullish"
        )
        self.assertEqual(
            {
                (condition["left_source_id"], condition["comparator"], condition["value"])
                for condition in rule["conditions"]
            },
            {
                ("news.composite_sentiment", "equals", "positive"),
                ("news.deepfm.forecast_eligible", "is_true", True),
            },
        )
        stream = next(
            row
            for row in discovery["signal_streams"]
            if row["signal_stream_id"] == "bullish-synthesis-deepfm-news-v1"
        )
        self.assertEqual(
            stream["inclusion_rule_sets"],
            ["signal-news-synthesis-deepfm-bullish"],
        )
        self.assertTrue(
            {
                "news_composite_sentiment",
                "news_deepfm_probability",
                "news_deepfm_eligible",
                "news_deepfm_status",
            }.issubset(set(stream["columns"]))
        )

    def test_schema_v27_migrates_generic_timeframes_to_field_dimensions(self) -> None:
        legacy = self._draft()
        legacy["schema_version"] = 27
        old_ref = "data.qmd.family.core_bars.10s@1:market.last_price"
        legacy["market_discovery"]["data_fields"].append({
            "data_field_id": "data.qmd.family.core_bars.10s",
            "revision": 1,
            "context": {"timeframes": ["10s"]},
            "outputs": [{
                "field_ref": old_ref,
                "source_id": "market.last_price",
                "context_timeframe": "10s",
            }],
        })
        vwap_rule = next(
            row
            for row in legacy["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "watchlist-vwap-breakout"
        )
        vwap_rule["conditions"][0]["left_field_ref"] = old_ref

        migrated = _migrate_draft(legacy)

        self.assertEqual(migrated["schema_version"], CONFIGURATION_SCHEMA_VERSION)
        last_price = [
            row
            for row in migrated["market_discovery"]["data_fields"]
            if row["outputs"][0]["source_id"] == "market.last_price"
        ]
        self.assertEqual(len(last_price), 1)
        self.assertEqual(last_price[0]["context"]["as_of"], "evaluation_clock")
        self.assertNotIn("timeframes", last_price[0]["context"])
        migrated_vwap_rule = next(
            row
            for row in migrated["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "watchlist-vwap-breakout"
        )
        self.assertEqual(
            migrated_vwap_rule["conditions"][0]["left_field_ref"],
            "data.market.last_price@1:value",
        )
        self.assertFalse(migrated_vwap_rule["conditions"][0].get("left_interval"))

    def test_current_schema_migrates_saved_legacy_vwap_rule_to_execution_vwap(self) -> None:
        legacy = self._draft()
        vwap_rule = next(
            row
            for row in legacy["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "watchlist-vwap-breakout"
        )
        condition = vwap_rule["conditions"][0]
        condition["right_source_id"] = "indicator.vwap.value"
        condition["right_field_ref"] = "data.indicator.vwap.value@1:value"

        migrated = _migrate_draft(legacy)

        migrated_rule = next(
            row
            for row in migrated["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "watchlist-vwap-breakout"
        )
        migrated_condition = migrated_rule["conditions"][0]
        self.assertEqual(
            migrated_condition["right_source_id"],
            "indicator.vwap.execution_value",
        )
        self.assertEqual(
            migrated_condition["right_field_ref"],
            "data.indicator.vwap.execution_value@1:value",
        )
        _validate_market_discovery(migrated["market_discovery"])

    def test_schema_v28_moves_interval_variants_to_rule_and_composition_instances(self) -> None:
        legacy = self._draft()
        legacy["schema_version"] = 28
        old_ref = "data.qmd.family.core_bars.5m@1:price_change_pct"
        legacy["market_discovery"]["data_fields"].append({
            "data_field_id": "data.qmd.family.core_bars.5m",
            "revision": 1,
            "context": {"dimension_kind": "interval", "interval": "5m"},
            "outputs": [{"field_ref": old_ref, "source_id": "price_change_pct", "context_interval": "5m"}],
        })
        legacy["market_discovery"]["column_catalog"].append({
            "column_id": "price_change_pct__5m",
            "field_ref": old_ref,
            "source_id": "price_change_pct",
            "source_kind": "data_field",
            "interval": "5m",
        })
        legacy["market_discovery"]["core_scan"]["columns"].append("price_change_pct__5m")
        custom_rule = {
            "rule_set_id": "legacy-five-minute-change",
            "enabled": True,
            "operator": "all",
            "conditions": [{
                "condition_id": "legacy-five-minute-change-condition",
                "enabled": True,
                "left_field_ref": old_ref,
                "left_source_id": "price_change_pct",
                "comparator": "greater_or_equal",
                "value": 2,
            }],
        }
        legacy["market_discovery"]["rule_sets"].append(custom_rule)

        migrated = _migrate_draft(legacy)

        core = migrated["market_discovery"]["core_scan"]
        self.assertIn("field__price__change__pct", core["columns"])
        self.assertEqual(core["column_intervals"]["field__price__change__pct"], {"value": 5, "unit": "minutes"})
        rule = next(row for row in migrated["market_discovery"]["rule_sets"] if row["rule_set_id"] == "legacy-five-minute-change")
        self.assertEqual(rule["conditions"][0]["left_field_ref"], "data.price_change_pct@1:value")
        self.assertEqual(rule["conditions"][0]["left_interval"], {"value": 5, "unit": "minutes"})

    def test_legacy_server_draft_table_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            row = journal._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'trading_configuration_draft'"
            ).fetchone()
            journal.close()

        self.assertIsNone(row)

    def test_environment_bound_paper_and_cash_accounts_keep_ids_out_of_draft(self) -> None:
        with patch.dict(os.environ, {
            "IBKR_PAPER_ACCOUNT_ID": "DU-PAPER-TEST",
            "IBKR_CASH_ACCOUNT_ID": "U-CASH-TEST",
        }, clear=False), patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            draft = _default_draft()
            accounts = {row["account_key"]: row for row in draft["accounts"]["bindings"]}
            self.assertEqual(set(accounts), {"replay", "paper", "cash"})
            self.assertEqual(accounts["replay"]["name"], "Backtest account")
            self.assertEqual(accounts["replay"]["modes"], ["replay", "backtest", "backtest_debug"])
            self.assertEqual(accounts["paper"]["source_account_env"], "IBKR_PAPER_ACCOUNT_ID")
            self.assertEqual(accounts["cash"]["source_account_env"], "IBKR_CASH_ACCOUNT_ID")
            self.assertEqual(accounts["paper"]["source_account_id"], "")
            self.assertEqual(accounts["cash"]["source_account_id"], "")
            self.assertFalse(accounts["paper"]["enabled"])
            self.assertFalse(accounts["cash"]["enabled"])
            self.assertEqual(_resolved_source_account_id(accounts["paper"]), "DU-PAPER-TEST")
            self.assertEqual(_resolved_source_account_id(accounts["cash"]), "U-CASH-TEST")

    def test_schema_v17_migration_adds_phase_modes_and_market_discovery(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            legacy = _default_draft()
        legacy["schema_version"] = 6
        paper_binding = next(
            row
            for row in legacy["accounts"]["bindings"]
            if "paper" in row.get("modes", [])
        )
        paper_binding["source_account_env"] = ""
        paper_binding["source_account_id"] = "DU-LEGACY"
        for capability in legacy["market_discovery"]["calculation_catalog"]:
            if capability["name"] == "Last price":
                capability["description"] = "Legacy repeated provider copy."
        legacy["strategy"]["profiles"][0]["lifecycle"]["trading_behavior"][
            "adopt_manual_positions"
        ] = True
        initial_intent = legacy["strategy"]["profiles"][0]["lifecycle"][
            "initial_entry"
        ]["order_intent"]
        initial_intent["time_in_force"] = "GTC"
        initial_intent["outside_rth"] = False
        oms_settings = legacy["oms"]["profiles"][0]["settings"]
        oms_settings["time_in_force"] = "IOC"
        oms_settings["outside_rth"] = True
        oms_settings.pop("session_routing", None)

        migrated = _migrate_draft(legacy)

        self.assertEqual(migrated["schema_version"], CONFIGURATION_SCHEMA_VERSION)
        migrated_paper = next(
            row
            for row in migrated["accounts"]["bindings"]
            if "paper" in row.get("modes", [])
        )
        self.assertEqual(migrated_paper["source_account_env"], "IBKR_PAPER_ACCOUNT_ID")
        self.assertEqual(migrated_paper["source_account_id"], "")
        self.assertTrue(migrated["market_discovery"]["calculation_catalog"])
        self.assertTrue(migrated["market_discovery"]["watchlists"])
        capabilities = {
            row["name"]: row
            for row in migrated["market_discovery"]["calculation_catalog"]
        }
        self.assertTrue(capabilities["Last price"]["system_required"])
        self.assertFalse(capabilities["Last price"]["configurable"])
        self.assertIn("causally available market price", capabilities["Last price"]["description"])
        self.assertTrue(capabilities["Company news score"]["configurable"])
        self.assertFalse(capabilities["Company news score"]["system_required"])
        self.assertEqual(capabilities["Last price"]["capability_type"], "market_data")
        self.assertEqual(capabilities["Previous close"]["capability_type"], "reference")
        self.assertEqual(capabilities["Previous high"]["capability_type"], "reference")
        self.assertEqual(capabilities["Confirmed swing high"]["capability_type"], "indicator")
        self.assertEqual(capabilities["Bullish change of character"]["capability_type"], "signal")
        self.assertEqual(capabilities["Company news score"]["capability_type"], "signal")
        self.assertEqual(capabilities["News observations"]["capability_type"], "event")
        self.assertEqual(capabilities["News labeled"]["capability_type"], "signal")
        self.assertEqual(capabilities["News labeled"]["tier"], "watchlist")
        self.assertEqual(capabilities["News labeled"]["availability"], "integration_pending")
        self.assertFalse(capabilities["News labeled"]["enabled"])
        self.assertFalse(capabilities["News labeled"]["configurable"])
        self.assertEqual(capabilities["SEC labeled"]["capability_type"], "signal")
        self.assertEqual(capabilities["SEC labeled"]["tier"], "watchlist")
        self.assertEqual(capabilities["SEC labeled"]["availability"], "integration_pending")
        self.assertFalse(capabilities["SEC labeled"]["enabled"])
        self.assertEqual(capabilities["News labeled"]["source_path"], "service://text-intelligence/news-synthesis-v1")
        self.assertEqual(capabilities["IPO event distance"]["query_plan_id"], "reference.scanner_asof.v1")
        self.assertEqual(capabilities["Split event distance"]["query_plan_id"], "reference.scanner_asof.v1")
        self.assertEqual(capabilities["IPO event distance"]["availability"], "implemented")
        universal = [
            row
            for row in migrated["market_discovery"]["calculation_catalog"]
            if row["execution_scope"] == "universal_ingest"
        ]
        self.assertEqual(len(universal), 6)
        self.assertTrue(all(row["system_required"] for row in universal))
        self.assertTrue(all(row["configuration_policy"] == "locked" for row in universal))
        self.assertTrue(all(row["allowed_scopes"] == ["universal_ingest"] for row in universal))
        self.assertEqual(
            migrated["market_discovery"]["watchlists"][0]["membership_expiry"],
            "end_of_trading_day",
        )
        lifecycle = migrated["strategy"]["profiles"][0]["lifecycle"]
        self.assertEqual(lifecycle["phase_modes"], {
            "initial_entry": "automatic",
            "manage": "automatic",
            "reentry": "automatic",
            "exit": "automatic",
        })
        self.assertNotIn("evaluation_trigger", lifecycle["trading_behavior"])
        self.assertNotIn("adopt_manual_positions", lifecycle["trading_behavior"])
        self.assertNotIn("re_evaluation", lifecycle)
        migrated_intent = migrated["strategy"]["profiles"][0]["lifecycle"][
            "initial_entry"
        ]["order_intent"]
        self.assertNotIn("time_in_force", migrated_intent)
        self.assertNotIn("outside_rth", migrated_intent)
        migrated_oms = migrated["oms"]["profiles"][0]["settings"]
        self.assertEqual(migrated_oms["session_routing"], "smart")
        self.assertNotIn("time_in_force", migrated_oms)
        self.assertNotIn("outside_rth", migrated_oms)
        self.assertTrue(migrated["oms"]["execution_policies"])
        self.assertTrue(migrated["oms"]["protection_profiles"])
        self.assertEqual(
            migrated_intent["protection_profile"],
            migrated_oms["protection_profile_id"],
        )

    def test_market_discovery_rejects_removal_of_required_capability(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            discovery = deepcopy(_default_draft()["market_discovery"])

        discovery["calculation_catalog"] = [
            row
            for row in discovery["calculation_catalog"]
            if row["capability_id"] != "market.last_price"
        ]

        with self.assertRaisesRegex(ValueError, "missing required QMD capabilities"):
            _validate_market_discovery(discovery)

    def test_rule_constants_follow_registered_data_field_domains(self) -> None:
        discovery = deepcopy(_default_draft()["market_discovery"])
        session_output = next(
            output
            for data_field in discovery["data_fields"]
            for output in data_field["outputs"]
            if output["source_id"] == "clock.session_phase"
        )
        rule = {
            "rule_set_id": "session-phase-rule",
            "name": "Session phase rule",
            "description": "Typed categorical validation.",
            "enabled": True,
            "operator": "all",
            "required_score": 1,
            "conditions": [{
                "condition_id": "session-phase",
                "enabled": True,
                "left_source_id": "clock.session_phase",
                "left_field_ref": session_output["field_ref"],
                "comparator": "equals",
                "right_source_id": "",
                "value": "regular",
            }],
        }
        discovery["rule_sets"].append(rule)
        _validate_market_discovery(discovery)
        rule["conditions"][0]["value"] = "open"
        with self.assertRaisesRegex(ValueError, "requires one registered value"):
            _validate_market_discovery(discovery)

    def test_watchlist_defaults_to_end_of_trading_day_expiry(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            discovery = deepcopy(_default_draft()["market_discovery"])

        watchlist = discovery["watchlists"][0]
        self.assertEqual(watchlist["membership_expiry"], "end_of_trading_day")
        watchlist["membership_expiry"] = "time_to_live"
        watchlist["membership_ttl_ms"] = 0
        with self.assertRaisesRegex(ValueError, "membership TTL must be positive"):
            _validate_market_discovery(discovery)

    def test_signal_stream_accepts_core_or_watchlist_candidate_authority(self) -> None:
        discovery = deepcopy(_default_draft()["market_discovery"])
        stream = {
            "signal_stream_id": "test-stream",
            "revision": 1,
            "name": "Test stream",
            "description": "Test materialized signal stream.",
            "enabled": True,
            "source_type": "core_scan",
            "source_id": discovery["core_scan"]["scan_id"],
            "source_scan_id": discovery["core_scan"]["scan_id"],
            "inclusion_rule_sets": [discovery["rule_sets"][0]["rule_set_id"]],
            "inclusion_operator": "all",
            "columns": list(discovery["core_scan"]["columns"]),
            "column_intervals": {},
            "column_aggregations": {},
            "refresh_interval_ms": 1000,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        }
        discovery["signal_streams"].append(stream)
        discovery = _migrate_draft({**_default_draft(), "market_discovery": discovery})["market_discovery"]
        stream = discovery["signal_streams"][0]
        watchlist_id = discovery["watchlists"][0]["watchlist_id"]

        stream.update({"source_type": "watchlist", "source_id": watchlist_id})
        _validate_market_discovery(discovery)

        stream["source_id"] = "missing-watchlist"
        with self.assertRaisesRegex(ValueError, "unknown Watchlist"):
            _validate_market_discovery(discovery)

    def test_market_discovery_materialization_overlays_only_discovery_authority(self) -> None:
        base = _default_draft()
        discovery = deepcopy(base["market_discovery"])
        discovery["signal_streams"] = [
            row
            for row in discovery["signal_streams"]
            if row["signal_stream_id"] != "market-halts"
        ]
        discovery["signal_streams"].append({
            "signal_stream_id": "materialized-stream",
            "revision": 1,
            "name": "Materialized stream",
            "description": "Test materialized signal stream.",
            "enabled": True,
            "source_type": "core_scan",
            "source_id": discovery["core_scan"]["scan_id"],
            "source_scan_id": discovery["core_scan"]["scan_id"],
            "inclusion_rule_sets": [discovery["rule_sets"][0]["rule_set_id"]],
            "inclusion_operator": "all",
            "columns": list(discovery["core_scan"]["columns"]),
            "column_intervals": {},
            "column_aggregations": {},
            "refresh_interval_ms": 1000,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        })
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            try:
                with patch(
                    "src.backend.trading_configuration_service.configuration_base",
                    return_value=deepcopy(base),
                ), patch(
                    "src.backend.trading_configuration_service.trading_journal",
                    return_value=journal,
                ):
                    first_materialization = materialize_market_discovery(discovery)
                    repeated_materialization = materialize_market_discovery(discovery)
                    runtime = market_discovery_runtime_configuration()
            finally:
                journal.close()

        materialized_stream = next(
            row
            for row in runtime["market_discovery"]["signal_streams"]
            if row["signal_stream_id"] == "materialized-stream"
        )
        self.assertEqual(materialized_stream["name"], "Materialized stream")
        self.assertIn(
            "market-halts",
            {
                row["signal_stream_id"]
                for row in runtime["market_discovery"]["signal_streams"]
            },
        )
        runtime_halt_stream = next(
            row
            for row in runtime["market_discovery"]["signal_streams"]
            if row["signal_stream_id"] == "market-halts"
        )
        self.assertEqual(runtime_halt_stream["revision"], 2)
        self.assertIn("Last 5 min", runtime_halt_stream["column_labels"].values())
        self.assertEqual(runtime["strategy"], base["strategy"])
        self.assertEqual(
            first_materialization["materialized_at"],
            repeated_materialization["materialized_at"],
        )

    def test_materialization_ignores_incomplete_unreferenced_rule_sets(self) -> None:
        discovery = deepcopy(_default_draft()["market_discovery"])
        discovery["rule_sets"].append({
            "rule_set_id": "unfinished-draft",
            "name": "Untitled Rule Set",
            "description": "Configuration-only work in progress.",
            "enabled": True,
            "operator": "all",
            "required_score": 1,
            "conditions": [{
                "condition_id": "unfinished-condition",
                "enabled": True,
                "left_source_id": "",
                "left_field_ref": "",
                "comparator": "equals",
                "right_source_id": "",
                "value": 0,
            }],
        })
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            try:
                with patch(
                    "src.backend.trading_configuration_service.trading_journal",
                    return_value=journal,
                ):
                    materialized = materialize_market_discovery(discovery)
                    discovery["core_scan"]["inclusion_rule_sets"] = [
                        "unfinished-draft"
                    ]
                    with self.assertRaisesRegex(ValueError, "requires a left source"):
                        materialize_market_discovery(discovery)
            finally:
                journal.close()

        self.assertEqual(
            materialized["market_discovery"]["rule_sets"][-1]["rule_set_id"],
            "unfinished-draft",
        )

    def test_market_discovery_fields_columns_and_filters_share_registry_authority(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            discovery = deepcopy(_default_draft()["market_discovery"])

        fields = {row["source_id"]: row for row in discovery["field_catalog"]}
        self.assertIn("registered Data Fields", discovery["core_scan"]["description"])
        self.assertEqual(
            next(
                row for row in discovery["watchlists"]
                if row["watchlist_id"] == "price-or-volume-squeeze"
            )["name"],
            "Session Price or Volume Expansion",
        )
        self.assertGreaterEqual(len(fields), 180)
        self.assertEqual(fields["market.last_price"]["column_id"], "last_price")
        self.assertTrue(fields["market.last_price"]["filterable"])
        self.assertEqual(fields["market.last_price"]["presentation_value_type"], "price")
        self.assertIn(
            "greater_or_equal", fields["market.last_price"]["filter_operators"]
        )
        self.assertEqual(
            fields["signal.company_news.score"]["implementation_status"],
            "integration_pending",
        )
        self.assertTrue(
            all(
                (row.get("source_kind") in {"rule_set", "data_field"})
                and row["registry_authority"] in {
                    "application_information_registry",
                    "application_registry",
                    "data_field_registry",
                    "qmd_runtime_catalog",
                    "rule_set_registry",
                }
                for row in discovery["column_catalog"]
            )
        )
        self.assertTrue(all(row.get("presentation_value_type") for row in discovery["column_catalog"]))

        active_core = [
            row for row in discovery["calculation_catalog"]
            if row["execution_scope"] == "core_scan"
            and (row["enabled"] or row["system_required"])
        ]
        self.assertGreaterEqual(len(active_core), 9)
        self.assertTrue(all(row["scanner_columns"] for row in active_core))
        self.assertGreaterEqual(
            sum(len(row["scanner_columns"]) for row in active_core),
            len(active_core),
        )
        columns_by_id = {
            row["column_id"]: row for row in discovery["column_catalog"]
        }
        self.assertTrue(
            all(
                column["name"] == columns_by_id[column["column_id"]]["name"]
                for capability in active_core
                for column in capability["scanner_columns"]
            )
        )
        self.assertIn(
            "Market quality",
            next(row for row in active_core if row["capability_id"] == "market-quality")["scanner_columns"][0]["name"],
        )

        invalid = deepcopy(discovery)
        next(
            row for row in invalid["calculation_catalog"]
            if row["capability_id"] == active_core[0]["capability_id"]
        )["scanner_columns"] = []
        with self.assertRaisesRegex(ValueError, "has no registered scanner column"):
            _validate_market_discovery(invalid)

        custom = deepcopy(discovery["rule_sets"][0])
        custom["rule_set_id"] = "invalid-unregistered-field"
        custom["scope"] = "watchlist"
        custom["conditions"][0]["left_source_id"] = "unknown.field"
        discovery["rule_sets"].append(custom)
        with self.assertRaisesRegex(ValueError, "references unknown field"):
            _validate_market_discovery(discovery)

    def test_schema_v21_moves_legacy_strategy_composition_to_run_plan(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            legacy = _default_draft()
        legacy["schema_version"] = 20
        profile = legacy["strategy"]["profiles"][0]
        profile["composition"] = {
            "watchlist_id": "top-mid-cap-gainers",
            "oms_profile_id": "adaptive-regular",
            "account_keys": ["backtest-default"],
            "allowed_environments": ["backtest"],
            "action_authority": {"default": "confirm"},
        }
        run_plan = legacy["run_plans"]["plans"][0]
        for key in (
            "watchlist_ids",
            "canvas_profile_id",
            "data_plan_ids",
            "source_revision_policy",
        ):
            run_plan.pop(key, None)

        migrated = _migrate_draft(legacy)

        migrated_profile = migrated["strategy"]["profiles"][0]
        migrated_plan = migrated["run_plans"]["plans"][0]
        self.assertNotIn("composition", migrated_profile)
        self.assertEqual(migrated_plan["watchlist_ids"], ["top-mid-cap-gainers"])
        self.assertEqual(migrated_plan["canvas_profile_id"], "current-canvas")
        self.assertEqual(migrated_plan["source_revision_policy"], "require_complete")
        self.assertEqual(
            migrated_plan["data_plan_ids"]["backtest"],
            "market.historical_scanner_materialization.v1",
        )

    def test_market_discovery_validates_selected_calculation_cadences(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            discovery = deepcopy(_default_draft()["market_discovery"])

        capability = next(
            row
            for row in discovery["calculation_catalog"]
            if row["configurable"] and row["enabled"] and row["timeframes"]
        )
        self.assertEqual(capability["selected_timeframes"], capability["timeframes"])

        capability["selected_timeframes"] = ["unsupported-clock"]
        with self.assertRaisesRegex(ValueError, "unsupported calculation cadences"):
            _validate_market_discovery(discovery)

        capability["selected_timeframes"] = []
        with self.assertRaisesRegex(ValueError, "requires at least one calculation cadence"):
            _validate_market_discovery(discovery)

    def test_schema_v14_maps_legacy_disabled_reentry_to_manual_mode(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            legacy = _default_draft()
        legacy["schema_version"] = 12
        lifecycle = legacy["strategy"]["profiles"][0]["lifecycle"]
        lifecycle.pop("phase_modes")
        lifecycle["reentry"]["enabled"] = False

        migrated = _migrate_draft(legacy)

        migrated_lifecycle = migrated["strategy"]["profiles"][0]["lifecycle"]
        self.assertEqual(migrated_lifecycle["phase_modes"]["reentry"], "manual")
        self.assertFalse(migrated_lifecycle["reentry"]["enabled"])

    def test_system_profiles_use_registered_actions_policies_and_rule_sets(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            draft = _default_draft()

        self.assertEqual(draft["schema_version"], CONFIGURATION_SCHEMA_VERSION)
        self.assertTrue(all(rule_set["name"] for rule_set in draft["market_discovery"]["rule_sets"]))
        self.assertTrue(all(rule_set["description"] for rule_set in draft["market_discovery"]["rule_sets"]))
        self.assertTrue(all(rule_set["origin"] == "system" and rule_set["protected"] for rule_set in draft["market_discovery"]["rule_sets"]))
        self.assertTrue(all(not rule_set["editable"] for rule_set in draft["market_discovery"]["rule_sets"]))
        self.assertTrue(
            {"Penny Stocks", "Small Caps", "Mid Caps", "Large Caps"}.isdisjoint(
                {rule_set["name"] for rule_set in draft["market_discovery"]["rule_sets"]}
            )
        )
        canonical_rule_set_ids = {
            rule_set["rule_set_id"]
            for rule_set in draft["market_discovery"]["rule_sets"]
        }
        self.assertTrue(all(
            "rule_set_catalog" not in profile
            and "rule_set_ids" not in profile
            and set(_profile_rule_set_ids(profile["lifecycle"])) <= canonical_rule_set_ids
            for profile in draft["strategy"]["profiles"]
        ))
        self.assertEqual(
            {row["profile_id"] for row in draft["strategy"]["profiles"]},
            {"long-momentum-balanced", "long-momentum-bullish-news"},
        )
        self.assertEqual(len(draft["strategy"]["profile_templates"]), 1)
        self.assertEqual(
            draft["strategy"]["profile_templates"][0]["name"],
            "Long Momentum · Balanced",
        )
        self.assertTrue(all(not profile["editable"] for profile in draft["strategy"]["profiles"]))
        default_profile = next(
            row
            for row in draft["strategy"]["profiles"]
            if row["profile_id"] == draft["strategy"]["default_profile_id"]
        )
        self.assertTrue(default_profile["protected"])
        self.assertEqual(default_profile["publication_status"], "template")
        self.assertNotIn("composition", default_profile)
        default_run_plan = draft["run_plans"]["plans"][0]
        self.assertEqual(default_run_plan["watchlist_ids"], ["squeeze-tradable-candidates"])
        self.assertEqual(default_run_plan["universe_id"], "configured-watch-universe")
        self.assertEqual(default_run_plan["canvas_profile_id"], "current-canvas")
        self.assertEqual(
            default_run_plan["data_plan_ids"]["replay"],
            "market.historical_scanner_materialization.v1",
        )
        self.assertNotIn("capability_catalog", draft["strategy"])
        self.assertTrue(draft["trading_actions"]["definitions"])
        self.assertTrue(draft["trading_actions"]["policies"])
        registered_action_policy_ids = {
            row["policy_id"] for row in draft["trading_actions"]["policies"]
        }
        self.assertTrue(all(
            set(profile["action_policy_ids"]) <= registered_action_policy_ids
            for profile in draft["strategy"]["profiles"]
        ))
        self.assertTrue(draft["strategy"]["input_catalog"])
        self.assertTrue(all(_profile_rule_set_ids(profile["lifecycle"]) for profile in draft["strategy"]["profiles"]))
        self.assertTrue(all(
            profile["lifecycle"]["initial_entry"]["opportunity"]["expression"]["children"]
            for profile in draft["strategy"]["profiles"]
        ))
        lifecycle = default_profile["lifecycle"]
        self.assertEqual(set(lifecycle["phase_modes"].values()), {"automatic"})
        self.assertEqual(lifecycle["trading_behavior"]["side"], "long")
        self.assertNotIn(
            "minimum_score", lifecycle["initial_entry"]["confirmation"]
        )
        self.assertTrue(lifecycle["exit"]["rule_sets"])
        self.assertNotIn("routes", lifecycle["exit"])
        self.assertEqual(
            lifecycle["initial_entry"]["capital_request"]["mode"],
            "mandate_fraction",
        )
        self.assertEqual(lifecycle["initial_entry"]["add_steps"], [])
        self.assertEqual(lifecycle["initial_entry"]["action_id"], "position.enter_long")
        confirmation_rule_ids = configuration_service._expression_rule_set_ids(
            lifecycle["initial_entry"]["confirmation"]["expression"]
        )
        self.assertEqual(
            confirmation_rule_ids,
            {
                "strategy-squeeze-volume-spread-quality",
                "strategy-squeeze-above-vwap-1s",
                "strategy-squeeze-macd-open-1s",
            },
        )
        self.assertEqual(
            lifecycle["reentry"]["require_new_signal_stream_id"],
            "",
        )
        macd_rule = next(
            row
            for row in draft["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "strategy-squeeze-macd-open-1s"
        )
        self.assertEqual(
            {row["condition_id"] for row in macd_rule["conditions"]},
            {
                "squeeze-macd-line-above-signal",
                "squeeze-macd-line-positive",
            },
        )
        squeeze_liquidity_rule = next(
            row
            for row in draft["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "strategy-squeeze-volume-spread-quality"
        )
        squeeze_spread_condition = next(
            row
            for row in squeeze_liquidity_rule["conditions"]
            if row["condition_id"] == "squeeze-spread-quality"
        )
        live_spread_rule = next(
            row
            for row in draft["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "strategy-live-spread-quality"
        )
        self.assertEqual(squeeze_spread_condition["value"], 60.0)
        self.assertEqual(live_spread_rule["conditions"][0]["value"], 60.0)
        self.assertTrue(
            default_profile["parameters"]["momentum_management"][
                "downside_loss_guard"
            ]["enabled"]
        )
        self.assertTrue(lifecycle["reentry"]["after_protective_exit"])
        self.assertFalse(
            default_profile["parameters"]["momentum_management"][
                "failure_to_extend"
            ]["enabled"]
        )
        self.assertFalse(lifecycle["reentry"]["target_replenishment"]["enabled"])
        self.assertEqual(lifecycle["reentry"]["cooldown_ms"], 0)
        self.assertTrue(
            default_profile["parameters"]["protection"]["trailing"]["enabled"]
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["trailing"]["activation_gain_pct"],
            0.0,
        )
        self.assertEqual(default_run_plan["campaign_lifecycle"]["maximum_reentries"], 0)
        self.assertEqual(default_run_plan["campaign_lifecycle"]["reentry_cooldown_ms"], 0)
        self.assertEqual(
            default_profile["parameters"]["momentum_management"]["macd_backstop"],
            {
                "enabled": True,
                "active_after_ms": 0,
                "closed_for_ms": 0,
                "timeframe": "1s",
                "close_condition": "signal_above_line",
            },
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["stop"],
            {
                "method": "ordinal_qualified_support",
                "structure_buffer_bps": 0.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 15.0,
                "minimum_ticker_relative_quality_score": 0.20,
                "minimum_hold_observations": 1,
                "support_level_ordinal": 2,
                "prefer_closer_hybrid": True,
            },
        )
        self.assertNotIn(
            "bearish_choch",
            default_profile["parameters"]["momentum_management"]["downside_loss_guard"],
        )
        self.assertEqual(
            lifecycle["initial_entry"]["order_intent"]["protection_profile"],
            "structural-single-target",
        )
        structural_profile = next(
            row
            for row in draft["oms"]["protection_profiles"]
            if row["profile_id"] == "structural-single-target"
        )
        self.assertEqual(len(structural_profile["slices"]), 1)
        self.assertEqual(
            [row["strategy_profit_target_index"] for row in structural_profile["slices"]],
            [0],
        )
        self.assertEqual(
            structural_profile["slices"][0]["trailing"]["rule_type"],
            "broker_amount",
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["profit_ladder"]["maximum_targets"],
            1,
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["profit_ladder"]["selection_mode"],
            "ordinal_qualified_level",
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["profit_ladder"]["target_level_ordinal"],
            3,
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["profit_ladder"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertEqual(
            default_profile["parameters"]["structural_entry"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertNotIn("minimum_hold_probability", default_profile["parameters"]["structural_entry"])
        self.assertNotIn("minimum_hold_quality_score", default_profile["parameters"]["structural_entry"])
        self.assertEqual(
            default_profile["parameters"]["structural_entry"]["maximum_break_count"],
            100,
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["profit_ladder"]["maximum_break_count"],
            100,
        )
        self.assertEqual(
            default_profile["parameters"]["protection"]["profit_ladder"]["minimum_entry_target_gap_bps"],
            0.0,
        )
        self.assertFalse(
            default_profile["parameters"]["structural_entry"]["require_swing_high_frontier"]
        )
        self.assertFalse(default_profile["parameters"]["profit_pocket"]["enabled"])
        self.assertEqual(default_profile["action_policy_ids"], [])
        self.assertTrue(all(not route["enabled"] for route in lifecycle["exit"]["rule_sets"]))
        self.assertNotIn("confirmed-pullback-add", default_profile["action_policy_ids"])
        self.assertTrue(all(route["action_id"] == "position.exit_long" for route in lifecycle["exit"]["rule_sets"]))
        self.assertNotIn("time_in_force", lifecycle["initial_entry"]["order_intent"])
        self.assertNotIn("outside_rth", lifecycle["initial_entry"]["order_intent"])
        self.assertEqual(
            draft["oms"]["profiles"][0]["settings"]["session_routing"], "smart"
        )
        self.assertNotIn("time_in_force", draft["oms"]["profiles"][0]["settings"])
        self.assertNotIn("outside_rth", draft["oms"]["profiles"][0]["settings"])
        self.assertEqual(len(draft["oms"]["execution_policies"]), 9)
        descriptions = {
            row["policy_id"]: row["description"]
            for row in draft["oms"]["execution_policies"]
        }
        self.assertIn("near-side quote", descriptions["passive"])
        self.assertIn("bid-ask midpoint", descriptions["midpoint"])
        self.assertIn(
            "cancels the unfilled remainder",
            descriptions["cancel_if_not_filled"],
        )
        self.assertTrue(
            all(not value.startswith("System ") for value in descriptions.values())
        )
        self.assertTrue(draft["oms"]["protection_profiles"])
        self.assertEqual(
            lifecycle["initial_entry"]["order_intent"]["protection_profile"],
            "structural-single-target",
        )
        self.assertTrue(lifecycle["reentry"]["rules"]["opportunity"]["expression"]["children"])
        self.assertTrue(lifecycle["exit"]["rule_sets"][1]["rules"]["expression"]["children"])
        self.assertTrue(
            any(
                condition["left_source_id"]
                == "indicator.flow_structure.score"
                for rule_set in draft["market_discovery"]["rule_sets"]
                if rule_set["rule_set_id"] in _profile_rule_set_ids(default_profile["lifecycle"])
                for condition in rule_set["conditions"]
            )
        )
        self.assertNotIn("sizing", default_profile["parameters"])
        self.assertNotIn("maximum_position_quantity", str(default_profile))
        self.assertTrue(draft["run_plans"]["universes"])
        self.assertEqual(
            draft["run_plans"]["plans"][0]["campaign_lifecycle"][
                "protective_exit_authority"
            ],
            "automatic",
        )

    def test_published_release_is_immutable_and_replay_resolves_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            with self._service_patches(journal):
                published = publish_configuration(
                    label="Replay acceptance",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=draft,
                )
                saved_draft = configuration_base()
                oms = draft["oms"]
                oms["profiles"][0]["settings"]["entry_urgency"] = "patient"
                pinned = replay_configuration_snapshot()
                latest = approved_configuration(required=True)
            journal.close()

        self.assertEqual(published["revision_id"], pinned["revision_id"])
        self.assertEqual(
            latest["payload"]["oms"]["profiles"][0]["settings"]["entry_urgency"],
            "urgent",
        )
        self.assertEqual(pinned["payload"]["canvas"]["revision"], "canvas-1")
        self.assertEqual(pinned["run_plan_id"], "balanced-replay")
        self.assertEqual(
            pinned["payload"]["strategy"]["profile_id"],
            "long-momentum-balanced",
        )
        self.assertTrue(published["payload"]["run_plans"]["plans"][0]["compiled"])
        dependencies = published["payload"]["run_plans"]["plans"][0][
            "observation_dependencies"
        ]
        qmd_dependencies = {
            row["capability_key"]: row
            for row in dependencies
            if row["producer"] == "qmd"
        }
        self.assertTrue(
            {
                "flow_structure_composite",
                "flow_price_divergence",
                "liquidity_dislocation",
                "momentum_core",
                "price_volume_expansion",
                "qmd_generic_structure",
                "vwap_transition",
            }.issubset(qmd_dependencies),
        )
        self.assertEqual(qmd_dependencies["momentum_core"]["input_keys"], ["macd", "vwap"])
        self.assertNotIn("indicator.structure.bullish_choch", qmd_dependencies)
        self.assertEqual(
            {row["capability_key"] for row in saved_draft["run_plans"]["plans"][0]["observation_dependencies"]},
            {row["capability_key"] for row in published["payload"]["run_plans"]["plans"][0]["observation_dependencies"]},
        )
        self.assertEqual(
            saved_draft["run_plans"]["plans"][0]["action_policy_rule_set_ids"],
            [],
        )

    def test_test_candidate_is_immutable_idempotent_and_does_not_approve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            with self._service_patches(journal):
                first = create_test_candidate(
                    label="Long momentum squeeze baseline",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=draft,
                    run_plan_id="balanced-replay",
                )
                second = create_test_candidate(
                    label="Duplicate content",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=first["payload"],
                    run_plan_id="balanced-replay",
                )
                pinned = backtest_configuration_snapshot(
                    "balanced-replay", candidate_id=first["candidate_id"]
                )
                latest = configuration_candidate(required=True)
                rows = configuration_candidates()
                approved = approved_configuration()
            journal.close()

        self.assertEqual(first["candidate_id"], second["candidate_id"])
        self.assertEqual(latest["candidate_id"], first["candidate_id"])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(approved)
        self.assertEqual(pinned["revision_id"], first["candidate_id"])
        self.assertEqual(pinned["release_state"], "test_candidate")
        self.assertEqual(pinned["mode"], "backtest")

    def test_candidate_release_canonicalizes_persisted_profile_before_immutability_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            persisted = self._draft()
            profile = persisted["strategy"]["profiles"][0]
            for stage in ("confirmation", "opportunity"):
                profile["lifecycle"]["initial_entry"][stage].pop("groups", None)
                profile["lifecycle"]["initial_entry"][stage].pop("operator", None)
            with self._service_patches(journal), patch(
                "src.backend.trading_configuration_service.configuration_base",
                return_value=deepcopy(persisted),
            ):
                candidate = create_test_candidate(
                    label="Canonical migration",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=persisted,
                    run_plan_id="balanced-replay",
                )
            journal.close()

        released_profile = next(
            row
            for row in candidate["payload"]["strategy"]["profiles"]
            if row["profile_id"] == profile["profile_id"]
        )
        self.assertEqual(
            released_profile["definition_revision"],
            long_momentum_strategy_definition()["revision"],
        )

    def test_squeeze_defaults_separate_backtest_and_real_capital(self) -> None:
        draft = self._draft()
        policies = {row["policy_id"]: row for row in draft["portfolio"]["policies"]}
        replay = policies["default"]
        real = policies["long-momentum-real-80"]
        plan = next(row for row in draft["run_plans"]["plans"] if row["run_plan_id"] == "balanced-replay")
        profile = next(row for row in draft["strategy"]["profiles"] if row["profile_id"] == "long-momentum-balanced")
        mandates = {
            row["mandate_id"]: row for row in draft["portfolio"]["mandates"]
        }
        very_urgent_execution = next(
            row
            for row in draft["oms"]["execution_policies"]
            if row["policy_id"] == "adaptive_very_urgent"
        )

        self.assertEqual(replay["eligible_equity_fraction"], 1.0)
        self.assertEqual(real["eligible_equity_fraction"], 0.8)
        self.assertEqual(replay["maximum_position_fraction"], 1.0)
        self.assertEqual(replay["maximum_ticker_fraction"], 1.0)
        self.assertEqual(replay["maximum_protection_slices"], 5)
        self.assertEqual(real["maximum_position_fraction"], 0.8)
        self.assertEqual(real["maximum_ticker_fraction"], 0.8)
        self.assertEqual(real["maximum_protection_slices"], 5)
        self.assertEqual(replay["maximum_planned_risk_fraction"], 0.08)
        self.assertEqual(replay["maximum_open_risk_fraction"], 0.08)
        self.assertEqual(replay["maximum_buying_power_utilization"], 0.995)
        self.assertEqual(real["maximum_planned_risk_fraction"], 0.08)
        self.assertEqual(real["maximum_open_risk_fraction"], 0.08)
        self.assertTrue(
            all(
                row["maximum_planned_risk_fraction"] == 0.08
                for mandate_id, row in mandates.items()
                if mandate_id.startswith("balanced-")
                or mandate_id.startswith("long-momentum-squeeze-")
            )
        )
        self.assertTrue(
            all(
                row["maximum_planned_risk_fraction"] == 0.0025
                for mandate_id, row in mandates.items()
                if mandate_id.startswith("long-momentum-news-")
            )
        )
        self.assertEqual(replay["maximum_open_positions"], 3)
        self.assertEqual(plan["watchlist_ids"], ["squeeze-tradable-candidates"])
        self.assertEqual(plan["universe_id"], "configured-watch-universe")
        self.assertEqual(
            plan["signal_stream_ids"],
            ["price-squeeze-early"],
        )
        self.assertEqual(plan["activation"]["watchlist_policy"], "not_required")
        self.assertEqual(
            profile["lifecycle"]["initial_entry"]["opportunity"]["expression"][
                "children"
            ][0]["rule_set_id"],
            "strategy-squeeze-unified-resistance-break",
        )
        swing_rule = next(
            row
            for row in draft["market_discovery"]["rule_sets"]
            if row["rule_set_id"] == "strategy-squeeze-unified-resistance-break"
        )
        swing_condition = swing_rule["conditions"][0]
        self.assertEqual(swing_condition["left_source_id"], "market.last_price")
        self.assertEqual(
            swing_condition["right_source_id"],
            "indicator.structure.unified_resistance_upper",
        )
        self.assertEqual(swing_condition["value"], 0.0)
        self.assertIn("Unified Structural Level Book", profile["description"])
        self.assertEqual(plan["campaign_lifecycle"]["maximum_reentries"], 0)
        self.assertEqual(plan["campaign_lifecycle"]["reentry_cooldown_ms"], 0)
        self.assertTrue(profile["lifecycle"]["reentry"]["unlimited_attempts"])
        self.assertEqual(profile["lifecycle"]["reentry"]["maximum_attempts"], 0)
        self.assertFalse(
            profile["lifecycle"]["reentry"]["require_new_confirmation"]
        )
        self.assertEqual(profile["lifecycle"]["reentry"]["pullback_reclaim"], {
            "enabled": False,
            "minimum_pullback_atr_multiple": 0.50,
            "minimum_pullback_bps": 25.0,
        })
        self.assertEqual(profile["lifecycle"]["trading_behavior"]["eligible_sessions"], ["premarket"])
        self.assertEqual(profile["lifecycle"]["trading_behavior"]["entry_cutoff_time"], "09:29:59")
        self.assertEqual(profile["lifecycle"]["trading_behavior"]["flatten_time"], "09:29:59")
        self.assertEqual(profile["lifecycle"]["initial_entry"]["capital_request"], {
            "mode": "all_available",
            "value": 1.0,
            "maximum_quantity": 10_000,
            "allow_replacement": False,
        })
        self.assertEqual(
            profile["lifecycle"]["reentry"]["capital_request"],
            profile["lifecycle"]["initial_entry"]["capital_request"],
        )
        self.assertEqual(
            profile["lifecycle"]["initial_entry"]["order_intent"]["deadline_ms"],
            300,
        )
        self.assertEqual(
            profile["lifecycle"]["initial_entry"]["order_intent"]["execution_policy"],
            "adaptive_very_urgent",
        )
        self.assertEqual(
            profile["lifecycle"]["reentry"]["order_intent"]["deadline_ms"],
            300,
        )
        self.assertEqual(
            profile["lifecycle"]["reentry"]["order_intent"]["execution_policy"],
            "adaptive_very_urgent",
        )
        self.assertEqual(
            very_urgent_execution["maximum_price_discretion_ticks"],
            4,
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["minimum_level_age_ms"],
            0,
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["minimum_hold_observations"],
            1,
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["selection_mode"],
            "event_price_top_n_below_session_high",
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["maximum_entry_levels"],
            3,
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["entry_tranche_count"],
            3,
        )
        self.assertEqual(
            profile["parameters"]["protection"]["profit_ladder"][
                "incomplete_target_exit"
            ]["extended_hours_execution_policy"],
            "adaptive_urgent",
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["maximum_break_probability"],
            1.0,
        )
        self.assertEqual(
            profile["parameters"]["structural_entry"]["maximum_breakout_extension_bps"],
            500.0,
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["minimum_current_trade_rate_10s"],
            5.0,
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["minimum_current_trade_rate_60s"],
            5.0,
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["minimum_vwap_extension_bps"],
            0.0,
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["minimum_initial_vwap_extension_bps"],
            0.0,
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["minimum_reentry_vwap_extension_bps"],
            0.0,
        )
        self.assertNotIn(
            "maximum_vwap_extension_bps",
            profile["parameters"]["liquidity_admission"],
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["maximum_admission_spread_bps"],
            60.0,
        )
        self.assertEqual(
            profile["parameters"]["liquidity_admission"]["maximum_current_spread_bps"],
            100.0,
        )
        self.assertEqual(
            profile["parameters"]["entry_momentum_confirmation"],
            {
                "enabled": False,
                "timeframe": "1s",
                "histogram_lookback_ms": 5_000,
                "minimum_histogram_increase": 0.0,
                "minimum_histogram_increase_bps": 0.25,
            },
        )

    def test_approved_canvas_projection_exposes_only_published_profile_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            canvas_profile = {
                "version": 3,
                "canvases": [{"id": "main", "label": "Main"}],
                "workspaceStates": {"main": {"openIds": ["chart"]}},
            }
            with self._service_patches(journal):
                published = publish_configuration(
                    label="Canvas runtime default",
                    canvas_revision="canvas-approved-1",
                    canvas_profile=canvas_profile,
                    configuration=draft,
                )
                projection = approved_canvas_profile()
            journal.close()

        self.assertTrue(projection["available"])
        self.assertEqual(projection["revision_id"], published["revision_id"])
        self.assertEqual(projection["canvas_revision"], "canvas-approved-1")
        self.assertEqual(projection["profile"], canvas_profile)
        self.assertNotIn("accounts", projection)

    def test_publish_is_idempotent_for_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            with self._service_patches(journal):
                first = publish_configuration(
                    label="First label",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=draft,
                )
                second = publish_configuration(
                    label="Second label",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=first["payload"],
                )
            journal.close()

        self.assertEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(second["label"], "First label")

    def test_published_strategy_requires_a_new_draft_identity_for_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            with self._service_patches(journal):
                published = publish_configuration(
                    label="Immutable strategy",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=draft,
                )
                current = deepcopy(published["payload"])
                immutable = deepcopy(current)
                immutable["strategy"]["profiles"][0]["name"] = "Changed in place"
                with self.assertRaisesRegex(ValueError, "immutable"):
                    publish_configuration(
                        label="Invalid mutation",
                        canvas_revision="canvas-2",
                        canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                        configuration=immutable,
                    )

                source = current["strategy"]["profiles"][0]
                clone = deepcopy(source)
                clone.update({
                    "profile_id": "long-momentum-balanced-copy",
                    "name": "Long Momentum copy",
                    "origin": "user",
                    "protected": False,
                    "editable": True,
                    "publication_status": "draft",
                    "derived_from_profile_id": source["profile_id"],
                })
                current["strategy"]["profiles"].append(clone)
                saved = publish_configuration(
                    label="Cloned strategy",
                    canvas_revision="canvas-2",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    configuration=current,
                    strategy_profile_id=clone["profile_id"],
                )
            journal.close()

        self.assertEqual(
            published["payload"]["strategy"]["profiles"][0]["publication_status"],
            "published",
        )
        self.assertEqual(saved["payload"]["strategy"]["profiles"][-1]["derived_from_profile_id"], source["profile_id"])

    def test_unpublished_session_configuration_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            original = self._draft()
            session_configuration = deepcopy(original)
            session_configuration["strategy"]["profiles"][0]["name"] = "Session-only name"
            with self._service_patches(journal):
                reloaded = configuration_base()
            journal.close()

        self.assertNotEqual(reloaded["strategy"]["profiles"][0]["name"], "Session-only name")

    def test_runtime_projection_uses_account_mandate_and_action_policy_settings(self) -> None:
        draft = self._draft()
        draft["run_plans"]["plans"][0]["runtime_assignments"] = [{
            "assignment_id": "configured-aapl",
            "account_key": "replay",
            "ticker": "AAPL",
            "conid": 265598,
            "status": "watching",
            "permissions": {},
            "parameters": {},
        }]
        draft["run_plans"]["universes"][0]["symbols"] = ["AAPL"]
        cloned_execution = deepcopy(next(
            row for row in draft["oms"]["execution_policies"]
            if row["policy_id"] == "adaptive_urgent"
        ))
        cloned_execution.update({"policy_id": "urgent-copy", "revision": 2})
        draft["oms"]["execution_policies"].append(cloned_execution)
        draft["portfolio"]["mandates"][0]["maximum_cash_fraction"] = 0.3
        profile = draft["strategy"]["profiles"][0]
        profile["lifecycle"]["phase_modes"].update({
            "manage": "manual",
            "reentry": "manual",
        })
        pocket = next(
            row for row in draft["trading_actions"]["policies"]
            if row["policy_id"] == "profit-pocket"
        )
        pocket["quantity"]["value"] = 0.4

        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            runtime = resolve_runtime_configuration(draft, mode="replay")

        self.assertEqual(runtime["accounts"]["bindings"][0]["strategy_allocation"], 0.3)
        self.assertFalse(runtime["strategy"]["parameters"]["profit_pocket"]["enabled"])
        self.assertEqual(runtime["strategy"]["parameters"]["profit_pocket"]["quantity_fraction"], 1.0)
        self.assertTrue(runtime["strategy"]["action_definitions"])
        self.assertEqual(runtime["strategy"]["action_policies"], [])
        self.assertEqual(runtime["run_plan"]["run_plan_id"], "balanced-replay")
        resolved = runtime["assignments"][0]["resolved_parameters"]
        self.assertEqual(
            resolved["execution_policy_catalog"]["adaptive_urgent"]["policy_id"],
            "adaptive_urgent",
        )
        self.assertEqual(
            resolved["execution_policy_catalog"]["urgent-copy"]["policy_id"],
            "urgent-copy",
        )
        self.assertEqual(
            resolved["protection_profile_catalog"]["hybrid-single"]["revision"],
            1,
        )
        self.assertEqual(resolved["phase_policy"]["manage"]["mode"], "manual")
        self.assertEqual(resolved["phase_policy"]["reentry"]["mode"], "manual")
        self.assertFalse(resolved["reentry"]["enabled"])
        self.assertEqual(resolved["protection"]["stop"]["method"], "ordinal_qualified_support")
        self.assertEqual(resolved["protection"]["stop"]["maximum_risk_pct"], 15.0)
        self.assertEqual(
            resolved["protection"]["stop"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertTrue(resolved["protection"]["trailing"]["enabled"])

    def test_paper_release_requires_exact_broker_binding_and_mode_deployment(self) -> None:
        draft = self._draft()
        binding = draft["accounts"]["bindings"][0]
        binding["modes"] = ["paper"]
        binding["account_class"] = "margin"
        binding["source_account_id"] = ""
        binding["source_account_env"] = "TEST_PAPER_ACCOUNT_ID"
        deployment = draft["run_plans"]["plans"][0]
        deployment["allowed_environments"] = ["paper"]

        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "exact broker account id"):
            _validate_draft(draft)

        with patch.dict(os.environ, {"TEST_PAPER_ACCOUNT_ID": "DU1234567"}), patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            _validate_draft(draft)

        binding["source_account_id"] = "DU-SHOULD-NOT-BE-STORED"
        with self.assertRaisesRegex(ValueError, "server-side"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_public_effective_configuration_never_resolves_broker_id(self) -> None:
        draft = self._draft()
        binding = draft["accounts"]["bindings"][0]
        binding.update(
            {
                "modes": ["paper"],
                "source_account_env": "TEST_PAPER_ACCOUNT_ID",
                "source_account_id": "",
            }
        )
        draft["run_plans"]["plans"][0]["allowed_environments"] = ["paper"]
        with patch.dict(os.environ, {"TEST_PAPER_ACCOUNT_ID": "DU-SECRET"}), patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            payload = effective_configuration_snapshot(
                mode="paper",
                configuration=draft,
            )
            internal = resolve_runtime_configuration(draft, mode="paper")

        runtime_binding = payload["runtimes"][0]["accounts"]["bindings"][0]
        self.assertEqual(runtime_binding["source_account_env"], "TEST_PAPER_ACCOUNT_ID")
        self.assertEqual(runtime_binding["source_account_id"], "")
        self.assertEqual(
            internal["accounts"]["bindings"][0]["source_account_id"],
            "DU-SECRET",
        )

    def test_public_revision_scrubs_legacy_broker_id(self) -> None:
        revision = {
            "payload": {
                "accounts": {
                    "bindings": [
                        {
                            "modes": ["live"],
                            "source_account_env": "IBKR_CASH_ACCOUNT_ID",
                            "source_account_id": "U-SECRET",
                        }
                    ]
                }
            }
        }

        public = public_configuration_revision(revision)

        self.assertEqual(
            public["payload"]["accounts"]["bindings"][0]["source_account_id"],
            "",
        )
        self.assertEqual(
            revision["payload"]["accounts"]["bindings"][0]["source_account_id"],
            "U-SECRET",
        )

    def test_approved_configuration_route_scrubs_legacy_broker_id(self) -> None:
        from src.backend import app as backend_app

        revision = {
            "payload": {
                "accounts": {
                    "bindings": [
                        {
                            "modes": ["paper"],
                            "source_account_env": "IBKR_PAPER_ACCOUNT_ID",
                            "source_account_id": "DU-SECRET",
                        }
                    ]
                }
            }
        }
        with patch.object(
            backend_app,
            "approved_configuration",
            return_value=revision,
        ):
            payload = backend_app.trading_configuration_approved()

        self.assertEqual(
            payload["approved"]["payload"]["accounts"]["bindings"][0][
                "source_account_id"
            ],
            "",
        )

    def test_runtime_resolves_every_eligible_run_plan_by_stable_identity(self) -> None:
        draft = self._draft()
        second = {
            **draft["run_plans"]["plans"][0],
            "run_plan_id": "second-replay",
            "name": "Second Replay",
            "runtime_assignments": [
                {
                    "assignment_id": "second-msft",
                    "account_key": "replay",
                    "ticker": "MSFT",
                    "conid": 272093,
                    "status": "watching",
                    "permissions": {},
                    "parameters": {},
                }
            ],
        }
        draft["run_plans"]["plans"].append(second)
        draft["portfolio"]["mandates"][0]["assignment_mode"] = "replicated"
        draft["portfolio"]["mandates"].append(
            {
                **draft["portfolio"]["mandates"][0],
                "mandate_id": "second-replay",
                "run_plan_id": "second-replay",
                "assignment_mode": "replicated",
            }
        )
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            runtimes = resolve_runtime_configurations(draft, mode="replay")

        self.assertEqual(
            [row["run_plan"]["run_plan_id"] for row in runtimes],
            ["balanced-replay", "second-replay"],
        )
        assignment = runtimes[1]["assignments"][0]
        self.assertEqual(assignment["campaign_id"], "second-replay:MSFT:long")
        self.assertEqual(assignment["profile_id"], "long-momentum-balanced")
        self.assertIn("entry_rules", assignment["resolved_parameters"])

    def test_incomplete_session_configuration_cannot_be_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            draft["portfolio"]["mandates"] = []
            with self._service_patches(journal):
                with self.assertRaisesRegex(ValueError, "requires at least one account mandate"):
                    publish_configuration(
                        label="Incomplete release",
                        canvas_revision="canvas-1",
                        canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                        configuration=draft,
                    )
            journal.close()

    def test_unknown_strategy_input_cannot_enter_runtime_projection(self) -> None:
        draft = self._draft()
        profile = draft["strategy"]["profiles"][0]
        opportunity_id = profile["lifecycle"]["initial_entry"]["opportunity"]["expression"]["children"][0]["rule_set_id"]
        condition = next(row for row in draft["market_discovery"]["rule_sets"] if row["rule_set_id"] == opportunity_id)["conditions"][0]
        condition["left_field_ref"] = "data.qmd.invalid@1:indicator.unregistered.value"
        condition["left_source_id"] = "indicator.unregistered.value"

        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "unknown Data Field output"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_protected_default_profile_cannot_be_removed_or_weakened(self) -> None:
        draft = self._draft()
        default_id = draft["strategy"]["default_profile_id"]
        replacement = deepcopy(draft["strategy"]["profiles"][0])
        replacement.update(
            {
                "profile_id": "user-replacement",
                "name": "User replacement",
                "origin": "user",
                "protected": False,
            }
        )
        draft["strategy"]["profiles"].append(replacement)
        draft["strategy"]["profiles"] = [
            row
            for row in draft["strategy"]["profiles"]
            if row["profile_id"] != default_id
        ]
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "protected default"):
            _validate_draft(draft, require_runtime_ready=False)

        draft = self._draft()
        default_profile = next(
            row
            for row in draft["strategy"]["profiles"]
            if row["profile_id"] == default_id
        )
        default_profile["protected"] = False
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "must remain protected"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_unpublished_strategy_clone_remains_outside_backend_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            clone = deepcopy(draft["strategy"]["profiles"][0])
            clone.update({
                "profile_id": "long-momentum-clone",
                "name": "Long Momentum clone",
                "origin": "user",
                "protected": False,
            })
            strategy = deepcopy(draft["strategy"])
            strategy["profiles"].append(clone)
            with self._service_patches(journal):
                reloaded = configuration_base()
            journal.close()

        self.assertNotIn("long-momentum-clone", {row["profile_id"] for row in reloaded["strategy"]["profiles"]})

    def test_strategy_definition_rejects_non_single_side_profile(self) -> None:
        draft = self._draft()
        draft["strategy"]["profiles"][0]["lifecycle"]["trading_behavior"]["side"] = "both"
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "exactly one side"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_external_universe_source_is_draftable_but_not_publishable(self) -> None:
        draft = self._draft()
        draft["run_plans"]["universes"][0].update(
            {"source": "scanner_view", "scanner_view_id": "momentum-open"}
        )
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            _validate_draft(draft, require_runtime_ready=False)
            with self.assertRaisesRegex(ValueError, "runtime resolver"):
                _validate_draft(draft)

    def test_automatic_initial_entry_requires_identity_bound_universe(self) -> None:
        draft = self._draft()
        draft["run_plans"]["universes"][0]["symbols"] = ["NVDA"]
        draft["run_plans"]["plans"][0]["action_authority"]["initial_entry"] = "automatic"
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "identity-bound assignments for: NVDA"):
            _validate_draft(draft)

    def test_safety_can_be_disabled_only_for_historical_environments(self) -> None:
        draft = self._draft()
        safety = draft["run_plans"]["plans"][0]["safety_supervisor"]["enabled_by_environment"]
        safety["replay"] = False
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            _validate_draft(draft, require_runtime_ready=False)
            safety["paper"] = False
            with self.assertRaisesRegex(ValueError, "cannot be disabled"):
                _validate_draft(draft, require_runtime_ready=False)

    def test_account_mandate_caps_run_plan_action_authority(self) -> None:
        draft = self._draft()
        draft["portfolio"]["mandates"][0]["maximum_action_authority"] = "confirm"
        draft["run_plans"]["plans"][0]["action_authority"]["initial_entry"] = "automatic"
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "action authority cap"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_account_mandate_cap_does_not_block_exposure_reducing_exit(self) -> None:
        draft = self._draft()
        draft["portfolio"]["mandates"][0]["maximum_action_authority"] = "confirm"
        draft["run_plans"]["plans"][0]["action_authority"].update({
            "initial_entry": "confirm",
            "add": "confirm",
            "reentry": "confirm",
            "strategic_exit": "automatic",
        })
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            _validate_draft(draft, require_runtime_ready=False)

    def test_schema_v9_migration_removes_generic_priorities(self) -> None:
        raw = self._draft()
        plan = raw["run_plans"]["plans"][0]
        plan["selection_priority"] = 99
        raw["portfolio"]["mandates"][0]["priority"] = 88
        raw["strategy"]["profiles"][0]["lifecycle"]["initial_entry"]["capital_request"]["priority"] = 77
        migrated = _migrate_draft(raw)
        self.assertNotIn("selection_priority", migrated["run_plans"]["plans"][0])
        self.assertNotIn("priority", migrated["portfolio"]["mandates"][0])
        self.assertNotIn("priority", migrated["strategy"]["profiles"][0]["lifecycle"]["initial_entry"]["capital_request"])

    def test_schema_v22_migrates_embedded_rule_catalog_to_references(self) -> None:
        legacy = self._draft()
        legacy["schema_version"] = 22
        canonical_rules = deepcopy(legacy["market_discovery"]["rule_sets"])
        for profile in legacy["strategy"]["profiles"]:
            profile["rule_set_catalog"] = deepcopy(canonical_rules)
            profile.pop("rule_set_ids", None)

        migrated = _migrate_draft(legacy)

        self.assertEqual(migrated["schema_version"], CONFIGURATION_SCHEMA_VERSION)
        canonical_ids = {
            rule_set["rule_set_id"]
            for rule_set in migrated["market_discovery"]["rule_sets"]
        }
        for profile in migrated["strategy"]["profiles"]:
            self.assertNotIn("rule_set_catalog", profile)
            self.assertNotIn("rule_set_ids", profile)
            self.assertLessEqual(set(_profile_rule_set_ids(profile["lifecycle"])), canonical_ids)

    def test_strategy_profile_rule_references_are_derived_from_lifecycle(self) -> None:
        draft = self._draft()
        unknown = deepcopy(draft)
        unknown["strategy"]["profiles"][0]["lifecycle"]["initial_entry"]["opportunity"] = {
            "expression": {"kind": "rule_set", "rule_set_id": "missing-rule"}
        }
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "references unknown rule sets"):
            _validate_draft(unknown, require_runtime_ready=False)

    def test_strategy_lifecycle_rejects_unregistered_trading_action(self) -> None:
        draft = self._draft()
        draft["strategy"]["profiles"][0]["lifecycle"]["initial_entry"]["action_id"] = "position.unknown"
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "unknown Trading Action"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_action_policy_rejects_quantity_not_supported_by_action(self) -> None:
        draft = self._draft()
        policy = next(
            row for row in draft["trading_actions"]["policies"]
            if row["policy_id"] == "confirmed-pullback-add"
        )
        policy["quantity"]["mode"] = "notional"
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "quantity mode unsupported"):
            _validate_draft(draft, require_runtime_ready=False)

    def test_action_policy_rule_sets_compile_into_qmd_demand(self) -> None:
        draft = self._draft()
        policy = next(
            row for row in draft["trading_actions"]["policies"]
            if row["policy_id"] == "confirmed-pullback-add"
        )
        rule_set_id = policy["trigger"]["rule_set_ids"][0]
        profile = draft["strategy"]["profiles"][0]
        profile["action_policy_ids"].append("confirmed-pullback-add")
        profile["lifecycle"]["initial_entry"]["add_steps"] = [{
            "action_id": "position.add_long",
            "action_policy_id": "confirmed-pullback-add",
            "rules": {"expression": {"kind": "rule_set", "rule_set_id": rule_set_id}},
            "capital_request": {"mode": "mandate_fraction", "value": 0.25},
            "order_intent": {"protection_profile": "hybrid-single"},
        }]
        compiled = deepcopy(draft)
        _compile_run_plans(compiled, canvas_profile_id="current-canvas")
        self.assertIn(rule_set_id, compiled["run_plans"]["plans"][0]["action_policy_rule_set_ids"])
        dependencies = compiled["run_plans"]["plans"][0]["observation_dependencies"]
        bullish_structure = next(
            row for row in dependencies
            if row["capability_key"] == "indicator.structure.bullish_choch"
        )
        self.assertEqual(bullish_structure["producer"], "qmd")
        self.assertIn("rule_set", bullish_structure["input_kinds"])
        self.assertIn("indicator.structure.bullish_choch", bullish_structure["input_keys"])
        self.assertEqual(bullish_structure["timeframes"], ["1s"])
        self.assertTrue(bullish_structure["required"])
        self.assertEqual(bullish_structure["capability_revision"], 1)
        self.assertEqual(bullish_structure["warm_up"]["status"], "not_required")

    def test_schema_v24_capabilities_migrate_to_action_policy_references(self) -> None:
        legacy = self._draft()
        legacy["schema_version"] = 24
        profile = legacy["strategy"]["profiles"][0]
        profile.pop("action_policy_ids", None)
        profile["capabilities"] = [
            {"capability_id": "profit-pocket", "enabled": True, "settings": {}},
            {"capability_id": "confirmed-pullback-add", "enabled": False, "settings": {}},
        ]
        migrated = _migrate_draft(legacy)
        migrated_profile = migrated["strategy"]["profiles"][0]
        self.assertEqual(migrated["schema_version"], CONFIGURATION_SCHEMA_VERSION)
        self.assertEqual(migrated_profile["action_policy_ids"], [])
        self.assertFalse(migrated_profile["parameters"]["profit_pocket"]["enabled"])
        self.assertNotIn("capabilities", migrated_profile)

    def test_schema_v46_migrates_event_native_entry_and_relative_quality(self) -> None:
        legacy = self._draft()
        legacy["schema_version"] = 46
        profile = legacy["strategy"]["profiles"][0]
        profile["action_policy_ids"] = ["profit-pocket"]
        profile["parameters"]["profit_pocket"]["enabled"] = True
        profile["parameters"]["entry_candle_confirmation"]["enabled"] = True
        profile["lifecycle"]["initial_entry"]["order_intent"]["deadline_ms"] = 5_000
        profile["lifecycle"]["reentry"]["order_intent"]["deadline_ms"] = 5_000

        migrated = _migrate_draft(legacy)

        migrated_profile = migrated["strategy"]["profiles"][0]
        self.assertEqual(migrated_profile["definition_revision"], STRATEGY_REVISION)
        self.assertEqual(migrated_profile["action_policy_ids"], [])
        self.assertFalse(migrated_profile["parameters"]["profit_pocket"]["enabled"])
        self.assertFalse(migrated_profile["parameters"]["entry_candle_confirmation"]["enabled"])
        self.assertEqual(
            migrated_profile["parameters"]["structural_entry"]["selection_mode"],
            "event_price_top_n_below_session_high",
        )
        self.assertEqual(
            migrated_profile["parameters"]["structural_entry"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertEqual(
            migrated_profile["parameters"]["protection"]["stop"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertEqual(
            migrated_profile["parameters"]["protection"]["profit_ladder"]["minimum_ticker_relative_quality_score"],
            0.20,
        )
        self.assertTrue(
            migrated_profile["parameters"]["structural_entry"]["strict_ticker_relative_quality_gate"]
        )
        self.assertTrue(
            migrated_profile["parameters"]["protection"]["stop"]["strict_ticker_relative_quality_gate"]
        )
        self.assertTrue(
            migrated_profile["parameters"]["protection"]["profit_ladder"]["strict_ticker_relative_quality_gate"]
        )
        self.assertEqual(migrated_profile["lifecycle"]["initial_entry"]["order_intent"]["deadline_ms"], 300)
        self.assertEqual(migrated_profile["lifecycle"]["reentry"]["order_intent"]["deadline_ms"], 300)

    def _draft(self) -> dict:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            return _default_draft()

    @staticmethod
    def _service_patches(journal: TradingJournal):
        class PatchContext:
            def __enter__(self):
                self.journal_patch = patch(
                    "src.backend.trading_configuration_service.trading_journal",
                    return_value=journal,
                )
                self.definition_patch = patch(
                    "src.backend.trading_configuration_service.get_strategy_definition",
                    return_value=long_momentum_strategy_definition(),
                )
                self.assignment_patch = patch(
                    "src.backend.trading_configuration_service.list_strategy_assignments",
                    return_value=[],
                )
                self.journal_patch.start()
                self.definition_patch.start()
                self.assignment_patch.start()
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.assignment_patch.stop()
                self.definition_patch.stop()
                self.journal_patch.stop()

        return PatchContext()


if __name__ == "__main__":
    unittest.main()
