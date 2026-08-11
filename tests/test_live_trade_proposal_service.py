from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.live_trade_proposal_service import (
    _validate_control_plane,
    stage_live_trade_proposal,
)
from src.trading_runtime.portfolio import PortfolioDecisionStatus
from src.trading_runtime.signals import StrategyIntent


class FakeJournal:
    def __init__(self) -> None:
        self.appended = []
        self.existing = []

    def recent_records(self, run_id, *, categories, limit):
        return list(self.existing)

    def append(self, **values):
        self.appended.append(values)


class LiveTradeProposalServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def _stage(self, payload=None, *, ticker_state=None):
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
            patch(
                "src.backend.live_trade_proposal_service._validate_control_plane",
                return_value={
                    "status": "validated_pending_broker_runtime",
                    "portfolio": {
                        "status": "approved",
                        "reservation_status": "released",
                    },
                    "oms": {
                        "status": "validated_not_submitted",
                        "order_count": 2,
                    },
                },
            ),
        ):
            return await stage_live_trade_proposal(
                "paper",
                dict(payload or self.payload),
                ticker_state=ticker_state or self._ticker_state,
                tradable_symbol=self._identity,
            )

    async def test_validates_and_journals_without_broker_submission(self) -> None:
        result = await self._stage()

        self.assertEqual(result["status"], "validated_pending_broker_runtime")
        self.assertEqual(result["account_key"], "paper")
        self.assertEqual(result["market_snapshot"]["source_sequence"], 42)
        self.assertEqual(
            result["identity_revision"],
            "tradable-universe:2026-08-11:AAPL:265598",
        )
        self.assertFalse(result["execution"]["broker_submission"])
        self.assertFalse(result["execution"]["portfolio_admission_required"])
        self.assertFalse(result["execution"]["oms_validation_required"])
        self.assertEqual(result["portfolio"]["reservation_status"], "released")
        self.assertEqual(result["oms"]["status"], "validated_not_submitted")
        self.assertEqual(len(self.journal.appended), 1)
        self.assertEqual(self.journal.appended[0]["account_id"], "paper")

    async def test_rejects_stale_authoritative_state(self) -> None:
        def stale_state(ticker: str):
            result = self._ticker_state(ticker)
            result["age_ms"] = 10_000
            return result

        with self.assertRaisesRegex(ValueError, "ticker state is stale"):
            await self._stage(ticker_state=stale_state)

    async def test_rejects_client_snapshot_without_price_sequence(self) -> None:
        payload = dict(self.payload)
        payload["market_snapshot"] = {
            **self.payload["market_snapshot"],
            "source_sequence": "",
        }
        with self.assertRaisesRegex(ValueError, "price sequence"):
            await self._stage(payload)

    async def test_rejects_directionally_invalid_protection(self) -> None:
        with self.assertRaisesRegex(ValueError, "stop must be below"):
            await self._stage({**self.payload, "invalidation_price": 201})

    async def test_rejects_stale_identity_revision(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity revision is stale"):
            await self._stage({**self.payload, "identity_revision": "old"})

    async def test_rejects_stale_chart_snapshot(self) -> None:
        payload = dict(self.payload)
        payload["market_snapshot"] = {
            **self.payload["market_snapshot"],
            "observed_at": (self.now - timedelta(seconds=10)).isoformat(),
        }
        with self.assertRaisesRegex(ValueError, "chart snapshot is stale"):
            await self._stage(payload)

    async def test_accepts_automatic_proposal_origin(self) -> None:
        result = await self._stage({**self.payload, "authority": "automatic"})

        self.assertEqual(result["authority"], "automatic")

    async def test_control_plane_releases_admission_after_oms_plan(self) -> None:
        released = []
        synchronized = []

        class Decision:
            status = PortfolioDecisionStatus.APPROVED

            @staticmethod
            def payload():
                return {"status": "approved", "reservation_id": "reservation-1"}

        class Portfolio:
            def __init__(self, *args, **kwargs):
                pass

            def synchronize_canonical(self, snapshot):
                synchronized.append(snapshot)

            async def approve(self, intent, *, account_id):
                return Decision(), intent

            def release_intent(self, intent_id, *, reason):
                released.append((intent_id, reason))

        order = SimpleNamespace(
            orderType="STP",
            side="SELL",
            parentId="entry-1",
        )
        planner = SimpleNamespace(
            plan=lambda **kwargs: SimpleNamespace(
                orders=(order,),
                broker_batches=((order,),),
            )
        )
        profile = SimpleNamespace(
            account_key="paper",
            account_id="DU123",
            mode="paper",
            base_currency="USD",
        )
        intent = StrategyIntent(
            intent_id="proposal:proposal-1",
            ticker="AAPL",
            event_time=self.now,
            action="enter_long",
            quantity=10,
            reference_price=200,
            invalidation_price=195,
        )
        with (
            patch(
                "src.backend.live_trade_proposal_service.approved_configuration",
                return_value={"payload": {}},
            ),
            patch(
                "src.backend.live_trade_proposal_service.configured_real_live_accounts",
                return_value=[self.account],
            ),
            patch(
                "src.backend.live_trade_proposal_service.configured_portfolio_profiles",
                return_value=((profile,), ()),
            ),
            patch(
                "src.backend.live_trade_proposal_service.canonical_live_snapshot",
                return_value="canonical-snapshot",
            ),
            patch(
                "src.backend.live_trade_proposal_service.PortfolioManagementEngine",
                Portfolio,
            ),
            patch(
                "src.backend.live_trade_proposal_service.IbkrStrategyOrderPlanner",
                return_value=planner,
            ),
            patch(
                "src.backend.live_trade_proposal_service.trading_journal",
                return_value=self.journal,
            ),
        ):
            result = await _validate_control_plane(
                mode="paper",
                account=self.account,
                intent=intent,
                conid=265598,
                exchange="SMART",
            )

        self.assertEqual(result["status"], "validated_pending_broker_runtime")
        self.assertEqual(result["oms"]["status"], "validated_not_submitted")
        self.assertEqual(result["portfolio"]["reservation_status"], "released")
        self.assertEqual(synchronized, ["canonical-snapshot"])
        self.assertEqual(released[0][0], intent.intent_id)


if __name__ == "__main__":
    unittest.main()
