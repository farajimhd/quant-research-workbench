from __future__ import annotations

import tempfile
import unittest
import os
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from src.backend.trading_configuration_service import (
    _default_draft,
    _migrate_draft,
    _resolved_source_account_id,
    _validate_draft,
    approved_configuration,
    capability_catalog,
    publish_configuration,
    replay_configuration_snapshot,
    replace_configuration_draft,
    resolve_runtime_configuration,
    resolve_runtime_configurations,
    update_configuration_section,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import long_momentum_strategy_definition


class TradingConfigurationServiceTests(unittest.TestCase):
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

    def test_schema_v9_migration_removes_legacy_session_overrides_and_adds_policy_catalogs(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            legacy = _default_draft()
        legacy["schema_version"] = 6
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

        self.assertEqual(migrated["schema_version"], 9)
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

    def test_system_profiles_and_capabilities_are_user_configurable(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            draft = _default_draft()

        self.assertEqual(draft["schema_version"], 9)
        self.assertEqual(len(draft["strategy"]["profiles"]), 1)
        self.assertEqual(len(draft["strategy"]["profile_templates"]), 2)
        self.assertTrue(all(profile["editable"] for profile in draft["strategy"]["profiles"]))
        default_profile = next(
            row
            for row in draft["strategy"]["profiles"]
            if row["profile_id"] == draft["strategy"]["default_profile_id"]
        )
        self.assertTrue(default_profile["protected"])
        self.assertEqual(
            {row["capability_id"] for row in draft["strategy"]["capability_catalog"]},
            {row["capability_id"] for row in capability_catalog()},
        )
        self.assertTrue(
            all(profile["capabilities"] for profile in draft["strategy"]["profiles"])
        )
        self.assertTrue(draft["strategy"]["input_catalog"])
        self.assertTrue(
            all(
                profile["lifecycle"]["initial_entry"]["opportunity"]["groups"]
                for profile in draft["strategy"]["profiles"]
            )
        )
        lifecycle = default_profile["lifecycle"]
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
        self.assertTrue(lifecycle["initial_entry"]["add_steps"])
        self.assertNotIn("time_in_force", lifecycle["initial_entry"]["order_intent"])
        self.assertNotIn("outside_rth", lifecycle["initial_entry"]["order_intent"])
        self.assertEqual(
            draft["oms"]["profiles"][0]["settings"]["session_routing"], "smart"
        )
        self.assertNotIn("time_in_force", draft["oms"]["profiles"][0]["settings"])
        self.assertNotIn("outside_rth", draft["oms"]["profiles"][0]["settings"])
        self.assertEqual(len(draft["oms"]["execution_policies"]), 9)
        self.assertTrue(draft["oms"]["protection_profiles"])
        self.assertEqual(
            lifecycle["initial_entry"]["order_intent"]["protection_profile"],
            "hybrid-single",
        )
        self.assertTrue(lifecycle["reentry"]["rules"]["opportunity"]["groups"])
        self.assertTrue(lifecycle["exit"]["rule_sets"][1]["rules"]["groups"])
        self.assertTrue(
            any(
                condition["left_source_id"]
                == "indicator.flow_structure.score"
                for group in lifecycle["exit"]["rule_sets"][1]["rules"]["groups"]
                for condition in group["conditions"]
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
            journal.save_trading_configuration_draft(draft)
            with self._service_patches(journal):
                published = publish_configuration(
                    label="Replay acceptance",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                )
                oms = draft["oms"]
                oms["profiles"][0]["settings"]["entry_urgency"] = "patient"
                update_configuration_section("oms", oms)
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

    def test_publish_is_idempotent_for_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.save_trading_configuration_draft(self._draft())
            with self._service_patches(journal):
                first = publish_configuration(
                    label="First label",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                )
                second = publish_configuration(
                    label="Second label",
                    canvas_revision="canvas-1",
                    canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                )
            journal.close()

        self.assertEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(second["label"], "First label")

    def test_complete_draft_replacement_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            original = self._draft()
            journal.save_trading_configuration_draft(original)
            replacement = deepcopy(original)
            replacement["strategy"]["profiles"][0]["name"] = "Cloned profile"
            replacement["run_plans"]["plans"][0]["name"] = "Cloned Run Plan"
            with self._service_patches(journal):
                saved = replace_configuration_draft(replacement)
                invalid = deepcopy(replacement)
                invalid["run_plans"]["plans"][0]["profile_id"] = "missing-profile"
                with self.assertRaisesRegex(ValueError, "unknown Strategy Profile"):
                    replace_configuration_draft(invalid)
                reloaded = journal.trading_configuration_draft()
            journal.close()

        self.assertEqual(saved["strategy"]["profiles"][0]["name"], "Cloned profile")
        self.assertEqual(saved["run_plans"]["plans"][0]["name"], "Cloned Run Plan")
        self.assertEqual(reloaded["run_plans"]["plans"][0]["name"], "Cloned Run Plan")

    def test_runtime_projection_uses_account_mandate_and_capability_settings(self) -> None:
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
        pocket = next(
            row for row in profile["capabilities"]
            if row["capability_id"] == "profit-pocket"
        )
        pocket["settings"]["quantity_fraction"] = 0.4

        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            runtime = resolve_runtime_configuration(draft, mode="replay")

        self.assertEqual(runtime["accounts"]["bindings"][0]["strategy_allocation"], 0.3)
        self.assertEqual(runtime["strategy"]["parameters"]["profit_pocket"]["quantity_fraction"], 0.4)
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

    def test_paper_release_requires_exact_broker_binding_and_mode_deployment(self) -> None:
        draft = self._draft()
        binding = draft["accounts"]["bindings"][0]
        binding["modes"] = ["paper"]
        binding["account_class"] = "margin"
        binding["source_account_id"] = ""
        deployment = draft["run_plans"]["plans"][0]
        deployment["allowed_environments"] = ["paper"]

        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "exact broker account id"):
            _validate_draft(draft)

        binding["source_account_id"] = "DU1234567"
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ):
            _validate_draft(draft)

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

    def test_incomplete_deployment_can_be_saved_but_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            draft["portfolio"]["mandates"] = []
            journal.save_trading_configuration_draft(draft)
            with self._service_patches(journal):
                saved = update_configuration_section("run_plans", draft["run_plans"])
                with self.assertRaisesRegex(ValueError, "requires at least one account mandate"):
                    publish_configuration(
                        label="Incomplete release",
                        canvas_revision="canvas-1",
                        canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    )
            journal.close()

        self.assertEqual(saved["run_plans"]["plans"][0]["run_plan_id"], "balanced-replay")

    def test_unknown_strategy_input_cannot_enter_runtime_projection(self) -> None:
        draft = self._draft()
        condition = draft["strategy"]["profiles"][0]["lifecycle"]["initial_entry"]["opportunity"]["groups"][0]["conditions"][0]
        condition["left_source_id"] = "indicator.unregistered.value"

        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), self.assertRaisesRegex(ValueError, "unknown left source"):
            resolve_runtime_configuration(draft, mode="replay")

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

    def test_cloned_strategy_profile_persists_in_the_authoritative_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            journal.save_trading_configuration_draft(draft)
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
                saved = update_configuration_section("strategy", strategy)
                reloaded = journal.trading_configuration_draft()
            journal.close()

        self.assertEqual(
            saved["strategy"]["profiles"][-1]["profile_id"],
            "long-momentum-clone",
        )
        self.assertEqual(
            reloaded["strategy"]["profiles"][-1]["profile_id"],
            "long-momentum-clone",
        )

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
