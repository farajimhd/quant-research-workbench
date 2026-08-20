from __future__ import annotations

import tempfile
import unittest
import os
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from src.backend.trading_configuration_service import (
    CONFIGURATION_SCHEMA_VERSION,
    _default_draft,
    _compiled_observation_dependencies,
    _compile_run_plans,
    _migrate_draft,
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
    configuration_base,
    effective_configuration_snapshot,
    market_discovery_runtime_configuration,
    market_discovery_presentation_configuration,
    materialize_market_discovery,
    publish_configuration,
    public_configuration_revision,
    replay_configuration_snapshot,
    resolve_runtime_configuration,
    resolve_runtime_configurations,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import long_momentum_strategy_definition


class TradingConfigurationServiceTests(unittest.TestCase):
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
        self.assertIn("market_is_halted", halt_stream["columns"])
        self.assertEqual(halt_stream["trigger_policy"], "false_to_true")
        self.assertEqual(halt_stream["rearm_policy"], "after_false")

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
            side_effect=lambda mode: {"mode": mode},
        ) as snapshot:
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
        self.assertEqual(default_run_plan["watchlist_ids"], ["core-candidates"])
        self.assertEqual(default_run_plan["canvas_profile_id"], "current-canvas")
        self.assertEqual(
            default_run_plan["data_plan_ids"]["replay"],
            "market.historical_scanner_materialization.v1",
        )
        self.assertNotIn("capability_catalog", draft["strategy"])
        self.assertTrue(draft["trading_actions"]["definitions"])
        self.assertTrue(draft["trading_actions"]["policies"])
        self.assertTrue(all(profile["action_policy_ids"] for profile in draft["strategy"]["profiles"]))
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
            "hybrid-single",
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
        self.assertIn("rule_set", qmd_dependencies["indicator.structure.bullish_choch"]["input_kinds"])
        self.assertEqual(
            {row["capability_key"] for row in saved_draft["run_plans"]["plans"][0]["observation_dependencies"]},
            {row["capability_key"] for row in published["payload"]["run_plans"]["plans"][0]["observation_dependencies"]},
        )
        self.assertEqual(
            saved_draft["run_plans"]["plans"][0]["action_policy_rule_set_ids"],
            [],
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
        self.assertEqual(runtime["strategy"]["parameters"]["profit_pocket"]["quantity_fraction"], 0.4)
        self.assertTrue(runtime["strategy"]["action_definitions"])
        self.assertEqual(runtime["strategy"]["action_policies"][0]["policy_id"], "profit-pocket")
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
        self.assertEqual(migrated_profile["action_policy_ids"], ["profit-pocket"])
        self.assertNotIn("capabilities", migrated_profile)

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
