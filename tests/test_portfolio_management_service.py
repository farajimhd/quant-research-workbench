from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
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

    def test_approved_release_replaces_legacy_portfolio_environment_authority(self) -> None:
        approved_payload = {
            "portfolio": {
                "policies": [{
                    "policy_id": "approved-cash",
                    "revision": 4,
                    "maximum_gross_exposure": 12345,
                }],
                "mandates": [{
                    "mandate_id": "approved-cash-mandate",
                    "deployment_id": "approved-live",
                    "account_key": "cash",
                    "enabled": True,
                    "maximum_cash_fraction": 0.25,
                }],
                "groups": [],
            },
            "accounts": {"bindings": [{
                "account_key": "cash",
                "source_account_id": "CASH1",
                "account_class": "cash",
                "portfolio_policy_id": "approved-cash",
                "session_key": "live-primary",
                "enabled": True,
                "modes": ["live"],
            }]},
            "assignments": {"deployments": [{
                "deployment_id": "approved-live",
                "profile_id": "approved-strategy-profile",
                "enabled": True,
                "modes": ["live"],
            }]},
            "strategy": {"profiles": [{
                "profile_id": "approved-strategy-profile",
                "definition_id": "approved-strategy",
            }]},
        }
        with patch(
            "src.backend.portfolio_management_service.approved_configuration",
            return_value={"revision_id": "revision-4", "revision": 4, "payload": approved_payload},
        ):
            payload = portfolio_management_snapshot(canonical_state())

        self.assertEqual(payload["configuration_authority"]["source"], "approved_release")
        self.assertEqual(payload["configuration_authority"]["revision_id"], "revision-4")
        self.assertEqual(len(payload["accounts"]), 1)
        self.assertEqual(payload["accounts"][0]["policy"]["identity"], "approved-cash@4")
        self.assertEqual(
            payload["accounts"][0]["strategy_allocations"],
            {"approved-strategy": 0.25},
        )
        self.assertEqual(
            payload["accounts"][0]["run_plan_allocations"],
            {"approved-live": 0.25},
        )

    def test_control_command_is_durable_and_visible_in_snapshot(self) -> None:
        result = portfolio_management_command("margin", "reduce_only", reason="operator")
        payload = portfolio_management_snapshot(canonical_state())
        margin = next(row for row in payload["accounts"] if row["account_key"] == "margin")
        self.assertEqual(result["control_mode"], "reduce_only")
        self.assertEqual(margin["control_mode"], "reduce_only")
        self.assertTrue(any(row["entity_type"] == "portfolio_control" for row in payload["recent_decisions"]))

    def test_snapshot_exposes_bounded_portfolio_and_oms_operational_metrics(self) -> None:
        journal = trading_journal()
        journal.append(
            run_id="run-a",
            category="portfolio_management",
            entity_type="portfolio_decision",
            entity_id="decision-a",
            account_id="CASH1",
            payload={"event": "portfolio_decision", "status": "approved"},
        )
        journal.append(
            run_id="run-a",
            category="portfolio_management",
            entity_type="portfolio_reservation",
            entity_id="reservation-a",
            account_id="CASH1",
            payload={"event": "reservation_created"},
        )
        journal.save_order_management_state(
            "group-a",
            run_id="run-a",
            account_id="CASH1",
            state={
                "state": "outcome_unknown",
                "protection_required_quantity": 100,
                "protection_coverage_quantity": 75,
            },
        )
        journal.append(
            run_id="run-a",
            category="order_management",
            entity_type="order_group_state",
            entity_id="group-a",
            account_id="CASH1",
            event_time=datetime.now(timezone.utc),
            payload={"event": "reconciliation_missing", "state": "rejected"},
        )

        metrics = portfolio_management_snapshot(canonical_state())["operational_metrics"]

        self.assertEqual(metrics["portfolio"]["disposition_counts"], {"approved": 1})
        self.assertEqual(
            metrics["portfolio"]["reservation_event_counts"],
            {"reservation_created": 1},
        )
        self.assertEqual(metrics["oms"]["state_counts"], {"outcome_unknown": 1})
        self.assertEqual(metrics["oms"]["unprotected_quantity"], 25)
        self.assertEqual(metrics["oms"]["reconciliation_event_count"], 1)
        self.assertEqual(metrics["oms"]["reconciliation_failure_count"], 1)
        self.assertIsNotNone(metrics["oms"]["last_reconciliation_at"])

    def test_resume_and_emergency_commands_are_runtime_queued_and_fail_closed(self) -> None:
        portfolio_management_command("margin", "pause_entries", reason="risk review")
        resumed = portfolio_management_command(
            "margin",
            "resume_entries",
            reason="operator reviewed",
        )
        flattened = portfolio_management_command(
            "cash",
            "emergency_flatten",
            reason="operator emergency",
        )
        states = trading_journal().portfolio_states()

        self.assertTrue(resumed["execution_required"])
        self.assertEqual(resumed["control_mode"], "entries_paused")
        self.assertEqual(states["MARGIN1"]["control_mode"], "entries_paused")
        self.assertEqual(
            states["MARGIN1"]["pending_operational_commands"][-1]["command"],
            "resume_entries",
        )
        self.assertEqual(flattened["control_mode"], "reduce_only")
        self.assertEqual(states["CASH1"]["control_mode"], "reduce_only")
        self.assertEqual(
            states["CASH1"]["pending_operational_commands"][-1]["command"],
            "emergency_flatten",
        )

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
