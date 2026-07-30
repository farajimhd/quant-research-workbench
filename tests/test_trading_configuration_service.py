from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.backend.trading_configuration_service import (
    _default_draft,
    approved_configuration,
    capability_catalog,
    publish_configuration,
    replay_configuration_snapshot,
    resolve_runtime_configuration,
    update_configuration_section,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import long_momentum_strategy_definition


class TradingConfigurationServiceTests(unittest.TestCase):
    def test_system_profiles_and_capabilities_are_user_configurable(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            draft = _default_draft()

        self.assertEqual(draft["schema_version"], 2)
        self.assertGreaterEqual(len(draft["strategy"]["profiles"]), 3)
        self.assertTrue(all(profile["editable"] for profile in draft["strategy"]["profiles"]))
        self.assertEqual(
            {row["capability_id"] for row in draft["strategy"]["capability_catalog"]},
            {row["capability_id"] for row in capability_catalog()},
        )
        self.assertTrue(
            all(profile["capabilities"] for profile in draft["strategy"]["profiles"])
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
        self.assertEqual(pinned["deployment_id"], "balanced-replay")
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

    def test_runtime_projection_uses_account_mandate_and_capability_settings(self) -> None:
        draft = self._draft()
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
        self.assertEqual(runtime["deployment"]["deployment_id"], "balanced-replay")

    def test_incomplete_deployment_can_be_saved_but_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            draft["portfolio"]["mandates"] = []
            journal.save_trading_configuration_draft(draft)
            with self._service_patches(journal):
                saved = update_configuration_section("assignments", draft["assignments"])
                with self.assertRaisesRegex(ValueError, "requires at least one account mandate"):
                    publish_configuration(
                        label="Incomplete release",
                        canvas_revision="canvas-1",
                        canvas_profile={"workspaceStates": {"main": {"openIds": ["chart"]}}},
                    )
            journal.close()

        self.assertEqual(saved["assignments"]["deployments"][0]["deployment_id"], "balanced-replay")

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
