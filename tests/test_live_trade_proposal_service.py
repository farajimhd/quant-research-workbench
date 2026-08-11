from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.live_trade_proposal_service import stage_live_trade_proposal


class FakeJournal:
    def __init__(self) -> None:
        self.appended = []
        self.existing = []

    def recent_records(self, run_id, *, categories, limit):
        return list(self.existing)

    def append(self, **values):
        self.appended.append(values)


class LiveTradeProposalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC)
        self.journal = FakeJournal()
        self.account = SimpleNamespace(
            account_key="paper", account_id="DU123", trading_mode="paper"
        )
        self.payload = {
            "proposal_id": "proposal-1",
            "authority": "manual",
            "account_id": "paper",
            "ticker": "AAPL",
            "conid": 265598,
            "action": "enter_long",
            "quantity": 10,
            "market_snapshot": {
                "freshness": "ready",
                "observed_at": self.now.isoformat(),
                "reference_price": 199.8,
                "source_sequence": "2026-08-11T16:00:00Z",
            },
            "invalidation_price": 195,
            "profit_target_price": 210,
        }

    def _ticker_state(self, _ticker: str):
        return {
            "found": True,
            "state": "ready",
            "age_ms": 125,
            "authority": "qmd_gateway_live_memory",
            "sequence": 42,
            "row": {
                "last_event_ts": self.now.isoformat(),
                "last_price": 200,
                "bid": 199.99,
                "ask": 200.01,
            },
        }

    @staticmethod
    def _identity(_ticker: str):
        return {
            "ticker": "AAPL",
            "ibkr_conid": 265598,
            "universe_date": "2026-08-11",
            "is_tradable": True,
        }

    def _stage(self, payload=None, *, ticker_state=None):
        with (
            patch(
                "src.backend.live_trade_proposal_service.resolve_real_live_accounts",
                return_value=[self.account],
            ),
            patch(
                "src.backend.live_trade_proposal_service._approved_configuration_checks",
                return_value=[{"status": "ready"}],
            ),
            patch(
                "src.backend.live_trade_proposal_service.trading_journal",
                return_value=self.journal,
            ),
        ):
            return stage_live_trade_proposal(
                "paper",
                dict(payload or self.payload),
                ticker_state=ticker_state or self._ticker_state,
                tradable_symbol=self._identity,
            )

    def test_validates_and_journals_without_broker_submission(self) -> None:
        result = self._stage()

        self.assertEqual(result["status"], "validated_pending_runtime")
        self.assertEqual(result["account_key"], "paper")
        self.assertEqual(result["market_snapshot"]["source_sequence"], 42)
        self.assertEqual(
            result["identity_revision"],
            "tradable-universe:2026-08-11:AAPL:265598",
        )
        self.assertFalse(result["execution"]["broker_submission"])
        self.assertTrue(result["execution"]["portfolio_admission_required"])
        self.assertTrue(result["execution"]["oms_validation_required"])
        self.assertEqual(len(self.journal.appended), 1)
        self.assertEqual(self.journal.appended[0]["account_id"], "paper")

    def test_rejects_stale_authoritative_state(self) -> None:
        def stale_state(ticker: str):
            result = self._ticker_state(ticker)
            result["age_ms"] = 10_000
            return result

        with self.assertRaisesRegex(ValueError, "ticker state is stale"):
            self._stage(ticker_state=stale_state)

    def test_rejects_client_snapshot_without_price_sequence(self) -> None:
        payload = dict(self.payload)
        payload["market_snapshot"] = {
            **self.payload["market_snapshot"],
            "source_sequence": "",
        }
        with self.assertRaisesRegex(ValueError, "price sequence"):
            self._stage(payload)

    def test_rejects_directionally_invalid_protection(self) -> None:
        with self.assertRaisesRegex(ValueError, "stop must be below"):
            self._stage({**self.payload, "invalidation_price": 201})

    def test_rejects_stale_identity_revision(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity revision is stale"):
            self._stage({**self.payload, "identity_revision": "old"})

    def test_rejects_stale_chart_snapshot(self) -> None:
        payload = dict(self.payload)
        payload["market_snapshot"] = {
            **self.payload["market_snapshot"],
            "observed_at": (self.now - timedelta(seconds=10)).isoformat(),
        }
        with self.assertRaisesRegex(ValueError, "chart snapshot is stale"):
            self._stage(payload)


if __name__ == "__main__":
    unittest.main()
