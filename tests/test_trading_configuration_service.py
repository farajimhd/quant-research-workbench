from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from src.backend.trading_configuration_service import (
    approved_configuration,
    publish_configuration,
    replay_configuration_snapshot,
    update_configuration_section,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime.strategy_engine import (
    STRATEGY_ID,
    STRATEGY_REVISION,
    default_long_momentum_parameters,
)


class TradingConfigurationServiceTests(unittest.TestCase):
    def test_published_revision_is_immutable_when_draft_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.save_trading_configuration_draft(_draft())
            with patch(
                "src.backend.trading_configuration_service.trading_journal",
                return_value=journal,
            ), patch(
                "src.backend.trading_configuration_service.get_strategy_definition",
                return_value={
                    "strategy_id": STRATEGY_ID,
                    "revision": STRATEGY_REVISION,
                    "enabled": True,
                },
            ):
                published = publish_configuration(
                    label="Replay acceptance",
                    canvas_revision="canvas-1",
                    canvas_profile={
                        "workspaceStates": {
                            "main": {"openIds": ["chart"]},
                        }
                    },
                )
                oms = dict(_draft()["oms"])
                oms["entry_urgency"] = "patient"
                update_configuration_section("oms", oms)
                pinned = replay_configuration_snapshot()
                latest = approved_configuration(required=True)

            journal.close()

        self.assertEqual(published["revision_id"], pinned["revision_id"])
        self.assertEqual(latest["payload"]["oms"]["entry_urgency"], "urgent")
        self.assertEqual(pinned["payload"]["canvas"]["revision"], "canvas-1")

    def test_publish_is_idempotent_for_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            journal.save_trading_configuration_draft(_draft())
            with patch(
                "src.backend.trading_configuration_service.trading_journal",
                return_value=journal,
            ), patch(
                "src.backend.trading_configuration_service.get_strategy_definition",
                return_value={
                    "strategy_id": STRATEGY_ID,
                    "revision": STRATEGY_REVISION,
                    "enabled": True,
                },
            ):
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


def _draft() -> dict:
    return {
        "strategy": {
            "strategy_id": STRATEGY_ID,
            "revision": STRATEGY_REVISION,
            "name": "Long Momentum Campaign",
            "parameters": default_long_momentum_parameters(),
        },
        "assignments": [],
        "portfolio": {
            "policies": [asdict(PortfolioPolicy())],
            "groups": [],
        },
        "oms": {
            "entry_urgency": "urgent",
            "exit_urgency": "very_urgent",
            "limit_offset_bps": 5.0,
            "tick_size": 0.01,
            "time_in_force": "DAY",
            "outside_rth": False,
            "protection": {
                "stop_method": "hybrid",
                "structure_buffer_bps": 8.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 1.5,
                "trailing_enabled": True,
            },
        },
        "accounts": {
            "bindings": [{
                "account_key": "primary",
                "source_account_id": "replay",
                "account_class": "simulated",
                "base_currency": "USD",
                "session_key": "replay",
                "portfolio_policy_id": "default",
                "enabled": True,
                "modes": ["replay", "backtest", "backtest_debug"],
            }]
        },
    }
