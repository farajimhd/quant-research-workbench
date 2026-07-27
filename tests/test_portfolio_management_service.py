from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.backend.portfolio_management_service import (
    portfolio_management_command,
    portfolio_management_snapshot,
)
from src.backend.trading_runtime_service import trading_journal


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes")


def canonical_state() -> dict:
    return {
        "as_of": "2026-07-27T16:00:00+00:00",
        "complete": True,
        "stale": False,
        "stale_reason": "",
        "accounts": [
            {"account_id": "CASH1"},
            {"account_id": "MARGIN1"},
        ],
        "account_values": [
            {"account_id": "CASH1", "key": "netliquidation", "segment": "base", "monetary_value": "100000", "source_event_time": "2026-07-27T16:00:00+00:00"},
            {"account_id": "CASH1", "key": "availablefunds", "segment": "base", "monetary_value": "50000", "source_event_time": "2026-07-27T16:00:00+00:00"},
            {"account_id": "MARGIN1", "key": "netliquidation", "segment": "base", "monetary_value": "200000", "source_event_time": "2026-07-27T16:00:00+00:00"},
            {"account_id": "MARGIN1", "key": "availablefunds", "segment": "base", "monetary_value": "300000", "source_event_time": "2026-07-27T16:00:00+00:00"},
        ],
        "ledger": [],
        "positions": [
            {"account_id": "CASH1", "quantity": "100", "market_value": "10000", "instrument": {"currency": "USD", "security_type": "STK"}},
            {"account_id": "MARGIN1", "quantity": "-50", "market_value": "-5000", "instrument": {"currency": "USD", "security_type": "STK"}},
        ],
        "orders": [],
        "executions": [],
    }


class PortfolioManagementServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=RUNTIME_ROOT)
        accounts = [
            {"key": "cash", "account_id": "CASH1", "account_class": "cash", "trading_mode": "live"},
            {"key": "margin", "account_id": "MARGIN1", "account_class": "margin", "trading_mode": "live"},
        ]
        policies = {
            "policies": {
                "cash@1": {"policy_id": "cash", "revision": 1, "maximum_gross_exposure": 50000},
                "margin@2": {
                    "policy_id": "margin",
                    "revision": 2,
                    "allow_margin": True,
                    "allow_short": True,
                    "maximum_gross_exposure": 250000,
                    "maximum_net_short_exposure": 100000,
                },
            },
            "accounts": {
                "cash": {"policy": "cash@1", "session_key": "live-primary"},
                "margin": {
                    "policy": "margin@2",
                    "session_key": "live-primary",
                    "strategy_allocations": {"strategy-a": 0.6},
                },
            },
            "groups": {
                "all-live": {
                    "accounts": ["cash", "margin"],
                    "maximum_gross_exposure": 300000,
                    "maximum_ticker_exposure": 50000,
                }
            },
        }
        self.environment = patch.dict(
            os.environ,
            {
                "IBKR_ACCOUNTS_JSON": json.dumps(accounts),
                "PORTFOLIO_MANAGEMENT_JSON": json.dumps(policies),
                "TRADING_JOURNAL_PATH": str(Path(self.temp.name) / "journal.sqlite3"),
            },
            clear=False,
        )
        self.environment.start()
        trading_journal.cache_clear()

    def tearDown(self) -> None:
        if trading_journal.cache_info().currsize:
            trading_journal().close()
        trading_journal.cache_clear()
        self.environment.stop()
        self.temp.cleanup()

    def test_snapshot_isolated_account_policies_and_aggregate_group(self) -> None:
        payload = portfolio_management_snapshot(canonical_state())
        by_key = {row["account_key"]: row for row in payload["accounts"]}
        self.assertEqual(by_key["cash"]["policy"]["identity"], "cash@1")
        self.assertEqual(by_key["margin"]["policy"]["identity"], "margin@2")
        self.assertEqual(
            {row["identity"] for row in by_key["cash"]["available_policies"]},
            {"cash@1", "margin@2"},
        )
        self.assertEqual(by_key["cash"]["metrics"]["gross_value"], "10000")
        self.assertEqual(by_key["margin"]["metrics"]["short_value"], "5000")
        self.assertEqual(payload["groups"][0]["gross_exposure"], 15000)
        self.assertEqual(payload["groups"][0]["sync_state"], "synchronized")

    def test_control_command_is_durable_and_visible_in_snapshot(self) -> None:
        result = portfolio_management_command("margin", "reduce_only", reason="operator")
        payload = portfolio_management_snapshot(canonical_state())
        margin = next(row for row in payload["accounts"] if row["account_key"] == "margin")
        self.assertEqual(result["control_mode"], "reduce_only")
        self.assertEqual(margin["control_mode"], "reduce_only")
        self.assertTrue(any(row["entity_type"] == "portfolio_control" for row in payload["recent_decisions"]))

    def test_policy_and_strategy_controls_are_durable_and_capability_narrowed(self) -> None:
        selected = portfolio_management_command(
            "cash",
            "select_policy",
            reason="operator review",
            detail={"policy_identity": "margin@2"},
        )
        disabled = portfolio_management_command(
            "margin",
            "disable_strategy",
            detail={"strategy_id": "strategy-a"},
        )
        payload = portfolio_management_snapshot(canonical_state())
        by_key = {row["account_key"]: row for row in payload["accounts"]}

        self.assertEqual(selected["control_mode"], "entries_paused")
        self.assertFalse(selected["policy"]["allow_margin"])
        self.assertFalse(selected["policy"]["allow_short"])
        self.assertEqual(by_key["cash"]["policy"]["identity"], "margin@2")
        self.assertFalse(by_key["cash"]["policy"]["allow_margin"])
        self.assertEqual(disabled["disabled_strategy_allocations"], ["strategy-a"])
        self.assertEqual(by_key["margin"]["disabled_strategy_allocations"], ["strategy-a"])
