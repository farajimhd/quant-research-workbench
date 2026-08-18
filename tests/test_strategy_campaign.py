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
    side: str = "long",
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
            "campaign_side": side,
        },
    }


class StrategyCampaignTests(unittest.TestCase):
    def test_watchers_do_not_claim_ticker_and_one_campaign_can_reserve_all_legs(self) -> None:
        orchestrator = StrategyCampaignOrchestrator()
        first = orchestrator.register(
            assignment("leg-1", campaign_id="campaign-a", account_id="account-1")
        )
        second = orchestrator.register(
            assignment("leg-2", campaign_id="campaign-a", account_id="account-2")
        )

        self.assertIsNone(first)
        self.assertIsNone(second)
        first = orchestrator.reserve(
            assignment("leg-1", campaign_id="campaign-a", account_id="account-1")
        )
        second = orchestrator.reserve(
            assignment("leg-2", campaign_id="campaign-a", account_id="account-2")
        )
        self.assertEqual(first, second)
        self.assertEqual(
            orchestrator.lease_for(book_id="primary", ticker="AAPL").campaign_id,
            "campaign-a",
        )

    def test_competing_watchers_are_allowed_but_only_one_can_reserve(self) -> None:
        orchestrator = StrategyCampaignOrchestrator(
            [assignment("leg-1", campaign_id="campaign-a")]
        )
        orchestrator.register(assignment("leg-2", campaign_id="campaign-b"))
        orchestrator.reserve(assignment("leg-1", campaign_id="campaign-a"))
        with self.assertRaisesRegex(ValueError, "already owned"):
            orchestrator.reserve(
                assignment("leg-2", campaign_id="campaign-b")
            )

    def test_opposite_side_campaigns_can_watch_but_only_one_can_reserve_ticker(self) -> None:
        orchestrator = StrategyCampaignOrchestrator(
            [assignment("long-leg", campaign_id="campaign-long", side="long")]
        )
        orchestrator.reserve(assignment("long-leg", campaign_id="campaign-long", side="long"))
        with self.assertRaisesRegex(ValueError, "already owned"):
            orchestrator.reserve(
                assignment("short-leg", campaign_id="campaign-short", side="short")
            )
        self.assertEqual(
            orchestrator.lease_for(book_id="primary", ticker="AAPL").campaign_id,
            "campaign-long",
        )

    def test_one_campaign_can_confirm_the_reserved_ticker_from_either_side(self) -> None:
        orchestrator = StrategyCampaignOrchestrator()
        orchestrator.reserve(assignment("long-leg", campaign_id="campaign-a", side="long"))
        lease = orchestrator.claim(
            assignment("short-leg", campaign_id="campaign-a", side="short")
        )
        self.assertEqual(lease.campaign_id, "campaign-a")
        self.assertEqual(
            orchestrator.lease_for(book_id="primary", ticker="AAPL").campaign_id,
            "campaign-a",
        )

    def test_confirmed_lease_remains_for_session_after_campaign_completes(self) -> None:
        orchestrator = StrategyCampaignOrchestrator(
            [
                assignment("leg-1", campaign_id="campaign-a", account_id="account-1"),
                assignment("leg-2", campaign_id="campaign-a", account_id="account-2"),
            ]
        )
        orchestrator.claim(assignment("leg-1", campaign_id="campaign-a", account_id="account-1"))
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
        self.assertIsNotNone(
            orchestrator.lease_for(book_id="primary", ticker="AAPL")
        )

    def test_failed_entry_releases_only_a_provisional_reservation(self) -> None:
        orchestrator = StrategyCampaignOrchestrator()
        contender = assignment("leg-1", campaign_id="campaign-a")
        self.assertEqual(orchestrator.reserve(contender).state, "reserved")
        orchestrator.release_reservation(contender)
        self.assertIsNone(orchestrator.lease_for(book_id="primary", ticker="AAPL"))

    def test_flat_position_after_exit_is_reentry_wait_not_initial_entry(self) -> None:
        payload = assignment(
            "leg-1",
            campaign_id="campaign-a",
            status="reentry_cooldown",
        )
        payload["state"]["reentries"] = 1
        self.assertEqual(campaign_phase_for(payload), CampaignPhase.REENTRY_WAIT)

    def test_negative_short_position_is_managing(self) -> None:
        payload = assignment(
            "short-leg",
            campaign_id="campaign-short",
            side="short",
        )
        self.assertEqual(
            campaign_phase_for(payload, position_quantity=-100),
            CampaignPhase.MANAGING_POSITION,
        )


if __name__ == "__main__":
    unittest.main()
