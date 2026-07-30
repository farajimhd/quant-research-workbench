from __future__ import annotations

import unittest

from src.trading_runtime.strategy_campaign import (
    CampaignPhase,
    StrategyCampaignOrchestrator,
    campaign_phase_for,
)


def assignment(
    assignment_id: str,
    *,
    campaign_id: str,
    status: str = "watching",
    account_id: str = "account-1",
) -> dict:
    return {
        "assignment_id": assignment_id,
        "strategy_id": "long-momentum-campaign",
        "account_id": account_id,
        "ticker": "AAPL",
        "status": status,
        "state": {
            "campaign_id": campaign_id,
            "campaign_book_id": "primary",
        },
    }


class StrategyCampaignTests(unittest.TestCase):
    def test_one_campaign_owns_a_ticker_across_multiple_account_legs(self) -> None:
        orchestrator = StrategyCampaignOrchestrator()
        first = orchestrator.register(
            assignment("leg-1", campaign_id="campaign-a", account_id="account-1")
        )
        second = orchestrator.register(
            assignment("leg-2", campaign_id="campaign-a", account_id="account-2")
        )

        self.assertEqual(first, second)
        self.assertEqual(
            orchestrator.lease_for(book_id="primary", ticker="AAPL").campaign_id,
            "campaign-a",
        )

    def test_competing_active_campaign_cannot_claim_owned_ticker(self) -> None:
        orchestrator = StrategyCampaignOrchestrator(
            [assignment("leg-1", campaign_id="campaign-a")]
        )
        with self.assertRaisesRegex(ValueError, "already owned"):
            orchestrator.register(
                assignment("leg-2", campaign_id="campaign-b")
            )

    def test_lease_releases_only_after_last_active_account_leg_completes(self) -> None:
        orchestrator = StrategyCampaignOrchestrator(
            [
                assignment("leg-1", campaign_id="campaign-a", account_id="account-1"),
                assignment("leg-2", campaign_id="campaign-a", account_id="account-2"),
            ]
        )
        orchestrator.register(
            assignment(
                "leg-1",
                campaign_id="campaign-a",
                account_id="account-1",
                status="completed",
            )
        )
        self.assertIsNotNone(
            orchestrator.lease_for(book_id="primary", ticker="AAPL")
        )
        orchestrator.register(
            assignment(
                "leg-2",
                campaign_id="campaign-a",
                account_id="account-2",
                status="completed",
            )
        )
        self.assertIsNone(
            orchestrator.lease_for(book_id="primary", ticker="AAPL")
        )

    def test_flat_position_after_exit_is_reentry_wait_not_initial_entry(self) -> None:
        payload = assignment(
            "leg-1",
            campaign_id="campaign-a",
            status="reentry_cooldown",
        )
        payload["state"]["reentries"] = 1
        self.assertEqual(campaign_phase_for(payload), CampaignPhase.REENTRY_WAIT)


if __name__ == "__main__":
    unittest.main()
