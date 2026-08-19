from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

from src.backend.replay_run_service import ReplayRunDefinition
from src.backend.trading_configuration_service import (
    _default_draft,
    _validate_draft,
    publish_configuration,
    resolve_runtime_configuration,
    resolve_session_configuration,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.strategy_engine import long_momentum_strategy_definition


class SessionExecutionArchitectureTests(unittest.TestCase):
    def _draft(self) -> dict:
        with patch(
            "src.backend.trading_configuration_service.get_strategy_definition",
            return_value=long_momentum_strategy_definition(),
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_definitions",
            return_value=[long_momentum_strategy_definition()],
        ), patch(
            "src.backend.trading_configuration_service.list_strategy_assignments",
            return_value=[],
        ):
            return _default_draft()

    def test_manual_session_resolves_without_strategy_or_canvas(self) -> None:
        draft = self._draft()
        draft.pop("canvas", None)
        resolved = resolve_session_configuration(
            draft,
            mode="replay",
            resolve_broker_ids=False,
        )
        self.assertEqual(resolved["execution_principal"]["kind"], "session")
        self.assertEqual(resolved["session_profile"]["session_profile_id"], "historical-session")
        self.assertTrue(resolved["execution_routes"])
        self.assertTrue(resolved["portfolio"]["mandates"])
        self.assertNotIn("strategy", resolved)
        self.assertNotIn("canvas", resolved)

    def test_strategy_runtime_resolves_through_deployment_and_route(self) -> None:
        resolved = resolve_runtime_configuration(
            self._draft(),
            mode="replay",
            run_plan_id="balanced-replay",
            resolve_broker_ids=False,
        )
        self.assertEqual(resolved["deployment"]["strategy_deployment_id"], "balanced-replay:historical-session")
        self.assertEqual(resolved["session_profile"]["session_profile_id"], "historical-session")
        self.assertTrue(resolved["execution_routes"])
        self.assertNotIn("canvas", resolved)

    def test_replicated_strategy_sizes_each_account_through_its_own_mandate(self) -> None:
        draft = self._draft()
        source_account = next(
            row for row in draft["accounts"]["bindings"]
            if row["account_key"] == "replay"
        )
        second_account = {
            **deepcopy(source_account),
            "account_key": "replay-secondary",
            "name": "Replay secondary",
            "source_account_id": "REPLAY-SECONDARY",
            "session_key": "replay-secondary",
        }
        draft["accounts"]["bindings"].append(second_account)

        source_route = next(
            row for row in draft["sessions"]["execution_routes"]
            if row["execution_route_id"] == "historical-session:replay"
        )
        second_route = {
            **deepcopy(source_route),
            "execution_route_id": "historical-session:replay-secondary",
            "name": "Replay secondary route",
            "account_key": "replay-secondary",
            "portfolio_mandate_id": "session:historical-session:replay-secondary",
            "system_generated": False,
        }
        draft["sessions"]["execution_routes"].append(second_route)
        historical = next(
            row for row in draft["sessions"]["profiles"]
            if row["session_profile_id"] == "historical-session"
        )
        historical["execution_route_ids"].append(second_route["execution_route_id"])

        source_session_mandate = next(
            row for row in draft["portfolio"]["mandates"]
            if row["mandate_id"] == "session:historical-session:replay"
        )
        draft["portfolio"]["mandates"].append({
            **deepcopy(source_session_mandate),
            "mandate_id": "session:historical-session:replay-secondary",
            "account_key": "replay-secondary",
        })

        deployment = next(
            row for row in draft["sessions"]["strategy_deployments"]
            if row["strategy_deployment_id"] == "balanced-replay:historical-session"
        )
        source_strategy_mandate = next(
            row for row in draft["portfolio"]["mandates"]
            if row["mandate_id"] == "balanced-replay"
        )
        source_strategy_mandate["assignment_mode"] = "replicated"
        source_strategy_mandate["maximum_cash_fraction"] = 0.3
        second_strategy_mandate = {
            **deepcopy(source_strategy_mandate),
            "mandate_id": "balanced-replay-secondary",
            "account_key": "replay-secondary",
            "maximum_cash_fraction": 0.15,
        }
        draft["portfolio"]["mandates"].append(second_strategy_mandate)
        deployment["execution_route_ids"].append(second_route["execution_route_id"])
        deployment["portfolio_mandate_ids"].append(second_strategy_mandate["mandate_id"])
        deployment["system_generated"] = False
        run_plan = next(
            row for row in draft["run_plans"]["plans"]
            if row["run_plan_id"] == "balanced-replay"
        )
        run_plan["mandate_ids"].append(second_strategy_mandate["mandate_id"])

        resolved = resolve_runtime_configuration(
            draft,
            mode="replay",
            run_plan_id="balanced-replay",
            resolve_broker_ids=False,
        )

        self.assertEqual(resolved["account_topology"]["mode"], "replicated")
        self.assertEqual(
            {row["account_key"]: row["maximum_cash_fraction"] for row in resolved["account_topology"]["legs"]},
            {"replay": 0.3, "replay-secondary": 0.15},
        )
        self.assertEqual(
            {row["account_key"]: row["strategy_allocation"] for row in resolved["accounts"]["bindings"]},
            {"replay": 0.3, "replay-secondary": 0.15},
        )

    def test_live_session_selects_only_the_route_for_the_requested_mode(self) -> None:
        draft = self._draft()
        for account in draft["accounts"]["bindings"]:
            if account["account_key"] in {"paper", "cash"}:
                account["enabled"] = True
        for route in draft["sessions"]["execution_routes"]:
            if route["account_key"] in {"paper", "cash"}:
                route["enabled"] = True
        paper = resolve_session_configuration(draft, mode="paper", resolve_broker_ids=False)
        live = resolve_session_configuration(draft, mode="live", resolve_broker_ids=False)
        self.assertEqual({row["modes"][0] for row in paper["execution_routes"]}, {"paper"})
        self.assertEqual({row["modes"][0] for row in live["execution_routes"]}, {"live"})
        self.assertNotEqual(
            paper["execution_routes"][0]["account_key"],
            live["execution_routes"][0]["account_key"],
        )

    def test_manual_session_fails_closed_when_manual_authority_is_disabled(self) -> None:
        draft = self._draft()
        historical = next(
            row for row in draft["sessions"]["profiles"]
            if row["session_profile_id"] == "historical-session"
        )
        historical["manual_authority"]["enabled"] = False
        with self.assertRaisesRegex(ValueError, "does not permit manual execution"):
            resolve_session_configuration(draft, mode="replay", resolve_broker_ids=False)

    def test_manual_replay_definition_pins_session_not_canvas(self) -> None:
        definition = ReplayRunDefinition(
            session_date=date(2026, 8, 18),
            start_time=time(9, 45),
            tickers=("AAPL",),
            execution_mode="manual",
            configuration_revision={
                "revision_id": "release-1",
                "revision": 1,
                "label": "Manual replay",
                "content_hash": "hash-1",
                "payload": {"session_profile": {"session_profile_id": "historical-session"}},
            },
            mode=RunMode.REPLAY,
        )
        payload = definition.payload()
        self.assertEqual(payload["execution_mode"], "manual")
        self.assertEqual(payload["tickers"], ["AAPL"])
        self.assertEqual(payload["canvas_revision"], "")
        self.assertEqual(payload["canvas_profile"], {})

    def test_release_can_publish_without_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            draft = self._draft()
            with patch(
                "src.backend.trading_configuration_service.trading_journal",
                return_value=journal,
            ), patch(
                "src.backend.trading_configuration_service.get_strategy_definition",
                return_value=long_momentum_strategy_definition(),
            ), patch(
                "src.backend.trading_configuration_service.list_strategy_assignments",
                return_value=[],
            ), patch(
                "src.backend.trading_configuration_service._resolved_source_account_id",
                return_value="SIMULATED",
            ), patch(
                "src.backend.trading_configuration_service.materialize_market_discovery",
            ):
                _validate_draft(draft, require_runtime_ready=False)
                published = publish_configuration(
                    label="Headless execution",
                    canvas_revision="",
                    canvas_profile={},
                    configuration=draft,
                )
            journal.close()
        self.assertFalse(published["payload"]["canvas"]["execution_authority"])
        self.assertEqual(published["payload"]["canvas"]["profile"], {})


if __name__ == "__main__":
    unittest.main()
